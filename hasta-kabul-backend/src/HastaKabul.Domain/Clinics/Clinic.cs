using HastaKabul.Domain.Common;

namespace HastaKabul.Domain.Clinics;

/// <summary>
/// Poliklinik. Es anlamlilar shared/clinics.json ile ayni listeden uretilir; niyet anlama
/// servisi poliklinik adini bu es anlamlilar uzerinden cozer.
/// </summary>
public class Clinic : Entity
{
    private Clinic() { }

    public Clinic(string code, string name, IEnumerable<string>? synonyms = null)
    {
        Code = code;
        Name = name;
        Synonyms = synonyms?.ToList() ?? new List<string>();
    }

    public string Code { get; private set; } = null!;
    public string Name { get; private set; } = null!;
    public List<string> Synonyms { get; private set; } = new();

    /// <summary>
    /// Kod, ad ya da es anlamlilardan biriyle eslesiyor mu. Kulturden bagimsizdir ve Turkce
    /// karakterli/karaktersiz yazimi ayni sayar ("KARDIYOLOJI", "Göz", "ic hastaliklari").
    /// </summary>
    public bool Matches(string? nameOrCode)
    {
        var needle = TurkishText.Fold(nameOrCode);
        if (needle.Length == 0)
            return false;

        return TurkishText.Fold(Code) == needle
            || TurkishText.Fold(Name) == needle
            || Synonyms.Any(s => TurkishText.Fold(s) == needle);
    }
}
