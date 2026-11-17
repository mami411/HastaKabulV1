using System.Reflection;
using System.Text.Json;
using HastaKabul.Api.Infrastructure;
using HastaKabul.Api.Middleware;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Appointments;
using HastaKabul.Application.Contracts.Kiosk;
using HastaKabul.Application.Contracts.Portal;
using HastaKabul.Application.Contracts.Realtime;
using HastaKabul.Application.Contracts.Summaries;
using HastaKabul.Application.Contracts.Voice;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Summaries;
using HastaKabul.Domain.Voice;
using HastaKabul.Infrastructure.Ai;
using HastaKabul.Infrastructure.Persistence.Repositories;
using HastaKabul.Infrastructure.Persistence.Seed;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Abstractions;
using Microsoft.AspNetCore.Mvc.ModelBinding;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;

namespace HastaKabul.Tests;

/// <summary>Madde 1 ve 3: cakisan randevu, gecmis slot, eszamanli slot/erisim kodu cakismasi.</summary>
public class BookingRuleTests
{
    private readonly DemoWorld _w = new();
    private DateTime Tomorrow(int hour, int minute = 0) => _w.Now.Date.AddDays(1).AddHours(hour - 3).AddMinutes(minute); // Istanbul saati

    private Task<CreateAppointmentResponse> Book(VoiceSession session, AppointmentSlot slot)
        => _w.Booking().CreateFromVoiceSessionAsync(new CreateAppointmentRequest(session.Id.ToString(), slot.Id.ToString()));

    [Fact]
    public async Task Ayni_saatte_baska_poliklinikten_randevu_409_APPOINTMENT_CONFLICT()
    {
        _w.Appointment(_w.DrDahiliye, Tomorrow(9));
        var slot = _w.Slot(_w.DrKardiyoloji, Tomorrow(9));
        var session = _w.VerifiedSession();

        var ex = await Assert.ThrowsAsync<DomainException>(() => Book(session, slot));

        Assert.Equal(ErrorCodes.AppointmentConflict, ex.Code);
        Assert.Contains("başka bir randevunuz", ex.Message);
        Assert.False(slot.IsTaken);
        Assert.Equal(VoiceSessionStatus.Verified, session.Status);   // hasta baska saat secebilir
        Assert.Single(_w.Appointments);
    }

    [Fact]
    public async Task Ortusen_aralik_da_cakisma_sayilir()
    {
        _w.Appointment(_w.DrDahiliye, Tomorrow(9));
        var slot = _w.Slot(_w.DrKardiyoloji, Tomorrow(9, 10));   // 09:10 - 09:00'daki 15 dk ile ortusur

        var ex = await Assert.ThrowsAsync<DomainException>(() => Book(_w.VerifiedSession(), slot));

        Assert.Equal(ErrorCodes.AppointmentConflict, ex.Code);
    }

    [Fact]
    public async Task Ayni_gun_ayni_poliklinige_ikinci_randevu_409()
    {
        _w.Appointment(_w.DrDahiliye, Tomorrow(9));
        var slot = _w.Slot(_w.DrDahiliye, Tomorrow(14));

        var ex = await Assert.ThrowsAsync<DomainException>(() => Book(_w.VerifiedSession(), slot));

        Assert.Equal(ErrorCodes.AppointmentConflict, ex.Code);
        Assert.Contains("Aynı gün bu poliklinikte", ex.Message);
    }

    [Fact]
    public async Task Ayni_gun_farkli_poliklinik_ve_farkli_saat_serbest()
    {
        _w.Appointment(_w.DrDahiliye, Tomorrow(9));
        var slot = _w.Slot(_w.DrKardiyoloji, Tomorrow(14));

        var result = await Book(_w.VerifiedSession(), slot);

        Assert.Equal("SCHEDULED", result.Status);
        Assert.True(slot.IsTaken);
    }

    [Fact]
    public async Task Baska_gun_ayni_poliklinik_serbest()
    {
        _w.Appointment(_w.DrDahiliye, Tomorrow(9));
        var slot = _w.Slot(_w.DrDahiliye, Tomorrow(9).AddDays(1));

        var result = await Book(_w.VerifiedSession(), slot);

        Assert.Equal("111111", result.AccessCode);
    }

    [Theory]
    [InlineData(AppointmentStatus.Completed)]
    [InlineData(AppointmentStatus.NoShow)]
    public async Task Etkin_olmayan_randevu_cakisma_yaratmaz(AppointmentStatus status)
    {
        var old = _w.Appointment(_w.DrDahiliye, Tomorrow(9));
        if (status is AppointmentStatus.NoShow)
            old.MarkNoShow(_w.Now);
        else
        {
            old.CheckIn(_w.Now);
            old.StartExam(_w.Now);
            old.Complete(_w.Now);
        }

        var result = await Book(_w.VerifiedSession(), _w.Slot(_w.DrDahiliye, Tomorrow(9)));

        Assert.Equal("SCHEDULED", result.Status);
    }

