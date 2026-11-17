using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Ai;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Abstractions.Realtime;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Realtime;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Encounters;
using HastaKabul.Domain.Summaries;

namespace HastaKabul.Application.Services;

/// <summary>
/// Sadelestirmenin uretim tarafi: istegi ai-simplify'a gonderir, servis yoksa yedek ozete ya da
/// FAILED/LLM_UNAVAILABLE'a duser, geri cagrisi gelmeyen PENDING ozeti zaman asiminda ayni sekilde
/// sonuclandirir ve her durum degisikliginde hekime summary.ready olayi (status alaniyla) yayar.
/// Klinik giris, hekim reddi (yeniden uretim) ve okuma uclari ayni kurali kullanir.
/// </summary>
public sealed class SummaryGenerationService(
    IClinicalEntryRepository clinicalEntries,
    IAppointmentRepository appointments,
    IUnitOfWork unitOfWork,
    ISimplifyClient simplifyClient,
    IFallbackSummaryProvider fallbackSummaries,
    IRealtimeNotifier notifier,
    SummaryOptions options,
    IClock clock)
{
    /// <summary>
    /// Ozet PENDING kaydedildikten sonra cagrilir. Servis istegi kabul ederse kayit PENDING kalir
    /// (sonuc geri cagriyla gelir); kabul etmezse hemen yedek ozet ya da FAILED yazilir.
    /// </summary>
    public async Task DispatchAsync(
        SimplifiedSummary summary, ClinicalEntry entry, Guid doctorId, CancellationToken ct = default)
    {
        var accepted = await simplifyClient.RequestSimplificationAsync(
            new SimplifyRequest(summary.PatientRef, entry.Diagnosis, entry.Medications, summary.RequestId, summary.Id),
            ct);

        if (accepted)
            return;

        await CompleteWithoutServiceAsync(summary, entry, doctorId, ct);
    }

    /// <summary>
    /// Okuma aninda yaslandirma: geri cagri <see cref="SummaryOptions.PendingTimeout"/> icinde gelmediyse
    /// kayit yedek ozete ya da FAILED/LLM_UNAVAILABLE'a gecer. Gec gelen gercek sonuc FAILED/READY kaydi
    /// yine doldurabilir (hekim karar verene kadar).
    /// </summary>
    /// <returns>Kayit bu cagrida degistiyse true.</returns>
    public async Task<bool> ExpireIfStaleAsync(SimplifiedSummary summary, Guid? doctorId, CancellationToken ct = default)
    {
        if (!summary.IsPendingTooLong(clock.UtcNow, options.PendingTimeout))
            return false;

        var entry = await clinicalEntries.GetByIdAsync(summary.ClinicalEntryId, ct);
        var owner = doctorId ?? (await appointments.GetByIdAsync(summary.AppointmentId, ct))?.DoctorId;

        await CompleteWithoutServiceAsync(summary, entry, owner, ct);
        return true;
    }

    /// <summary>Hekim paneline ozet durum degisikligi. Olay adi sabit (summary.ready); status alani durumu soyler.</summary>
    public Task NotifyAsync(SimplifiedSummary summary, Guid doctorId, CancellationToken ct = default)
        => notifier.SummaryReadyAsync(
            doctorId,
            new SummaryReadyEvent(
                summary.AppointmentId.ToString(),
                summary.Id.ToString(),
                StatusNames.ToWire(summary.Status),
                summary.UpdatedAtUtc),
            ct);

    private async Task CompleteWithoutServiceAsync(
        SimplifiedSummary summary, ClinicalEntry? entry, Guid? doctorId, CancellationToken ct)
    {
        var fallback = entry is null
            ? null
            : await fallbackSummaries.FindAsync(entry.Diagnosis, entry.Medications, ct);

        if (fallback is null)
        {
            summary.MarkFailed(ErrorCodes.LlmUnavailable, clock.UtcNow);
        }
        else
        {
            summary.MarkReady(
                fallback.DiagnosisPlain,
                fallback.Medications,
                fallback.Warnings,
                fallback.UnknownTerms,
                fallback.SourceIds,
                clock.UtcNow);
        }

        await unitOfWork.SaveChangesAsync(ct);

        if (doctorId is { } id)
            await NotifyAsync(summary, id, ct);
    }
}
