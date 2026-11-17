using HastaKabul.Application.Abstractions.Realtime;
using HastaKabul.Application.Contracts.Realtime;
using Microsoft.AspNetCore.SignalR;

namespace HastaKabul.Api.Realtime;

/// <summary>
/// IRealtimeNotifier'in SignalR gerceklemesi. Olay adlari Sprint 0'da donduruldu:
/// appointment.statusChanged ve summary.ready.
/// </summary>
public sealed class SignalRNotifier(IHubContext<DoctorHub> hub) : IRealtimeNotifier
{
    public Task AppointmentStatusChangedAsync(
        Guid doctorId, AppointmentStatusChangedEvent payload, CancellationToken ct = default)
        => hub.Clients
            .Group(DoctorHub.GroupName(doctorId))
            .SendAsync(RealtimeEventNames.AppointmentStatusChanged, payload, ct);

    public Task SummaryReadyAsync(Guid doctorId, SummaryReadyEvent payload, CancellationToken ct = default)
        => hub.Clients
            .Group(DoctorHub.GroupName(doctorId))
            .SendAsync(RealtimeEventNames.SummaryReady, payload, ct);
}
