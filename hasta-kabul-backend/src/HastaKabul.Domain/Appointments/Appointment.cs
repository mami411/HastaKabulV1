using HastaKabul.Domain.Common;
using HastaKabul.Domain.Doctors;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Patients;

namespace HastaKabul.Domain.Appointments;

/// <summary>
/// Randevu ve durum makinesi. Gecerli gecisler yalnizca
/// SCHEDULED -> CHECKED_IN -> IN_EXAM -> COMPLETED ve SCHEDULED -> NO_SHOW'dur;
/// gecersiz gecis <see cref="InvalidStatusTransitionException"/> ile 409 doner.
/// </summary>
public class Appointment : Entity
{
    private Appointment() { }

    public Appointment(Guid patientId, Guid doctorId, Guid clinicId, Guid slotId, DateTime scheduledAtUtc, string accessCode)
    {
        PatientId = patientId;
        DoctorId = doctorId;
        ClinicId = clinicId;
        SlotId = slotId;
        ScheduledAtUtc = DateTime.SpecifyKind(scheduledAtUtc, DateTimeKind.Utc);
        AccessCode = accessCode;
        Status = AppointmentStatus.Scheduled;
        UpdatedAtUtc = CreatedAtUtc;
    }

    public Guid PatientId { get; private set; }
    public Patient? Patient { get; private set; }

    public Guid DoctorId { get; private set; }
    public Doctor? Doctor { get; private set; }

    public Guid ClinicId { get; private set; }

    public Guid SlotId { get; private set; }
    public AppointmentSlot? Slot { get; private set; }

    public DateTime ScheduledAtUtc { get; private set; }
    public AppointmentStatus Status { get; private set; }
    public DateTime UpdatedAtUtc { get; private set; }

    /// <summary>Demo icin randevu basina 6 haneli portal erisim kodu.</summary>
    public string AccessCode { get; private set; } = null!;

    /// <summary>Hasta icin hala gecerli (iptal/gelmedi/tamamlandi olmayan) randevu.</summary>
    public bool IsActive => Status is AppointmentStatus.Scheduled or AppointmentStatus.CheckedIn or AppointmentStatus.InExam;

    /// <summary>
    /// Erisim kodu benzersiz indekse takilirsa (cok dusuk olasilik) kayit oncesi yeni kodla degistirilir.
    /// </summary>
    public void ReplaceAccessCode(string accessCode)
    {
        if (string.IsNullOrWhiteSpace(accessCode) || accessCode.Length != 6)
            throw new ArgumentException("Erişim kodu 6 haneli olmalıdır.", nameof(accessCode));
        AccessCode = accessCode;
    }

    public void CheckIn(DateTime nowUtc) => Transition(AppointmentStatus.CheckedIn, nowUtc);

    public void StartExam(DateTime nowUtc) => Transition(AppointmentStatus.InExam, nowUtc);

    public void Complete(DateTime nowUtc) => Transition(AppointmentStatus.Completed, nowUtc);

    public void MarkNoShow(DateTime nowUtc) => Transition(AppointmentStatus.NoShow, nowUtc);

    /// <summary>Panelden gelen serbest durum guncellemesi; kurali yine durum makinesi uygular.</summary>
    public void ChangeStatus(AppointmentStatus target, DateTime nowUtc) => Transition(target, nowUtc);

    private void Transition(AppointmentStatus target, DateTime nowUtc)
    {
        if (!IsAllowed(Status, target))
            throw new InvalidStatusTransitionException(Status.ToString(), target.ToString());

        Status = target;
        UpdatedAtUtc = DateTime.SpecifyKind(nowUtc, DateTimeKind.Utc);
    }

    private static bool IsAllowed(AppointmentStatus from, AppointmentStatus to) => (from, to) switch
    {
        (AppointmentStatus.Scheduled, AppointmentStatus.CheckedIn) => true,
        (AppointmentStatus.Scheduled, AppointmentStatus.NoShow) => true,
        (AppointmentStatus.CheckedIn, AppointmentStatus.InExam) => true,
        (AppointmentStatus.InExam, AppointmentStatus.Completed) => true,
        _ => false
    };
}
