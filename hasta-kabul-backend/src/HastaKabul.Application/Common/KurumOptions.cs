using System.Globalization;

namespace HastaKabul.Application.Common;

/// <summary>
/// Kurum (ozel hastane / klinik) yapilandirmasi: <c>appsettings.json</c> icindeki <c>"Kurum"</c> bolumu.
/// Varsayilan MedTech Ozel Hastanesi'dir (kurgusal demo kurumu). Baska bir klinik icin bu bolum ve demo
/// katalogu (<c>Infrastructure/Persistence/Seed/DemoKatalog.cs</c>) degistirilir; ayrinti kokteki KURUM.md.
/// Ortam degiskeniyle de verilebilir: <c>Kurum__TamAd</c>, <c>Kurum__CagriMerkezi</c>,
/// <c>Kurum__CalismaSaatleri__Cumartesi__0</c> gibi.
/// </summary>
public sealed class KurumOptions
{
    public const string SectionName = "Kurum";

    public const string VarsayilanTamAd = "MedTech Özel Hastanesi";
    public const string VarsayilanKisaAd = "MedTech";
    public const string VarsayilanUygulamaAdi = "MedTech Cep";
    public const string VarsayilanAsistanAdi = "MedTech Asistan";
    public const string VarsayilanCagriMerkezi = "0850 000 00 00";

    public string TamAd { get; set; } = VarsayilanTamAd;
    public string KisaAd { get; set; } = VarsayilanKisaAd;
    public string UygulamaAdi { get; set; } = VarsayilanUygulamaAdi;
    public string AsistanAdi { get; set; } = VarsayilanAsistanAdi;

    /// <summary>Demo numarasidir, gercek degildir.</summary>
    public string CagriMerkezi { get; set; } = VarsayilanCagriMerkezi;

    /// <summary>Poliklinik calisma saatleri; demo slotlari bu bloklara gore uretilir.</summary>
    public CalismaSaatleriOptions CalismaSaatleri { get; set; } = new();
}

/// <summary>
/// Gun turune gore poliklinik calisma bloklari ("09:00-12:00" bicimi, Europe/Istanbul). Bos ya da gecersiz
/// liste o gun icin varsayilana duser: demo hangi gun acilirsa acilsin her gun bos saat olmalidir.
/// </summary>
/// <remarks>
/// Diziler bilerek null baslar: ConfigurationBinder dizi ozelliginde varsayilan elemanlarin uzerine yazmaz,
/// sonuna ekler. Varsayilanlar <see cref="BlocksFor"/> icinde uygulanir.
/// </remarks>
public sealed class CalismaSaatleriOptions
{
    public static readonly string[] VarsayilanHaftaIci = ["09:00-12:00", "13:00-17:00"];
    public static readonly string[] VarsayilanCumartesi = ["09:00-13:00"];
    public static readonly string[] VarsayilanPazar = ["09:00-13:00"];

    /// <summary>Pazartesi-cuma. Varsayilan 09:00-12:00 ve 13:00-17:00.</summary>
    public string[]? HaftaIci { get; set; }

    /// <summary>Varsayilan 09:00-13:00 (yarim gun).</summary>
    public string[]? Cumartesi { get; set; }

    /// <summary>Varsayilan 09:00-13:00 (yarim gun; demo pazar gunu de acilabilsin diye bos gun yok).</summary>
    public string[]? Pazar { get; set; }

    /// <summary>Verilen gunun gecerli bloklari (sirali, cakismasiz denetimi yapilmaz; bos olmaz).</summary>
    public IReadOnlyList<(TimeOnly From, TimeOnly To)> BlocksFor(DayOfWeek day)
    {
        var (configured, fallback) = day switch
        {
            DayOfWeek.Saturday => (Cumartesi, VarsayilanCumartesi),
            DayOfWeek.Sunday => (Pazar, VarsayilanPazar),
            _ => (HaftaIci, VarsayilanHaftaIci)
        };

        var parsed = Parse(configured);
        return parsed.Count > 0 ? parsed : Parse(fallback);
    }

    /// <summary>"09:00-12:00" listesini cozer; bozuk ya da bitisi baslangictan once olan girdi atlanir.</summary>
    public static List<(TimeOnly From, TimeOnly To)> Parse(IEnumerable<string>? blocks)
    {
        var result = new List<(TimeOnly From, TimeOnly To)>();
        foreach (var block in blocks ?? [])
        {
            var parts = (block ?? string.Empty).Split('-', StringSplitOptions.TrimEntries);
            if (parts.Length == 2
                && TimeOnly.TryParseExact(parts[0], "HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out var from)
                && TimeOnly.TryParseExact(parts[1], "HH:mm", CultureInfo.InvariantCulture, DateTimeStyles.None, out var to)
                && from < to)
            {
                result.Add((from, to));
            }
        }

        return result.OrderBy(b => b.From).ToList();
    }
}
