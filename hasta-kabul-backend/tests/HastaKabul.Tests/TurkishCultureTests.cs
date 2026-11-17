using System.Globalization;
using HastaKabul.Domain.Clinics;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Doctors;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Knowledge;
using HastaKabul.Domain.Patients;
using HastaKabul.Infrastructure.Ai;
using HastaKabul.Infrastructure.Persistence.Seed;

namespace HastaKabul.Tests;

/// <summary>
/// Gercek backend'e geciste bulunan kusur: tr-TR surecte "KARDIYOLOJI".ToLower() "kardıyolojı"
/// verdigi icin /slots?clinic=KARDIYOLOJI 404 donuyordu. Testler kulturu tr-TR'ye ayarlar.
/// </summary>
public sealed class TurkishCultureTests : IDisposable
{
    private readonly CultureInfo _culture = CultureInfo.CurrentCulture;
    private readonly CultureInfo _uiCulture = CultureInfo.CurrentUICulture;

    public TurkishCultureTests()
    {
        var tr = new CultureInfo("tr-TR");
        CultureInfo.CurrentCulture = tr;
        CultureInfo.CurrentUICulture = tr;
    }

    public void Dispose()
    {
        CultureInfo.CurrentCulture = _culture;
        CultureInfo.CurrentUICulture = _uiCulture;
    }

    [Fact]
    public void Kultur_gercekten_tr_TR_ve_tuzak_mevcut()
    {
        // Duzeltmenin neyi onledigini belgeler: kulture bagli ToLower noktasiz ı uretir.
        Assert.Equal("kardıyolojı", "KARDIYOLOJI".ToLower());
    }

    [Theory]
    [InlineData("KARDIYOLOJI", "kardiyoloji")]
    [InlineData("Kardiyoloji", "kardiyoloji")]
    [InlineData("İÇ HASTALIKLARI", "ic hastaliklari")]
    [InlineData("iç  hastalıkları ", "ic hastaliklari")]
    [InlineData("Göz Hastalıkları", "goz hastaliklari")]
    [InlineData("GÖZ", "goz")]
    [InlineData("DİSPEPSİ", "dispepsi")]
    [InlineData("Şeker Hastalığı", "seker hastaligi")]
    public void Fold_kulturden_bagimsiz(string girdi, string beklenen)
        => Assert.Equal(beklenen, TurkishText.Fold(girdi));

    [Theory]
    [InlineData("KARDIYOLOJI", "KARDIYOLOJI")]
    [InlineData("kardiyoloji", "KARDIYOLOJI")]
    [InlineData("Kardiyoloji", "KARDIYOLOJI")]
    [InlineData("kalp damar", "KARDIYOLOJI")]
    [InlineData("DAHILIYE", "DAHILIYE")]
    [InlineData("dahiliye", "DAHILIYE")]
    [InlineData("iç hastalıkları", "DAHILIYE")]
    [InlineData("ic hastaliklari", "DAHILIYE")]
    [InlineData("İÇ HASTALIKLARI POLİKLİNİĞİ", "DAHILIYE")]
    [InlineData("ORTOPEDI", "ORTOPEDI")]
    [InlineData("ortopedi", "ORTOPEDI")]
    [InlineData("GOZ", "GOZ")]
    [InlineData("göz", "GOZ")]
    [InlineData("goz", "GOZ")]
    [InlineData("Göz Hastalıkları", "GOZ")]
    [InlineData("goz hastaliklari", "GOZ")]
    [InlineData("KBB", "KBB")]
    [InlineData("Kulak Burun Boğaz", "KBB")]
    [InlineData("KULAK BURUN BOĞAZ", "KBB")]
    [InlineData("cildiye", "DERMATOLOJI")]
    [InlineData("DERMATOLOJİ", "DERMATOLOJI")]
    [InlineData("kadın doğum", "KADIN_DOGUM")]
    [InlineData("KADIN HASTALIKLARI VE DOĞUM", "KADIN_DOGUM")]
    [InlineData("çocuk", "COCUK_SAGLIGI")]
    [InlineData("ÇOCUK SAĞLIĞI VE HASTALIKLARI", "COCUK_SAGLIGI")]
    [InlineData("noroloji", "NOROLOJI")]
    [InlineData("NÖROLOJİ", "NOROLOJI")]
    [InlineData("üroloji", "UROLOJI")]
    [InlineData("UROLOJI", "UROLOJI")]
    [InlineData("genel cerrahi", "GENEL_CERRAHI")]
    [InlineData("GENEL_CERRAHI", "GENEL_CERRAHI")]
    public void Seed_poliklinikleri_tr_TR_kulturunde_cozulur(string girdi, string kod)
    {
        var eslesen = DemoDataSeeder.SeedClinics().Where(c => c.Matches(girdi)).ToList();

        var tek = Assert.Single(eslesen);
        Assert.Equal(kod, tek.Code);
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("psikiyatri")]
    [InlineData("kardiyolojiii")]
    public void Bilinmeyen_poliklinik_eslesmez(string girdi)
        => Assert.DoesNotContain(DemoDataSeeder.SeedClinics(), c => c.Matches(girdi));

