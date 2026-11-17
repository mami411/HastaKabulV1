using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Summaries;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Summaries;

namespace HastaKabul.Application.Services;

/// <summary>
/// Sade ozetin yasam dongusu: AI callback'i, hekim onayi/reddi ve okuma.
/// Hekim onayi zorunludur; halusinasyona karsi tek gercek bariyer budur.
/// </summary>
public sealed class SummaryService(
    ISummaryRepository summaries,
    IAppointmentRepository appointments,
    IClinicalEntryRepository clinicalEntries,
    IKbEntryRepository knowledgeBase,
    IAuditLogRepository auditLog,
    IUnitOfWork unitOfWork,
    SummaryGenerationService generation,
    IRequestContext requestContext,
    IClock clock)
{
    /// <summary>Veritabani kolon sinirlari (simplified_summaries).</summary>
    public const int MaxDiagnosisPlainLength = 2000;
    public const int MaxFailureCodeLength = 40;

    /// <summary>PUT /api/v1/summaries/{summaryId} - yalnizca X-Service-Key ile cagrilir.</summary>
    public async Task<SummaryView> ApplyCallbackAsync(
        Guid summaryId, SummaryCallbackRequest request, CancellationToken ct = default)
    {
        var summary = await summaries.GetByIdAsync(summaryId, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Özet kaydı bulunamadı.");

        // Hekimin karar verdigi (APPROVED/REJECTED) ozete gec gelen sonuc yazilmaz: 409.
        if (!string.IsNullOrWhiteSpace(request.ErrorCode) && summary.Status is SummaryStatus.Ready)
        {
            // Zaman asiminda yedek ozetle READY olmus kayda gec gelen hata bildirimi, hekimin
            // gorebilecegi icerigi silmez; kayit oldugu gibi doner (200, idempotent).
            await auditLog.AddAsync(
                new AuditLogEntry(
                    "summary.callbackIgnored",
                    "ai-simplify",
                    nameof(SimplifiedSummary),
                    summary.Id,
                    requestContext.RequestId,
                    StatusNames.ToWire(summary.Status)),
                ct);
            await unitOfWork.SaveChangesAsync(ct);
            return await ToViewAsync(summary, ct);
        }

        if (!string.IsNullOrWhiteSpace(request.ErrorCode))
        {
            var code = request.ErrorCode!.Trim();
            summary.MarkFailed(code.Length > MaxFailureCodeLength ? code[..MaxFailureCodeLength] : code, clock.UtcNow);
        }
        else
        {
            if (string.IsNullOrWhiteSpace(request.DiagnosisPlain))
                throw new DomainException(ErrorCodes.ValidationFailed, "diagnosisPlain boş olamaz.");

            if (request.DiagnosisPlain!.Length > MaxDiagnosisPlainLength)
                throw new DomainException(
                    ErrorCodes.ValidationFailed, $"diagnosisPlain en fazla {MaxDiagnosisPlainLength} karakter olabilir.");

            summary.MarkReady(
                request.DiagnosisPlain!,
                (request.Medications ?? [])
                    .Where(m => m is not null)
                    .Select(m => new SimplifiedMedication
                    {
                        Name = m.Name ?? string.Empty,
                        Purpose = m.Purpose ?? string.Empty,
                        Usage = m.Usage ?? string.Empty
                    }),
                (request.Warnings ?? []).Where(w => !string.IsNullOrWhiteSpace(w)),
                (request.UnknownTerms ?? []).Where(t => !string.IsNullOrWhiteSpace(t)),
                (request.SourceIds ?? []).Where(s => !string.IsNullOrWhiteSpace(s)),
                clock.UtcNow);
        }

        await auditLog.AddAsync(
            new AuditLogEntry(
                "summary.callback",
                "ai-simplify",
                nameof(SimplifiedSummary),
                summary.Id,
                requestContext.RequestId,
                StatusNames.ToWire(summary.Status)),
            ct);
        await unitOfWork.SaveChangesAsync(ct);

        // READY de FAILED da panele bildirilir (olay adi summary.ready, status alani durumu soyler).
        var appointment = await appointments.GetByIdAsync(summary.AppointmentId, ct);
        if (appointment is not null)
            await generation.NotifyAsync(summary, appointment.DoctorId, ct);

        return await ToViewAsync(summary, ct);
    }

    /// <summary>Hekim onizlemesi: yalniz ozetin ait oldugu randevunun hekimi okuyabilir.</summary>
    public async Task<SummaryView> GetAsync(Guid doctorId, Guid summaryId, CancellationToken ct = default)
    {
        var summary = await summaries.GetByIdAsync(summaryId, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Özet kaydı bulunamadı.");

        var appointment = await EnsureOwnedByDoctorAsync(summary, doctorId, ct);

        // Geri cagri gelmeden zaman asimi dolduysa ozet burada sonuclandirilir.
        await generation.ExpireIfStaleAsync(summary, appointment.DoctorId, ct);

        return await ToViewAsync(summary, ct);
    }

    public async Task<SummaryDecisionResponse> ApproveAsync(Guid doctorId, Guid summaryId, CancellationToken ct = default)
    {
        var summary = await summaries.GetByIdAsync(summaryId, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Özet kaydı bulunamadı.");

        await EnsureOwnedByDoctorAsync(summary, doctorId, ct);

        summary.Approve(doctorId, clock.UtcNow);
        await RecordDecisionAsync(summary, doctorId, "summary.approved", ct);

        return new SummaryDecisionResponse(summary.Id.ToString(), StatusNames.ToWire(summary.Status), summary.UpdatedAtUtc);
    }

    /// <summary>
    /// Hekim reddi = "yeniden olustur" (README): karar REJECTED olarak kaydedilir ve yanitta REJECTED
    /// doner; ardindan ayni kayit ayni girdiyle yeniden uretime gonderilir (PENDING). Servis yoksa
    /// yedek ozet (READY) ya da FAILED yazilir. Yeni sonuc summary.ready olayiyla gelir.
    /// </summary>
    public async Task<SummaryDecisionResponse> RejectAsync(Guid doctorId, Guid summaryId, CancellationToken ct = default)
    {
        var summary = await summaries.GetByIdAsync(summaryId, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Özet kaydı bulunamadı.");

        await EnsureOwnedByDoctorAsync(summary, doctorId, ct);

        summary.Reject(clock.UtcNow);
        await RecordDecisionAsync(summary, doctorId, "summary.rejected", ct);
        var decision = new SummaryDecisionResponse(
            summary.Id.ToString(), StatusNames.ToWire(summary.Status), summary.UpdatedAtUtc);

        var entry = await clinicalEntries.GetByIdAsync(summary.ClinicalEntryId, ct);
        if (entry is not null)
        {
            summary.Retry(requestContext.RequestId, clock.UtcNow);
            await auditLog.AddAsync(
                new AuditLogEntry(
                    "summary.regenerationRequested",
                    $"doctor:{doctorId}",
                    nameof(SimplifiedSummary),
                    summary.Id,
                    requestContext.RequestId,
                    StatusNames.ToWire(summary.Status)),
                ct);
            await unitOfWork.SaveChangesAsync(ct);
            await generation.NotifyAsync(summary, doctorId, ct);
            await generation.DispatchAsync(summary, entry, doctorId, ct);
        }

        return decision;
    }

    private async Task<Appointment> EnsureOwnedByDoctorAsync(SimplifiedSummary summary, Guid doctorId, CancellationToken ct)
    {
        var appointment = await appointments.GetByIdAsync(summary.AppointmentId, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Randevu bulunamadı.");

        if (appointment.DoctorId != doctorId)
            throw new DomainException(ErrorCodes.Forbidden, "Bu özet başka bir hekimin hastasına ait.");

        return appointment;
    }

    private async Task RecordDecisionAsync(SimplifiedSummary summary, Guid doctorId, string action, CancellationToken ct)
    {
        await auditLog.AddAsync(
            new AuditLogEntry(
                action,
                $"doctor:{doctorId}",
                nameof(SimplifiedSummary),
                summary.Id,
                requestContext.RequestId,
                StatusNames.ToWire(summary.Status)),
            ct);

        await unitOfWork.SaveChangesAsync(ct);
    }

    internal async Task<SummaryView> ToViewAsync(SimplifiedSummary summary, CancellationToken ct)
    {
        var sources = await ResolveSourcesAsync(summary.SourceIds, ct);

        return new SummaryView(
            summary.Id.ToString(),
            summary.AppointmentId.ToString(),
            summary.PatientRef.ToString(),
            StatusNames.ToWire(summary.Status),
            summary.DiagnosisPlain,
            summary.Medications.Select(m => new MedicationView(m.Name, m.Purpose, m.Usage)).ToList(),
            summary.Warnings,
            summary.UnknownTerms,
            sources,
            summary.FailureCode,
            summary.UpdatedAtUtc);
    }

    /// <summary>Kaynaklar sources: [{ id, title }] olarak gosterilir.</summary>
    private async Task<IReadOnlyList<SourceView>> ResolveSourcesAsync(IReadOnlyList<string> sourceIds, CancellationToken ct)
    {
        if (sourceIds.Count == 0)
            return [];

        var guids = sourceIds
            .Select(id => Guid.TryParse(id, out var g) ? g : (Guid?)null)
            .Where(g => g.HasValue)
            .Select(g => g!.Value)
            .ToList();

        var entries = guids.Count == 0
            ? []
            : await knowledgeBase.GetByIdsAsync(guids, ct);

        var titles = entries.ToDictionary(e => e.Id.ToString(), e => e.Title);

        return sourceIds
            .Select(id => new SourceView(id, titles.TryGetValue(id, out var title) ? title : id))
            .ToList();
    }
}
