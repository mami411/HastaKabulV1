using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;
using Xunit;

namespace HastaKabul.Tests;

/// <summary>
/// Durum makinesi testleri: panel ile kiosk birbirinin durumunu ezemesin diye
/// yalnizca sozlesmedeki gecisler serbesttir.
/// </summary>
public class AppointmentStatusTests
{
    private static Appointment CreateAppointment() => new(
        Guid.NewGuid(), Guid.NewGuid(), Guid.NewGuid(), Guid.NewGuid(), DateTime.UtcNow, "246802");

    [Fact]
    public void Tam_akis_sirayla_ilerler()
    {
        var appointment = CreateAppointment();
        Assert.Equal(AppointmentStatus.Scheduled, appointment.Status);

        appointment.CheckIn(DateTime.UtcNow);
        Assert.Equal(AppointmentStatus.CheckedIn, appointment.Status);

        appointment.StartExam(DateTime.UtcNow);
        Assert.Equal(AppointmentStatus.InExam, appointment.Status);

        appointment.Complete(DateTime.UtcNow);
        Assert.Equal(AppointmentStatus.Completed, appointment.Status);
    }

    [Fact]
    public void Randevulu_hasta_gelmedi_olarak_isaretlenebilir()
    {
        var appointment = CreateAppointment();

        appointment.MarkNoShow(DateTime.UtcNow);

        Assert.Equal(AppointmentStatus.NoShow, appointment.Status);
    }

    [Fact]
    public void Adim_atlayan_gecis_reddedilir()
    {
        var appointment = CreateAppointment();

        var error = Assert.Throws<InvalidStatusTransitionException>(() => appointment.StartExam(DateTime.UtcNow));

        Assert.Equal(ErrorCodes.InvalidStatusTransition, error.Code);
    }

    [Fact]
    public void Tamamlanan_randevu_geri_alinamaz()
    {
        var appointment = CreateAppointment();
        appointment.CheckIn(DateTime.UtcNow);
        appointment.StartExam(DateTime.UtcNow);
        appointment.Complete(DateTime.UtcNow);

        Assert.Throws<InvalidStatusTransitionException>(() => appointment.CheckIn(DateTime.UtcNow));
    }

    [Fact]
    public void Ayni_slot_ikinci_kez_rezerve_edilemez()
    {
        var slot = new AppointmentSlot(Guid.NewGuid(), Guid.NewGuid(), DateTime.UtcNow);
        slot.Reserve();

        var error = Assert.Throws<DomainException>(() => slot.Reserve());

        Assert.Equal(ErrorCodes.SlotTaken, error.Code);
    }
}
