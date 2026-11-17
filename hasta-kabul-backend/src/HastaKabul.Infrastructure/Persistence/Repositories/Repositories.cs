using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Clinics;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Doctors;
using HastaKabul.Domain.Encounters;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Knowledge;
using HastaKabul.Domain.Patients;
using HastaKabul.Domain.Summaries;
using HastaKabul.Domain.Voice;
using Microsoft.EntityFrameworkCore;

namespace HastaKabul.Infrastructure.Persistence.Repositories;

public sealed class PatientRepository(HastaKabulDbContext db) : IPatientRepository
{
    public Task<Patient?> FindByTcHashAsync(string tcHash, CancellationToken ct = default)
        => db.Patients.FirstOrDefaultAsync(p => p.TcHash == tcHash, ct);

    public Task<Patient?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => db.Patients.FirstOrDefaultAsync(p => p.Id == id, ct);

    public Task<Patient?> GetByPatientRefAsync(Guid patientRef, CancellationToken ct = default)
        => db.Patients.FirstOrDefaultAsync(p => p.PatientRef == patientRef, ct);

    public async Task AddAsync(Patient patient, CancellationToken ct = default)
        => await db.Patients.AddAsync(patient, ct);
}

public sealed class ClinicRepository(HastaKabulDbContext db) : IClinicRepository
{
    public Task<Clinic?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => db.Clinics.FirstOrDefaultAsync(c => c.Id == id, ct);

    /// <summary>
    /// Kod, ad veya es anlamli uzerinden cozer. Karsilastirma bellekte, kulturden bagimsiz
    /// (<see cref="Clinic.Matches"/>): tr-TR surecte "KARDIYOLOJI" de, "göz"/"goz" da cozulur.
    /// Poliklinik sayisi kucuk oldugundan hepsi okunur; once kod/ad, sonra es anlamli eslesmesi.
    /// </summary>
    public async Task<Clinic?> ResolveAsync(string nameOrCode, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(nameOrCode))
            return null;

        var all = await db.Clinics.ToListAsync(ct);
        return all.FirstOrDefault(c => TurkishText.FoldEquals(c.Code, nameOrCode) || TurkishText.FoldEquals(c.Name, nameOrCode))
            ?? all.FirstOrDefault(c => c.Matches(nameOrCode));
    }

    public async Task<IReadOnlyList<Clinic>> GetAllAsync(CancellationToken ct = default)
        => await db.Clinics.OrderBy(c => c.Name).ToListAsync(ct);
}

public sealed class DoctorRepository(HastaKabulDbContext db) : IDoctorRepository
{
    public Task<Doctor?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => db.Doctors.Include(d => d.Clinic).FirstOrDefaultAsync(d => d.Id == id, ct);

    public async Task<IReadOnlyList<Doctor>> GetAllAsync(CancellationToken ct = default)
        => await db.Doctors.Include(d => d.Clinic).OrderBy(d => d.FullName).ToListAsync(ct);
}

public sealed class SlotRepository(HastaKabulDbContext db) : ISlotRepository
{
    public Task<AppointmentSlot?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => db.AppointmentSlots.Include(s => s.Doctor).FirstOrDefaultAsync(s => s.Id == id, ct);

    public async Task<IReadOnlyList<AppointmentSlot>> GetAvailableAsync(
        Guid clinicId, DateTime fromUtc, DateTime toUtc, CancellationToken ct = default)
        => await db.AppointmentSlots
            .Include(s => s.Doctor)
            .Where(s => s.ClinicId == clinicId
                        && !s.IsTaken
                        && s.StartTimeUtc >= fromUtc
                        && s.StartTimeUtc < toUtc)
            .OrderBy(s => s.StartTimeUtc)
            .ToListAsync(ct);
}

public sealed class AppointmentRepository(HastaKabulDbContext db) : IAppointmentRepository, IPatientAppointmentReader
{
    public Task<Appointment?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => db.Appointments.FirstOrDefaultAsync(a => a.Id == id, ct);

    public Task<Appointment?> GetByIdWithDetailsAsync(Guid id, CancellationToken ct = default)
        => db.Appointments
            .Include(a => a.Patient)
            .Include(a => a.Doctor)
            .Include(a => a.Slot)
            .FirstOrDefaultAsync(a => a.Id == id, ct);

