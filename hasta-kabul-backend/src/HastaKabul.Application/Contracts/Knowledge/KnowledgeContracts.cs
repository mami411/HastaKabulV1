namespace HastaKabul.Application.Contracts.Knowledge;

/// <summary>
/// GET /api/v1/kb yaniti (yalnizca X-Service-Key ile). ai-simplify servisi RAG eslemesini
/// bu kayitlarla yapar ve callback'te sourceIds olarak buradaki id'leri gonderir; boylece
/// kaynak basliklari SummaryService tarafinda cozulebilir. Kind: DIAGNOSIS | MEDICATION.
/// </summary>
public sealed record KbEntryView(
    string Id,
    string Term,
    string Title,
    string Body,
    string Kind,
    IReadOnlyList<string> Synonyms);
