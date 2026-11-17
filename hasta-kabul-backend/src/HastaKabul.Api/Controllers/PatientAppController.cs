using System.Globalization;
using HastaKabul.Application.Contracts.Appointments;
using HastaKabul.Application.Contracts.PatientApp;
using HastaKabul.Application.Contracts.Voice;
using HastaKabul.Application.Services;
using HastaKabul.Domain.Common;
using Microsoft.AspNetCore.Mvc;

namespace HastaKabul.Api.Controllers;

/// <summary>
/// Hasta uygulamasinin asistansiz sayfalari. Kimlik, tarayicinin TC tus takimiyla dogruladigi ses
/// oturumudur (yoldaki sessionId): oturum VERIFIED/COMPLETED ve suresi dolmamis olmali, hasta
/// oturumdan bulunur, baska hastanin verisine yol yoktur. Servis anahtari istemez (tarayici cagirir);
/// orkestratorun kullandigi uclar (medical-history, POST /appointments) degismez.
/// </summary>
[ApiController]
[Route("api/v1/voice-sessions/{sessionId:guid}")]
public sealed class PatientAppController(
    PatientAppService patientApp,
    AppointmentBookingService booking,
    MedicalHistoryService medicalHistory) : ControllerBase
{
    /// <summary>Karsilama: maskeli ad ve oturumun bitis ani.</summary>
    [HttpGet("profile")]
    public Task<PatientProfileView> GetProfileAsync(Guid sessionId, CancellationToken ct)
        => patientApp.GetProfileAsync(sessionId, ct);

    [HttpGet("clinics")]
    public Task<IReadOnlyList<PatientClinicView>> GetClinicsAsync(Guid sessionId, CancellationToken ct)
        => patientApp.GetClinicsAsync(sessionId, ct);

    /// <summary>Bugunden itibaren 8 gunun bos saat sayilari (clinic zorunlu, doctorId istege bagli).</summary>
    [HttpGet("slot-days")]
    public Task<IReadOnlyList<SlotDayView>> GetSlotDaysAsync(
        Guid sessionId, [FromQuery] string? clinic, [FromQuery] string? doctorId, CancellationToken ct)
        => patientApp.GetSlotDaysAsync(sessionId, clinic, doctorId, ct);

    [HttpGet("slots")]
    public Task<IReadOnlyList<PatientSlotView>> GetSlotsAsync(
        Guid sessionId,
        [FromQuery] string? clinic,
        [FromQuery] string? date,
        [FromQuery] string? doctorId,
        CancellationToken ct)
        => patientApp.GetSlotsAsync(sessionId, clinic, ParseDate(date), doctorId, ct);

    [HttpGet("appointments")]
    public Task<PatientAppointmentsResponse> GetAppointmentsAsync(Guid sessionId, CancellationToken ct)
        => patientApp.GetAppointmentsAsync(sessionId, ct);

    /// <summary>
    /// Asistansiz randevu: { slotId }. Sesli yolla ayni kurallar (409 SLOT_IN_PAST, SLOT_TAKEN,
    /// APPOINTMENT_CONFLICT); yanit sesli yolla ayni bicimde (erisim kodu dahil).
    /// </summary>
    [HttpPost("appointments")]
    public Task<CreateAppointmentResponse> CreateAppointmentAsync(
        Guid sessionId, [FromBody] CreatePatientAppointmentRequest request, CancellationToken ct)
        => booking.CreateForPatientAppAsync(sessionId, request.SlotId, ct);

    /// <summary>Hekim onayli gecmis muayeneler ve ilaclar (limit 1-20, varsayilan 10).</summary>
    [HttpGet("visits")]
    public Task<MedicalHistoryResponse> GetVisitsAsync(Guid sessionId, [FromQuery] string? limit, CancellationToken ct)
        => medicalHistory.GetForPatientAppAsync(sessionId, ParseLimit(limit), ct);

    private static DateOnly ParseDate(string? value)
    {
        if (!DateOnly.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var day))
            throw new DomainException(ErrorCodes.ValidationFailed, "date parametresi YYYY-AA-GG (ör. 2026-11-17) biçiminde olmalıdır.");

        return day;
    }

    private static int ParseLimit(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return MedicalHistoryService.AppDefaultLimit;

        if (!int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out var limit))
            throw new DomainException(
                ErrorCodes.ValidationFailed,
                $"limit {MedicalHistoryService.MinLimit} ile {MedicalHistoryService.AppMaxLimit} arasında bir sayı olmalıdır.");

        return limit;
    }
}