    [Fact]
    public async Task Baslangici_gecmis_slot_409_SLOT_IN_PAST()
    {
        var slot = _w.Slot(_w.DrDahiliye, _w.Now.AddMinutes(-15));

        var ex = await Assert.ThrowsAsync<DomainException>(() => Book(_w.VerifiedSession(), slot));

        Assert.Equal(ErrorCodes.SlotInPast, ex.Code);
        Assert.Equal(409, ErrorResponseWriter.MapStatusCode(ex.Code));
        Assert.False(slot.IsTaken);
    }

    [Fact]
    public async Task Dolu_slot_cakismadan_once_SLOT_TAKEN_doner()
    {
        _w.Appointment(_w.DrDahiliye, Tomorrow(9));
        var slot = _w.Slot(_w.DrDahiliye, Tomorrow(9));
        slot.Reserve();

        var ex = await Assert.ThrowsAsync<DomainException>(() => Book(_w.VerifiedSession(), slot));

        Assert.Equal(ErrorCodes.SlotTaken, ex.Code);
    }

    [Fact]
    public async Task Eszamanli_slot_cakismasi_benzersiz_indeksten_409_SLOT_TAKEN()
    {
        var slot = _w.Slot(_w.DrDahiliye, Tomorrow(10));
        _w.SaveConflicts.Enqueue(UniqueConflictTarget.AppointmentSlot);

        var ex = await Assert.ThrowsAsync<DomainException>(() => Book(_w.VerifiedSession(), slot));

        Assert.Equal(ErrorCodes.SlotTaken, ex.Code);
        Assert.Equal("Seçilen saat başka bir hasta tarafından alındı.", ex.Message);
    }

    [Fact]
    public async Task Erisim_kodu_cakismasinda_yeni_kodla_tekrar_denenir()
    {
        var slot = _w.Slot(_w.DrDahiliye, Tomorrow(10));
        _w.SaveConflicts.Enqueue(UniqueConflictTarget.AccessCode);
        _w.SaveConflicts.Enqueue(UniqueConflictTarget.AccessCode);

        var result = await Book(_w.VerifiedSession(), slot);

        Assert.Equal("333333", result.AccessCode);
        Assert.Equal(3, _w.Saves);
    }

    [Fact]
    public void Benzersiz_indeks_adlari_dogru_siniflandirilir()
    {
        Assert.Equal(UniqueConflictTarget.AppointmentSlot, UnitOfWork.ClassifyUniqueViolation("appointments", "ix_appointments_slot_id"));
        Assert.Equal(UniqueConflictTarget.AccessCode, UnitOfWork.ClassifyUniqueViolation("appointments", "ix_appointments_access_code"));
        Assert.Equal(UniqueConflictTarget.Other, UnitOfWork.ClassifyUniqueViolation("patients", "ix_patients_tc_hash"));
    }
}

/// <summary>Madde 2: bicimi gecersiz TC deneme hakkindan dusmez; basarili dogrulama deneme sayilmaz.</summary>
public class TcAttemptTests
{
    private readonly DemoWorld _w = new();

    [Theory]
    [InlineData("123")]
    [InlineData("11111111111")]   // 11 hane ama kontrol hanesi tutmuyor
    [InlineData("01234567890")]
    [InlineData("")]
    public async Task Bicimi_gecersiz_TC_400_ve_sayac_artmaz(string tc)
    {
        var session = _w.ActiveSession();

        var ex = await Assert.ThrowsAsync<DomainException>(
            () => _w.Voice().VerifyAsync(session.Id, new VerifyVoiceSessionRequest(tc)));

        Assert.Equal(ErrorCodes.ValidationFailed, ex.Code);
        Assert.Contains("biçimi geçersiz", ex.Message);
        Assert.Equal(0, session.Attempts);
        Assert.Equal(VoiceSessionStatus.Active, session.Status);
    }

    [Fact]
    public async Task Bicimi_gecerli_ama_kayitsiz_TC_deneme_sayilir()
    {
        Assert.True(TcKimlikNo.IsWellFormed(DemoWorld.UnregisteredTc));
        var session = _w.ActiveSession();

        var result = await _w.Voice().VerifyAsync(session.Id, new VerifyVoiceSessionRequest(DemoWorld.UnregisteredTc));

        Assert.False(result.Verified);
        Assert.Equal(1, result.Attempts);
    }

    [Fact]
    public async Task Basarili_dogrulama_deneme_sayilmaz()
    {
        var session = _w.ActiveSession();
        await _w.Voice().VerifyAsync(session.Id, new VerifyVoiceSessionRequest(DemoWorld.UnregisteredTc));

        var result = await _w.Voice().VerifyAsync(session.Id, new VerifyVoiceSessionRequest(DemoWorld.DemoTc));

        Assert.True(result.Verified);
        Assert.Equal(1, result.Attempts);
    }

