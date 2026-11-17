using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Common;

namespace HastaKabul.Domain.Encounters;

/// <summary>
/// Hekimin panelden girdigi tani ve recete. Sadelestirme istegi yalnizca bu alanlardan
/// (ve takma adli kimlikten) uretilir; ad, TC, iletisim bilgisi gonderilmez.
/// </summary>
public class ClinicalEntry : Entity
{
    private ClinicalEntry() { }

    public ClinicalEntry(Guid appointmentId, Guid doctorId, string diagnosis, IEnumerable<string> medications)
    {
        AppointmentId = appointmentId;
        DoctorId = doctorId;
        Diagnosis = diagnosis;
        Medications = medications.Select(m => m.Trim()).Where(m => m.Length > 0).ToList();
    }

    public Guid AppointmentId { get; private set; }
    public Appointment? Appointment { get; private set; }

    public Guid DoctorId { get; private set; }

    public string Diagnosis { get; private set; } = null!;
    public List<string> Medications { get; private set; } = new();
}
