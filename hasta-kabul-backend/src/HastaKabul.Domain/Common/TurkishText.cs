using System.Text;

namespace HastaKabul.Domain.Common;

/// <summary>
/// Kulturden bagimsiz Turkce metin karsilastirmasi. Surec kulturu tr-TR iken
/// <c>"KARDIYOLOJI".ToLower()</c> "kardıyolojı" verir; SQL <c>lower()</c> ve kullanicinin
/// Turkce karaktersiz yazimi ("ic hastaliklari") ile eslesmez. Bu sinif her iki yazimi
/// ayni anahtara katlar: kucuk harf, Turkce harfler ASCII karsiligina (ı/İ/I→i, ş→s,
/// ğ→g, ü→u, ö→o, ç→c, â→a), birlesik nokta (U+0307) atilir, bosluklar teklenir.
/// Yalniz eslestirme anahtari uretir; gosterilen metin degismez.
/// </summary>
public static class TurkishText
{
    public static string Fold(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
            return string.Empty;

        var sb = new StringBuilder(text.Length);
        foreach (var ch in text.Trim())
        {
            var c = ch switch
            {
                'I' or 'İ' or 'ı' or 'i' or 'Î' or 'î' => 'i',
                'Ş' or 'ş' => 's',
                'Ğ' or 'ğ' => 'g',
                'Ü' or 'ü' or 'Û' or 'û' => 'u',
                'Ö' or 'ö' => 'o',
                'Ç' or 'ç' => 'c',
                'Â' or 'â' => 'a',
                '̇' => '\0',
                _ => char.ToLowerInvariant(ch)
            };

            if (c == '\0')
                continue;

            if (char.IsWhiteSpace(c))
            {
                if (sb.Length > 0 && sb[^1] != ' ')
                    sb.Append(' ');
                continue;
            }

            sb.Append(c);
        }

        return sb.ToString().TrimEnd();
    }

    /// <summary>Iki metin katlanmis halleriyle esit mi (bos metinler esit sayilmaz).</summary>
    public static bool FoldEquals(string? a, string? b)
    {
        var fa = Fold(a);
        return fa.Length > 0 && fa == Fold(b);
    }
}
