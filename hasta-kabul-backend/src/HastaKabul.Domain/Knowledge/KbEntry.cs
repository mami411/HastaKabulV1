using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;

namespace HastaKabul.Domain.Knowledge;

/// <summary>
/// RAG bilgi tabani kaydi (tani aciklamasi / prospektus ozeti).
/// Karar tablosu: kb_entries tablosu P0'da anahtar kelimeyle eslesir, embedding kolonu
/// nullable kalir; P1'de pgvector ile vektor aramasi eklenirken sema degismez.
/// </summary>
public class KbEntry : Entity
{
    private KbEntry() { }

    /// <param name="id">
    /// Verilirse sabit kimlik (seed): --reset-demo sonrasi da ayni kalir, boylece ai-simplify'in
    /// onbellekteki kimlikleri ve ozetlerdeki sourceIds gecerliligini korur.
    /// </param>
    public KbEntry(string term, string title, string body, KbEntryKind kind, IEnumerable<string>? synonyms = null,
        Guid? id = null)
    {
        if (id is { } fixedId && fixedId != Guid.Empty)
            Id = fixedId;
        Term = term;
        Title = title;
        Body = body;
        Kind = kind;
        Synonyms = synonyms?.ToList() ?? new List<string>();
    }

    public string Term { get; private set; } = null!;
    public string Title { get; private set; } = null!;
    public string Body { get; private set; } = null!;
    public KbEntryKind Kind { get; private set; }
    public List<string> Synonyms { get; private set; } = new();

    /// <summary>P1'de doldurulur; P0'da null.</summary>
    public float[]? Embedding { get; private set; }

    public void SetEmbedding(float[] embedding) => Embedding = embedding;

    /// <summary>
    /// Anahtar kelime eslesmesi (kulturden bagimsiz, Turkce karakterli/karaktersiz ayni):
    /// terim aranan ifadenin icinde ya da ifade terimin icinde gecer veya bir es anlamliya esittir.
    /// </summary>
    public bool MatchesTerm(string? text)
    {
        var needle = TurkishText.Fold(text);
        if (needle.Length == 0)
            return false;

        var term = TurkishText.Fold(Term);
        return term.Contains(needle, StringComparison.Ordinal)
            || needle.Contains(term, StringComparison.Ordinal)
            || Synonyms.Any(s => TurkishText.Fold(s) == needle);
    }
}
