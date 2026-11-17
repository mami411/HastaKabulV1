namespace HastaKabul.Application.Contracts.Realtime;

/// <summary>Sprint 0'da dondurulan WebSocket olay adlari.</summary>
public static class RealtimeEventNames
{
    public const string AppointmentStatusChanged = "appointment.statusChanged";
    public const string SummaryReady = "summary.ready";
}

/// <summary>
/// Olay icerigi: TC ve tam ad yok. Panel kopmadan sonra GET /appointments ile tam listeyi
/// ceker, eski updatedAt'li olaylari yok sayar.
/// </summary>
public sealed record AppointmentStatusChangedEvent(
    string AppointmentId,
    string DisplayName,
    string Status,
    DateTime UpdatedAt);

public sealed record SummaryReadyEvent(
    string AppointmentId,
    string SummaryId,
    string Status,
    DateTime UpdatedAt);
