using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Clinics;
using HastaKabul.Domain.Doctors;
using HastaKabul.Domain.Encounters;
using HastaKabul.Domain.Knowledge;
using HastaKabul.Domain.Patients;
using HastaKabul.Domain.Summaries;
using HastaKabul.Domain.Voice;

namespace HastaKabul.Application.Abstractions.Persistence;

public interface IPatientRepository
{
    /// <summary>Eslesme yalnizca tc_hash uzerinden yapilir; duz TC hic sorgulanmaz.</summary>
    Task<Patient?> FindByTcHashAsync(string tcHash, CancellationToken ct = default);
    Task<Patient?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task<Patient?> GetByPatientRefAsync(Guid patientRef, CancellationToken ct = default);
    Task AddAsync(Patient patient, CancellationToken ct = default);
}

public interface IClinicRepository
{
    Task<Clinic?> GetByIdAsync(Guid id, CancellationToken ct = default);
    /// <summary>Kod, ad veya es anlamli uzerinden cozer (shared/clinics.json ile ayni liste).</summary>
    Task<Clinic?> ResolveAsync(string nameOrCode, CancellationToken ct = default);
    Task<IReadOnlyList<Clinic>> GetAllAsync(CancellationToken ct = default);
}

public interface IDoctorRepository
{
    Task<Doctor?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task<IReadOnlyList<Doctor>> GetAllAsync(CancellationToken ct = default);
}

public interface ISlotRepository
{
    Task<AppointmentSlot?> GetByIdAsync(Guid id, CancellationToken ct = default);
    /// <summary>Bos slotlar; tarih Europe/Istanbul gunudur, sinirlar UTC olarak gelir.</summary>
    Task<IReadOnlyList<AppointmentSlot>> GetAvailableAsync(
        Guid clinicId, DateTime fromUtc, DateTime toUtc, CancellationToken ct = default);
}

public interface IAppointmentRepository
{
    Task<Appointment?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task<Appointment?> GetByIdWithDetailsAsync(Guid id, CancellationToken ct = default);

    /// <summary>
    /// Kiosk eslestirme kurali: hasta + bugun + SCHEDULED; birden fazlaysa en erken randevu.
    /// </summary>
    Task<Appointment?> FindTodaysScheduledAsync(
        Guid patientId, DateTime dayStartUtc, DateTime dayEndUtc, CancellationToken ct = default);

    Task<IReadOnlyList<Appointment>> GetByDoctorAndDayAsync(
        Guid doctorId, DateTime dayStartUtc, DateTime dayEndUtc, CancellationToken ct = default);

    Task<Appointment?> FindByAccessCodeAsync(string accessCode, CancellationToken ct = default);

    /// <summary>
    /// Hastanin [fromUtc, toUtc) araliginda baslayan etkin (SCHEDULED/CHECKED_IN/IN_EXAM) randevulari.
    /// Cakisan randevu ve ayni gun ayni poliklinik kurali icin.
    /// </summary>
    Task<IReadOnlyList<Appointment>> GetActiveByPatientAsync(
        Guid patientId, DateTime fromUtc, DateTime toUtc, CancellationToken ct = default);

    Task AddAsync(Appointment appointment, CancellationToken ct = default);
}

/// <summary>
/// Hasta uygulamasinin "Randevularim" listesi. <see cref="IAppointmentRepository"/>'den ayri tutuldu:
/// yalniz hasta tarafi okuma, hekimiyle birlikte.
/// </summary>
public interface IPatientAppointmentReader
{
    /// <summary>Hastanin <paramref name="fromUtc"/> ve sonrasinda baslayan etkin randevulari, zamana gore.</summary>
    Task<IReadOnlyList<Appointment>> GetUpcomingActiveByPatientAsync(
        Guid patientId, DateTime fromUtc, CancellationToken ct = default);
}

public interface IClinicalEntryRepository
{
    Task<ClinicalEntry?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task AddAsync(ClinicalEntry entry, CancellationToken ct = default);
}

public interface ISummaryRepository
{
    Task<SimplifiedSummary?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task<SimplifiedSummary?> GetByAppointmentIdAsync(Guid appointmentId, CancellationToken ct = default);

    /// <summary>Her randevunun en guncel ozeti, tek sorguda (hekim paneli listesi; N+1 yerine).</summary>
    Task<IReadOnlyDictionary<Guid, SimplifiedSummary>> GetLatestByAppointmentIdsAsync(
        IReadOnlyCollection<Guid> appointmentIds, CancellationToken ct = default);

    /// <summary>
    /// Hastanin yalnizca hekim onayli (APPROVED) ozetleri; randevu ve hekimiyle birlikte,
    /// randevu zamanina gore yeniden eskiye.
    /// </summary>
    Task<IReadOnlyList<SimplifiedSummary>> GetApprovedByPatientAsync(Guid patientId, CancellationToken ct = default);

    Task AddAsync(SimplifiedSummary summary, CancellationToken ct = default);
}

public interface IVoiceSessionRepository
{
    Task<VoiceSession?> GetByIdAsync(Guid id, CancellationToken ct = default);
    Task AddAsync(VoiceSession session, CancellationToken ct = default);
}

public interface IKbEntryRepository
{
    /// <summary>P0: anahtar kelime eslesmesi. P1'de ayni arayuz vektor aramasiyla doldurulur.</summary>
    Task<IReadOnlyList<KbEntry>> SearchAsync(IEnumerable<string> terms, CancellationToken ct = default);

    /// <summary>Ozet ekraninda gosterilecek kaynak basliklari icin.</summary>
    Task<IReadOnlyList<KbEntry>> GetByIdsAsync(IEnumerable<Guid> ids, CancellationToken ct = default);

    /// <summary>Tum kayitlar: ai-simplify servisi KB'yi GET /api/v1/kb ile bir kez ceker.</summary>
    Task<IReadOnlyList<KbEntry>> ListAllAsync(CancellationToken ct = default);
}

public interface IAuditLogRepository
{
    Task AddAsync(AuditLogEntry entry, CancellationToken ct = default);
}

public interface IUnitOfWork
{
    Task<int> SaveChangesAsync(CancellationToken ct = default);
}
