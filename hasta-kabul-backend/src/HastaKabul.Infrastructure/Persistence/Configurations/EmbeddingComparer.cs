using Microsoft.EntityFrameworkCore.ChangeTracking;

namespace HastaKabul.Infrastructure.Persistence.Configurations;

/// <summary>
/// EF'in embedding dizisindeki degisikligi dogru yakalamasi icin karsilastirici.
/// </summary>
public sealed class EmbeddingComparer : ValueComparer<float[]?>
{
    public EmbeddingComparer()
        : base(
            (left, right) => left == null ? right == null : right != null && left.SequenceEqual(right),
            value => value == null ? 0 : value.Aggregate(0, (hash, item) => HashCode.Combine(hash, item)),
            value => value == null ? null : value.ToArray())
    {
    }
}
