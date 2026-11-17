using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Abstractions.Realtime;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Appointments;
using HastaKabul.Application.Contracts.Realtime;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Common;

namespace HastaKabul.Application.Services;

/// <summary>
/// Hekim paneli: gunun randevu listesi ve durum guncelleme.
/// Panel kopup geri baglandiginda once bu listeyi ceker, sonra olaylari dinler.
/// </summary>
public sealed class DoctorPanelService(
    IAppointmentRepository appointments,
    ISummaryRepository summaries,
    IAuditLogRepository auditLog,
    IUnitOfWork unitOfWork,
    IRealtimeNotifier notifier,
    SummaryGenerationService generation,
    IRequestContext requestContext,
    IClock clock)
{
    public async Task<IReadOnlyList<AppointmentListItem>> GetDayAsync(
        Guid doctorId, DateOnly? localDay, CancellationToken ct = default)
    {
        var (dayStart, dayEnd) = localDay is null
            ? IstanbulTime.DayRangeUtc(clock.UtcNow)
            : IstanbulTime.DayRangeUtc(localDay.Value);

        var list = await appointments.GetByDoctorAndDayAsync(doctorId, dayStart, dayEnd, ct);

        // Ozetler tek sorguda (N+1 yerine); geri cagrisi zaman asimina ugrayan PENDING burada sonuclanir.
        var latest = await summaries.GetLatestByAppointmentIdsAsync(list.Select(a => a.Id).ToList(), ct);
        foreach (var pending in latest.Values)
            await generation.ExpireIfStaleAsync(pending, doctorId, ct);

        var items = new List<AppointmentListItem>(list.Count);
        foreach (var appointment in list)
        {
            var summary = latest.GetValueOrDefault(appointment.Id);

            items.Add(new AppointmentListItem(
                appointment.Id.ToString(),
                appointment.Patient?.PatientRef.ToString() ?? string.Empty,
                appointment.Patient?.DisplayName ?? string.Empty,
                appointment.ScheduledAtUtc,
                StatusNames.ToWire(appointment.Status),
                appointment.UpdatedAtUtc,
                summary?.Id.ToString(),
                summary is null ? null : StatusNames.ToWire(summary.Status)));
        }

        return items;
    }

    /// <summary>Muayenede / Tamamlandi gecisleri. Gecersiz gecis 409 ile reddedilir.</summary>
    public async Task<AppointmentStatusResponse> UpdateStatusAsync(
        Guid doctorId, Guid appointmentId, UpdateAppointmentStatusRequest request, CancellationToken ct = default)
    {
        var target = StatusNames.ParseAppointmentStatus(request.Status);

        var appointment = await appointments.GetByIdWithDetailsAsync(appointmentId, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Randevu bulunamadı.");

        if (appointment.DoctorId != doctorId)
            throw new DomainException(ErrorCodes.Forbidden, "Bu randevu başka bir hekime ait.");

        appointment.ChangeStatus(target, clock.UtcNow);

        await auditLog.AddAsync(
            new AuditLogEntry(
                "appointment.statusChanged",
                $"doctor:{doctorId}",
                nameof(Appointment),
                appointment.Id,
                requestContext.RequestId,
                StatusNames.ToWire(target)),
            ct);
        await unitOfWork.SaveChangesAsync(ct);

        await notifier.AppointmentStatusChangedAsync(
            appointment.DoctorId,
            new AppointmentStatusChangedEvent(
                appointment.Id.ToString(),
                appointment.Patient?.DisplayName ?? string.Empty,
                StatusNames.ToWire(appointment.Status),
                appointment.UpdatedAtUtc),
            ct);

        return new AppointmentStatusResponse(
            appointment.Id.ToString(),
            StatusNames.ToWire(appointment.Status),
            appointment.UpdatedAtUtc);
    }
}
