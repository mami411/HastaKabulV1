using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Portal;
using HastaKabul.Application.Contracts.Summaries;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;

namespace HastaKabul.Application.Services;

/// <summary>
/// Hasta portali. Yalnizca hekim onayli ozet doner; onaysiz kayitta
/// SUMMARY_NOT_APPROVED hatasi verilir. Giris demo icin 6 haneli erisim kodudur.
/// Yanitta takma adli kimlik (patientRef), unknownTerms ve errorCode yoktur.
/// </summary>
public sealed class PortalService(
    IAppointmentRepository appointments,
    ISummaryRepository summaries,
    IClinicRepository clinics,
    SummaryService summaryService)
{
    public async Task<PortalSummaryResponse> GetByAccessCodeAsync(
        PortalAccessRequest request, CancellationToken ct = default)
    {
        var code = request.AccessCode?.Trim() ?? string.Empty;
        if (code.Length != 6)
            throw new DomainException(ErrorCodes.ValidationFailed, "Erişim kodu 6 haneli olmalıdır.");

        var appointment = await appointments.FindByAccessCodeAsync(code, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Bu kodla randevu bulunamadı.");

        var summary = await summaries.GetByAppointmentIdAsync(appointment.Id, ct)
            ?? throw new DomainException(ErrorCodes.SummaryNotApproved, "Özetiniz henüz hazır değil.");

        if (summary.Status is not SummaryStatus.Approved)
            throw new DomainException(
                ErrorCodes.SummaryNotApproved,
                "Özetiniz hekim onayından sonra görüntülenebilir.");

        var clinic = await clinics.GetByIdAsync(appointment.ClinicId, ct);
        var view = await summaryService.ToViewAsync(summary, ct);

        return new PortalSummaryResponse(
            appointment.Id.ToString(),
            appointment.Patient?.DisplayName ?? string.Empty,
            clinic?.Name ?? string.Empty,
            appointment.Doctor?.DisplayName ?? string.Empty,
            appointment.ScheduledAtUtc,
            ToPortalView(view));
    }

    /// <summary>Hastaya gidecek alanlar: kimlik, hekime yonelik terim listesi ve hata kodu cikarilir.</summary>
    public static PortalSummaryView ToPortalView(SummaryView view) => new(
        view.SummaryId,
        view.AppointmentId,
        view.Status,
        view.DiagnosisPlain,
        view.Medications,
        view.Warnings,
        view.Sources,
        view.UpdatedAt);
}
