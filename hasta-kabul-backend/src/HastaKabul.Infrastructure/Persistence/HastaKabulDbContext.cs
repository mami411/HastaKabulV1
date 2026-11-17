using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Clinics;
using HastaKabul.Domain.Doctors;
using HastaKabul.Domain.Encounters;
using HastaKabul.Domain.Knowledge;
using HastaKabul.Domain.Patients;
using HastaKabul.Domain.Summaries;
using HastaKabul.Domain.Voice;
using HastaKabul.Infrastructure.Persistence.Configurations;
using Microsoft.EntityFrameworkCore;

namespace HastaKabul.Infrastructure.Persistence;

/// <summary>
/// Veritabani snake_case, JSON camelCase; donusum yalnizca API katmaninda yapilir.
/// snake_case eslemesi UseSnakeCaseNamingConvention ile saglanir.
/// </summary>
public class HastaKabulDbContext(
    DbContextOptions<HastaKabulDbContext> options,
    DatabaseFeatures? features = null) : DbContext(options)
{
    private readonly DatabaseFeatures _features = features ?? new DatabaseFeatures();

    public DbSet<Patient> Patients => Set<Patient>();
    public DbSet<Clinic> Clinics => Set<Clinic>();
    public DbSet<Doctor> Doctors => Set<Doctor>();
    public DbSet<AppointmentSlot> AppointmentSlots => Set<AppointmentSlot>();
    public DbSet<Appointment> Appointments => Set<Appointment>();
    public DbSet<ClinicalEntry> ClinicalEntries => Set<ClinicalEntry>();
    public DbSet<SimplifiedSummary> Summaries => Set<SimplifiedSummary>();
    public DbSet<VoiceSession> VoiceSessions => Set<VoiceSession>();
    public DbSet<KbEntry> KbEntries => Set<KbEntry>();
    public DbSet<AuditLogEntry> AuditLogs => Set<AuditLogEntry>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.ApplyConfigurationsFromAssembly(typeof(HastaKabulDbContext).Assembly);

        // Varsayilan ve sozlesmedeki yol: pgvector. Eklenti yoksa ayni kolon real[]
        // olarak kurulur, kayit yapisi degismez.
        var embedding = modelBuilder.Entity<KbEntry>().Property(e => e.Embedding);

        if (_features.UsePgvector)
        {
            modelBuilder.HasPostgresExtension("vector");
            embedding
                .HasConversion(new EmbeddingConverter(), new EmbeddingComparer())
                .HasColumnType("vector(1536)");
        }
        else
        {
            embedding.HasColumnType("real[]");
        }
    }
}
