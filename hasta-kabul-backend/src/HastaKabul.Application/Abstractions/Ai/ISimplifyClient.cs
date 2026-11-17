namespace HastaKabul.Application.Abstractions.Ai;

/// <summary>
/// AI'ya giden beyaz liste. Listede olmayan hicbir alan gonderilmez:
/// patientRef, diagnosis, medications[], requestId, summaryId.
/// </summary>
public sealed record SimplifyRequest(
    Guid PatientRef,
    string Diagnosis,
    IReadOnlyList<string> Medications,
    string RequestId,
    Guid SummaryId);

/// <summary>
/// POST :8001/simplify cagrisi. Servis 202 doner; ozet hazir olunca
/// PUT /api/v1/summaries/{summaryId} ile geri cagirir.
/// </summary>
public interface ISimplifyClient
{
    Task<bool> RequestSimplificationAsync(SimplifyRequest request, CancellationToken ct = default);
}
