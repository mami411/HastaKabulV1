namespace HastaKabul.Application.Abstractions;

/// <summary>Test edilebilirlik icin zaman kaynagi. API her yerde UTC uretir.</summary>
public interface IClock
{
    DateTime UtcNow { get; }
}
