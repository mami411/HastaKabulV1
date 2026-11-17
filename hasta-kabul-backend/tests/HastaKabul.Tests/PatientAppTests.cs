using System.Reflection;
using HastaKabul.Api.Controllers;
using HastaKabul.Api.Infrastructure;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Contracts.Appointments;
using HastaKabul.Application.Contracts.PatientApp;
using HastaKabul.Application.Services;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Patients;
using HastaKabul.Domain.Voice;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Routing;

namespace HastaKabul.Tests;

/// <summary>
/// Hasta uygulamasinin asistansiz sayfalari: /api/v1/voice-sessions/{id}/(profile|clinics|slot-days|slots|
/// appointments|visits). Oturuma bagli yetki, sesli yolla ayni randevu kurallari, yalniz kendi verisi.
/// </summary>
public class PatientAppTests
{
    private readonly DemoWorld _w = new();

    /// <summary>DemoWorld'un randevu listesi uzerinden "Randevularim" okuyucusu.</summary>
    private sealed class Reader(DemoWorld w) : IPatientAppointmentReader
    {
        public Task<IReadOnlyList<Appointment>> GetUpcomingActiveByPatientAsync(
            Guid patientId, DateTime fromUtc, CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<Appointment>>(w.Appointments
                .Where(a => a.PatientId == patientId && a.IsActive && a.ScheduledAtUtc >= fromUtc)
                .OrderBy(a => a.ScheduledAtUtc)
                .ToList());
    }

    private PatientAppService App() =>
        new(_w, _w, _w, _w, new Reader(_w), _w.SlotService(), _w, _w, _w, _w);

    /// <summary>Istanbul saatiyle (UTC+3) gun ofseti + saat.</summary>
    private DateTime Local(int dayOffset, int hour, int minute = 0)
        => _w.Now.Date.AddDays(dayOffset).AddHours(hour - 3).AddMinutes(minute);

    private Task<CreateAppointmentResponse> AppBook(VoiceSession session, AppointmentSlot slot)
        => _w.Booking().CreateForPatientAppAsync(session.Id, slot.Id.ToString());

    // ---- Randevu olusturma (asistansiz)

    [Fact]
    public async Task Uygulamadan_randevu_olusur_oturum_acik_kalir_ve_kanal_kaydedilir()
    {
        var slot = _w.Slot(_w.DrKardiyoloji, Local(1, 9, 15));
        var session = _w.VerifiedSession();

        var response = await AppBook(session, slot);

        Assert.Equal("SCHEDULED", response.Status);
        Assert.Equal("Kardiyoloji", response.ClinicName);
        Assert.Equal("Elif K.", response.DoctorDisplayName);
        Assert.Equal("111111", response.AccessCode);
        Assert.True(slot.IsTaken);
        Assert.Equal(VoiceSessionStatus.Verified, session.Status); // ayni oturumda baska randevu/asistan surer
        var audit = Assert.Single(_w.Audit, a => a.Action == "appointment.created");
        Assert.Equal("patient-app", audit.Actor);
    }

    [Fact]
    public async Task Ayni_gun_ayni_poliklinige_ikinci_uygulama_randevusu_409_APPOINTMENT_CONFLICT()
    {
        var first = _w.Slot(_w.DrKardiyoloji, Local(1, 9));
        var second = _w.Slot(_w.DrKardiyoloji, Local(1, 14));
        var session = _w.VerifiedSession();
        await AppBook(session, first);

        var ex = await Assert.ThrowsAsync<DomainException>(() => AppBook(session, second));

        Assert.Equal(ErrorCodes.AppointmentConflict, ex.Code);
        Assert.Contains("Aynı gün bu poliklinikte", ex.Message);
        Assert.False(second.IsTaken);
        Assert.Single(_w.Appointments);
    }

