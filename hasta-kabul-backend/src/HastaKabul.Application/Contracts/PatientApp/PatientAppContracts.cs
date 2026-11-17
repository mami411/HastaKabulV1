namespace HastaKabul.Application.Contracts.PatientApp;

// Hasta uygulamasinin asistansiz sayfalari: /api/v1/voice-sessions/{sessionId}/... altindaki uclar.
// Hasta, dogrulanmis ses oturumundan bulunur; yanitlar TC, tam ad ve patientRef tasimaz. Zamanlar UTC.

/// <summary>
/// GET .../profile: karsilama icin maskeli ad ("Ayşe H.") ve oturumun bitis ani. Kalan sure sunucu
/// saatiyle hesaplanir; istemci saati kaysa da uygulama oturumu dogru anda kapatir.
/// </summary>
public sealed record PatientProfileView(string DisplayName, DateTime SessionExpiresAt, int SessionRemainingSeconds);

/// <summary>GET .../clinics: poliklinik ve hekimleri (ekranda gosterilen adlarla).</summary>
public sealed record PatientClinicView(string Code, string Name, IReadOnlyList<PatientDoctorView> Doctors);

public sealed record PatientDoctorView(string DoctorId, string DisplayName);

/// <summary>GET .../slot-days: Europe/Istanbul gunu (YYYY-AA-GG) ve o gunun bos saat sayisi.</summary>
public sealed record SlotDayView(string Date, int AvailableCount);

/// <summary>GET .../slots: bir gunun bos saatleri (gecmis saat donmez).</summary>
public sealed record PatientSlotView(string SlotId, DateTime StartTime, string DoctorId, string DoctorDisplayName);

/// <summary>GET .../appointments: hastanin bugun ve sonrasindaki etkin randevulari.</summary>
public sealed record PatientAppointmentsResponse(IReadOnlyList<PatientAppointmentView> Upcoming);

public sealed record PatientAppointmentView(
    string AppointmentId,
    DateTime ScheduledAt,
    string ClinicName,
    string DoctorDisplayName,
    string Room,
    string Status,
    string AccessCode);

/// <summary>
/// POST .../appointments: govde yalnizca slotId tasir; hasta oturumdan bulunur. Yanit sesli
/// asistanin kullandigi <c>CreateAppointmentResponse</c> ile aynidir.
/// </summary>
public sealed record CreatePatientAppointmentRequest(string SlotId);
