namespace HastaKabul.Application.Contracts.Kiosk;

/// <summary>Kiosk tus takimindan gelen TC. Bu deger hicbir log satirina yazilmaz.</summary>
public sealed record VerifyTcRequest(string Tc);

/// <summary>
/// Dogrulama yaniti: dogrulandi mi + takma adli kimlik + karsilama icin gereken en az bilgi.
/// Tam ad hic donmez; displayName "Ayşe H." bicimindedir.
/// </summary>
public sealed record KioskVerificationResponse(
    bool Verified,
    string? PatientRef,
    string? DisplayName,
    KioskAppointmentView? Appointment);

public sealed record KioskAppointmentView(
    string AppointmentId,
    string ClinicName,
    string DoctorDisplayName,
    string Room,
    DateTime ScheduledAt,
    string Status);

/// <summary>"Geldim" onayi; randevu durumunu CHECKED_IN yapar.</summary>
public sealed record CheckInRequest(string PatientRef);

public sealed record CheckInResponse(
    string AppointmentId,
    string Status,
    DateTime UpdatedAt);
