namespace HastaKabul.Application.Common;

/// <summary>
/// Sadelestirme akisinin demo dayanikliligi. Geri cagri bu sure icinde gelmezse PENDING ozet okuma
/// aninda yedek ozete (varsa) ya da FAILED/LLM_UNAVAILABLE durumuna gecer.
/// Yapilandirma: Simplify:PendingTimeoutSeconds (varsayilan 90).
/// </summary>
public sealed class SummaryOptions
{
    public const int DefaultPendingTimeoutSeconds = 90;

    public TimeSpan PendingTimeout { get; init; } = TimeSpan.FromSeconds(DefaultPendingTimeoutSeconds);
}
