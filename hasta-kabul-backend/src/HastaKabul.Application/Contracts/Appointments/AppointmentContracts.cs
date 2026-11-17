namespace HastaKabul.Application.Contracts.Appointments;

/// <summary>Hekim panelindeki gun listesi satiri. TC ve tam ad tasimaz.</summary>
public sealed record AppointmentListItem(
    string AppointmentId,
    string PatientRef,
    string DisplayName,
    DateTime ScheduledAt,
    string Status,
    DateTime UpdatedAt,
    string? SummaryId,
    string? SummaryStatus);

public sealed record UpdateAppointmentStatusRequest(string Status);

public sealed record AppointmentStatusResponse(
    string AppointmentId,
    string Status,
    DateTime UpdatedAt);

/// <summary>
/// Ses asistanindan gelen randevu istegi. Hasta kimligi tasinmaz; API hastayi
/// dogrulanmis ses oturumundan bulur.
/// </summary>
public sealed record CreateAppointmentRequest(string VoiceSessionId, string SlotId);

public sealed record CreateAppointmentResponse(
    string AppointmentId,
    string SlotId,
    DateTime ScheduledAt,
    string DoctorDisplayName,
    string ClinicName,
    string Status,
    string AccessCode);

/// <summary>Hekimin panelden girdigi tani ve recete.</summary>
public sealed record CreateClinicalEntryRequest(string Diagnosis, IReadOnlyList<string>? Medications);

public sealed record CreateClinicalEntryResponse(
    string ClinicalEntryId,
    string SummaryId,
    string SummaryStatus);