    [Fact]
    public async Task Hak_bitmis_oturumda_bicim_hatasi_yerine_EXHAUSTED_doner()
    {
        var session = _w.ActiveSession();
        for (var i = 0; i < VoiceSession.MaxAttempts; i++)
            session.RegisterFailedAttempt();

        var ex = await Assert.ThrowsAsync<DomainException>(
            () => _w.Voice().VerifyAsync(session.Id, new VerifyVoiceSessionRequest("123")));

        Assert.Equal(ErrorCodes.VoiceSessionExhausted, ex.Code);
    }

    [Fact]
    public async Task Dogrulanmis_oturuma_yeniden_TC_kodu_ayni_mesaj_anlasilir()
    {
        var session = _w.VerifiedSession();

        var ex = await Assert.ThrowsAsync<DomainException>(
            () => _w.Voice().VerifyAsync(session.Id, new VerifyVoiceSessionRequest(DemoWorld.DemoTc)));

        Assert.Equal(ErrorCodes.InvalidStatusTransition, ex.Code);   // kiosk bu koda gore davranir
        Assert.Equal("Bu oturumda kimlik zaten doğrulandı.", ex.Message);
    }

    [Fact]
    public async Task Kiosk_bicim_hatasi_Turkce_mesajla_400()
    {
        var kiosk = new HastaKabul.Application.Services.KioskService(_w, _w, _w, _w, _w, _w, _w, _w, _w);

        var ex = await Assert.ThrowsAsync<DomainException>(() => kiosk.VerifyAsync(new VerifyTcRequest("123")));

        Assert.Equal(ErrorCodes.ValidationFailed, ex.Code);
        Assert.Contains("biçimi geçersiz", ex.Message);
    }
}

/// <summary>Madde 5: sadelestirme akisinin demo dayanikliligi.</summary>
public class SummaryFlowTests
{
    private readonly DemoWorld _w = new();

    private List<SummaryReadyEvent> SummaryEvents() => _w.Events
        .Where(e => e.Event == RealtimeEventNames.SummaryReady)
        .Select(e => (SummaryReadyEvent)e.Payload)
        .ToList();

    [Fact]
    public async Task Zaman_asimini_gecen_PENDING_okumada_yedek_ozete_duser_ve_olay_yayar()
    {
        var summary = _w.PendingSummary("Hipertansiyon", ["Amlodipin 5 mg 1x1"], TimeSpan.FromSeconds(91));

        var view = await _w.Summary().GetAsync(_w.DrDahiliye.Id, summary.Id);

        Assert.Equal("READY", view.Status);
        Assert.Contains("tansiyon", view.DiagnosisPlain);
        Assert.Equal("Amlodipin", Assert.Single(view.Medications).Name);
        var ev = Assert.Single(SummaryEvents());
        Assert.Equal("READY", ev.Status);
    }

    [Fact]
    public async Task Zaman_asimi_dolmadan_PENDING_kalir()
    {
        var summary = _w.PendingSummary("Hipertansiyon", [], TimeSpan.FromSeconds(30));

        var view = await _w.Summary().GetAsync(_w.DrDahiliye.Id, summary.Id);

        Assert.Equal("PENDING", view.Status);
        Assert.Empty(_w.Events);
    }

    [Fact]
    public async Task Yedegi_olmayan_tanida_zaman_asimi_FAILED_LLM_UNAVAILABLE()
    {
        var summary = _w.PendingSummary("Lomber disk hernisi", [], TimeSpan.FromMinutes(5));

        var list = await _w.Panel().GetDayAsync(_w.DrDahiliye.Id, IstanbulDay(_w.Now));

        var item = Assert.Single(list);
        Assert.Equal("FAILED", item.SummaryStatus);
        Assert.Equal(ErrorCodes.LlmUnavailable, summary.FailureCode);
        Assert.Equal("FAILED", Assert.Single(SummaryEvents()).Status);
    }

    [Fact]
    public async Task Ret_REJECTED_doner_ve_ayni_kayit_yeniden_uretime_gider()
    {
        _w.SimplifyUp = true;
        var summary = _w.PendingSummary("Gastrit", ["Famotidin 40 mg"], TimeSpan.Zero);
        summary.MarkReady("eski", [], [], [], [], _w.Now);
        _w.RequestId = "req-ret";

        var decision = await _w.Summary().RejectAsync(_w.DrDahiliye.Id, summary.Id);

        Assert.Equal("REJECTED", decision.Status);
        Assert.Equal(SummaryStatus.Pending, summary.Status);
        var call = Assert.Single(_w.SimplifyCalls);
        Assert.Equal(summary.Id, call.SummaryId);
        Assert.Equal("req-ret", call.RequestId);
        Assert.Equal("Gastrit", call.Diagnosis);
        Assert.Contains(_w.Audit, a => a.Action == "summary.rejected");
        Assert.Contains(_w.Audit, a => a.Action == "summary.regenerationRequested");
    }

