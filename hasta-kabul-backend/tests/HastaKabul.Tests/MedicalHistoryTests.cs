using System.Reflection;
using HastaKabul.Api.Controllers;
using HastaKabul.Api.Infrastructure;
using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Abstractions.Security;
using HastaKabul.Application.Contracts.Appointments;
using HastaKabul.Application.Contracts.Voice;
using HastaKabul.Application.Services;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Clinics;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Doctors;
using HastaKabul.Domain.Encounters;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Patients;
using HastaKabul.Domain.Summaries;
using HastaKabul.Domain.Voice;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Xunit;

namespace HastaKabul.Tests;

/// <summary>
/// GET /api/v1/voice-sessions/{id}/medical-history: yalnizca dogrulanmis ve suresi dolmamis
/// oturumun hastasina ait, hekim onayli ozetler; her okuma audit log'a yazilir.
/// </summary>
public class MedicalHistoryTests
{
    private static readonly TimeSpan Lifetime = TimeSpan.FromMinutes(15);

    private readonly Fakes _fakes = new();

    private MedicalHistoryService Service() => new(
        _fakes, _fakes, _fakes, _fakes, _fakes, _fakes, _fakes);

    [Fact]
    public async Task Yalnizca_onayli_ozetler_yeniden_eskiye_doner()
    {
        var patient = _fakes.Patient();
        var session = _fakes.VerifiedSession(patient);
        var eski = _fakes.Visit(patient, daysAgo: 100, approve: true, diagnosis: "Eski");
        var yeni = _fakes.Visit(patient, daysAgo: 10, approve: true, diagnosis: "Yeni");
        _fakes.Visit(patient, daysAgo: 2, approve: false, diagnosis: "Onaysiz");
        _fakes.Visit(_fakes.Patient(), daysAgo: 1, approve: true, diagnosis: "Baska hasta");

        var result = await Service().GetAsync(session.Id, 3);

        Assert.Equal(2, result.Visits.Count);
        Assert.Equal(yeni.AppointmentId.ToString(), result.Visits[0].AppointmentId);
        Assert.Equal(eski.AppointmentId.ToString(), result.Visits[1].AppointmentId);
        Assert.Equal("Yeni", result.Visits[0].DiagnosisPlain);
        Assert.Equal("Dahiliye", result.Visits[0].ClinicName);
        Assert.Equal("Mehmet D.", result.Visits[0].DoctorDisplayName);
        Assert.Equal(DateTimeKind.Utc, result.Visits[0].ScheduledAt.Kind);
        var ilac = Assert.Single(result.Visits[0].Medications);
        Assert.Equal("Metformin", ilac.Name);
        Assert.Equal(["Uyari"], result.Visits[0].Warnings);
    }

    [Fact]
    public async Task Limit_en_yeni_kayitlari_sinirlar()
    {
        var patient = _fakes.Patient();
        var session = _fakes.VerifiedSession(patient);
        _fakes.Visit(patient, daysAgo: 30, approve: true, diagnosis: "A");
        _fakes.Visit(patient, daysAgo: 20, approve: true, diagnosis: "B");
        _fakes.Visit(patient, daysAgo: 10, approve: true, diagnosis: "C");

        var result = await Service().GetAsync(session.Id, 1);

        Assert.Equal("C", Assert.Single(result.Visits).DiagnosisPlain);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(6)]
    [InlineData(-1)]
    public async Task Limit_1_ile_5_disinda_reddedilir(int limit)
    {
        var session = _fakes.VerifiedSession(_fakes.Patient());

        var ex = await Assert.ThrowsAsync<DomainException>(() => Service().GetAsync(session.Id, limit));

        Assert.Equal(ErrorCodes.ValidationFailed, ex.Code);
        Assert.Empty(_fakes.Audit);
    }

    [Fact]
    public async Task Kayit_yoksa_bos_liste_doner_ve_erisim_yine_kaydedilir()
    {
        var session = _fakes.VerifiedSession(_fakes.Patient());

        var result = await Service().GetAsync(session.Id, 3);

        Assert.Empty(result.Visits);
        var audit = Assert.Single(_fakes.Audit);
        Assert.Equal("voiceSession.medicalHistoryRead", audit.Action);
        Assert.Equal("voice-orchestrator", audit.Actor);
        Assert.Equal(session.Id, audit.EntityId);
        Assert.Equal("req-test", audit.RequestId);
    }

