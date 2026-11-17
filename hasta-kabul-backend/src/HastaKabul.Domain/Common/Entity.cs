namespace HastaKabul.Domain.Common;

/// <summary>
/// Tum kayitlar icin ortak taban. Karar tablosu: "Her yerde UUID, string olarak tasinir".
/// </summary>
public abstract class Entity
{
    public Guid Id { get; protected set; } = Guid.NewGuid();
    public DateTime CreatedAtUtc { get; protected set; } = DateTime.UtcNow;
}