    [Fact]
    public async Task Ret_sonrasi_servis_kapaliysa_yedek_ozet_READY_olur()
    {
        var summary = _w.PendingSummary("Gastrit", ["Famotidin 40 mg 1x1"], TimeSpan.Zero);
        summary.MarkReady("eski", [], [], [], [], _w.Now);

        await _w.Summary().RejectAsync(_w.DrDahiliye.Id, summary.Id);

        Assert.Equal(SummaryStatus.Ready, summary.Status);
        Assert.Contains("mide", summary.DiagnosisPlain!, StringComparison.OrdinalIgnoreCase);
        Assert.Equal(["PENDING", "READY"], SummaryEvents().Select(e => e.Status));
    }

    [Fact]
    public void Reddedilen_ozete_gec_gelen_geri_cagri_READY_yapamaz()
    {
        var summary = new SimplifiedSummary(Guid.NewGuid(), Guid.NewGuid(), Guid.NewGuid(), "r");
        summary.MarkReady("a", [], [], [], [], DateTime.UtcNow);
        summary.Reject(DateTime.UtcNow);

        Assert.Throws<InvalidStatusTransitionException>(() => summary.MarkReady("gec", [], [], [], [], DateTime.UtcNow));
        Assert.Throws<InvalidStatusTransitionException>(() => summary.MarkFailed("X", DateTime.UtcNow));
        Assert.Equal(SummaryStatus.Rejected, summary.Status);
    }

    [Fact]
    public void Retry_yalniz_reddedilen_ya_da_basarisiz_ozette()
    {
        var summary = new SimplifiedSummary(Guid.NewGuid(), Guid.NewGuid(), Guid.NewGuid(), "r");
        Assert.Throws<InvalidStatusTransitionException>(() => summary.Retry("r2", DateTime.UtcNow));

        summary.MarkFailed(ErrorCodes.LlmUnavailable, DateTime.UtcNow);
        summary.Retry("r2", DateTime.UtcNow);
        Assert.Equal(SummaryStatus.Pending, summary.Status);
    }

    [Fact]
    public async Task READY_ozete_gec_gelen_hata_geri_cagrisi_icerigi_silmez()
    {
        var summary = _w.PendingSummary("Migren", [], TimeSpan.Zero);
        summary.MarkReady("hazır içerik", [], [], [], [], _w.Now);

        var view = await _w.Summary().ApplyCallbackAsync(
            summary.Id, new SummaryCallbackRequest(null, null, null, null, null, "SIMPLIFY_FAILED"));

        Assert.Equal("READY", view.Status);
        Assert.Equal("hazır içerik", view.DiagnosisPlain);
        Assert.Contains(_w.Audit, a => a.Action == "summary.callbackIgnored");
    }

    [Fact]
    public async Task FAILED_geri_cagrisi_de_panele_bildirilir()
    {
        var summary = _w.PendingSummary("Migren", [], TimeSpan.Zero);

        await _w.Summary().ApplyCallbackAsync(
            summary.Id, new SummaryCallbackRequest(null, null, null, null, null, ErrorCodes.SimplifyFailed));

        Assert.Equal("FAILED", Assert.Single(SummaryEvents()).Status);
    }

    [Fact]
    public async Task Baska_hekim_ozeti_okuyamaz_403()
    {
        var summary = _w.PendingSummary("Migren", [], TimeSpan.Zero);

        var ex = await Assert.ThrowsAsync<DomainException>(() => _w.Summary().GetAsync(_w.DrKardiyoloji.Id, summary.Id));

        Assert.Equal(ErrorCodes.Forbidden, ex.Code);
    }

    [Fact]
    public async Task Klinik_giris_otomatik_muayene_ve_yedek_READY_olaylarini_yayar()
    {
        var appointment = _w.Appointment(_w.DrDahiliye, _w.Now);
        appointment.CheckIn(_w.Now);

        var result = await _w.ClinicalEntry().CreateAsync(
            _w.DrDahiliye.Id, appointment.Id, new CreateClinicalEntryRequest("Dispepsi", ["Lansoprazol 30 mg 1x1 aç"]));

        Assert.Equal("READY", result.SummaryStatus);
        Assert.Equal(AppointmentStatus.InExam, appointment.Status);
        Assert.Equal(
            [RealtimeEventNames.AppointmentStatusChanged, RealtimeEventNames.SummaryReady],
            _w.Events.Select(e => e.Event));
        Assert.Equal("IN_EXAM", ((AppointmentStatusChangedEvent)_w.Events[0].Payload).Status);
    }

    [Fact]
    public async Task Klinik_giris_ilac_listesi_bos_ya_da_null_olabilir()
    {
        var appointment = _w.Appointment(_w.DrDahiliye, _w.Now);
        appointment.CheckIn(_w.Now);

        var result = await _w.ClinicalEntry().CreateAsync(
            _w.DrDahiliye.Id, appointment.Id, new CreateClinicalEntryRequest("Migren", null));

        Assert.Equal("READY", result.SummaryStatus);
        Assert.Empty(Assert.Single(_w.Entries).Medications);
    }

