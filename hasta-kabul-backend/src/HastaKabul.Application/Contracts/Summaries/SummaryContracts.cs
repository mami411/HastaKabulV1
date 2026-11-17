namespace HastaKabul.Application.Contracts.Summaries;

/// <summary>Ozet cikti semasi: serbest metin degil JSON.</summary>
public sealed record MedicationView(string Name, string Purpose, string Usage);

public sealed record SummaryView(
    string SummaryId,
    string AppointmentId,
    string PatientRef,
    string Status,
    string? DiagnosisPlain,
    IReadOnlyList<MedicationView> Medications,
    IReadOnlyList<string> Warnings,
    IReadOnlyList<string> UnknownTerms,
    IReadOnlyList<SourceView> Sources,
    string? ErrorCode,
    DateTime UpdatedAt);

/// <summary>Ozet ekraninda gosterilen kaynaklar.</summary>
public sealed record SourceView(string Id, string Title);

/// <summary>
/// AI servisinin callback govdesi (PUT /api/v1/summaries/{summaryId}, X-Service-Key ile).
/// Hata durumunda yalnizca errorCode gonderilir.
/// </summary>
public sealed record SummaryCallbackRequest(
    string? DiagnosisPlain,
    IReadOnlyList<MedicationView>? Medications,
    IReadOnlyList<string>? Warnings,
    IReadOnlyList<string>? UnknownTerms,
    IReadOnlyList<string>? SourceIds,
    string? ErrorCode);

/// <summary>Hekim onayi / reddi. P0'da duzenleme yok, yalnizca onay-ret.</summary>
public sealed record SummaryDecisionResponse(
    string SummaryId,
    string Status,
    DateTime UpdatedAt);