    /// <summary>Birden fazla randevu varsa en erken olani secilir.</summary>
    public Task<Appointment?> FindTodaysScheduledAsync(
        Guid patientId, DateTime dayStartUtc, DateTime dayEndUtc, CancellationToken ct = default)
        => db.Appointments
            .Include(a => a.Patient)
            .Include(a => a.Doctor)
            .Where(a => a.PatientId == patientId
                        && a.Status == AppointmentStatus.Scheduled
                        && a.ScheduledAtUtc >= dayStartUtc
                        && a.ScheduledAtUtc < dayEndUtc)
            .OrderBy(a => a.ScheduledAtUtc)
            .FirstOrDefaultAsync(ct);

    public async Task<IReadOnlyList<Appointment>> GetByDoctorAndDayAsync(
        Guid doctorId, DateTime dayStartUtc, DateTime dayEndUtc, CancellationToken ct = default)
        => await db.Appointments
            .Include(a => a.Patient)
            .Where(a => a.DoctorId == doctorId
                        && a.ScheduledAtUtc >= dayStartUtc
                        && a.ScheduledAtUtc < dayEndUtc)
            .OrderBy(a => a.ScheduledAtUtc)
            .ToListAsync(ct);

    public Task<Appointment?> FindByAccessCodeAsync(string accessCode, CancellationToken ct = default)
        => db.Appointments
            .Include(a => a.Patient)
            .Include(a => a.Doctor)
            .FirstOrDefaultAsync(a => a.AccessCode == accessCode, ct);

    public async Task<IReadOnlyList<Appointment>> GetActiveByPatientAsync(
        Guid patientId, DateTime fromUtc, DateTime toUtc, CancellationToken ct = default)
        => await db.Appointments
            .Where(a => a.PatientId == patientId
                        && (a.Status == AppointmentStatus.Scheduled
                            || a.Status == AppointmentStatus.CheckedIn
                            || a.Status == AppointmentStatus.InExam)
                        && a.ScheduledAtUtc >= fromUtc
                        && a.ScheduledAtUtc < toUtc)
            .ToListAsync(ct);

    public async Task<IReadOnlyList<Appointment>> GetUpcomingActiveByPatientAsync(
        Guid patientId, DateTime fromUtc, CancellationToken ct = default)
        => await db.Appointments
            .AsNoTracking()
            .Include(a => a.Doctor)
            .Where(a => a.PatientId == patientId
                        && (a.Status == AppointmentStatus.Scheduled
                            || a.Status == AppointmentStatus.CheckedIn
                            || a.Status == AppointmentStatus.InExam)
                        && a.ScheduledAtUtc >= fromUtc)
            .OrderBy(a => a.ScheduledAtUtc)
            .ToListAsync(ct);

    public async Task AddAsync(Appointment appointment, CancellationToken ct = default)
        => await db.Appointments.AddAsync(appointment, ct);
}

public sealed class ClinicalEntryRepository(HastaKabulDbContext db) : IClinicalEntryRepository
{
    public Task<ClinicalEntry?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => db.ClinicalEntries.FirstOrDefaultAsync(e => e.Id == id, ct);

    public async Task AddAsync(ClinicalEntry entry, CancellationToken ct = default)
        => await db.ClinicalEntries.AddAsync(entry, ct);
}

public sealed class SummaryRepository(HastaKabulDbContext db) : ISummaryRepository
{
    public Task<SimplifiedSummary?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => db.Summaries.FirstOrDefaultAsync(s => s.Id == id, ct);

    /// <summary>
    /// Bir randevunun en guncel ozeti: en son olusturulan (ayni randevuya ikinci klinik giris yeni
    /// ozet acar). Guncellenme zamanina gore secmek, eski kayda gelen gec bir geri cagriyla yanlis
    /// ozeti one cikarabiliyordu.
    /// </summary>
    public Task<SimplifiedSummary?> GetByAppointmentIdAsync(Guid appointmentId, CancellationToken ct = default)
        => db.Summaries
            .Where(s => s.AppointmentId == appointmentId)
            .OrderByDescending(s => s.CreatedAtUtc)
            .ThenByDescending(s => s.UpdatedAtUtc)
            .FirstOrDefaultAsync(ct);

    public async Task<IReadOnlyDictionary<Guid, SimplifiedSummary>> GetLatestByAppointmentIdsAsync(
        IReadOnlyCollection<Guid> appointmentIds, CancellationToken ct = default)
    {
        if (appointmentIds.Count == 0)
            return new Dictionary<Guid, SimplifiedSummary>();

        var ids = appointmentIds.Distinct().ToList();
        var all = await db.Summaries.Where(s => ids.Contains(s.AppointmentId)).ToListAsync(ct);

        return all
            .GroupBy(s => s.AppointmentId)
            .ToDictionary(
                g => g.Key,
                g => g.OrderByDescending(s => s.CreatedAtUtc).ThenByDescending(s => s.UpdatedAtUtc).First());
    }