    [Fact]
    public async Task Klinik_giris_cok_uzun_tani_400()
    {
        var appointment = _w.Appointment(_w.DrDahiliye, _w.Now);
        appointment.CheckIn(_w.Now);

        var ex = await Assert.ThrowsAsync<DomainException>(() => _w.ClinicalEntry().CreateAsync(
            _w.DrDahiliye.Id, appointment.Id, new CreateClinicalEntryRequest(new string('a', 401), [])));

        Assert.Equal(ErrorCodes.ValidationFailed, ex.Code);
    }

    [Fact]
    public async Task Kabul_edilmemis_hastaya_tani_Turkce_aciklamayla_409()
    {
        var appointment = _w.Appointment(_w.DrDahiliye, _w.Now);

        var ex = await Assert.ThrowsAsync<DomainException>(() => _w.ClinicalEntry().CreateAsync(
            _w.DrDahiliye.Id, appointment.Id, new CreateClinicalEntryRequest("Migren", [])));

        Assert.Equal(ErrorCodes.InvalidStatusTransition, ex.Code);
        Assert.Contains("SCHEDULED", ex.Message);
    }

    private static DateOnly IstanbulDay(DateTime utc)
        => DateOnly.FromDateTime(TimeZoneInfo.ConvertTimeFromUtc(utc, IstanbulTime.Zone));
}

/// <summary>Madde 5d: yedek ozetler (8 tani) ve ilaclarin hekim girdisinden gelmesi.</summary>
public class FallbackSummaryTests
{
    [Theory]
    [InlineData("Dispepsi", "asit")]
    [InlineData("Gastrit", "iltihabı")]
    [InlineData("Hipertansiyon", "tansiyon")]
    [InlineData("Tip 2 Diyabet", "şeker")]
    [InlineData("Demir Eksikliği Anemisi", "kansızlık")]
    [InlineData("MİGREN", "baş ağrınız")]
    [InlineData("akut sinuzit", "sinüs")]
    [InlineData("Hipotiroidi", "tiroid")]
    public void Seed_KB_deki_her_tani_icin_yedek_var(string tani, string beklenen)
    {
        var ozet = SeedFallbackSummaryProvider.Build(tani, []);

        Assert.NotNull(ozet);
        Assert.Contains(beklenen, ozet!.DiagnosisPlain, StringComparison.OrdinalIgnoreCase);
        Assert.NotEmpty(ozet.Warnings);
        Assert.Empty(ozet.Medications);   // hekim ilac yazmadiysa ilac uydurulmaz
        Assert.Empty(ozet.UnknownTerms);
        var kbIds = DemoDataSeeder.SeedKnowledgeBase().Select(e => e.Id.ToString()).ToHashSet();
        Assert.All(ozet.SourceIds, id => Assert.Contains(id, kbIds));
    }

    [Fact]
    public void Ilaclar_hekim_girdisinden_bilinmeyen_ilac_isaretlenir()
    {
        var ozet = SeedFallbackSummaryProvider.Build("Hipertansiyon", ["Amlodipin 5 mg tablet 1x1", "Zyxofen 10 mg 2x1 tok"])!;

        Assert.Equal(["Amlodipin", "Zyxofen 10 mg 2x1 tok"], ozet.Medications.Select(m => m.Name));
        Assert.Equal("Günde 1 kez 1 tane alın. Her gün aynı saatte alın.", ozet.Medications[0].Usage);
        Assert.Equal("Günde 2 kez 1 tane, tok karnına alın.", ozet.Medications[1].Usage);
        Assert.Contains("hekiminize", ozet.Medications[1].Purpose);
        Assert.Equal(["Zyxofen 10 mg 2x1 tok"], ozet.UnknownTerms);
        Assert.Contains("İlaçlarınızı kendi kararınızla bırakmayın.", ozet.Warnings);
        Assert.Contains(DemoDataSeeder.KnowledgeBaseId("Amlodipin").ToString(), ozet.SourceIds);
    }

    [Fact]
    public void Hekim_ac_tok_belirttiyse_celiskili_ipucu_eklenmez()
    {
        var ozet = SeedFallbackSummaryProvider.Build("Dispepsi", ["Lansoprazol 30 mg 1x1 tok"])!;

        Assert.Equal("Günde 1 kez 1 tane, tok karnına alın.", ozet.Medications.Single().Usage);
    }

    [Fact]
    public void Bilesik_tanida_taninmayan_bolum_unknownTerms_e_gider()
    {
        var ozet = SeedFallbackSummaryProvider.Build("Tip 2 Diyabet; Lomber disk hernisi", ["Metformin 1000 mg 2x1"])!;

        Assert.Contains("şeker", ozet.DiagnosisPlain);
        Assert.Equal(["Lomber disk hernisi"], ozet.UnknownTerms);
    }
}

