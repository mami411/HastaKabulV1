using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Contracts.Summaries;
using HastaKabul.Application.Contracts.Voice;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Voice;

namespace HastaKabul.Application.Services;

/// <summary>
/// Ses asistaninin "gecmis muayenelerim" sorusu. Hasta, orkestratorun tasidigi bir kimlikle
/// degil, dogrulanmis ses oturumundan bulunur. Yalnizca hekim onayli ozetler doner ve
/// her okuma audit log'a yazilir.
/// </summary>
public sealed class MedicalHistoryService(
    IVoiceSessionRepository sessions,
    ISummaryRepository summaries,
    IClinicRepository clinics,
    IAuditLogRepository auditLog,
    IUnitOfWork unitOfWork,
    IRequestContext requestContext,
    IClock clock)
{
    public const int DefaultLimit = 3;
    public const int MinLimit = 1;
    public const int MaxLimit = 5;

    /// <summary>Hasta uygulamasinin "Muayenelerim / Ilaclarim" sayfalari icin limit araligi (varsayilan 10).</summary>
    public const int AppDefaultLimit = 10;
    public const int AppMaxLimit = 20;

    /// <summary>Orkestratorun sorgusu (X-Service-Key): limit 1-5, aktor "voice-orchestrator".</summary>
    public Task<MedicalHistoryResponse> GetAsync(Guid sessionId, int limit, CancellationToken ct = default)
        => ReadAsync(sessionId, limit, MaxLimit, "voice-orchestrator", ct);

    /// <summary>
    /// Hasta uygulamasinin sorgusu (oturum sahibinin tarayicisi): ayni kurallar ve ayni onayli ozet
    /// filtresi; limit 1-20, aktor "patient-app".
    /// </summary>
    public Task<MedicalHistoryResponse> GetForPatientAppAsync(Guid sessionId, int limit, CancellationToken ct = default)
        => ReadAsync(sessionId, limit, AppMaxLimit, "patient-app", ct);

    private async Task<MedicalHistoryResponse> ReadAsync(
        Guid sessionId, int limit, int maxLimit, string actor, CancellationToken ct)
    {
        if (limit < MinLimit || limit > maxLimit)
            throw new DomainException(
                ErrorCodes.ValidationFailed, $"limit {MinLimit} ile {maxLimit} arasında olmalıdır.");

        var (session, patientId) = await PatientSessionGuard.RequireAsync(
            sessions, unitOfWork, sessionId, clock.UtcNow,
            "Geçmiş muayeneler için kimlik doğrulaması gerekir.", ct);

        var approved = await summaries.GetApprovedByPatientAsync(patientId, ct);

        // Bir randevuda birden fazla onayli ozet olabilir; en guncel olani alinir.
        // Durum filtresi depoda da var; onaysiz icerik hastaya hicbir yoldan sizmasin diye burada da.
        var latestPerAppointment = approved
            .Where(s => s.Status is SummaryStatus.Approved
                        && s.Appointment is not null
                        && s.Appointment.PatientId == patientId
                        && !string.IsNullOrWhiteSpace(s.DiagnosisPlain))
            .GroupBy(s => s.AppointmentId)
            .Select(g => g.OrderByDescending(s => s.UpdatedAtUtc).First())
            .OrderByDescending(s => s.Appointment!.ScheduledAtUtc)
            .Take(limit)
            .ToList();

        var clinicNames = new Dictionary<Guid, string>();
        foreach (var clinicId in latestPerAppointment.Select(s => s.Appointment!.ClinicId).Distinct())
        {
            var clinic = await clinics.GetByIdAsync(clinicId, ct);
            clinicNames[clinicId] = clinic?.Name ?? string.Empty;
        }

        var visits = latestPerAppointment
            .Select(s => new MedicalHistoryVisit(
                s.AppointmentId.ToString(),
                DateTime.SpecifyKind(s.Appointment!.ScheduledAtUtc, DateTimeKind.Utc),
                clinicNames.GetValueOrDefault(s.Appointment.ClinicId, string.Empty),
                s.Appointment.Doctor?.DisplayName ?? string.Empty,
                s.DiagnosisPlain!,
                s.Medications.Select(m => new MedicationView(m.Name, m.Purpose, m.Usage)).ToList(),
                s.Warnings.ToList()))
            .ToList();

        // Saglik verisine her erisim kayit altina alinir (KVKK); icerik yazilmaz.
        await auditLog.AddAsync(
            new AuditLogEntry(
                "voiceSession.medicalHistoryRead",
                actor,
                nameof(VoiceSession),
                session.Id,
                requestContext.RequestId,
                $"limit={limit};visits={visits.Count}"),
            ct);
        await unitOfWork.SaveChangesAsync(ct);

        return new MedicalHistoryResponse(visits);
    }
}
