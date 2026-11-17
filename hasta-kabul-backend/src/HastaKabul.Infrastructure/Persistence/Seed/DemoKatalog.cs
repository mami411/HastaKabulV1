using HastaKabul.Domain.Clinics;

namespace HastaKabul.Infrastructure.Persistence.Seed;

/// <summary>
/// Kurumun demo katalogu: poliklinikler ve hekimler (MedTech Ozel Hastanesi; adlar ve odalar kurgusaldir).
/// Baska bir klinik icin yalniz bu dosya ve appsettings "Kurum" bolumu degistirilir (bkz. KURUM.md).
///
/// Kurallar:
/// - Poliklinik kodu ses asistaninin listesiyle (hasta-kabul-ai/ses_servisi/alanlar/hasta_kabul.py
///   POLIKLINIKLER) AYNI olmalidir; asistan /slots?clinic= parametresine kodu gonderir.
/// - Hekim sirasi onemlidir: ilk dort hekim demo senaryosunun hekimleridir (demo hastasinin bugunku
///   Dahiliye randevusu ve gecmis muayeneleri bunlara baglidir). Yeni hekimler sona eklenir.
/// - Hekim adi unvanla yazilir ("Uzm. Dr. Ad Soyad"); arayuze ve asistana "Uzm. Dr. Ad S." olarak gider.
///   Ayni poliklinikteki hekimlerin ilk adlari farkli olmalidir (asistan hekimi ilk adindan tanir).
/// </summary>
public static class DemoKatalog
{
    public sealed record PoliklinikTanimi(string Kod, string Ad, string[] EsAnlamlilar);

    public sealed record HekimTanimi(string PoliklinikKodu, string UnvanliAd, string Oda);

    public static readonly IReadOnlyList<PoliklinikTanimi> Poliklinikler =
    [
        new("DAHILIYE", "Dahiliye", ["iç hastalıkları", "dahiliye polikliniği", "iç hastalıkları polikliniği"]),
        new("KARDIYOLOJI", "Kardiyoloji", ["kalp", "kalp damar", "kardiyoloji polikliniği"]),
        new("ORTOPEDI", "Ortopedi", ["kemik", "ortopedi ve travmatoloji"]),
        new("GOZ", "Göz Hastalıkları", ["göz", "göz polikliniği"]),
        new("KBB", "Kulak Burun Boğaz", ["kbb", "kulak burun boğaz hastalıkları", "kbb polikliniği"]),
        new("DERMATOLOJI", "Dermatoloji", ["cildiye", "cilt hastalıkları", "deri ve zührevi hastalıklar"]),
        new("KADIN_DOGUM", "Kadın Hastalıkları ve Doğum", ["kadın doğum", "jinekoloji", "kadın hastalıkları"]),
        new("COCUK_SAGLIGI", "Çocuk Sağlığı ve Hastalıkları", ["çocuk", "pediatri", "çocuk hastalıkları"]),
        new("NOROLOJI", "Nöroloji", ["nöroloji polikliniği", "sinir hastalıkları"]),
        new("UROLOJI", "Üroloji", ["üroloji polikliniği", "idrar yolları"]),
        new("GENEL_CERRAHI", "Genel Cerrahi", ["cerrahi", "genel cerrahi polikliniği"])
    ];

    public static readonly IReadOnlyList<HekimTanimi> Hekimler =
    [
        // Demo senaryosunun hekimleri (sira degismez).
        new("DAHILIYE", "Uzm. Dr. Mehmet Demir", "204"),
        new("KARDIYOLOJI", "Doç. Dr. Elif Kaya", "112"),
        new("ORTOPEDI", "Op. Dr. Ahmet Yıldırım", "305"),
        new("GOZ", "Uzm. Dr. Zeynep Arslan", "118"),
        // Ozel hastane genislemesi.
        new("KBB", "Op. Dr. Tolga Aksoy", "210"),
        new("DERMATOLOJI", "Uzm. Dr. Deniz Kara", "215"),
        new("KADIN_DOGUM", "Prof. Dr. Nermin Tunç", "301"),
        new("KADIN_DOGUM", "Op. Dr. Selin Ergin", "302"),
        new("COCUK_SAGLIGI", "Uzm. Dr. Burak Şen", "120"),
        new("COCUK_SAGLIGI", "Doç. Dr. Ceren Uysal", "121"),
        new("NOROLOJI", "Doç. Dr. Kerem Bilgin", "220"),
        new("UROLOJI", "Op. Dr. Serkan Tok", "310"),
        new("GENEL_CERRAHI", "Prof. Dr. Levent Sarı", "315"),
        new("DAHILIYE", "Uzm. Dr. Sevgi Akın", "206")
    ];

    public static List<Clinic> Clinics() =>
        Poliklinikler.Select(p => new Clinic(p.Kod, p.Ad, p.EsAnlamlilar)).ToList();
}