/// <summary>Madde 6 ve 7: slotlar, portal alanlari, servis anahtari.</summary>
public class DemoDataAndSecurityTests
{
    [Fact]
    public async Task Bugunun_gecmis_slotlari_sunulmaz()
    {
        var w = new DemoWorld();   // Istanbul 10:00
        var past = w.Slot(w.DrDahiliye, w.Now.AddMinutes(-30));
        var now = w.Slot(w.DrDahiliye, w.Now);
        var future = w.Slot(w.DrDahiliye, w.Now.AddMinutes(30));

        var list = await w.SlotService().GetAvailableAsync("dahiliye", DateOnly.FromDateTime(w.Now.AddHours(3)));

        Assert.Equal([future.Id.ToString()], list.Select(s => s.SlotId));
        Assert.NotEqual(past.Id, now.Id);
    }

    [Fact]
    public void Planlanan_slotlar_8_gun_kurum_calisma_saatlerine_gore()
    {
        var today = new DateOnly(2026, 11, 21);   // cumartesi: hafta sonu dahil
        var starts = DemoDataSeeder.PlannedSlotStarts(today, DemoDataSeeder.UpcomingSlotDays).ToList();

        // Cmt 21, Paz 22, Pzt-Cum 23-27, Cmt 28: hafta sonu 09:00-13:00 (16 slot), hafta ici
        // 09:00-12:00 + 13:00-17:00 (12 + 16 = 28 slot).
        Assert.Equal(3 * 16 + 5 * 28, starts.Count);
        var local = starts.Select(s => TimeZoneInfo.ConvertTimeFromUtc(s, IstanbulTime.Zone)).ToList();
        Assert.Equal(new DateTime(2026, 11, 21, 9, 0, 0), local.First());
        Assert.Equal(new DateTime(2026, 11, 28, 12, 45, 0), local.Last());
        Assert.DoesNotContain(local, t => t.Hour >= 17 || t.Hour < 9);

        // Her gun bos saat olur (demo hangi gun acilirsa acilsin) ve her gun 09:00'da baslar.
        var days = local.GroupBy(t => t.Date).ToList();
        Assert.Equal(8, days.Count);
        Assert.All(days, d => Assert.Equal(9, d.Min().Hour));

        // Hafta ici ogle arasi 12:00-13:00, ogleden sonra 16:45'e kadar.
        var monday = local.Where(t => t.Date == new DateTime(2026, 11, 23)).ToList();
        Assert.Equal(28, monday.Count);
        Assert.DoesNotContain(monday, t => t.Hour == 12);
        Assert.Contains(monday, t => t.Hour == 13 && t.Minute == 0);
        Assert.Equal(new TimeSpan(16, 45, 0), monday.Max().TimeOfDay);

        // Hafta sonu yarim gun.
        var sunday = local.Where(t => t.Date == new DateTime(2026, 11, 22)).ToList();
        Assert.Equal(16, sunday.Count);
        Assert.Equal(new TimeSpan(12, 45, 0), sunday.Max().TimeOfDay);
    }

    [Fact]
    public void Calisma_saatleri_yapilandirmadan_gelir_bos_ya_da_bozuk_gun_varsayilana_duser()
    {
        var hours = new CalismaSaatleriOptions
        {
            HaftaIci = ["08:30-10:00"],
            Cumartesi = [],                    // bos: varsayilana duser (demo bos gun gormez)
            Pazar = ["bozuk", "14:00-13:00"]   // gecersiz: varsayilana duser
        };

        Assert.Equal([(new TimeOnly(8, 30), new TimeOnly(10, 0))], hours.BlocksFor(DayOfWeek.Wednesday));
        Assert.Equal([(new TimeOnly(9, 0), new TimeOnly(13, 0))], hours.BlocksFor(DayOfWeek.Saturday));
        Assert.Equal([(new TimeOnly(9, 0), new TimeOnly(13, 0))], hours.BlocksFor(DayOfWeek.Sunday));

        var monday = new DateOnly(2026, 11, 23);
        var starts = DemoDataSeeder.PlannedSlotStarts(monday, 0, hours)
            .Select(s => TimeZoneInfo.ConvertTimeFromUtc(s, IstanbulTime.Zone)).ToList();
        Assert.Equal(6, starts.Count);   // 08:30-10:00, 15 dk
        Assert.Equal(new TimeSpan(8, 30, 0), starts[0].TimeOfDay);
    }

    [Fact]
    public void Kurum_varsayilanlari_MedTech()
    {
        var kurum = new KurumOptions();

        Assert.Equal("MedTech Özel Hastanesi", kurum.TamAd);
        Assert.Equal("MedTech", kurum.KisaAd);
        Assert.Equal("MedTech Cep", kurum.UygulamaAdi);
        Assert.Equal("MedTech Asistan", kurum.AsistanAdi);
        Assert.Equal("0850 000 00 00", kurum.CagriMerkezi);
    }

    [Fact]
    public void Kurum_ayarlari_appsettings_bolumunden_baglanir()
    {
        var config = new Microsoft.Extensions.Configuration.ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?>
            {
                ["Kurum:TamAd"] = "Örnek Göz Kliniği",
                ["Kurum:CagriMerkezi"] = "0850 111 11 11",
                ["Kurum:CalismaSaatleri:Cumartesi:0"] = "10:00-12:00"
            })
            .Build();