    [Fact]
    public async Task Her_okuma_audit_loga_yazilir()
    {
        var patient = _fakes.Patient();
        var session = _fakes.VerifiedSession(patient);
        _fakes.Visit(patient, daysAgo: 5, approve: true, diagnosis: "A");

        await Service().GetAsync(session.Id, 3);
        await Service().GetAsync(session.Id, 2);

        Assert.Equal(2, _fakes.Audit.Count);
        Assert.All(_fakes.Audit, a => Assert.Equal("voiceSession.medicalHistoryRead", a.Action));
        Assert.Equal("limit=3;visits=1", _fakes.Audit[0].Detail);
    }

    [Fact]
    public async Task Randevu_alindiktan_sonra_completed_oturumla_da_sorulabilir()
    {
        var patient = _fakes.Patient();
        var session = _fakes.VerifiedSession(patient);
        session.Complete();
        _fakes.Visit(patient, daysAgo: 5, approve: true, diagnosis: "A");

        var result = await Service().GetAsync(session.Id, 3);

        Assert.Single(result.Visits);
    }

    [Fact]
    public async Task Bulunamayan_oturum_404_kodu_doner()
    {
        var ex = await Assert.ThrowsAsync<DomainException>(() => Service().GetAsync(Guid.NewGuid(), 3));

        Assert.Equal(ErrorCodes.VoiceSessionNotFound, ex.Code);
    }

    [Fact]
    public async Task Dogrulanmamis_oturum_reddedilir()
    {
        var session = new VoiceSession(Lifetime);
        _fakes.Sessions.Add(session);

        var ex = await Assert.ThrowsAsync<DomainException>(() => Service().GetAsync(session.Id, 3));

        Assert.Equal(ErrorCodes.VoiceSessionNotVerified, ex.Code);
        Assert.Empty(_fakes.Audit);
    }

    [Fact]
    public async Task Handoff_olmus_oturum_reddedilir()
    {
        var session = new VoiceSession(Lifetime);
        for (var i = 0; i < VoiceSession.MaxAttempts; i++)
            session.RegisterFailedAttempt();
        _fakes.Sessions.Add(session);

        var ex = await Assert.ThrowsAsync<DomainException>(() => Service().GetAsync(session.Id, 3));

        Assert.Equal(ErrorCodes.VoiceSessionNotVerified, ex.Code);
    }

    [Fact]
    public async Task Suresi_dolan_dogrulanmis_oturum_expired_olur_ve_reddedilir()
    {
        var patient = _fakes.Patient();
        var session = _fakes.VerifiedSession(patient);
        _fakes.Visit(patient, daysAgo: 5, approve: true, diagnosis: "A");
        _fakes.Now = DateTime.UtcNow.Add(Lifetime).AddMinutes(1);

        var ex = await Assert.ThrowsAsync<DomainException>(() => Service().GetAsync(session.Id, 3));

        Assert.Equal(ErrorCodes.VoiceSessionNotVerified, ex.Code);
        Assert.Equal(VoiceSessionStatus.Expired, session.Status);
        Assert.Empty(_fakes.Audit);
    }

    [Fact]
    public async Task Suresi_dolan_completed_oturum_da_reddedilir()
    {
        var patient = _fakes.Patient();
        var session = _fakes.VerifiedSession(patient);
        session.Complete();
        _fakes.Now = DateTime.UtcNow.Add(Lifetime).AddMinutes(1);

        var ex = await Assert.ThrowsAsync<DomainException>(() => Service().GetAsync(session.Id, 3));

        Assert.Equal(ErrorCodes.VoiceSessionNotVerified, ex.Code);
    }

    [Fact]
    public void Yanit_kimlik_ve_onaysiz_alan_tasimaz()
    {
        var names = typeof(MedicalHistoryVisit).GetProperties().Select(p => p.Name).ToList();

        Assert.Equal(
            ["AppointmentId", "ScheduledAt", "ClinicName", "DoctorDisplayName", "DiagnosisPlain", "Medications", "Warnings"],
            names);
        Assert.Equal(["Visits"], typeof(MedicalHistoryResponse).GetProperties().Select(p => p.Name));
    }

