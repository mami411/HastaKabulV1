using HastaKabul.Application.Contracts.Summaries;

namespace HastaKabul.Application.Contracts.Voice;

/// <summary>
/// GET /api/v1/voice-sessions/{id}/medical-history yaniti. Yalnizca hekim onayli
/// (APPROVED) ozetler; TC, tam ad, patientRef, onaysiz icerik ve unknownTerms tasinmaz.
/// </summary>
public sealed record MedicalHistoryResponse(IReadOnlyList<MedicalHistoryVisit> Visits);

/// <summary>Gecmis bir muayenenin hastaya gosterilebilir, onayli ozeti. Zamanlar UTC.</summary>
public sealed record MedicalHistoryVisit(
    string AppointmentId,
    DateTime ScheduledAt,
    string ClinicName,
    string DoctorDisplayName,
    string DiagnosisPlain,
    IReadOnlyList<MedicationView> Medications,
    IReadOnlyList<string> Warnings);
