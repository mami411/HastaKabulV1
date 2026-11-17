using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Ai;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Abstractions.Realtime;
using HastaKabul.Application.Abstractions.Security;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Realtime;
using HastaKabul.Application.Services;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Clinics;
using HastaKabul.Domain.Doctors;
using HastaKabul.Domain.Encounters;
using HastaKabul.Domain.Knowledge;
using HastaKabul.Domain.Patients;
using HastaKabul.Domain.Summaries;
using HastaKabul.Domain.Voice;
using HastaKabul.Infrastructure.Ai;

namespace HastaKabul.Tests;

/// <summary>
/// Demo sertlestirmesi testleri icin bellek ici dunya: tum depo, saat, bildirim ve AI istemcisi
/// portlari tek nesnede. Servisler gercek; yalniz kalicilik/ag taklit edilir.
/// </summary>
internal sealed class DemoWorld :
    IVoiceSessionRepository, ISlotRepository, IAppointmentRepository, IClinicRepository, IDoctorRepository,
    IPatientRepository, IClinicalEntryRepository, ISummaryRepository, IKbEntryRepository, IAuditLogRepository,
    IUnitOfWork, IAccessCodeGenerator, IRequestContext, IClock, ITcHasher, ISimplifyClient, IRealtimeNotifier
{
    public const string DemoTc = "10000000146";
    /// <summary>Bicimi gecerli (kontrol haneleri tutuyor) ama kayitli olmayan numara.</summary>
    public const string UnregisteredTc = "12345678950";

    public DateTime Now = new(2026, 11, 16, 7, 0, 0, DateTimeKind.Utc); // Istanbul 10:00
    public string RequestId { get; set; } = "req-1";
    public Guid? DoctorId => null;
    public DateTime UtcNow => Now;

    public readonly Clinic Dahiliye = new("DAHILIYE", "Dahiliye");
    public readonly Clinic Kardiyoloji = new("KARDIYOLOJI", "Kardiyoloji");
    public readonly Doctor DrDahiliye;
    public readonly Doctor DrKardiyoloji;
    public readonly Patient Ayse;

    public readonly List<VoiceSession> Sessions = [];
    public readonly List<AppointmentSlot> Slots = [];
    public readonly List<Appointment> Appointments = [];
    public readonly List<ClinicalEntry> Entries = [];
    public readonly List<SimplifiedSummary> Summaries = [];
    public readonly List<AuditLogEntry> Audit = [];
    public readonly List<SimplifyRequest> SimplifyCalls = [];
    public readonly List<(Guid DoctorId, string Event, object Payload)> Events = [];

    /// <summary>Sadelestirme servisi istegi kabul ediyor mu (false: kapali/ulasilamaz).</summary>
    public bool SimplifyUp;

    /// <summary>Siradaki SaveChanges cagrilarinda atilacak benzersizlik ihlalleri.</summary>
    public readonly Queue<UniqueConflictTarget> SaveConflicts = new();
    public int Saves;

    public readonly Queue<string> AccessCodes = new(["111111", "222222", "333333", "444444", "555555", "666666"]);

    public DemoWorld()
    {
        DrDahiliye = new Doctor(Dahiliye.Id, "Mehmet Demir", "204");
        DrKardiyoloji = new Doctor(Kardiyoloji.Id, "Elif Kaya", "112");
        Ayse = new Patient("h:" + DemoTc, [1], "Ayşe", "Hanımeli");
    }

    public SummaryOptions Options { get; } = new() { PendingTimeout = TimeSpan.FromSeconds(90) };

    // ---- Kurulum yardimcilari

    public AppointmentSlot Slot(Doctor doctor, DateTime startUtc)
    {
        var slot = new AppointmentSlot(doctor.ClinicId, doctor.Id, startUtc);
        Set(slot, nameof(AppointmentSlot.Doctor), doctor);
        Slots.Add(slot);
        return slot;
    }

    public VoiceSession VerifiedSession(Patient? patient = null)
    {
        patient ??= Ayse;
        var session = new VoiceSession(TimeSpan.FromMinutes(15));
        Set(session, nameof(VoiceSession.ExpiresAtUtc), Now.AddMinutes(15));
        session.MarkVerified(patient.Id, patient.PatientRef);
        Sessions.Add(session);
        return session;
    }

    public VoiceSession ActiveSession()
    {
        var session = new VoiceSession(TimeSpan.FromMinutes(15));
        Set(session, nameof(VoiceSession.ExpiresAtUtc), Now.AddMinutes(15));
        Sessions.Add(session);
        return session;
    }

    public Appointment Appointment(Doctor doctor, DateTime atUtc, Patient? patient = null, string code = "246802")
    {
        patient ??= Ayse;
        var appointment = new Appointment(patient.Id, doctor.Id, doctor.ClinicId, Guid.NewGuid(), atUtc, code);
        Set(appointment, nameof(HastaKabul.Domain.Appointments.Appointment.Patient), patient);
        Set(appointment, nameof(HastaKabul.Domain.Appointments.Appointment.Doctor), doctor);
        Appointments.Add(appointment);
        return appointment;
    }

    /// <summary>Muayenedeki randevu + klinik giris + PENDING ozet (simdi - age).</summary>
    public SimplifiedSummary PendingSummary(string diagnosis, string[] medications, TimeSpan age, Doctor? doctor = null)
    {
        doctor ??= DrDahiliye;
        var appointment = Appointment(doctor, Now.AddHours(-1));
        appointment.CheckIn(Now.AddHours(-1));
        appointment.StartExam(Now.AddHours(-1));
        var entry = new ClinicalEntry(appointment.Id, doctor.Id, diagnosis, medications);
        Entries.Add(entry);
        var summary = new SimplifiedSummary(appointment.Id, entry.Id, Ayse.PatientRef, "req-0");
        Set(summary, nameof(SimplifiedSummary.UpdatedAtUtc), Now - age);
        Summaries.Add(summary);
        return summary;
    }

    public static void Set(object target, string property, object? value)
        => target.GetType().GetProperty(property)!.SetValue(target, value);

    // ---- Servis fabrikalari

    public SummaryGenerationService Generation() =>
        new(this, this, this, this, new SeedFallbackSummaryProvider(), this, Options, this);

    public AppointmentBookingService Booking() => new(this, this, this, this, this, this, this, this, this, this);

    public VoiceSessionService Voice() => new(this, this, this, this, this, this, this);

    public SummaryService Summary() => new(this, this, this, this, this, this, Generation(), this, this);

    public ClinicalEntryService ClinicalEntry() => new(this, this, this, this, this, Generation(), this, this, this);

    public DoctorPanelService Panel() => new(this, this, this, this, this, Generation(), this, this);

    public SlotService SlotService() => new(this, this, this);

    public PortalService Portal() => new(this, this, this, Summary());

    // ---- IVoiceSessionRepository
    Task<VoiceSession?> IVoiceSessionRepository.GetByIdAsync(Guid id, CancellationToken ct)
        => Task.FromResult(Sessions.FirstOrDefault(s => s.Id == id));
    Task IVoiceSessionRepository.AddAsync(VoiceSession session, CancellationToken ct) { Sessions.Add(session); return Task.CompletedTask; }

    // ---- ISlotRepository
    Task<AppointmentSlot?> ISlotRepository.GetByIdAsync(Guid id, CancellationToken ct)
        => Task.FromResult(Slots.FirstOrDefault(s => s.Id == id));
    public Task<IReadOnlyList<AppointmentSlot>> GetAvailableAsync(Guid clinicId, DateTime fromUtc, DateTime toUtc, CancellationToken ct = default)
        => Task.FromResult<IReadOnlyList<AppointmentSlot>>(Slots
            .Where(s => s.ClinicId == clinicId && !s.IsTaken && s.StartTimeUtc >= fromUtc && s.StartTimeUtc < toUtc).ToList());

    // ---- IAppointmentRepository
    Task<Appointment?> IAppointmentRepository.GetByIdAsync(Guid id, CancellationToken ct)
        => Task.FromResult(Appointments.FirstOrDefault(a => a.Id == id));
    public Task<Appointment?> GetByIdWithDetailsAsync(Guid id, CancellationToken ct = default)
        => Task.FromResult(Appointments.FirstOrDefault(a => a.Id == id));
    public Task<Appointment?> FindTodaysScheduledAsync(Guid patientId, DateTime dayStartUtc, DateTime dayEndUtc, CancellationToken ct = default)
        => Task.FromResult(Appointments.FirstOrDefault(a => a.PatientId == patientId));
    public Task<IReadOnlyList<Appointment>> GetByDoctorAndDayAsync(Guid doctorId, DateTime dayStartUtc, DateTime dayEndUtc, CancellationToken ct = default)
        => Task.FromResult<IReadOnlyList<Appointment>>(Appointments
            .Where(a => a.DoctorId == doctorId && a.ScheduledAtUtc >= dayStartUtc && a.ScheduledAtUtc < dayEndUtc).ToList());
    public Task<Appointment?> FindByAccessCodeAsync(string accessCode, CancellationToken ct = default)
        => Task.FromResult(Appointments.FirstOrDefault(a => a.AccessCode == accessCode));
    public Task<IReadOnlyList<Appointment>> GetActiveByPatientAsync(Guid patientId, DateTime fromUtc, DateTime toUtc, CancellationToken ct = default)
        => Task.FromResult<IReadOnlyList<Appointment>>(Appointments
            .Where(a => a.PatientId == patientId && a.IsActive && a.ScheduledAtUtc >= fromUtc && a.ScheduledAtUtc < toUtc).ToList());
    Task IAppointmentRepository.AddAsync(Appointment appointment, CancellationToken ct) { Appointments.Add(appointment); return Task.CompletedTask; }

    // ---- IClinicRepository
    Task<Clinic?> IClinicRepository.GetByIdAsync(Guid id, CancellationToken ct)
        => Task.FromResult(new[] { Dahiliye, Kardiyoloji }.FirstOrDefault(c => c.Id == id));
    public Task<Clinic?> ResolveAsync(string nameOrCode, CancellationToken ct = default)
        => Task.FromResult(new[] { Dahiliye, Kardiyoloji }.FirstOrDefault(c => c.Matches(nameOrCode)));
    Task<IReadOnlyList<Clinic>> IClinicRepository.GetAllAsync(CancellationToken ct)
        => Task.FromResult<IReadOnlyList<Clinic>>([Dahiliye, Kardiyoloji]);

    // ---- IDoctorRepository
    Task<Doctor?> IDoctorRepository.GetByIdAsync(Guid id, CancellationToken ct)
        => Task.FromResult(new[] { DrDahiliye, DrKardiyoloji }.FirstOrDefault(d => d.Id == id));
    Task<IReadOnlyList<Doctor>> IDoctorRepository.GetAllAsync(CancellationToken ct)
        => Task.FromResult<IReadOnlyList<Doctor>>([DrDahiliye, DrKardiyoloji]);

    // ---- IPatientRepository
    public Task<Patient?> FindByTcHashAsync(string tcHash, CancellationToken ct = default)
        => Task.FromResult(tcHash == Ayse.TcHash ? Ayse : null);
    Task<Patient?> IPatientRepository.GetByIdAsync(Guid id, CancellationToken ct)
        => Task.FromResult(id == Ayse.Id ? Ayse : null);
    public Task<Patient?> GetByPatientRefAsync(Guid patientRef, CancellationToken ct = default)
        => Task.FromResult(patientRef == Ayse.PatientRef ? Ayse : null);
    Task IPatientRepository.AddAsync(Patient patient, CancellationToken ct) => Task.CompletedTask;

    // ---- IClinicalEntryRepository
    Task<ClinicalEntry?> IClinicalEntryRepository.GetByIdAsync(Guid id, CancellationToken ct)
        => Task.FromResult(Entries.FirstOrDefault(e => e.Id == id));
    Task IClinicalEntryRepository.AddAsync(ClinicalEntry entry, CancellationToken ct) { Entries.Add(entry); return Task.CompletedTask; }

    // ---- ISummaryRepository
    Task<SimplifiedSummary?> ISummaryRepository.GetByIdAsync(Guid id, CancellationToken ct)
        => Task.FromResult(Summaries.FirstOrDefault(s => s.Id == id));
    public Task<SimplifiedSummary?> GetByAppointmentIdAsync(Guid appointmentId, CancellationToken ct = default)
        => Task.FromResult(Summaries.LastOrDefault(s => s.AppointmentId == appointmentId));
    public Task<IReadOnlyDictionary<Guid, SimplifiedSummary>> GetLatestByAppointmentIdsAsync(IReadOnlyCollection<Guid> appointmentIds, CancellationToken ct = default)
        => Task.FromResult<IReadOnlyDictionary<Guid, SimplifiedSummary>>(Summaries
            .Where(s => appointmentIds.Contains(s.AppointmentId))
            .GroupBy(s => s.AppointmentId)
            .ToDictionary(g => g.Key, g => g.Last()));
    public Task<IReadOnlyList<SimplifiedSummary>> GetApprovedByPatientAsync(Guid patientId, CancellationToken ct = default)
        => Task.FromResult<IReadOnlyList<SimplifiedSummary>>([]);
    Task ISummaryRepository.AddAsync(SimplifiedSummary summary, CancellationToken ct) { Summaries.Add(summary); return Task.CompletedTask; }

    // ---- IKbEntryRepository
    public Task<IReadOnlyList<KbEntry>> SearchAsync(IEnumerable<string> terms, CancellationToken ct = default)
        => Task.FromResult<IReadOnlyList<KbEntry>>([]);
    public Task<IReadOnlyList<KbEntry>> GetByIdsAsync(IEnumerable<Guid> ids, CancellationToken ct = default)
        => Task.FromResult<IReadOnlyList<KbEntry>>([]);
    public Task<IReadOnlyList<KbEntry>> ListAllAsync(CancellationToken ct = default)
        => Task.FromResult<IReadOnlyList<KbEntry>>([]);

    // ---- IAuditLogRepository / IUnitOfWork
    public Task AddAsync(AuditLogEntry entry, CancellationToken ct = default) { Audit.Add(entry); return Task.CompletedTask; }

    public Task<int> SaveChangesAsync(CancellationToken ct = default)
    {
        Saves++;
        if (SaveConflicts.TryDequeue(out var target))
            throw new UniqueConflictException(target);
        return Task.FromResult(1);
    }

    // ---- Guvenlik / AI / bildirim
    public string Generate() => AccessCodes.Dequeue();
    public string Hash(string tc) => "h:" + tc.Trim();

    public Task<bool> RequestSimplificationAsync(SimplifyRequest request, CancellationToken ct = default)
    {
        SimplifyCalls.Add(request);
        return Task.FromResult(SimplifyUp);
    }

    public Task AppointmentStatusChangedAsync(Guid doctorId, AppointmentStatusChangedEvent payload, CancellationToken ct = default)
    {
        Events.Add((doctorId, RealtimeEventNames.AppointmentStatusChanged, payload));
        return Task.CompletedTask;
    }

    public Task SummaryReadyAsync(Guid doctorId, SummaryReadyEvent payload, CancellationToken ct = default)
    {
        Events.Add((doctorId, RealtimeEventNames.SummaryReady, payload));
        return Task.CompletedTask;
    }
}