    [Fact]
    public async Task Asistanda_alinan_randevu_uygulamada_cakisma_sayilir_completed_oturumla_da_denenebilir()
    {
        var voiceSlot = _w.Slot(_w.DrKardiyoloji, Local(1, 9));
        var appSlot = _w.Slot(_w.DrDahiliye, Local(1, 9));   // ayni saat, baska poliklinik
        var session = _w.VerifiedSession();
        await _w.Booking().CreateFromVoiceSessionAsync(
            new CreateAppointmentRequest(session.Id.ToString(), voiceSlot.Id.ToString()));
        Assert.Equal(VoiceSessionStatus.Completed, session.Status);

        var ex = await Assert.ThrowsAsync<DomainException>(() => AppBook(session, appSlot));

        Assert.Equal(ErrorCodes.AppointmentConflict, ex.Code);
        Assert.Contains("başka bir randevunuz", ex.Message);
    }

    [Fact]
    public async Task Uygulamada_alinan_randevudan_sonra_asistan_da_randevu_alabilir_kurallar_ortak()
    {
        var session = _w.VerifiedSession();
        await AppBook(session, _w.Slot(_w.DrDahiliye, Local(2, 10)));

        var conflict = await Assert.ThrowsAsync<DomainException>(() => _w.Booking().CreateFromVoiceSessionAsync(
            new CreateAppointmentRequest(session.Id.ToString(), _w.Slot(_w.DrDahiliye, Local(2, 15)).Id.ToString())));
        var ok = await _w.Booking().CreateFromVoiceSessionAsync(
            new CreateAppointmentRequest(session.Id.ToString(), _w.Slot(_w.DrKardiyoloji, Local(2, 11)).Id.ToString()));

        Assert.Equal(ErrorCodes.AppointmentConflict, conflict.Code);
        Assert.Equal("SCHEDULED", ok.Status);
        Assert.Equal(VoiceSessionStatus.Completed, session.Status);
    }

    [Fact]
    public async Task Gecmis_saat_409_SLOT_IN_PAST_dolu_saat_409_SLOT_TAKEN()
    {
        var session = _w.VerifiedSession();
        var past = _w.Slot(_w.DrDahiliye, _w.Now.AddMinutes(-15));
        var taken = _w.Slot(_w.DrDahiliye, Local(3, 9));
        taken.Reserve();

        var inPast = await Assert.ThrowsAsync<DomainException>(() => AppBook(session, past));
        var takenEx = await Assert.ThrowsAsync<DomainException>(() => AppBook(session, taken));

        Assert.Equal(ErrorCodes.SlotInPast, inPast.Code);
        Assert.Equal(ErrorCodes.SlotTaken, takenEx.Code);
        Assert.Empty(_w.Appointments);
    }

    [Fact]
    public async Task Eszamanli_slot_cakismasi_uygulamada_da_409_SLOT_TAKEN()
    {
        var slot = _w.Slot(_w.DrDahiliye, Local(1, 10));
        _w.SaveConflicts.Enqueue(UniqueConflictTarget.AppointmentSlot);

        var ex = await Assert.ThrowsAsync<DomainException>(() => AppBook(_w.VerifiedSession(), slot));

        Assert.Equal(ErrorCodes.SlotTaken, ex.Code);
    }

    [Theory]
    [InlineData("")]
    [InlineData("abc")]
    [InlineData(null)]
    public async Task Gecersiz_slotId_400(string? slotId)
    {
        var ex = await Assert.ThrowsAsync<DomainException>(
            () => _w.Booking().CreateForPatientAppAsync(_w.VerifiedSession().Id, slotId));

        Assert.Equal(ErrorCodes.ValidationFailed, ex.Code);
    }

    [Fact]
    public async Task Dogrulanmamis_suresi_dolmus_ya_da_bilinmeyen_oturumla_randevu_olusmaz()
    {
        var slot = _w.Slot(_w.DrDahiliye, Local(1, 10));
        var active = _w.ActiveSession();
        var expired = _w.VerifiedSession();
        DemoWorld.Set(expired, nameof(VoiceSession.ExpiresAtUtc), _w.Now.AddSeconds(-1));

        var notVerified = await Assert.ThrowsAsync<DomainException>(() => AppBook(active, slot));
        var expiredEx = await Assert.ThrowsAsync<DomainException>(() => AppBook(expired, slot));
        var missing = await Assert.ThrowsAsync<DomainException>(
            () => _w.Booking().CreateForPatientAppAsync(Guid.NewGuid(), slot.Id.ToString()));

        Assert.Equal(ErrorCodes.VoiceSessionNotVerified, notVerified.Code);
        Assert.Equal(ErrorCodes.VoiceSessionNotVerified, expiredEx.Code);
        Assert.Equal(VoiceSessionStatus.Expired, expired.Status);
        Assert.Equal(ErrorCodes.VoiceSessionNotFound, missing.Code);
        Assert.False(slot.IsTaken);
    }