    [Fact]
    public void Seed_gorunen_adlar_Turkce_karakterli()
    {
        var clinics = DemoDataSeeder.SeedClinics();
        Assert.Contains(clinics, c => c.Name == "Göz Hastalıkları");
        Assert.Contains(clinics, c => c.Synonyms.Contains("iç hastalıkları"));

        Assert.Equal("Ayşe H.", new Patient("h", [1], "Ayşe", "Hanımeli").DisplayName);
        Assert.Equal("İbrahim K.", new Patient("h", [1], "İbrahim", "Kurt").DisplayName);
        Assert.Equal("Zehra Ö.", new Patient("h", [1], "Zehra", "Öztürk").DisplayName);
        Assert.Equal("Mustafa Ç.", new Patient("h", [1], "Mustafa", "Çelik").DisplayName);
        Assert.Equal("Ahmet Y.", new Doctor(Guid.NewGuid(), "Ahmet Yıldırım", "305").DisplayName);
        // Unvanli ad: unvan korunur, soyadi bas harfe iner (Turkce buyuk harf).
        Assert.Equal("Op. Dr. Ahmet Y.", new Doctor(Guid.NewGuid(), "Op. Dr. Ahmet Yıldırım", "305").DisplayName);
        Assert.Equal("Uzm. Dr. Burak Ş.", new Doctor(Guid.NewGuid(), "Uzm. Dr. Burak Şen", "120").DisplayName);
        Assert.Equal("Prof. Dr. Levent S.", new Doctor(Guid.NewGuid(), "Prof. Dr. Levent Sarı", "315").DisplayName);
        Assert.Equal("Doç. Dr. Elif K.", new Doctor(Guid.NewGuid(), "Doç. Dr. Elif Kaya", "112").DisplayName);
    }

    [Theory]
    [InlineData("Demir Sülfat")]
    [InlineData("DEMİR SÜLFAT")]
    [InlineData("demir sulfat")]
    [InlineData("demir ilacı")]
    [InlineData("demir ilaci")]
    public void Kb_terim_eslesmesi_kulturden_bagimsiz(string girdi)
    {
        var kb = DemoDataSeeder.SeedKnowledgeBase();
        Assert.Contains(kb, e => e.Term == "Demir Sülfat" && e.MatchesTerm(girdi));
    }

    [Fact]
    public void Kb_kimlikleri_sabit_benzersiz_ve_yazimdan_bagimsiz()
    {
        var a = DemoDataSeeder.SeedKnowledgeBase();
        var b = DemoDataSeeder.SeedKnowledgeBase();

        Assert.Equal(16, a.Count);
        Assert.Equal(a.Select(e => e.Id), b.Select(e => e.Id));        // --reset-demo sonrasi ayni
        Assert.Equal(16, a.Select(e => e.Id).Distinct().Count());
        Assert.DoesNotContain(a, e => e.Id == Guid.Empty);
        Assert.Equal(DemoDataSeeder.KnowledgeBaseId("Demir Sülfat"), DemoDataSeeder.KnowledgeBaseId("Demir Sulfat"));
        Assert.Equal(DemoDataSeeder.KnowledgeBaseId("Demir Sülfat"), a.Single(e => e.Term == "Demir Sülfat").Id);
        // Bilinen deger: kimlik uretimi degisirse eski ozetlerin sourceIds'i kopar.
        Assert.Equal(3, DemoDataSeeder.KnowledgeBaseId("Dispepsi").Version);
    }

    [Fact]
    public void Kb_basliklari_Turkce_karakterli()
    {
        var kb = DemoDataSeeder.SeedKnowledgeBase();
        Assert.Contains(kb, e => e.Title == "Dispepsi (hazımsızlık)");
        Assert.Contains(kb, e => e.Title == "Demir eksikliği anemisi (kansızlık)");
        Assert.All(kb, e => Assert.DoesNotContain("hazimsizlik", e.Title));
    }

    [Fact]
    public void Kb_id_parametresi_bossa_yeni_kimlik_uretilir()
    {
        var e = new KbEntry("X", "X", "x", KbEntryKind.Diagnosis, id: Guid.Empty);
        Assert.NotEqual(Guid.Empty, e.Id);
    }

    [Theory]
    [InlineData("Dispepsi")]
    [InlineData("DİSPEPSİ")]
    [InlineData("dispepsi, gastrit")]
    [InlineData("Hazımsızlık")]
    [InlineData("HAZIMSIZLIK")]
    public async Task Yedek_ozet_tr_TR_kulturunde_bulunur_ve_Turkce_karakterli(string tani)
    {
        var ozet = await new SeedFallbackSummaryProvider().FindAsync(tani, ["Lansoprazol 30 mg 1x1"]);

        Assert.NotNull(ozet);
        Assert.Contains("üretiyor", ozet!.DiagnosisPlain);
        Assert.Equal("Mide asidini azaltır.", ozet.Medications.Single().Purpose);
    }

    [Theory]
    [InlineData("Lomber disk hernisi")]
    [InlineData("")]
    public async Task Yedek_ozet_bilinmeyen_tanida_yok(string tani)
        => Assert.Null(await new SeedFallbackSummaryProvider().FindAsync(tani, []));
}
