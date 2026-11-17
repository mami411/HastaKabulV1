using HastaKabul.Domain.Clinics;
using HastaKabul.Domain.Common;

namespace HastaKabul.Domain.Doctors;

public class Doctor : Entity
{
    private Doctor() { }

    public Doctor(Guid clinicId, string fullName, string room)
    {
        ClinicId = clinicId;
        FullName = fullName;
        Room = room;
    }

    public Guid ClinicId { get; private set; }
    public Clinic? Clinic { get; private set; }

    public string FullName { get; private set; } = null!;
    public string Room { get; private set; } = null!;

    /// <summary>Arayuze ve ses asistanina giden ad: "Dr. Ayse Y.".</summary>
    public string DisplayName
    {
        get
        {
            var parts = FullName.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length < 2) return FullName;
            var surname = parts[^1];
            var given = string.Join(' ', parts[..^1]);
            return $"{given} {char.ToUpperInvariant(surname[0])}.";
        }
    }
}