    [Fact]
    public async Task Suresi_dolmus_completed_oturum_da_reddedilir()
    {
        var session = _w.VerifiedSession();
        session.Complete();
        DemoWorld.Set(session, nameof(VoiceSession.ExpiresAtUtc), _w.Now.AddSeconds(-1));

        var ex = await Assert.ThrowsAsync<DomainException>(() => AppBook(session, _w.Slot(_w.DrDahiliye, Local(1, 10))));

        Assert.Equal(ErrorCodes.VoiceSessionNotVerified, ex.Code);
    }

    // ---- Cikis: oturum backend'de de kapanir

    [Fact]
    public async Task Cikista_oturum_kapanir_hasta_uclari_ve_randevu_artik_reddedilir()
    {
        var session = _w.VerifiedSession();
        var slot = _w.Slot(_w.DrKardiyoloji, Local(1, 9, 15));

        await _w.Voice().EndAsync(session.Id);

        Assert.Equal(VoiceSessionStatus.Expired, session.Status);
        Assert.Equal("EXPIRED", (await _w.Voice().GetAsync(session.Id)).Status); // orkestrator de kapali gorur
        var profile = await Assert.ThrowsAsync<DomainException>(() => App().GetProfileAsync(session.Id));
        var list = await Assert.ThrowsAsync<DomainException>(() => App().GetAppointmentsAsync(session.Id));
        var book = await Assert.ThrowsAsync<DomainException>(() => AppBook(session, slot));
        var voiceBook = await Assert.ThrowsAsync<DomainException>(() => _w.Booking().CreateFromVoiceSessionAsync(
            new CreateAppointmentRequest(session.Id.ToString(), slot.Id.ToString())));
        Assert.All([profile, list, book, voiceBook], ex => Assert.Equal(ErrorCodes.VoiceSessionNotVerified, ex.Code));
        Assert.False(slot.IsTaken);
        Assert.Single(_w.Audit, a => a.Action == "voiceSession.ended");
    }

    [Fact]
    public async Task Cikis_completed_oturumu_da_kapatir_tekrar_cagri_zararsiz_bilinmeyen_404()
    {
        var completed = _w.VerifiedSession();
        completed.Complete();
        var handedOff = _w.ActiveSession();
        handedOff.HandOff();

        await _w.Voice().EndAsync(completed.Id);
        await _w.Voice().EndAsync(completed.Id);
        await _w.Voice().EndAsync(handedOff.Id);
        var missing = await Assert.ThrowsAsync<DomainException>(() => _w.Voice().EndAsync(Guid.NewGuid()));

        Assert.Equal(VoiceSessionStatus.Expired, completed.Status);
        Assert.Equal(VoiceSessionStatus.HandedOff, handedOff.Status);
        Assert.Equal(ErrorCodes.VoiceSessionNotFound, missing.Code);
        Assert.Single(_w.Audit, a => a.Action == "voiceSession.ended");
        await Assert.ThrowsAsync<DomainException>(() => App().GetProfileAsync(completed.Id));
    }

    [Fact]
    public void Cikis_ucu_servis_anahtari_istemez_ve_POST_end_yolundadir()
    {
        var method = typeof(VoiceSessionsController).GetMethod(nameof(VoiceSessionsController.EndAsync))!;
        var post = method.GetCustomAttribute<HttpPostAttribute>();

        Assert.Equal("{sessionId:guid}/end", post?.Template);
        Assert.Null(method.GetCustomAttribute<RequireServiceKeyAttribute>());
    }

    // ---- Okuma uclari