    public async Task<IReadOnlyList<SimplifiedSummary>> GetApprovedByPatientAsync(
        Guid patientId, CancellationToken ct = default)
        => await db.Summaries
            .AsNoTracking()
            .Include(s => s.Appointment!)
                .ThenInclude(a => a.Doctor)
            .Where(s => s.Status == SummaryStatus.Approved && s.Appointment!.PatientId == patientId)
            .OrderByDescending(s => s.Appointment!.ScheduledAtUtc)
            .ThenByDescending(s => s.UpdatedAtUtc)
            .ToListAsync(ct);

    public async Task AddAsync(SimplifiedSummary summary, CancellationToken ct = default)
        => await db.Summaries.AddAsync(summary, ct);
}

public sealed class VoiceSessionRepository(HastaKabulDbContext db) : IVoiceSessionRepository
{
    public Task<VoiceSession?> GetByIdAsync(Guid id, CancellationToken ct = default)
        => db.VoiceSessions.FirstOrDefaultAsync(s => s.Id == id, ct);

    public async Task AddAsync(VoiceSession session, CancellationToken ct = default)
        => await db.VoiceSessions.AddAsync(session, ct);
}

public sealed class KbEntryRepository(HastaKabulDbContext db) : IKbEntryRepository
{
    /// <summary>
    /// P0: anahtar kelime eslesmesi (terim ve es anlamlilar). P1'de ayni imza
    /// pgvector benzerlik aramasiyla doldurulur, cagiranlar degismez.
    /// </summary>
    public async Task<IReadOnlyList<KbEntry>> SearchAsync(IEnumerable<string> terms, CancellationToken ct = default)
    {
        var needles = terms
            .Select(TurkishText.Fold)
            .Where(t => t.Length > 0)
            .Distinct()
            .ToList();

        if (needles.Count == 0)
            return [];

        var all = await db.KbEntries.ToListAsync(ct);

        // Kulturden bagimsiz; Turkce karakterli ve karaktersiz yazim ayni sayilir.
        return all.Where(e => needles.Any(e.MatchesTerm)).ToList();
    }

    public async Task<IReadOnlyList<KbEntry>> GetByIdsAsync(IEnumerable<Guid> ids, CancellationToken ct = default)
    {
        var list = ids.Distinct().ToList();
        if (list.Count == 0)
            return [];

        return await db.KbEntries.Where(e => list.Contains(e.Id)).ToListAsync(ct);
    }

    public async Task<IReadOnlyList<KbEntry>> ListAllAsync(CancellationToken ct = default)
        => await db.KbEntries.AsNoTracking().ToListAsync(ct);
}

public sealed class AuditLogRepository(HastaKabulDbContext db) : IAuditLogRepository
{
    public async Task AddAsync(AuditLogEntry entry, CancellationToken ct = default)
        => await db.AuditLogs.AddAsync(entry, ct);
}

public sealed class UnitOfWork(HastaKabulDbContext db) : IUnitOfWork
{
    /// <summary>
    /// PostgreSQL benzersiz indeks ihlali (23505) <see cref="UniqueConflictException"/>'a cevrilir;
    /// use-case servisi bunu 409 SLOT_TAKEN'a ya da erisim kodu yeniden denemesine donusturur.
    /// </summary>
    public async Task<int> SaveChangesAsync(CancellationToken ct = default)
    {
        try
        {
            return await db.SaveChangesAsync(ct);
        }
        catch (DbUpdateException ex) when (ex.InnerException is Npgsql.PostgresException { SqlState: Npgsql.PostgresErrorCodes.UniqueViolation } pg)
        {
            throw new UniqueConflictException(ClassifyUniqueViolation(pg.TableName, pg.ConstraintName), ex);
        }
    }

    public static UniqueConflictTarget ClassifyUniqueViolation(string? table, string? constraint)
    {
        var name = constraint ?? string.Empty;
        if (table is null or "appointments")
        {
            if (name.Contains("slot_id", StringComparison.OrdinalIgnoreCase))
                return UniqueConflictTarget.AppointmentSlot;
            if (name.Contains("access_code", StringComparison.OrdinalIgnoreCase))
                return UniqueConflictTarget.AccessCode;
        }

        return UniqueConflictTarget.Other;
    }
}