        var kurum = Microsoft.Extensions.Configuration.ConfigurationBinder.Get<KurumOptions>(
            config.GetSection(KurumOptions.SectionName))!;

        Assert.Equal("Örnek Göz Kliniği", kurum.TamAd);
        Assert.Equal("0850 111 11 11", kurum.CagriMerkezi);
        Assert.Equal("MedTech", kurum.KisaAd);   // verilmeyen alan varsayilanda kalir
        // Dizi varsayilanin sonuna eklenmez, yerine gecer.
        Assert.Equal([(new TimeOnly(10, 0), new TimeOnly(12, 0))], kurum.CalismaSaatleri.BlocksFor(DayOfWeek.Saturday));
        Assert.Equal(2, kurum.CalismaSaatleri.BlocksFor(DayOfWeek.Monday).Count);
    }

    [Fact]
    public void Demo_katalogu_ozel_hastane_poliklinikleri_ve_cok_hekimli_poliklinikler()
    {
        var clinics = DemoDataSeeder.SeedClinics();
        var codes = clinics.Select(c => c.Code).ToList();

        // Eski dort poliklinik korunur, ozel hastane branslari eklenir; kodlar benzersiz.
        Assert.Equal(["DAHILIYE", "KARDIYOLOJI", "ORTOPEDI", "GOZ"], codes.Take(4));
        foreach (var code in new[] { "KBB", "DERMATOLOJI", "KADIN_DOGUM", "COCUK_SAGLIGI", "NOROLOJI", "UROLOJI", "GENEL_CERRAHI" })
            Assert.Contains(code, codes);
        Assert.Equal(codes.Count, codes.Distinct().Count());

        var doctors = DemoDataSeeder.SeedDoctors(clinics);
        string ClinicOf(HastaKabul.Domain.Doctors.Doctor d) => clinics.Single(c => c.Id == d.ClinicId).Code;

        // Senaryo hekimleri ilk dortte ve ayni sirada (demo hastasinin randevu/gecmisi bunlara bagli).
        Assert.Equal(["DAHILIYE", "KARDIYOLOJI", "ORTOPEDI", "GOZ"], doctors.Take(4).Select(ClinicOf));
        Assert.Equal("Uzm. Dr. Mehmet D.", doctors[0].DisplayName);
        Assert.Equal("204", doctors[0].Room);
        Assert.Equal("Doç. Dr. Elif K.", doctors[1].DisplayName);

        // Her poliklinikte en az bir hekim; bazilarinda birden cok (hekim secme adimi gorunsun).
        Assert.All(clinics, c => Assert.Contains(doctors, d => d.ClinicId == c.Id));
        var multi = doctors.GroupBy(ClinicOf).Where(g => g.Count() > 1).Select(g => g.Key).ToList();
        Assert.Contains("DAHILIYE", multi);
        Assert.Contains("KADIN_DOGUM", multi);
        Assert.Contains("COCUK_SAGLIGI", multi);
        Assert.DoesNotContain("KARDIYOLOJI", multi);   // DEMO.md senaryosu (yarin 09:15, Elif K.) degismesin

        // Unvanli adlar; ayni poliklinikte ilk adlar farkli, gorunen adlar benzersiz.
        string[] titles = ["Uzm. Dr. ", "Doç. Dr. ", "Prof. Dr. ", "Op. Dr. "];
        Assert.All(doctors, d => Assert.Contains(titles, t => d.FullName.StartsWith(t, StringComparison.Ordinal)));
        Assert.Equal(doctors.Count, doctors.Select(d => d.DisplayName).Distinct().Count());
        foreach (var group in doctors.GroupBy(ClinicOf))
        {
            var firstNames = group.Select(d => d.FullName.Split(' ')[^2]).ToList();
            Assert.Equal(firstNames.Count, firstNames.Distinct().Count());
        }
    }

    [Fact]
    public void Portal_ozeti_patientRef_unknownTerms_errorCode_tasimaz()
    {
        var names = typeof(PortalSummaryView).GetProperties().Select(p => p.Name).ToList();

        Assert.DoesNotContain("PatientRef", names);
        Assert.DoesNotContain("UnknownTerms", names);
        Assert.DoesNotContain("ErrorCode", names);
        Assert.Contains("DiagnosisPlain", names);
        Assert.Contains("Medications", names);
    }

    [Fact]
    public async Task Portal_onayli_ozeti_kimliksiz_doner()
    {
        var w = new DemoWorld();
        var summary = w.PendingSummary("Migren", [], TimeSpan.Zero);
        summary.MarkReady("sade", [], ["uyarı"], ["gizli terim"], [], w.Now);
        summary.Approve(w.DrDahiliye.Id, w.Now);

        var result = await w.Portal().GetByAccessCodeAsync(new PortalAccessRequest("246802"));

        Assert.Equal("sade", result.Summary.DiagnosisPlain);
        var json = JsonSerializer.Serialize(result, new JsonSerializerOptions(JsonSerializerDefaults.Web));
        Assert.DoesNotContain("patientRef", json);
        Assert.DoesNotContain("gizli terim", json);
        Assert.DoesNotContain(w.Ayse.PatientRef.ToString(), json);
    }

    [Theory]
    [InlineData("dogru-anahtar", true)]
    [InlineData("dogru-anahtaR", false)]
    [InlineData("dogru", false)]
    [InlineData("", false)]
    [InlineData(null, false)]
    public void Servis_anahtari_sabit_zamanli_karsilastirilir(string? verilen, bool beklenen)
        => Assert.Equal(beklenen, RequireServiceKeyAttribute.KeysMatch(verilen, "dogru-anahtar"));
}