    [Fact]
    public async Task Profil_maskeli_ad_ve_oturum_bitisini_verir()
    {
        var session = _w.VerifiedSession();

        var profile = await App().GetProfileAsync(session.Id);

        Assert.Equal("Ayşe H.", profile.DisplayName);
        Assert.Equal(session.ExpiresAtUtc, profile.SessionExpiresAt);
        Assert.Equal(DateTimeKind.Utc, profile.SessionExpiresAt.Kind);
        Assert.Equal(15 * 60, profile.SessionRemainingSeconds);
    }

    [Fact]
    public async Task Okuma_uclari_dogrulanmamis_oturuma_kapali()
    {
        var active = _w.ActiveSession();
        var app = App();

        var calls = new Func<Task>[]
        {
            () => app.GetProfileAsync(active.Id),
            () => app.GetClinicsAsync(active.Id),
            () => app.GetSlotDaysAsync(active.Id, "DAHILIYE", null),
            () => app.GetSlotsAsync(active.Id, "DAHILIYE", DateOnly.FromDateTime(_w.Now), null),
            () => app.GetAppointmentsAsync(active.Id),
        };

        foreach (var call in calls)
        {
            var ex = await Assert.ThrowsAsync<DomainException>(call);
            Assert.Equal(ErrorCodes.VoiceSessionNotVerified, ex.Code);
        }
        Assert.Empty(_w.Audit);
    }

    [Fact]
    public async Task Poliklinikler_hekimleriyle_doner()
    {
        var clinics = await App().GetClinicsAsync(_w.VerifiedSession().Id);

        Assert.Equal(["Dahiliye", "Kardiyoloji"], clinics.Select(c => c.Name));
        Assert.Equal("DAHILIYE", clinics[0].Code);
        var doctor = Assert.Single(clinics[0].Doctors);
        Assert.Equal("Mehmet D.", doctor.DisplayName);
        Assert.Equal(_w.DrDahiliye.Id.ToString(), doctor.DoctorId);
    }

    [Fact]
    public async Task Gun_listesi_8_gun_ve_gecmis_saatleri_saymaz()
    {
        _w.Slot(_w.DrDahiliye, Local(0, 9));      // bugun 09:00: saat 10:00 oldugu icin gecmis
        _w.Slot(_w.DrDahiliye, Local(0, 11));
        _w.Slot(_w.DrDahiliye, Local(1, 9));
        _w.Slot(_w.DrDahiliye, Local(1, 9, 15)).Reserve();   // dolu
        _w.Slot(_w.DrKardiyoloji, Local(1, 9));   // baska poliklinik
        _w.Slot(_w.DrDahiliye, Local(9, 9));      // pencere disi

        var days = await App().GetSlotDaysAsync(_w.VerifiedSession().Id, "dahiliye", null);

        Assert.Equal(PatientAppService.SlotDayWindow, days.Count);
        Assert.Equal("2026-11-16", days[0].Date);
        Assert.Equal(1, days[0].AvailableCount);
        Assert.Equal(1, days[1].AvailableCount);
        Assert.All(days.Skip(2), d => Assert.Equal(0, d.AvailableCount));
    }

    [Fact]
    public async Task Gun_listesi_ve_saatler_hekime_gore_suzulur()
    {
        var other = new HastaKabul.Domain.Doctors.Doctor(_w.Dahiliye.Id, "Can Er", "205");
        _w.Slot(_w.DrDahiliye, Local(1, 9));
        _w.Slot(other, Local(1, 9, 15));
        var session = _w.VerifiedSession();
        var day = DateOnly.FromDateTime(Local(1, 12));

        var days = await App().GetSlotDaysAsync(session.Id, "DAHILIYE", other.Id.ToString());
        var slots = await App().GetSlotsAsync(session.Id, "DAHILIYE", day, other.Id.ToString());
        var all = await App().GetSlotsAsync(session.Id, "DAHILIYE", day, null);

        Assert.Equal(1, days[1].AvailableCount);
        var slot = Assert.Single(slots);
        Assert.Equal(other.Id.ToString(), slot.DoctorId);
        Assert.Equal("Can E.", slot.DoctorDisplayName);
        Assert.Equal(2, all.Count);
        Assert.True(all[0].StartTime < all[1].StartTime);
    }