    [Fact]
    public void Uc_yalniz_servis_anahtariyla_acilir()
    {
        var action = typeof(VoiceSessionsController).GetMethod(nameof(VoiceSessionsController.GetMedicalHistoryAsync))!;

        Assert.NotNull(action.GetCustomAttribute<RequireServiceKeyAttribute>());
        Assert.Null(action.GetCustomAttribute<AllowAnonymousAttribute>());
        Assert.Equal("{sessionId:guid}/medical-history", action.GetCustomAttribute<HttpGetAttribute>()!.Template);
    }

    // ---- Ortak sahte altyapi

    internal sealed class Fakes :
        IVoiceSessionRepository, ISummaryRepository, IClinicRepository, IAuditLogRepository,
        IUnitOfWork, IRequestContext, IClock
    {
        public readonly List<VoiceSession> Sessions = [];
        public readonly List<SimplifiedSummary> Summaries = [];
        public readonly List<AuditLogEntry> Audit = [];
        public readonly Clinic Dahiliye = new("DAHILIYE", "Dahiliye");
        public readonly Doctor Doctor;
        public DateTime Now = DateTime.UtcNow;
        public int Saves;

        public Fakes() => Doctor = new Doctor(Dahiliye.Id, "Mehmet Demir", "204");

        public Patient Patient() => new($"hash-{Guid.NewGuid():N}", [1], "Ayse", "Hanimeli");

        public VoiceSession VerifiedSession(Patient patient)
        {
            var session = new VoiceSession(Lifetime);
            session.MarkVerified(patient.Id, patient.PatientRef);
            Sessions.Add(session);
            return session;
        }

        public SimplifiedSummary Visit(Patient patient, int daysAgo, bool approve, string diagnosis)
        {
            var at = DateTime.UtcNow.AddDays(-daysAgo);
            var appointment = new Appointment(patient.Id, Doctor.Id, Dahiliye.Id, Guid.NewGuid(), at, "123456");
            SetPrivate(appointment, nameof(Appointment.Doctor), Doctor);
            var entry = new ClinicalEntry(appointment.Id, Doctor.Id, diagnosis, ["Metformin"]);
            var summary = new SimplifiedSummary(appointment.Id, entry.Id, patient.PatientRef, "seed");
            summary.MarkReady(
                diagnosis,
                [new SimplifiedMedication { Name = "Metformin", Purpose = "Seker", Usage = "Tok" }],
                ["Uyari"], ["bilinmeyen"], [], at);
            if (approve)
                summary.Approve(Doctor.Id, at);
            SetPrivate(summary, nameof(SimplifiedSummary.Appointment), appointment);
            Summaries.Add(summary);
            return summary;
        }

        private static void SetPrivate(object target, string property, object value)
            => target.GetType().GetProperty(property)!.SetValue(target, value);

        // Depo davranisini taklit etmez: hastanin TUM ozetlerini verir ki servis
        // filtresi (yalniz APPROVED) de sinansin.
        public Task<IReadOnlyList<SimplifiedSummary>> GetApprovedByPatientAsync(Guid patientId, CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<SimplifiedSummary>>(Summaries.ToList());

        public Task<VoiceSession?> GetByIdAsync(Guid id, CancellationToken ct = default)
            => Task.FromResult(Sessions.FirstOrDefault(s => s.Id == id));

        Task IVoiceSessionRepository.AddAsync(VoiceSession session, CancellationToken ct)
        {
            Sessions.Add(session);
            return Task.CompletedTask;
        }

        Task<SimplifiedSummary?> ISummaryRepository.GetByIdAsync(Guid id, CancellationToken ct)
            => Task.FromResult(Summaries.FirstOrDefault(s => s.Id == id));

        public Task<SimplifiedSummary?> GetByAppointmentIdAsync(Guid appointmentId, CancellationToken ct = default)
            => Task.FromResult(Summaries.FirstOrDefault(s => s.AppointmentId == appointmentId));

        public Task<IReadOnlyDictionary<Guid, SimplifiedSummary>> GetLatestByAppointmentIdsAsync(
            IReadOnlyCollection<Guid> appointmentIds, CancellationToken ct = default)
            => Task.FromResult<IReadOnlyDictionary<Guid, SimplifiedSummary>>(Summaries
                .Where(s => appointmentIds.Contains(s.AppointmentId))
                .GroupBy(s => s.AppointmentId)
                .ToDictionary(g => g.Key, g => g.Last()));

        Task ISummaryRepository.AddAsync(SimplifiedSummary summary, CancellationToken ct)
        {
            Summaries.Add(summary);
            return Task.CompletedTask;
        }

        Task<Clinic?> IClinicRepository.GetByIdAsync(Guid id, CancellationToken ct)
            => Task.FromResult(id == Dahiliye.Id ? Dahiliye : null);

        public Task<Clinic?> ResolveAsync(string nameOrCode, CancellationToken ct = default)
            => Task.FromResult<Clinic?>(Dahiliye);

        public Task<IReadOnlyList<Clinic>> GetAllAsync(CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<Clinic>>([Dahiliye]);

        public Task AddAsync(AuditLogEntry entry, CancellationToken ct = default)
        {
            Audit.Add(entry);
            return Task.CompletedTask;
        }

        public Task<int> SaveChangesAsync(CancellationToken ct = default)
        {
            Saves++;
            return Task.FromResult(1);
        }

        public string RequestId => "req-test";
        public Guid? DoctorId => null;
        public DateTime UtcNow => Now;
    }
}

/// <summary>Dogrulanmis ses oturumunun da omru doldugunda kapanmasi.</summary>
public class VoiceSessionExpiryTests
{
    private static readonly TimeSpan Lifetime = TimeSpan.FromMinutes(15);

    [Fact]
    public void Dogrulanmis_oturum_omur_dolunca_expired_olur()
    {
        var session = new VoiceSession(Lifetime);
        session.MarkVerified(Guid.NewGuid(), Guid.NewGuid());

        Assert.False(session.ExpireIfElapsed(DateTime.UtcNow.AddMinutes(10)));
        Assert.True(session.Verified);

        Assert.True(session.ExpireIfElapsed(DateTime.UtcNow.Add(Lifetime).AddSeconds(1)));
        Assert.Equal(VoiceSessionStatus.Expired, session.Status);
        Assert.False(session.Verified);
    }

    [Fact]
    public void Aktif_oturum_omur_dolunca_expired_olur()
    {
        var session = new VoiceSession(Lifetime);

        Assert.True(session.ExpireIfElapsed(DateTime.UtcNow.Add(Lifetime).AddSeconds(1)));
        Assert.Equal(VoiceSessionStatus.Expired, session.Status);
    }

    [Fact]
    public void Kapanmis_oturumlarin_durumu_degismez()
    {
        var completed = new VoiceSession(Lifetime);
        completed.MarkVerified(Guid.NewGuid(), Guid.NewGuid());
        completed.Complete();
        var handedOff = new VoiceSession(Lifetime);
        handedOff.HandOff();
        var later = DateTime.UtcNow.Add(Lifetime).AddMinutes(1);

        Assert.False(completed.ExpireIfElapsed(later));
        Assert.False(handedOff.ExpireIfElapsed(later));
        Assert.Equal(VoiceSessionStatus.Completed, completed.Status);
        Assert.Equal(VoiceSessionStatus.HandedOff, handedOff.Status);
    }

    [Fact]
    public async Task Get_suresi_dolan_dogrulanmis_oturumu_expired_gosterir()
    {
        var fakes = new MedicalHistoryTests.Fakes();
        var session = fakes.VerifiedSession(fakes.Patient());
        fakes.Now = DateTime.UtcNow.Add(Lifetime).AddMinutes(1);
        var service = new VoiceSessionService(fakes, new NoPatients(), fakes, fakes, new NoHasher(), fakes, fakes);

        var view = await service.GetAsync(session.Id);

        Assert.Equal("EXPIRED", view.Status);
        Assert.False(view.Verified);
    }

    [Fact]
    public async Task Suresi_dolan_oturumla_randevu_olusturulamaz()
    {
        var fakes = new MedicalHistoryTests.Fakes();
        var session = fakes.VerifiedSession(fakes.Patient());
        var slot = new AppointmentSlot(fakes.Dahiliye.Id, fakes.Doctor.Id, DateTime.UtcNow.AddDays(1));
        var booking = new BookingFakes(slot);
        fakes.Now = DateTime.UtcNow.Add(Lifetime).AddMinutes(1);
        var service = new AppointmentBookingService(
            fakes, booking, booking, fakes, booking, fakes, fakes, booking, fakes, fakes);

        var ex = await Assert.ThrowsAsync<DomainException>(() => service.CreateFromVoiceSessionAsync(
            new CreateAppointmentRequest(session.Id.ToString(), slot.Id.ToString())));

        Assert.Equal(ErrorCodes.VoiceSessionNotVerified, ex.Code);
        Assert.Equal(VoiceSessionStatus.Expired, session.Status);
        Assert.False(slot.IsTaken);
        Assert.Empty(booking.Appointments);
    }

    [Fact]
    public async Task Suresi_dolmamis_dogrulanmis_oturumla_randevu_olusur()
    {
        var fakes = new MedicalHistoryTests.Fakes();
        var session = fakes.VerifiedSession(fakes.Patient());
        var slot = new AppointmentSlot(fakes.Dahiliye.Id, fakes.Doctor.Id, DateTime.UtcNow.AddDays(1));
        var booking = new BookingFakes(slot);
        var service = new AppointmentBookingService(
            fakes, booking, booking, fakes, booking, fakes, fakes, booking, fakes, fakes);

        var response = await service.CreateFromVoiceSessionAsync(
            new CreateAppointmentRequest(session.Id.ToString(), slot.Id.ToString()));

        Assert.Equal("SCHEDULED", response.Status);
        Assert.True(slot.IsTaken);
        Assert.Equal(VoiceSessionStatus.Completed, session.Status);
    }

    private sealed class NoPatients : IPatientRepository
    {
        public Task<Patient?> FindByTcHashAsync(string tcHash, CancellationToken ct = default) => Task.FromResult<Patient?>(null);
        public Task<Patient?> GetByIdAsync(Guid id, CancellationToken ct = default) => Task.FromResult<Patient?>(null);
        public Task<Patient?> GetByPatientRefAsync(Guid patientRef, CancellationToken ct = default) => Task.FromResult<Patient?>(null);
        public Task AddAsync(Patient patient, CancellationToken ct = default) => Task.CompletedTask;
    }

    private sealed class NoHasher : ITcHasher
    {
        public string Hash(string tc) => "hash";
    }

    private sealed class BookingFakes(AppointmentSlot slot)
        : ISlotRepository, IAppointmentRepository, IDoctorRepository, IAccessCodeGenerator
    {
        public readonly List<Appointment> Appointments = [];

        Task<AppointmentSlot?> ISlotRepository.GetByIdAsync(Guid id, CancellationToken ct)
            => Task.FromResult(id == slot.Id ? slot : null);

        public Task<IReadOnlyList<AppointmentSlot>> GetAvailableAsync(
            Guid clinicId, DateTime fromUtc, DateTime toUtc, CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<AppointmentSlot>>([slot]);

        Task<Appointment?> IAppointmentRepository.GetByIdAsync(Guid id, CancellationToken ct)
            => Task.FromResult(Appointments.FirstOrDefault(a => a.Id == id));

        public Task<Appointment?> GetByIdWithDetailsAsync(Guid id, CancellationToken ct = default)
            => Task.FromResult(Appointments.FirstOrDefault(a => a.Id == id));

        public Task<Appointment?> FindTodaysScheduledAsync(
            Guid patientId, DateTime dayStartUtc, DateTime dayEndUtc, CancellationToken ct = default)
            => Task.FromResult<Appointment?>(null);

        public Task<IReadOnlyList<Appointment>> GetByDoctorAndDayAsync(
            Guid doctorId, DateTime dayStartUtc, DateTime dayEndUtc, CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<Appointment>>([]);

        public Task<Appointment?> FindByAccessCodeAsync(string accessCode, CancellationToken ct = default)
            => Task.FromResult<Appointment?>(null);

        public Task<IReadOnlyList<Appointment>> GetActiveByPatientAsync(
            Guid patientId, DateTime fromUtc, DateTime toUtc, CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<Appointment>>(Appointments
                .Where(a => a.PatientId == patientId && a.IsActive && a.ScheduledAtUtc >= fromUtc && a.ScheduledAtUtc < toUtc)
                .ToList());

        public Task AddAsync(Appointment appointment, CancellationToken ct = default)
        {
            Appointments.Add(appointment);
            return Task.CompletedTask;
        }

        Task<Doctor?> IDoctorRepository.GetByIdAsync(Guid id, CancellationToken ct) => Task.FromResult<Doctor?>(null);

        public Task<IReadOnlyList<Doctor>> GetAllAsync(CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<Doctor>>([]);

        public string Generate() => "654321";
    }
}