/// <summary>Madde 4: ortak hata zarfi.</summary>
public class ErrorEnvelopeTests
{
    [Theory]
    [InlineData(ErrorCodes.Internal, 500)]
    [InlineData(ErrorCodes.AppointmentConflict, 409)]
    [InlineData(ErrorCodes.SlotInPast, 409)]
    [InlineData(ErrorCodes.SlotTaken, 409)]
    [InlineData(ErrorCodes.ValidationFailed, 400)]
    [InlineData(ErrorCodes.TcNotFound, 404)]
    public void Kod_durum_eslemesi(string code, int status) => Assert.Equal(status, ErrorResponseWriter.MapStatusCode(code));

    [Fact]
    public void Model_dogrulama_hatasi_zarfta_ve_alan_adiyla()
    {
        var state = new ModelStateDictionary();
        state.AddModelError("Tc", "The Tc field is required.");
        var http = new DefaultHttpContext();
        http.Items[HttpRequestContext.HeaderName] = "rid-1";

        var result = Assert.IsType<ContentResult>(ErrorResponseWriter.InvalidModelState(
            new ActionContext(http, new RouteData(), new ActionDescriptor(), state)));

        Assert.Equal(400, result.StatusCode);
        using var doc = JsonDocument.Parse(result.Content!);
        var error = doc.RootElement.GetProperty("error");
        Assert.Equal("VALIDATION_FAILED", error.GetProperty("code").GetString());
        Assert.Equal("Eksik ya da hatalı alan: tc.", error.GetProperty("message").GetString());
        Assert.Equal("rid-1", error.GetProperty("requestId").GetString());
        Assert.Contains("hatalı", result.Content);   // Turkce karakter kacislanmaz
    }

    [Fact]
    public void Bozuk_JSON_anlasilir_mesaj()
    {
        var state = new ModelStateDictionary();
        state.AddModelError("$", "'t' is an invalid start of a property name.");
        state.AddModelError("request", "The request field is required.");

        Assert.Equal("İstek gövdesi geçerli bir JSON değil.", ErrorResponseWriter.DescribeModelState(state));
    }

    [Theory]
    [InlineData(typeof(InvalidOperationException), 500, "INTERNAL_ERROR")]
    [InlineData(typeof(DomainException), 500, "INTERNAL_ERROR")]
    public async Task Beklenmeyen_hata_500_INTERNAL_ERROR(Type kind, int status, string code)
    {
        Exception ex = kind == typeof(DomainException)
            ? new DomainException(ErrorCodes.Internal, "iç hata")
            : new InvalidOperationException("boom");
        var middleware = new ErrorHandlingMiddleware(_ => throw ex, NullLogger<ErrorHandlingMiddleware>.Instance);
        var http = new DefaultHttpContext();
        http.Response.Body = new MemoryStream();

        await middleware.InvokeAsync(http);

        Assert.Equal(status, http.Response.StatusCode);
        http.Response.Body.Position = 0;
        using var doc = await JsonDocument.ParseAsync(http.Response.Body);
        Assert.Equal(code, doc.RootElement.GetProperty("error").GetProperty("code").GetString());
    }

    [Theory]
    [InlineData(401, "UNAUTHORIZED")]
    [InlineData(404, "NOT_FOUND")]
    [InlineData(415, "VALIDATION_FAILED")]
    [InlineData(405, "VALIDATION_FAILED")]
    public void Govdesiz_durumlar_koda_eslenir(int status, string code)
        => Assert.Equal(code, ErrorResponseWriter.DescribeStatus(status).Code);

    [Theory]
    [InlineData("abc-123", "abc-123")]
    [InlineData("a b\r\nc", "abc")]
    [InlineData("   ", "")]
    public void Istek_kimligi_temizlenir(string girdi, string beklenen)
        => Assert.Equal(beklenen, RequestLoggingMiddleware.SanitizeRequestId(girdi));

    [Fact]
    public void Istek_kimligi_64_karakterle_sinirli()
        => Assert.Equal(64, RequestLoggingMiddleware.SanitizeRequestId(new string('a', 300)).Length);

    [Fact]
    public void Gecersiz_gecis_mesaji_tel_adlari_ve_Turkce()
    {
        var ex = new InvalidStatusTransitionException("CheckedIn", "Completed");

        Assert.Equal("'CHECKED_IN' durumundan 'COMPLETED' durumuna geçilemez.", ex.Message);
    }
}
