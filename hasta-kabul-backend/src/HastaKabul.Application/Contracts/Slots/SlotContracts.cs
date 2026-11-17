namespace HastaKabul.Application.Contracts.Slots;

/// <summary>
/// GET /api/v1/slots?clinic=&amp;date= yaniti. Goreli tarihleri ("yarin", "cuma")
/// orkestrator Europe/Istanbul'a gore cozer ve date=YYYY-MM-DD gonderir; saatler UTC doner.
/// <c>doctorId</c> sonradan eklendi (geriye uyumlu ek alan): cok hekimli poliklinikte asistan hekim
/// tercihini bununla suzer.
/// </summary>
public sealed record SlotView(
    string SlotId,
    DateTime StartTime,
    string DoctorDisplayName,
    string DoctorId);