    [Fact]
    public async Task Gecersiz_hekim_ve_bilinmeyen_poliklinik_anlasilir_hata_verir()
    {
        var session = _w.VerifiedSession();

        var badDoctor = await Assert.ThrowsAsync<DomainException>(
            () => App().GetSlotDaysAsync(session.Id, "DAHILIYE", "x"));
        var unknown = await Assert.ThrowsAsync<DomainException>(
            () => App().GetSlotDaysAsync(session.Id, "Uzay Hekimligi", null));
        var missing = await Assert.ThrowsAsync<DomainException>(
            () => App().GetSlotsAsync(session.Id, " ", DateOnly.FromDateTime(_w.Now), null));

        Assert.Equal(ErrorCodes.ValidationFailed, badDoctor.Code);
        Assert.Equal(ErrorCodes.NotFound, unknown.Code);
        Assert.Equal(ErrorCodes.ValidationFailed, missing.Code);
    }

    [Fact]
    public async Task Randevularim_yalniz_kendi_etkin_bugun_ve_sonraki_randevulari()
    {
        var baskasi = new Patient("h:x", [1], "Ali", "Veli");
        var today = _w.Appointment(_w.DrDahiliye, Local(0, 9), code: "246802");   // bugun, saati gecmis ama etkin
        var later = _w.Appointment(_w.DrKardiyoloji, Local(2, 10), code: "135790");
        _w.Appointment(_w.DrDahiliye, Local(-3, 9), code: "000001");               // gecmis gun
        _w.Appointment(_w.DrDahiliye, Local(1, 9), patient: baskasi, code: "000002");
        var noShow = _w.Appointment(_w.DrKardiyoloji, Local(0, 9, 30), code: "000003");
        noShow.MarkNoShow(_w.Now);
        var session = _w.VerifiedSession();

        var result = await App().GetAppointmentsAsync(session.Id);

        Assert.Equal([today.Id.ToString(), later.Id.ToString()], result.Upcoming.Select(a => a.AppointmentId));
        var first = result.Upcoming[0];
        Assert.Equal("Dahiliye", first.ClinicName);
        Assert.Equal("Mehmet D.", first.DoctorDisplayName);
        Assert.Equal("204", first.Room);
        Assert.Equal("SCHEDULED", first.Status);
        Assert.Equal("246802", first.AccessCode);
        var audit = Assert.Single(_w.Audit);
        Assert.Equal("voiceSession.appointmentsRead", audit.Action);
        Assert.Equal("patient-app", audit.Actor);
        Assert.Equal("upcoming=2", audit.Detail);
    }

    [Fact]
    public async Task Uygulamada_alinan_randevu_randevularimda_gorunur()
    {
        var session = _w.VerifiedSession();
        var booked = await AppBook(session, _w.Slot(_w.DrKardiyoloji, Local(1, 9)));

        var result = await App().GetAppointmentsAsync(session.Id);

        var item = Assert.Single(result.Upcoming);
        Assert.Equal(booked.AppointmentId, item.AppointmentId);
        Assert.Equal(booked.AccessCode, item.AccessCode);
    }

    // ---- Gecmis muayeneler (uygulama)

