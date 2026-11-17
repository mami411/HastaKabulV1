namespace HastaKabul.Infrastructure.Persistence;

/// <summary>
/// Veritabani yetenekleri. Demo ve uretim yolu pgvector'ludur (Docker Compose'daki
/// pgvector/pgvector imaji) ve migration'lar bu semaya gore uretilir.
/// <para>
/// Eklentinin kurulu olmadigi bir gelistirme makinesinde <see cref="UsePgvector"/>
/// false yapilabilir: embedding kolonu real[] olarak olusur ve sema migration yerine
/// EnsureCreated ile kurulur. Bu yalnizca yerel deneme icindir; sozlesmedeki
/// vector(1536) karari degismez.
/// </para>
/// </summary>
public sealed class DatabaseFeatures
{
    public const string SectionName = "Database";

    public bool UsePgvector { get; set; } = true;

    public bool AutoMigrate { get; set; } = true;
}
