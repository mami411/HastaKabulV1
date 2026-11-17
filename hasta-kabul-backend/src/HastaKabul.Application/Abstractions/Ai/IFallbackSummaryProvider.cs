using HastaKabul.Domain.Summaries;

namespace HastaKabul.Application.Abstractions.Ai;

/// <summary>
/// Onceden uretilip seed'e konan onayli ozetler. LLM_UNAVAILABLE gelirse API bunlara duser,
/// boylece sunumda internet/LLM erisilemese de akis kesilmez.
/// </summary>
public interface IFallbackSummaryProvider
{
    Task<FallbackSummary?> FindAsync(string diagnosis, IEnumerable<string> medications, CancellationToken ct = default);
}

public sealed record FallbackSummary(
    string DiagnosisPlain,
    IReadOnlyList<SimplifiedMedication> Medications,
    IReadOnlyList<string> Warnings,
    IReadOnlyList<string> UnknownTerms,
    IReadOnlyList<string> SourceIds);
