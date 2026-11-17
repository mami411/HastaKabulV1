using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.PatientApp;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Voice;

namespace HastaKabul.Application.Services;

/// <summary>
/// Hasta uygulamasinin asistansiz sayfalari (ana sayfa, randevu al, randevularim). Her cagri
/// dogrulanmis ve suresi dolmamis ses oturumuna baglidir (<see cref="PatientSessionGuard"/>); hasta
/// oturumdan bulunur, istek hasta kimligi tasimaz. Yanitlarda TC, tam ad ve patientRef yoktur.
/// Randevu olusturma <see cref="AppointmentBookingService"/>, gecmis muayeneler
/// <see cref="MedicalHistoryService"/> uzerinden gider (kurallar kopyalanmaz).
/// </summary>
public sealed class PatientAppService(
    IVoiceSessionRepository sessions,
    IPatientRepository patients,
    IClinicRepository clinics,
    IDoctorRepository doctors,
    IPatientAppointmentReader appointments,
    SlotService slotService,
    IAuditLogRepository auditLog,
    IUnitOfWork unitOfWork,
    IRequestContext requestContext,
    IClock clock)
{
    /// <summary>Gun seciminde bugun dahil kac gun gosterilir (demo slotlari bugun + 7 gun).</summary>
    public const int SlotDayWindow = 8;

    private const string NotVerifiedMessage = "Bu bilgiler için kimlik doğrulaması gerekir.";

    public async Task<PatientProfileView> GetProfileAsync(Guid sessionId, CancellationToken ct = default)
    {
        var (session, patientId) = await RequireAsync(sessionId, ct);
        var patient = await patients.GetByIdAsync(patientId, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Hasta kaydı bulunamadı.");

        var remaining = (int)Math.Max(0, Math.Floor((session.ExpiresAtUtc - clock.UtcNow).TotalSeconds));
        return new PatientProfileView(
            patient.DisplayName, DateTime.SpecifyKind(session.ExpiresAtUtc, DateTimeKind.Utc), remaining);
    }

    /// <summary>Poliklinikler (ada gore) ve her birinin hekimleri.</summary>
    public async Task<IReadOnlyList<PatientClinicView>> GetClinicsAsync(Guid sessionId, CancellationToken ct = default)
    {
        await RequireAsync(sessionId, ct);
        var allClinics = await clinics.GetAllAsync(ct);
        var allDoctors = await doctors.GetAllAsync(ct);

        return allClinics
            .OrderBy(c => c.Name, StringComparer.Create(System.Globalization.CultureInfo.GetCultureInfo("tr-TR"), false))
            .Select(c => new PatientClinicView(
                c.Code,
                c.Name,
                allDoctors
                    .Where(d => d.ClinicId == c.Id)
                    .Select(d => new PatientDoctorView(d.Id.ToString(), d.DisplayName))
                    .ToList()))
            .ToList();
    }

    /// <summary>Bugunden itibaren <see cref="SlotDayWindow"/> gunun her biri icin bos saat sayisi.</summary>
    public async Task<IReadOnlyList<SlotDayView>> GetSlotDaysAsync(
        Guid sessionId, string? clinicCode, string? doctorId, CancellationToken ct = default)
    {
        await RequireAsync(sessionId, ct);
        var clinic = await slotService.ResolveClinicAsync(clinicCode, ct);
        var doctor = ParseDoctorId(doctorId);
        var today = IstanbulToday();

        var available = await slotService.GetAvailableSlotsAsync(clinic, today, SlotDayWindow, ct);
        var counts = available
            .Where(s => doctor is null || s.DoctorId == doctor)
            .GroupBy(s => LocalDay(s.StartTimeUtc))
            .ToDictionary(g => g.Key, g => g.Count());

        return Enumerable.Range(0, SlotDayWindow)
            .Select(offset => today.AddDays(offset))
            .Select(day => new SlotDayView(day.ToString("yyyy-MM-dd"), counts.GetValueOrDefault(day)))
            .ToList();
    }

    /// <summary>Bir Istanbul gununun bos saatleri (gecmis saat donmez), istenirse tek hekim.</summary>
    public async Task<IReadOnlyList<PatientSlotView>> GetSlotsAsync(
        Guid sessionId, string? clinicCode, DateOnly localDay, string? doctorId, CancellationToken ct = default)
    {
        await RequireAsync(sessionId, ct);
        var clinic = await slotService.ResolveClinicAsync(clinicCode, ct);
        var doctor = ParseDoctorId(doctorId);

        var available = await slotService.GetAvailableSlotsAsync(clinic, localDay, 1, ct);

        return available
            .Where(s => doctor is null || s.DoctorId == doctor)
            .Select(s => new PatientSlotView(
                s.Id.ToString(),
                s.StartTimeUtc,
                s.DoctorId.ToString(),
                s.Doctor?.DisplayName ?? string.Empty))
            .ToList();
    }

    /// <summary>
    /// Bugun (Istanbul) ve sonrasindaki etkin randevular; sesli asistanda alinanlar da burada gorunur.
    /// Okuma audit log'a yazilir (icerik yazilmaz).
    /// </summary>
    public async Task<PatientAppointmentsResponse> GetAppointmentsAsync(Guid sessionId, CancellationToken ct = default)
    {
        var (session, patientId) = await RequireAsync(sessionId, ct);
        var (todayStart, _) = IstanbulTime.DayRangeUtc(IstanbulToday());

        var upcoming = await appointments.GetUpcomingActiveByPatientAsync(patientId, todayStart, ct);

        var clinicNames = new Dictionary<Guid, string>();
        foreach (var clinicId in upcoming.Select(a => a.ClinicId).Distinct())
        {
            var clinic = await clinics.GetByIdAsync(clinicId, ct);
            clinicNames[clinicId] = clinic?.Name ?? string.Empty;
        }

        var views = upcoming
            .Where(a => a.IsActive)
            .Select(a => ToView(a, clinicNames.GetValueOrDefault(a.ClinicId, string.Empty)))
            .ToList();

        await auditLog.AddAsync(
            new AuditLogEntry(
                "voiceSession.appointmentsRead",
                "patient-app",
                nameof(VoiceSession),
                session.Id,
                requestContext.RequestId,
                $"upcoming={views.Count}"),
            ct);
        await unitOfWork.SaveChangesAsync(ct);

        return new PatientAppointmentsResponse(views);
    }

    private static PatientAppointmentView ToView(Appointment appointment, string clinicName) => new(
        appointment.Id.ToString(),
        DateTime.SpecifyKind(appointment.ScheduledAtUtc, DateTimeKind.Utc),
        clinicName,
        appointment.Doctor?.DisplayName ?? string.Empty,
        appointment.Doctor?.Room ?? string.Empty,
        StatusNames.ToWire(appointment.Status),
        appointment.AccessCode);

    private Task<(VoiceSession Session, Guid PatientId)> RequireAsync(Guid sessionId, CancellationToken ct)
        => PatientSessionGuard.RequireAsync(sessions, unitOfWork, sessionId, clock.UtcNow, NotVerifiedMessage, ct);

    private DateOnly IstanbulToday() => LocalDay(clock.UtcNow);

    private static DateOnly LocalDay(DateTime utc)
        => DateOnly.FromDateTime(TimeZoneInfo.ConvertTimeFromUtc(DateTime.SpecifyKind(utc, DateTimeKind.Utc), IstanbulTime.Zone));

    private static Guid? ParseDoctorId(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return null;

        return Guid.TryParse(value, out var id)
            ? id
            : throw new DomainException(ErrorCodes.ValidationFailed, "doctorId geçerli bir kimlik olmalıdır.");
    }
}
