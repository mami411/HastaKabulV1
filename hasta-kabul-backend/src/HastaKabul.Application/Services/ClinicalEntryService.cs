using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Abstractions.Realtime;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Appointments;
using HastaKabul.Application.Contracts.Realtime;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Encounters;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Summaries;

namespace HastaKabul.Application.Services;

/// <summary>
/// Hekim tani/recete girisi ve asenkron sadelestirmenin tetiklenmesi.
/// Akis: hekim girdisi -> API PENDING ozet kaydi acar -> AI servisine yalnizca
/// beyaz listedeki alanlar gider -> servis 202 doner -> hazir olunca callback gelir.
/// </summary>
public sealed class ClinicalEntryService(
    IAppointmentRepository appointments,
    IClinicalEntryRepository clinicalEntries,
    ISummaryRepository summaries,
    IAuditLogRepository auditLog,
    IUnitOfWork unitOfWork,
    SummaryGenerationService generation,
    IRealtimeNotifier notifier,
    IRequestContext requestContext,
    IClock clock)
{
    /// <summary>Veritabani kolon siniri (clinical_entries.diagnosis varchar(400)).</summary>
    public const int MaxDiagnosisLength = 400;
    public const int MaxMedications = 20;
    public const int MaxMedicationLength = 200;

    public async Task<CreateClinicalEntryResponse> CreateAsync(
        Guid doctorId, Guid appointmentId, CreateClinicalEntryRequest request, CancellationToken ct = default)
    {
        Validate(request);

        var appointment = await appointments.GetByIdWithDetailsAsync(appointmentId, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Randevu bulunamadı.");

        if (appointment.DoctorId != doctorId)
            throw new DomainException(ErrorCodes.Forbidden, "Bu randevu başka bir hekime ait.");

        // Hasta muayeneye alinmadan tani girilemez; panelden "Geldi ve Bekliyor" ise
        // kayit sirasinda muayeneye gecilir (ve panele durum olayi gider).
        var startedExam = false;
        if (appointment.Status is AppointmentStatus.CheckedIn)
        {
            appointment.StartExam(clock.UtcNow);
            startedExam = true;
        }
        else if (appointment.Status is not (AppointmentStatus.InExam or AppointmentStatus.Completed))
        {
            throw new DomainException(
                ErrorCodes.InvalidStatusTransition,
                $"Tanı girmek için hastanın kabul edilmiş olması gerekir (randevu durumu: {StatusNames.ToWire(appointment.Status)}).");
        }

        var patientRef = appointment.Patient?.PatientRef
            ?? throw new DomainException(ErrorCodes.Internal, "Randevunun hasta kaydı yüklenemedi.");

        var entry = new ClinicalEntry(appointment.Id, doctorId, request.Diagnosis.Trim(),
            (request.Medications ?? []).Where(m => !string.IsNullOrWhiteSpace(m)));
        await clinicalEntries.AddAsync(entry, ct);

        var summary = new SimplifiedSummary(appointment.Id, entry.Id, patientRef, requestContext.RequestId);
        await summaries.AddAsync(summary, ct);

        await auditLog.AddAsync(
            new AuditLogEntry(
                "clinicalEntry.created",
                $"doctor:{doctorId}",
                nameof(ClinicalEntry),
                entry.Id,
                requestContext.RequestId),
            ct);
        await unitOfWork.SaveChangesAsync(ct);

        if (startedExam)
        {
            await notifier.AppointmentStatusChangedAsync(
                appointment.DoctorId,
                new AppointmentStatusChangedEvent(
                    appointment.Id.ToString(),
                    appointment.Patient?.DisplayName ?? string.Empty,
                    StatusNames.ToWire(appointment.Status),
                    appointment.UpdatedAtUtc),
                ct);
        }

        // Servis yoksa yedek ozet ya da FAILED; her iki durumda da summary.ready olayi yayilir.
        await generation.DispatchAsync(summary, entry, doctorId, ct);

        return new CreateClinicalEntryResponse(
            entry.Id.ToString(),
            summary.Id.ToString(),
            StatusNames.ToWire(summary.Status));
    }

    private static void Validate(CreateClinicalEntryRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.Diagnosis))
            throw new DomainException(ErrorCodes.ValidationFailed, "Tanı boş olamaz.");

        if (request.Diagnosis.Trim().Length > MaxDiagnosisLength)
            throw new DomainException(
                ErrorCodes.ValidationFailed, $"Tanı en fazla {MaxDiagnosisLength} karakter olabilir.");

        var medications = request.Medications ?? [];
        if (medications.Count > MaxMedications)
            throw new DomainException(
                ErrorCodes.ValidationFailed, $"En fazla {MaxMedications} ilaç girilebilir.");

        if (medications.Any(m => m is not null && m.Trim().Length > MaxMedicationLength))
            throw new DomainException(
                ErrorCodes.ValidationFailed, $"Bir ilaç satırı en fazla {MaxMedicationLength} karakter olabilir.");
    }
}
