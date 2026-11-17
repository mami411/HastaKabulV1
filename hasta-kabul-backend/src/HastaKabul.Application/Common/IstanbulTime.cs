namespace HastaKabul.Application.Common;

/// <summary>
/// Karar tablosu: API'de ISO 8601 UTC, ekranda Europe/Istanbul. Gun sinirlari
/// (bugunun randevulari, ?date=YYYY-MM-DD) Istanbul gunune gore hesaplanir.
/// </summary>
public static class IstanbulTime
{
    public static readonly TimeZoneInfo Zone = ResolveZone();

    private static TimeZoneInfo ResolveZone()
    {
        foreach (var id in new[] { "Europe/Istanbul", "Turkey Standard Time" })
        {
            try
            {
                return TimeZoneInfo.FindSystemTimeZoneById(id);
            }
            catch (TimeZoneNotFoundException)
            {
                // Sonraki kimligi dene (Linux konteyner / Windows farki).
            }
            catch (InvalidTimeZoneException)
            {
                // Ayni sekilde yoksay.
            }
        }

        // Son care: Turkiye 2016'dan beri sabit UTC+03.
        return TimeZoneInfo.CreateCustomTimeZone("TR+03", TimeSpan.FromHours(3), "Turkey", "Turkey");
    }

    /// <summary>Verilen UTC anin Istanbul takvimindeki gununu [baslangic, bitis) UTC araligi olarak verir.</summary>
    public static (DateTime StartUtc, DateTime EndUtc) DayRangeUtc(DateTime instantUtc)
        => DayRangeUtc(DateOnly.FromDateTime(TimeZoneInfo.ConvertTimeFromUtc(instantUtc, Zone)));

    /// <summary>Istanbul takvim gununu [baslangic, bitis) UTC araligina cevirir.</summary>
    public static (DateTime StartUtc, DateTime EndUtc) DayRangeUtc(DateOnly localDay)
    {
        var localStart = localDay.ToDateTime(TimeOnly.MinValue);
        var startUtc = TimeZoneInfo.ConvertTimeToUtc(DateTime.SpecifyKind(localStart, DateTimeKind.Unspecified), Zone);
        return (startUtc, startUtc.AddDays(1));
    }
}
