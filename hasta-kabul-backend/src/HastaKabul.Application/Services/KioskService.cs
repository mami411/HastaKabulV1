using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Abstractions.Realtime;
using HastaKabul.Application.Abstractions.Security;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Kiosk;
using HastaKabul.Application.Contracts.Realtime;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Common;

namespace HastaKabul.Application.Services;

/// <summary>
/// Kiosk akisi: TC dogrulama ve "Geldim" onayi.
/// KVKK siniri burada bitiyor: TC bu servise gelir, hash'e cevrilir ve bir daha
/// ne doner ne loglanir. Disariya yalnizca patientRef ve displayName cikar.
/// </summary>
public sealed class KioskService(
    IPatientRepository patients,
    IAppointmentRepository appointments,
    IClinicRepository clinics,
    IAuditLogRepository auditLog,
    IUnitOfWork unitOfWork,
    ITcHasher tcHasher,
    IRealtimeNotifier notifier,
    IRequestContext requestContext,
    IClock clock)
{
    public async Task<KioskVerificationResponse> VerifyAsync(VerifyTcRequest request, CancellationToken ct = default)
    {
        if (!TcKimlikNo.IsWellFormed(request.Tc))
            throw new DomainException(ErrorCodes.ValidationFailed, "TC kimlik numarası biçimi geçersiz; 11 haneli geçerli bir numara girin.");

        var patient = await patients.FindByTcHashAsync(tcHasher.Hash(request.Tc), ct)
            ?? throw new DomainException(ErrorCodes.TcNotFound, "Bu kimlik numarasıyla kayıt bulunamadı.");

        var (dayStart, dayEnd) = IstanbulTime.DayRangeUtc(clock.UtcNow);
        var appointment = await appointments.FindTodaysScheduledAsync(patient.Id, dayStart, dayEnd, ct)
            ?? throw new DomainException(ErrorCodes.NoAppointmentToday, "Bugün için randevunuz bulunmuyor.");

        var clinic = await clinics.GetByIdAsync(appointment.ClinicId, ct);

        await auditLog.AddAsync(
            new AuditLogEntry("kiosk.verify", "kiosk", nameof(Appointment), appointment.Id, requestContext.RequestId),
            ct);
        await unitOfWork.SaveChangesAsync(ct);

        return new KioskVerificationResponse(
            Verified: true,
            PatientRef: patient.PatientRef.ToString(),
            DisplayName: patient.DisplayName,
            Appointment: new KioskAppointmentView(
                appointment.Id.ToString(),
                clinic?.Name ?? string.Empty,
                appointment.Doctor?.DisplayName ?? string.Empty,
                appointment.Doctor?.Room ?? string.Empty,
                appointment.ScheduledAtUtc,
                StatusNames.ToWire(appointment.Status)));
    }

    /// <summary>"Geldim" butonu: randevu CHECKED_IN olur ve hekim panelinde satir aninda degisir.</summary>
    public async Task<CheckInResponse> CheckInAsync(CheckInRequest request, CancellationToken ct = default)
    {
        if (!Guid.TryParse(request.PatientRef, out var patientRef))
            throw new DomainException(ErrorCodes.ValidationFailed, "Geçersiz hasta referansı.");

        var patient = await patients.GetByPatientRefAsync(patientRef, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Hasta kaydı bulunamadı.");

        var (dayStart, dayEnd) = IstanbulTime.DayRangeUtc(clock.UtcNow);
        var appointment = await appointments.FindTodaysScheduledAsync(patient.Id, dayStart, dayEnd, ct)
            ?? throw new DomainException(ErrorCodes.NoAppointmentToday, "Bugün için bekleyen randevunuz bulunmuyor.");

        appointment.CheckIn(clock.UtcNow);

        await auditLog.AddAsync(
            new AuditLogEntry("kiosk.checkIn", "kiosk", nameof(Appointment), appointment.Id, requestContext.RequestId),
            ct);
        await unitOfWork.SaveChangesAsync(ct);

        await notifier.AppointmentStatusChangedAsync(
            appointment.DoctorId,
            new AppointmentStatusChangedEvent(
                appointment.Id.ToString(),
                patient.DisplayName,
                StatusNames.ToWire(appointment.Status),
                appointment.UpdatedAtUtc),
            ct);

        return new CheckInResponse(
            appointment.Id.ToString(),
            StatusNames.ToWire(appointment.Status),
            appointment.UpdatedAtUtc);
    }
}
