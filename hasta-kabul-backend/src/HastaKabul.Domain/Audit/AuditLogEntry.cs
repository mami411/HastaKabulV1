using HastaKabul.Domain.Common;

namespace HastaKabul.Domain.Audit;

/// <summary>
/// Erisim ve islem kaydi. KVKK tedbiri; TC hic yazilmaz, maskeli bile yazilmaz.
/// Kayitlar yalnizca takma adli kimlik ve kaynak kimligi tasir.
/// </summary>
public class AuditLogEntry : Entity
{
    private AuditLogEntry() { }

    public AuditLogEntry(string action, string actor, string entityType, Guid? entityId, string requestId, string? detail = null)
    {
        Action = action;
        Actor = actor;
        EntityType = entityType;
        EntityId = entityId;
        RequestId = requestId;
        Detail = detail;
    }

    public string Action { get; private set; } = null!;

    /// <summary>"kiosk", "doctor:{doctorId}", "ai-simplify" gibi kaynak etiketi.</summary>
    public string Actor { get; private set; } = null!;

    public string EntityType { get; private set; } = null!;
    public Guid? EntityId { get; private set; }
    public string RequestId { get; private set; } = null!;
    public string? Detail { get; private set; }
}
