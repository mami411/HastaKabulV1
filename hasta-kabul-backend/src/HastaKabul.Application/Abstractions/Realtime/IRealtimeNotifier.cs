using HastaKabul.Application.Contracts.Realtime;

namespace HastaKabul.Application.Abstractions.Realtime;

/// <summary>
/// Hekim bazli kanal: doctor:{doctorId}. Baglantida token ile dogrulanir, boylece
/// herkes herkesin olayini almaz.
/// </summary>
public interface IRealtimeNotifier
{
    Task AppointmentStatusChangedAsync(Guid doctorId, AppointmentStatusChangedEvent payload, CancellationToken ct = default);
    Task SummaryReadyAsync(Guid doctorId, SummaryReadyEvent payload, CancellationToken ct = default);
}
