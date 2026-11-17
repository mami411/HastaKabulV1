using HastaKabul.Application.Contracts.Summaries;

namespace HastaKabul.Application.Contracts.Portal;

/// <summary>Demo icin portal girisi: randevu basina 6 haneli erisim kodu.</summary>
public sealed record PortalAccessRequest(string AccessCode);

/// <summary>
/// Portal yaniti yalnizca hekim onayli ozeti icerir; onaysizsa SUMMARY_NOT_APPROVED doner.
/// </summary>
public sealed record PortalSummaryResponse(
    string AppointmentId,
    string DisplayName,
    string ClinicName,
    string DoctorDisplayName,
    DateTime ScheduledAt,
    PortalSummaryView Summary);

/// <summary>
/// Hastaya gosterilen ozet. Hekim ekranindaki <see cref="SummaryView"/>'dan farki: takma adli kimlik
/// (patientRef), hekime yonelik bilinmeyen terim listesi (unknownTerms) ve hata kodu (errorCode)
/// hastaya gitmez. Diger alan adlari ayni kalir.
/// </summary>
public sealed record PortalSummaryView(
    string SummaryId,
    string AppointmentId,
    string Status,
    string? DiagnosisPlain,
    IReadOnlyList<MedicationView> Medications,
    IReadOnlyList<string> Warnings,
    IReadOnlyList<SourceView> Sources,
    DateTime UpdatedAt);
