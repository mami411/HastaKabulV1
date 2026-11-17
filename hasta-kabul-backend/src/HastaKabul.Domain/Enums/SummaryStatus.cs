namespace HastaKabul.Domain.Enums;

/// <summary>Asenkron sadelestirme akisi: PENDING -> READY -> APPROVED / REJECTED.</summary>
public enum SummaryStatus
{
    Pending = 0,
    Ready = 1,
    Approved = 2,
    Rejected = 3,
    Failed = 4
}
