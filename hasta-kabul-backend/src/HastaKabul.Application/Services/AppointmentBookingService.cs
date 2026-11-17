using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Abstractions.Security;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Appointments;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;

namespace HastaKabul.Application.Services;

/// <summary>
/// Randevu olusturma: sesli asistan (orkestrator, { voiceSessionId, slotId }) ve hasta uygulamasi
/// (oturum sahibinin tarayicisi, { slotId }). Iki kanal da hastayi API dogrulanmis oturumdan bulur,
/// istek hasta kimligi tasimaz ve ayni kurallardan (<see cref="BookAsync"/>) gecer.
/// Kurallar (sirasiyla): gecmis slot 409 SLOT_IN_PAST, dolu slot 409 SLOT_TAKEN, hastanin
/// ayni zaman araliginda ya da ayni gun ayni poliklinikte etkin randevusu varsa
/// 409 APPOINTMENT_CONFLICT. Eszamanli iki istek ayni slotu alirsa veritabani benzersiz
/// indeksi devreye girer ve yine 409 SLOT_TAKEN doner (500 degil).
/// </summary>
public sealed class AppointmentBookingService(
    IVoiceSessionRepository sessions,
    ISlotRepository slots,
    IAppointmentRepository appointments,
    IClinicRepository clinics,
    IDoctorRepository doctors,
    IAuditLogRepository auditLog,
    IUnitOfWork unitOfWork,
    IAccessCodeGenerator accessCodes,
    IRequestContext requestContext,
    IClock clock)
{
    /// <summary>Erisim kodu benzersiz indekse takilirsa en fazla bu kadar yeni kod denenir.</summary>
    public const int MaxAccessCodeAttempts = 5;

    public async Task<CreateAppointmentResponse> CreateFromVoiceSessionAsync(
        CreateAppointmentRequest request, CancellationToken ct = default)
    {
        if (!Guid.TryParse(request.VoiceSessionId, out var sessionId))
            throw new DomainException(ErrorCodes.ValidationFailed, "Geçersiz voiceSessionId.");

        if (!Guid.TryParse(request.SlotId, out var slotId))
            throw new DomainException(ErrorCodes.ValidationFailed, "Geçersiz slotId.");

        var session = await sessions.GetByIdAsync(sessionId, ct)
            ?? throw new DomainException(ErrorCodes.VoiceSessionNotFound, "Ses oturumu bulunamadı.");

        var now = clock.UtcNow;

        // Dogrulanmis oturum da omur dolunca kapanir; suresi dolmus oturumla randevu alinmaz.
        if (session.ExpireIfElapsed(now))
        {
            await unitOfWork.SaveChangesAsync(ct);
            throw new DomainException(
                ErrorCodes.VoiceSessionNotVerified,
                "Ses oturumunun süresi doldu; kimlik yeniden doğrulanmalı.");
        }

        if (session.Status is not VoiceSessionStatus.Verified || session.PatientId is null)
            throw new DomainException(
                ErrorCodes.VoiceSessionNotVerified,
                "Randevu oluşturmak için kimlik doğrulaması gerekir.");

        // Sesli asistan oturum basina bir randevu alir: oturum ayni kayitla COMPLETED olur.
        return await BookAsync(session.PatientId.Value, slotId, "voice", session.Complete, ct);
    }

    /// <summary>
    /// Hasta uygulamasindan (asistansiz sayfa) randevu: govde yalnizca slotId tasir, hasta ayni
    /// dogrulanmis oturumdan bulunur ve sesli yolla AYNI kurallardan gecer (gecmis slot, dolu slot,
    /// cakisma, benzersiz indeks). Oturum VERIFIED ya da COMPLETED ve suresi dolmamis olmali; oturum
    /// kapatilmaz, hasta ayni oturumda baska randevu da alabilir (cakisma kurallari yine gecerli).
    /// </summary>
    public async Task<CreateAppointmentResponse> CreateForPatientAppAsync(
        Guid sessionId, string? slotIdText, CancellationToken ct = default)
    {
        if (!Guid.TryParse(slotIdText, out var slotId))
            throw new DomainException(ErrorCodes.ValidationFailed, "Geçersiz slotId.");

        var (_, patientId) = await PatientSessionGuard.RequireAsync(
            sessions, unitOfWork, sessionId, clock.UtcNow,
            "Randevu oluşturmak için kimlik doğrulaması gerekir.", ct);

        return await BookAsync(patientId, slotId, "patient-app", null, ct);
    }

    /// <summary>
    /// Iki kanalin ortak randevu kurallari. <paramref name="beforeSave"/> ayni kayit islemine girecek
    /// ek degisiklik (sesli yolda oturumu kapatmak) icindir.
    /// </summary>
    private async Task<CreateAppointmentResponse> BookAsync(
        Guid patientId, Guid slotId, string channel, Action? beforeSave, CancellationToken ct)
    {
        var now = clock.UtcNow;

        var slot = await slots.GetByIdAsync(slotId, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Seçilen saat bulunamadı.");

        if (slot.HasStarted(now))
            throw new DomainException(
                ErrorCodes.SlotInPast, "Seçilen saat geçmişte kaldı; lütfen ileri bir saat seçin.");

        // Slot zaten alinmissa SLOT_TAKEN doner; orkestrator yeni saat onerir.
        if (slot.IsTaken)
            slot.Reserve();

        await EnsureNoConflictAsync(patientId, slot, ct);

        slot.Reserve();

        var appointment = new Appointment(
            patientId,
            slot.DoctorId,
            slot.ClinicId,
            slot.Id,
            slot.StartTimeUtc,
            accessCodes.Generate());

        await appointments.AddAsync(appointment, ct);
        beforeSave?.Invoke();

        await auditLog.AddAsync(
            new AuditLogEntry(
                "appointment.created",
                channel,
                nameof(Appointment),
                appointment.Id,
                requestContext.RequestId),
            ct);

        await SaveWithConflictHandlingAsync(appointment, ct);

        var clinic = await clinics.GetByIdAsync(slot.ClinicId, ct);
        var doctor = slot.Doctor ?? await doctors.GetByIdAsync(slot.DoctorId, ct);

        return new CreateAppointmentResponse(
            appointment.Id.ToString(),
            slot.Id.ToString(),
            appointment.ScheduledAtUtc,
            doctor?.DisplayName ?? string.Empty,
            clinic?.Name ?? string.Empty,
            StatusNames.ToWire(appointment.Status),
            appointment.AccessCode);
    }

    /// <summary>
    /// Hastanin etkin randevulariyla cakisma: (1) zaman araligi ortusen randevu (poliklinik fark etmez),
    /// (2) ayni Istanbul gununde ayni poliklinikte ikinci randevu. Ikisi de 409 APPOINTMENT_CONFLICT.
    /// </summary>
    private async Task EnsureNoConflictAsync(Guid patientId, AppointmentSlot slot, CancellationToken ct)
    {
        var (dayStart, dayEnd) = IstanbulTime.DayRangeUtc(slot.StartTimeUtc);
        var duration = TimeSpan.FromMinutes(slot.DurationMinutes > 0 ? slot.DurationMinutes : AppointmentSlot.DefaultDurationMinutes);

        // Gun araligini, gun sinirina tasan ortusmeleri de kapsayacak kadar genislet.
        var from = Min(dayStart, slot.StartTimeUtc - duration);
        var to = Max(dayEnd, slot.EndTimeUtc);
        var active = await appointments.GetActiveByPatientAsync(patientId, from, to, ct);

        var start = slot.StartTimeUtc;
        var end = slot.EndTimeUtc;
        if (active.Any(a => a.IsActive && a.ScheduledAtUtc < end && a.ScheduledAtUtc + duration > start))
            throw new DomainException(
                ErrorCodes.AppointmentConflict,
                "Bu saatte başka bir randevunuz var; lütfen farklı bir saat seçin.");

        if (active.Any(a => a.IsActive
                            && a.ClinicId == slot.ClinicId
                            && a.ScheduledAtUtc >= dayStart
                            && a.ScheduledAtUtc < dayEnd))
            throw new DomainException(
                ErrorCodes.AppointmentConflict,
                "Aynı gün bu poliklinikte zaten randevunuz var; lütfen başka bir gün seçin.");
    }

    /// <summary>
    /// Eszamanli ikinci istek ayni slotu kaydetmeye calisirsa benzersiz indeks 409 SLOT_TAKEN'a,
    /// erisim kodu cakismasi ise yeni kodla tekrar denemeye donusur.
    /// </summary>
    private async Task SaveWithConflictHandlingAsync(Appointment appointment, CancellationToken ct)
    {
        for (var attempt = 1; ; attempt++)
        {
            try
            {
                await unitOfWork.SaveChangesAsync(ct);
                return;
            }
            catch (UniqueConflictException ex) when (ex.Target is UniqueConflictTarget.AppointmentSlot)
            {
                throw new DomainException(
                    ErrorCodes.SlotTaken, "Seçilen saat başka bir hasta tarafından alındı.");
            }
            catch (UniqueConflictException ex) when (
                ex.Target is UniqueConflictTarget.AccessCode && attempt < MaxAccessCodeAttempts)
            {
                appointment.ReplaceAccessCode(accessCodes.Generate());
            }
        }
    }

    private static DateTime Min(DateTime a, DateTime b) => a < b ? a : b;
    private static DateTime Max(DateTime a, DateTime b) => a > b ? a : b;
}
