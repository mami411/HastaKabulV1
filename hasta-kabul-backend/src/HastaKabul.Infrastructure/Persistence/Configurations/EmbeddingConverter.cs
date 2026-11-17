using Microsoft.EntityFrameworkCore.Storage.ValueConversion;
using Pgvector;

namespace HastaKabul.Infrastructure.Persistence.Configurations;

/// <summary>
/// Domain katmani pgvector'u bilmez; embedding orada duz float[] olarak durur.
/// Donusum burada yapilir, boylece kolon tipi P1'de degismeden vector(1536) kalir.
/// </summary>
public sealed class EmbeddingConverter : ValueConverter<float[]?, Vector?>
{
    public EmbeddingConverter()
        : base(
            value => value == null ? null : new Vector(value),
            value => value == null ? null : value.ToArray())
    {
    }
}