    [Fact]
    public async Task Uygulama_gecmisi_ayni_onayli_filtreyle_ve_kendi_aktoruyle_okur()
    {
        var fakes = new MedicalHistoryTests.Fakes();
        var service = new MedicalHistoryService(fakes, fakes, fakes, fakes, fakes, fakes, fakes);
        var patient = fakes.Patient();
        var session = fakes.VerifiedSession(patient);
        for (var i = 0; i < 7; i++)
            fakes.Visit(patient, daysAgo: 10 + i, approve: true, diagnosis: $"T{i}");
        fakes.Visit(patient, daysAgo: 1, approve: false, diagnosis: "Onaysiz");

        var result = await service.GetForPatientAppAsync(session.Id, MedicalHistoryService.AppDefaultLimit);

        Assert.Equal(7, result.Visits.Count);   // orkestrator siniri (5) uygulamaya uygulanmaz
        Assert.DoesNotContain(result.Visits, v => v.DiagnosisPlain == "Onaysiz");
        var audit = Assert.Single(fakes.Audit);
        Assert.Equal("patient-app", audit.Actor);
        Assert.Equal("voiceSession.medicalHistoryRead", audit.Action);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(21)]
    public async Task Uygulama_gecmisi_limit_1_ile_20_disinda_reddedilir(int limit)
    {
        var fakes = new MedicalHistoryTests.Fakes();
        var service = new MedicalHistoryService(fakes, fakes, fakes, fakes, fakes, fakes, fakes);
        var session = fakes.VerifiedSession(fakes.Patient());

        var ex = await Assert.ThrowsAsync<DomainException>(() => service.GetForPatientAppAsync(session.Id, limit));

        Assert.Equal(ErrorCodes.ValidationFailed, ex.Code);
    }

    // ---- Sozlesme ve yetki

    [Fact]
    public void Yanitlar_kimlik_alani_tasimaz()
    {
        var forbidden = new[] { "Tc", "PatientRef", "PatientId", "FullName", "FirstName", "LastName" };
        var types = new[]
        {
            typeof(PatientProfileView), typeof(PatientClinicView), typeof(PatientDoctorView), typeof(SlotDayView),
            typeof(PatientSlotView), typeof(PatientAppointmentView), typeof(PatientAppointmentsResponse),
            typeof(CreatePatientAppointmentRequest), typeof(CreateAppointmentResponse),
        };

        foreach (var type in types)
            Assert.DoesNotContain(type.GetProperties(), p => forbidden.Contains(p.Name));
        Assert.Equal(["SlotId"], typeof(CreatePatientAppointmentRequest).GetProperties().Select(p => p.Name));
    }

    [Theory]
    [InlineData(nameof(PatientAppController.GetProfileAsync), "profile")]
    [InlineData(nameof(PatientAppController.GetClinicsAsync), "clinics")]
    [InlineData(nameof(PatientAppController.GetSlotDaysAsync), "slot-days")]
    [InlineData(nameof(PatientAppController.GetSlotsAsync), "slots")]
    [InlineData(nameof(PatientAppController.GetAppointmentsAsync), "appointments")]
    [InlineData(nameof(PatientAppController.CreateAppointmentAsync), "appointments")]
    [InlineData(nameof(PatientAppController.GetVisitsAsync), "visits")]
    public void Uclar_oturuma_bagli_servis_anahtari_ve_hekim_tokeni_istemez(string method, string template)
    {
        var controller = typeof(PatientAppController);
        var action = controller.GetMethod(method)!;
        var route = action.GetCustomAttributes().OfType<HttpMethodAttribute>().Single();

        Assert.Equal("api/v1/voice-sessions/{sessionId:guid}", controller.GetCustomAttribute<RouteAttribute>()!.Template);
        Assert.Equal(template, route.Template);
        Assert.Null(action.GetCustomAttribute<RequireServiceKeyAttribute>());
        Assert.Null(action.GetCustomAttribute<AuthorizeAttribute>());
        Assert.Null(controller.GetCustomAttribute<AuthorizeAttribute>());
    }

    [Fact]
    public void Orkestratorun_uclari_degismedi()
    {
        var history = typeof(VoiceSessionsController).GetMethod(nameof(VoiceSessionsController.GetMedicalHistoryAsync))!;
        var create = typeof(AppointmentsController).GetMethod(nameof(AppointmentsController.CreateAsync))!;
        var slots = typeof(SlotsController).GetMethod(nameof(SlotsController.GetAsync))!;

        Assert.NotNull(history.GetCustomAttribute<RequireServiceKeyAttribute>());
        Assert.NotNull(create.GetCustomAttribute<RequireServiceKeyAttribute>());
        Assert.NotNull(slots.GetCustomAttribute<RequireServiceKeyAttribute>());
        Assert.Equal(["VoiceSessionId", "SlotId"], typeof(CreateAppointmentRequest).GetProperties().Select(p => p.Name));
    }
}
