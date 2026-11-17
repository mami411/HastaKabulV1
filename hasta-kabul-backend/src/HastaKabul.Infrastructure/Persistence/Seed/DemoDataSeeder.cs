using HastaKabul.Application.Abstractions.Security;
using HastaKabul.Application.Common;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Clinics;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Doctors;
using HastaKabul.Domain.Encounters;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Knowledge;
using HastaKabul.Domain.Patients;
using HastaKabul.Domain.Summaries;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace HastaKabul.Infrastructure.Persistence.Seed;

/// <summary>
/// Demo verisi: 12 hasta, kurum katalogundaki poliklinik ve hekimler (<see cref="DemoKatalog"/>; MedTech:
/// 11 poliklinik, 14 hekim), bugun+7 gun slot (kurumun calisma saatlerine gore), bugune gore uretilen randevular
/// ve 16 kayitlik bilgi tabani. Randevular hep "bugune gore" uretilir, boylece demo gunu
/// kiosk "bugun randevunuz yok" demez. Tek komutla sifirlanir.
/// </summary>
public sealed class DemoDataSeeder(
    HastaKabulDbContext db,
    ITcHasher tcHasher,
    ITcProtector tcProtector,
    IAccessCodeGenerator accessCodes,
    ILogger<DemoDataSeeder> logger,
    IOptions<KurumOptions>? kurum = null)
{
    private CalismaSaatleriOptions CalismaSaatleri => kurum?.Value.CalismaSaatleri ?? new CalismaSaatleriOptions();

    /// <summary>Senaryodaki hasta: Ayşe H., Dahiliye, Oda 204.</summary>
    public const string DemoPatientTc = "10000000146";

    /// <summary>Demo hastasinin bugunku Dahiliye randevusunun portal erisim kodu.</summary>
    public const string DemoAccessCode = "246802";

    /// <summary>Bugun dahil kac gun ileriye slot garanti edilir (hafta sonu dahil).</summary>
    public const int UpcomingSlotDays = 7;

    // Slot bloklari kurumun calisma saatlerinden gelir (appsettings "Kurum:CalismaSaatleri"; varsayilan
    // hafta ici 09:00-12:00 + 13:00-17:00, cumartesi ve pazar 09:00-13:00). Bos gun yoktur.

    public async Task SeedAsync(bool reset, CancellationToken ct = default)
    {
        if (reset)
        {
            logger.LogInformation("Demo verisi sifirlaniyor.");
            await ClearAsync(ct);
        }
        else if (await db.Patients.AnyAsync(ct))
        {
            logger.LogInformation("Demo verisi zaten yuklu; yalniz onumuzdeki gunlerin slotlari tamamlaniyor.");
            await EnsureUpcomingSlotsAsync(UpcomingSlotDays, ct);
            return;
        }

        var clinics = SeedClinics();
        await db.Clinics.AddRangeAsync(clinics, ct);

        var doctors = SeedDoctors(clinics);
        await db.Doctors.AddRangeAsync(doctors, ct);

        var patients = SeedPatients();
        await db.Patients.AddRangeAsync(patients, ct);

        var knowledgeBase = SeedKnowledgeBase();
        await db.KbEntries.AddRangeAsync(knowledgeBase, ct);
        await db.SaveChangesAsync(ct);

        var slots = await EnsureUpcomingSlotsAsync(UpcomingSlotDays, ct);

        await db.Appointments.AddRangeAsync(SeedAppointments(patients, doctors, slots), ct);
        await db.SaveChangesAsync(ct);

        var history = await SeedVisitHistoryAsync(patients[0], doctors, knowledgeBase, ct);

        logger.LogInformation(
            "Demo verisi yuklendi: {Patients} hasta, {Doctors} hekim, {Slots} slot, {History} gecmis muayene.",
            patients.Count, doctors.Count, slots.Count, history);
    }

    /// <summary>
    /// Demo verisi bayat mi: hasta kayitlari var ama demo hastasinin sabit erisim kodlu randevusu
    /// bugune (Europe/Istanbul) ait degil. Ornegin dun seed edilmis veritabaniyla bugun acilista
    /// kiosk "bugün randevunuz yok" derdi; bu durumda acilista otomatik sifirlanir (Demo:ResetIfStale).
    /// </summary>
    public async Task<bool> IsStaleAsync(DateTime nowUtc, CancellationToken ct = default)
    {
        if (!await db.Patients.AnyAsync(ct))
            return false;

        var (dayStart, dayEnd) = IstanbulTime.DayRangeUtc(nowUtc);
        return !await db.Appointments.AnyAsync(
            a => a.AccessCode == DemoAccessCode && a.ScheduledAtUtc >= dayStart && a.ScheduledAtUtc < dayEnd, ct);
    }

    /// <summary>
    /// Bugunden itibaren <paramref name="days"/> gun ileriye (bugun dahil, hafta sonu dahil) her hekim
    /// icin sabah ve ogleden sonra slotlarinin var oldugunu garanti eder. Idempotent: yalniz eksik
    /// (hekim, baslangic) ciftleri eklenir; acilista ve saatlik arka plan isinde cagrilir.
    /// </summary>
    /// <returns>Bugunun ve ileriki gunlerin tum slotlari (yeni eklenenler dahil).</returns>
    public async Task<List<AppointmentSlot>> EnsureUpcomingSlotsAsync(int days, CancellationToken ct = default)
    {
        var doctors = await db.Doctors.OrderBy(d => d.CreatedAtUtc).ToListAsync(ct);
        if (doctors.Count == 0)
            return [];

        var today = DateOnly.FromDateTime(TimeZoneInfo.ConvertTimeFromUtc(DateTime.UtcNow, IstanbulTime.Zone));
        var (rangeStart, _) = IstanbulTime.DayRangeUtc(today);
        var (_, rangeEnd) = IstanbulTime.DayRangeUtc(today.AddDays(days));

        var existing = await db.AppointmentSlots
            .Where(s => s.StartTimeUtc >= rangeStart && s.StartTimeUtc < rangeEnd)
            .ToListAsync(ct);
        var keys = existing.Select(s => (s.DoctorId, s.StartTimeUtc)).ToHashSet();

        var added = new List<AppointmentSlot>();
        foreach (var start in PlannedSlotStarts(today, days, CalismaSaatleri))
        {
            foreach (var doctor in doctors)
            {
                if (keys.Contains((doctor.Id, start)))
                    continue;

                var slot = new AppointmentSlot(doctor.ClinicId, doctor.Id, start);
                added.Add(slot);
                keys.Add((doctor.Id, start));
            }
        }

        if (added.Count > 0)
        {
            await db.AppointmentSlots.AddRangeAsync(added, ct);
            await db.SaveChangesAsync(ct);
            logger.LogInformation("Demo slotlari tamamlandi: {Added} yeni slot ({Days} gun).", added.Count, days + 1);
        }

        return existing.Concat(added).ToList();
    }

    /// <summary>
    /// Planlanan slot baslangiclari (UTC): bugun..bugun+days, her gun o gunun calisma bloklari
    /// (<paramref name="hours"/> verilmezse kurum varsayilanlari).
    /// </summary>
    public static IEnumerable<DateTime> PlannedSlotStarts(DateOnly today, int days, CalismaSaatleriOptions? hours = null)
    {
        hours ??= new CalismaSaatleriOptions();
        for (var offset = 0; offset <= days; offset++)
        {
            var day = today.AddDays(offset);
            var (dayStartUtc, _) = IstanbulTime.DayRangeUtc(day);
            foreach (var (from, to) in hours.BlocksFor(day.DayOfWeek))
            {
                for (var t = from.ToTimeSpan(); t < to.ToTimeSpan(); t += TimeSpan.FromMinutes(AppointmentSlot.DefaultDurationMinutes))
                    yield return dayStartUtc.Add(t);
            }
        }
    }

    private async Task ClearAsync(CancellationToken ct)
    {
        await db.Database.ExecuteSqlRawAsync(
            """
            TRUNCATE TABLE audit_logs, simplified_summaries, clinical_entries, appointments,
                           appointment_slots, voice_sessions, kb_entries, doctors, patients, clinics
            RESTART IDENTITY CASCADE;
            """,
            ct);
    }

    /// <summary>Kurum katalogundaki poliklinikler (<see cref="DemoKatalog.Poliklinikler"/>).</summary>
    public static List<Clinic> SeedClinics() => DemoKatalog.Clinics();

    /// <summary>
    /// Kurum katalogundaki hekimler, katalog sirasiyla (ilk dort hekim demo senaryosunundur: Dahiliye,
    /// Kardiyoloji, Ortopedi, Goz). Bilinmeyen poliklinik kodlu hekim yapilandirma hatasidir.
    /// </summary>
    public static List<Doctor> SeedDoctors(List<Clinic> clinics) =>
        DemoKatalog.Hekimler
            .Select(h => new Doctor(
                (clinics.FirstOrDefault(c => c.Code == h.PoliklinikKodu)
                    ?? throw new InvalidOperationException(
                        $"Demo katalogunda '{h.UnvanliAd}' icin poliklinik bulunamadi: {h.PoliklinikKodu}.")).Id,
                h.UnvanliAd,
                h.Oda))
            .ToList();

    private List<Patient> SeedPatients()
    {
        var people = new (string First, string Last)[]
        {
            ("Ayşe", "Hanımeli"),
            ("Fatma", "Yılmaz"),
            ("Mustafa", "Çelik"),
            ("Hatice", "Şahin"),
            ("Ali", "Doğan"),
            ("Emine", "Koç"),
            ("Hasan", "Aydın"),
            ("Zehra", "Öztürk"),
            ("İbrahim", "Kurt"),
            ("Meryem", "Polat"),
            ("Osman", "Erdoğan"),
            ("Sultan", "Bulut")
        };

        var patients = new List<Patient>(people.Length);
        for (var i = 0; i < people.Length; i++)
        {
            // Ilk hasta senaryodaki sabit demo hastasidir; digerleri uretilir.
            // Adim, 9 haneli tabanin ust basamaklarini da degistirecek kadar buyuk
            // secilir; kucuk bir adim kirpilip ayni numarayi uretirdi.
            var tc = i == 0 ? DemoPatientTc : SyntheticTc.Create(100_000_000L + (i * 7_654_321L));
            patients.Add(new Patient(
                tcHasher.Hash(tc),
                tcProtector.Protect(tc),
                people[i].First,
                people[i].Last,
                $"0{5}** *** {10 + i:00}{i:0}"));
        }

        return patients;
    }

    /// <summary>Bugune ait randevular; ilk hasta Dahiliye'de ilk slotta.</summary>
    private List<Appointment> SeedAppointments(List<Patient> patients, List<Doctor> doctors, List<AppointmentSlot> slots)
    {
        var (todayStartUtc, todayEndUtc) = IstanbulTime.DayRangeUtc(DateTime.UtcNow);

        // Hekim sirasi seed listesindeki siradir (Dahiliye ilk). Rastgele Guid'e gore siralamak
        // demo hastasini her seferinde baska poliklinige dusuruyordu.
        var doctorOrder = doctors.Select(d => d.Id).ToList();
        var todaysSlots = slots
            .Where(s => s.StartTimeUtc >= todayStartUtc && s.StartTimeUtc < todayEndUtc)
            .OrderBy(s => doctorOrder.IndexOf(s.DoctorId))
            .ThenBy(s => s.StartTimeUtc)
            .ToList();

        var appointments = new List<Appointment>();
        var taken = 0;

        // Her hekime ikiser randevu dagit.
        foreach (var group in todaysSlots.GroupBy(s => s.DoctorId))
        {
            foreach (var slot in group.Take(2))
            {
                if (taken >= patients.Count)
                    break;

                slot.Reserve();
                appointments.Add(new Appointment(
                    patients[taken].Id,
                    slot.DoctorId,
                    slot.ClinicId,
                    slot.Id,
                    slot.StartTimeUtc,
                    taken == 0 ? DemoAccessCode : accessCodes.Generate()));

                taken++;
            }
        }

        return appointments;
    }

    /// <summary>
    /// Demo hastasinin gecmis muayeneleri (GET /voice-sessions/{id}/medical-history icin):
    /// iki tamamlanmis randevu + klinik giris + hekim onayli (APPROVED) ozet ve onaylanmamis
    /// (READY) ozetli bir randevu. READY olan uc noktada gorunmemelidir. Gecmis gunlerdeki
    /// slotlar bugun/yarin listesine girmez; kiosk ve portal senaryosu (246802) degismez.
    /// </summary>
    private async Task<int> SeedVisitHistoryAsync(
        Patient patient, List<Doctor> doctors, List<KbEntry> knowledgeBase, CancellationToken ct)
    {
        var today = DateOnly.FromDateTime(TimeZoneInfo.ConvertTimeFromUtc(DateTime.UtcNow, IstanbulTime.Zone));
        string[] Sources(params string[] terms) => knowledgeBase
            .Where(e => terms.Any(term => TurkishText.FoldEquals(term, e.Term)))
            .Select(e => e.Id.ToString())
            .ToArray();

        var visits = new[]
        {
            new HistoricVisit(
                Doctor: doctors[0],                       // Dahiliye
                Day: today.AddDays(-118),
                LocalTime: new TimeOnly(10, 15),
                Diagnosis: "Tip 2 Diyabet; Demir Eksikliği Anemisi",
                Prescription: ["Metformin 1000 mg tablet 2x1 tok", "Demir Sülfat 80 mg tablet 1x1 aç"],
                DiagnosisPlain:
                    "Kan şekeriniz olması gerekenden yüksek seyrediyor (tip 2 diyabet). Ayrıca kanınızdaki " +
                    "demir deposu azalmış; halsizliğiniz bundan kaynaklanıyor olabilir. Düzenli ilaç kullanımı " +
                    "ve beslenme düzeniyle ikisi de kontrol altına alınabilir.",
                Medications:
                [
                    Medication("Metformin", "Kan şekerinizi dengeler.",
                        "Günde iki kez, sabah ve akşam yemekle birlikte birer tablet alın."),
                    Medication("Demir sülfat", "Azalan demir deponuzu doldurur.",
                        "Günde bir kez, sabah aç karnına, portakal suyu gibi C vitamini içeren bir içecekle alın; süt ve çayla birlikte almayın.")
                ],
                Warnings:
                [
                    "Evde ölçtüğünüz kan şekeri değerlerini not edip kontrole getirin.",
                    "Demir ilacı dışkınızın rengini koyulaştırabilir; bu beklenen bir durumdur.",
                    "İlaçlarınızı kendi kararınızla bırakmayın."
                ],
                Sources: Sources("Tip 2 Diyabet", "Metformin", "Demir Eksikliği Anemisi", "Demir Sülfat"),
                UnknownTerms: [],
                Approve: true),
            new HistoricVisit(
                Doctor: doctors[1],                       // Kardiyoloji
                Day: today.AddDays(-46),
                LocalTime: new TimeOnly(9, 30),
                Diagnosis: "Hipertansiyon",
                Prescription: ["Amlodipin 5 mg tablet 1x1"],
                DiagnosisPlain:
                    "Tansiyonunuz sürekli olarak yüksek seyrediyor (yüksek tansiyon). Tuzu azaltmak ve " +
                    "ilacınızı her gün düzenli kullanmak kalbinizi ve damarlarınızı korur.",
                Medications:
                [
                    Medication("Amlodipin", "Damarlarınızı gevşeterek tansiyonunuzu düşürür.",
                        "Günde bir kez, her gün aynı saatte bir tablet alın.")
                ],
                Warnings:
                [
                    "Ayak bileklerinizde şişlik olursa hekiminize haber verin.",
                    "Evde ölçtüğünüz tansiyon değerlerini kaydedin.",
                    "Günlük tuz tüketiminizi azaltın."
                ],
                Sources: Sources("Hipertansiyon", "Amlodipin"),
                UnknownTerms: [],
                Approve: true),
            new HistoricVisit(
                Doctor: doctors[2],                       // Ortopedi - hekim henuz onaylamadi
                Day: today.AddDays(-9),
                LocalTime: new TimeOnly(11, 0),
                Diagnosis: "Lomber disk hernisi",
                Prescription: ["Parasetamol 500 mg tablet 3x1 tok"],
                DiagnosisPlain:
                    "Bel omurlarınızın arasındaki yastıkçıklardan biri yerinden taşmış ve sinire baskı yapıyor " +
                    "olabilir. Ağrınız dinlenme ve ağrı kesiciyle genellikle azalır.",
                Medications:
                [
                    Medication("Parasetamol", "Ağrınızı azaltır.",
                        "Günde üç kez, tok karnına birer tablet alın; günlük dozu aşmayın.")
                ],
                Warnings: ["Bacaklarınızda uyuşma ya da güç kaybı olursa hemen başvurun."],
                Sources: Sources("Parasetamol"),
                UnknownTerms: ["Lomber disk hernisi"],
                Approve: false)
        };

        var count = 0;
        foreach (var visit in visits)
        {
            var (dayStartUtc, _) = IstanbulTime.DayRangeUtc(visit.Day);
            var startUtc = dayStartUtc.Add(visit.LocalTime.ToTimeSpan());

            var slot = new AppointmentSlot(visit.Doctor.ClinicId, visit.Doctor.Id, startUtc);
            slot.Reserve();
            await db.AppointmentSlots.AddAsync(slot, ct);

            var appointment = new Appointment(
                patient.Id, visit.Doctor.Id, visit.Doctor.ClinicId, slot.Id, startUtc, accessCodes.Generate());
            appointment.CheckIn(startUtc.AddMinutes(-10));
            appointment.StartExam(startUtc.AddMinutes(2));
            appointment.Complete(startUtc.AddMinutes(20));
            await db.Appointments.AddAsync(appointment, ct);

            var entry = new ClinicalEntry(appointment.Id, visit.Doctor.Id, visit.Diagnosis, visit.Prescription);
            await db.ClinicalEntries.AddAsync(entry, ct);

            count++;
            var summary = new SimplifiedSummary(appointment.Id, entry.Id, patient.PatientRef, $"seed-history-{count}");
            summary.MarkReady(
                visit.DiagnosisPlain, visit.Medications, visit.Warnings, visit.UnknownTerms, visit.Sources,
                startUtc.AddMinutes(16));
            if (visit.Approve)
                summary.Approve(visit.Doctor.Id, startUtc.AddMinutes(19));
            await db.Summaries.AddAsync(summary, ct);
        }

        await db.SaveChangesAsync(ct);
        return count;
    }

    private static SimplifiedMedication Medication(string name, string purpose, string usage)
        => new() { Name = name, Purpose = purpose, Usage = usage };

    private sealed record HistoricVisit(
        Doctor Doctor,
        DateOnly Day,
        TimeOnly LocalTime,
        string Diagnosis,
        string[] Prescription,
        string DiagnosisPlain,
        SimplifiedMedication[] Medications,
        string[] Warnings,
        string[] Sources,
        string[] UnknownTerms,
        bool Approve);

    /// <summary>
    /// 16 kayitlik bilgi tabani. Sadelestirme yalnizca bu kayitlara dayanir;
    /// listede olmayan terim unknownTerms icinde doner, uydurulmaz. Metinler dogru Turkce
    /// karakterlerle yazilir (basliklar portalda kaynak olarak gorunur). Kimlikler sabittir
    /// (<see cref="KnowledgeBaseId"/>): --reset-demo sonrasi da degismez, boylece ai-simplify'in
    /// onbellegindeki kimlikler ve onceki ozetlerin sourceIds'i gecerli kalir.
    /// </summary>
    public static List<KbEntry> SeedKnowledgeBase() =>
    [
        Kb("Dispepsi", "Dispepsi (hazımsızlık)", "Midede yanma, şişme ve erken doyma ile giden hazımsızlık tablosu. Genellikle mide asidinin fazla üretilmesine bağlıdır.", KbEntryKind.Diagnosis, ["hazımsızlık", "mide şikayeti"]),
        Kb("Lansoprazol", "Lansoprazol", "Mide asidi üretimini azaltan ilaç. Günde bir kez, kahvaltıdan yarım saat önce aç karnına alınır.", KbEntryKind.Medication, ["lansor", "proton pompa inhibitörü"]),
        Kb("Gastrit", "Gastrit (mide iltihabı)", "Mide iç yüzeyinin tahriş olması. Acılı veya yanma hissi veren şikayetlere yol açar.", KbEntryKind.Diagnosis, ["mide iltihabı"]),
        Kb("Famotidin", "Famotidin", "Mide asidini azaltan ilaç. Genellikle akşam yemeğinden sonra alınır.", KbEntryKind.Medication),
        Kb("Hipertansiyon", "Hipertansiyon (yüksek tansiyon)", "Kan basıncının sürekli yüksek seyretmesi. Tuz kısıtlaması ve düzenli ilaç kullanımı gerektirir.", KbEntryKind.Diagnosis, ["yüksek tansiyon", "tansiyon"]),
        Kb("Amlodipin", "Amlodipin", "Damarları gevşeterek tansiyonu düşüren ilaç. Günde bir kez, her gün aynı saatte alınır.", KbEntryKind.Medication),
        Kb("Tip 2 Diyabet", "Tip 2 diyabet (şeker hastalığı)", "Vücudun insülini yeterince kullanamaması. Beslenme düzeni ve ilaç ile kontrol altına alınır.", KbEntryKind.Diagnosis, ["şeker hastalığı", "diyabet"]),
        Kb("Metformin", "Metformin", "Kan şekerini dengeleyen ilaç. Mide şikayetini azaltmak için yemekle birlikte alınır.", KbEntryKind.Medication),
        Kb("Demir Eksikliği Anemisi", "Demir eksikliği anemisi (kansızlık)", "Kandaki demir deposunun azalması. Halsizlik ve solukluk yapar.", KbEntryKind.Diagnosis, ["kansızlık", "anemi"]),
        Kb("Demir Sülfat", "Demir sülfat", "Demir deposunu dolduran ilaç. C vitamini içeren meyve suyuyla, süt ve çay olmadan alınır.", KbEntryKind.Medication, ["demir ilacı"]),
        Kb("Migren", "Migren", "Zonklayıcı, genellikle tek taraflı baş ağrısı. Işık ve sese hassasiyet eşlik edebilir.", KbEntryKind.Diagnosis),
        Kb("Parasetamol", "Parasetamol", "Ağrı ve ateş düşürücü ilaç. Günde en fazla belirtilen dozda ve tok karnına alınır.", KbEntryKind.Medication, ["ağrı kesici"]),
        Kb("Akut Sinüzit", "Akut sinüzit", "Burun çevresindeki sinüslerin iltihaplanması. Yüzde basınç hissi ve burun tıkanıklığı yapar.", KbEntryKind.Diagnosis, ["sinüzit"]),
        Kb("Amoksisilin", "Amoksisilin", "Bakteri kaynaklı enfeksiyonlarda kullanılan antibiyotik. Kur yarıda bırakılmaz.", KbEntryKind.Medication, ["antibiyotik"]),
        Kb("Hipotiroidi", "Hipotiroidi (tiroid bezinin az çalışması)", "Tiroid hormonunun yetersiz üretilmesi. Halsizlik, üşüme ve kilo artışı yapabilir.", KbEntryKind.Diagnosis, ["tiroid"]),
        Kb("Levotiroksin", "Levotiroksin", "Eksik tiroid hormonunu yerine koyan ilaç. Sabah aç karnına, bir bardak su ile alınır.", KbEntryKind.Medication)
    ];

    /// <summary>
    /// Terimden turetilen sabit kimlik (MD5 tabanli ad-UUID'si, surum 3). Terim Turkce karakterli
    /// ya da karaktersiz yazilsa da ayni kimlik cikar (katlanmis bicimden uretilir).
    /// </summary>
    public static Guid KnowledgeBaseId(string term)
    {
        var bytes = System.Security.Cryptography.MD5.HashData(
            System.Text.Encoding.UTF8.GetBytes("hastakabul:kb:" + TurkishText.Fold(term)));
        bytes[6] = (byte)((bytes[6] & 0x0F) | 0x30);   // surum 3
        bytes[8] = (byte)((bytes[8] & 0x3F) | 0x80);   // RFC 4122 varyanti
        return new Guid(bytes, bigEndian: true);
    }

    private static KbEntry Kb(string term, string title, string body, KbEntryKind kind, string[]? synonyms = null)
        => new(term, title, body, kind, synonyms, KnowledgeBaseId(term));
}
/// <summary>
/// Sentetik ama bicim denetiminden gecen TC uretir. Gercek kisilerle eslesmemesi icin
/// yalnizca demo araliginda kullanilir.
/// </summary>
internal static class SyntheticTc
{
    public static string Create(long baseNumber)
    {
        var text = baseNumber.ToString("D9");
        if (text.Length != 9)
            throw new ArgumentOutOfRangeException(
                nameof(baseNumber), baseNumber, "Taban numara tam 9 haneli olmalidir.");

        var digits = text.Select(c => c - '0').ToArray();
        if (digits[0] == 0) digits[0] = 1;

        var oddSum = digits[0] + digits[2] + digits[4] + digits[6] + digits[8];
        var evenSum = digits[1] + digits[3] + digits[5] + digits[7];

        var tenth = ((oddSum * 7) - evenSum) % 10;
        if (tenth < 0) tenth += 10;

        var eleventh = (digits.Sum() + tenth) % 10;

        return string.Concat(digits.Select(d => d.ToString())) + tenth + eleventh;
    }
}
