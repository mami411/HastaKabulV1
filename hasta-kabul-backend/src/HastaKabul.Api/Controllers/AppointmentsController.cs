using System.Globalization;
using HastaKabul.Api.Infrastructure;
using HastaKabul.Application.Contracts.Appointments;
using HastaKabul.Application.Services;
using HastaKabul.Domain.Common;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace HastaKabul.Api.Controllers;

/// <summary>
/// Randevu uc noktalari: hekim panelinin gun listesi, durum guncelleme, tani girisi
/// ve ses asistanindan randevu olusturma.
/// </summary>
[ApiController]
[Route("api/v1/appointments")]
public sealed class AppointmentsController(
    DoctorPanelService panel,
    ClinicalEntryService clinicalEntries,
    AppointmentBookingService booking,
    IDoctorContext doctorContext) : ControllerBase
{
    /// <summary>Panelin baglanti sonrasi cektigi tam liste.</summary>
    [HttpGet]
    [Authorize]
    public Task<IReadOnlyList<AppointmentListItem>> GetDayAsync(
        [FromQuery] Guid? doctorId, [FromQuery] string? date, CancellationToken ct)
        => panel.GetDayAsync(doctorContext.Require(doctorId), ParseDate(date), ct);

    /// <summary>Muayenede / Tamamlandi gecisleri; gecersiz gecis 409 doner.</summary>
    [HttpPatch("{appointmentId:guid}/status")]
    [Authorize]
    public Task<AppointmentStatusResponse> UpdateStatusAsync(
        Guid appointmentId,
        [FromBody] UpdateAppointmentStatusRequest request,
        CancellationToken ct)
        => panel.UpdateStatusAsync(doctorContext.Require(null), appointmentId, request, ct);

    /// <summary>Hekimin tani ve recete girisi; asenkron sadelestirmeyi tetikler.</summary>
    [HttpPost("{appointmentId:guid}/clinical-entries")]
    [Authorize]
    public Task<CreateClinicalEntryResponse> CreateClinicalEntryAsync(
        Guid appointmentId,
        [FromBody] CreateClinicalEntryRequest request,
        CancellationToken ct)
        => clinicalEntries.CreateAsync(doctorContext.Require(null), appointmentId, request, ct);

    /// <summary>
    /// Ses asistanindan randevu olusturma. Govde yalnizca { voiceSessionId, slotId }
    /// tasir; hastayi API dogrulanmis oturumdan bulur.
    /// </summary>
    [HttpPost]
    [RequireServiceKey]
    public Task<CreateAppointmentResponse> CreateAsync(
        [FromBody] CreateAppointmentRequest request, CancellationToken ct)
        => booking.CreateFromVoiceSessionAsync(request, ct);

    private static DateOnly? ParseDate(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return null;

        if (!DateOnly.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var date))
            throw new DomainException(ErrorCodes.ValidationFailed, "date parametresi YYYY-AA-GG (ör. 2026-11-17) biçiminde olmalıdır.");

        return date;
    }
}
