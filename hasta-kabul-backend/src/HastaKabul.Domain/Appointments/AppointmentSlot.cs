using HastaKabul.Domain.Common;
using HastaKabul.Domain.Doctors;

namespace HastaKabul.Domain.Appointments;

/// <summary>
/// Randevu slotu. Karar tablosu: ISO 8601 UTC saklanir, slot uzunlugu 15 dakikadir;
/// Europe/Istanbul donusumu yalnizca arayuzde yapilir.
/// </summary>
public class AppointmentSlot : Entity
{
    public const int DefaultDurationMinutes = 15;

    private AppointmentSlot() { }

    public AppointmentSlot(Guid clinicId, Guid doctorId, DateTime startTimeUtc, int durationMinutes = DefaultDurationMinutes)
    {
        ClinicId = clinicId;
        DoctorId = doctorId;
        StartTimeUtc = DateTime.SpecifyKind(startTimeUtc, DateTimeKind.Utc);
        DurationMinutes = durationMinutes;
    }

    public Guid ClinicId { get; private set; }
    public Guid DoctorId { get; private set; }
    public Doctor? Doctor { get; private set; }

    public DateTime StartTimeUtc { get; private set; }
    public int DurationMinutes { get; private set; }
    public bool IsTaken { get; private set; }

    public DateTime EndTimeUtc => StartTimeUtc.AddMinutes(DurationMinutes);

    /// <summary>Slotu rezerve eder. Ayni slota ikinci istek gelirse SLOT_TAKEN doner.</summary>
    public void Reserve()
    {
        if (IsTaken)
            throw new DomainException(ErrorCodes.SlotTaken, "Seçilen saat başka bir hasta tarafından alındı.");

        IsTaken = true;
    }

    public void Release() => IsTaken = false;

    /// <summary>Slotun baslangici verilen andan once mi (ya da tam o an mi): gecmis slot sunulmaz/satilmaz.</summary>
    public bool HasStarted(DateTime nowUtc) => StartTimeUtc <= nowUtc;
}
