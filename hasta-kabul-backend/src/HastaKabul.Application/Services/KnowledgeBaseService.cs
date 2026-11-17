using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Contracts.Knowledge;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Knowledge;

namespace HastaKabul.Application.Services;

/// <summary>
/// Bilgi tabanini AI servisine acar. Salt okunur; hasta verisi icermez.
/// Embedding kolonu disari verilmez.
/// </summary>
public sealed class KnowledgeBaseService(IKbEntryRepository knowledgeBase)
{
    public async Task<IReadOnlyList<KbEntryView>> ListAsync(CancellationToken ct = default)
    {
        var entries = await knowledgeBase.ListAllAsync(ct);

        return entries
            .OrderBy(e => e.Kind)
            .ThenBy(e => e.Term, StringComparer.Ordinal)
            .Select(ToView)
            .ToList();
    }

    public static string ToWire(KbEntryKind kind) => kind switch
    {
        KbEntryKind.Diagnosis => "DIAGNOSIS",
        KbEntryKind.Medication => "MEDICATION",
        _ => kind.ToString().ToUpperInvariant()
    };

    private static KbEntryView ToView(KbEntry e) => new(
        e.Id.ToString(),
        e.Term,
        e.Title,
        e.Body,
        ToWire(e.Kind),
        e.Synonyms.ToList());
}
