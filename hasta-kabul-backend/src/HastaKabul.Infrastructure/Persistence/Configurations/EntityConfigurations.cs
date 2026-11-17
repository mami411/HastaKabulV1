using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Clinics;
using HastaKabul.Domain.Doctors;
using HastaKabul.Domain.Encounters;
using HastaKabul.Domain.Knowledge;
using HastaKabul.Domain.Patients;
using HastaKabul.Domain.Summaries;
using HastaKabul.Domain.Voice;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata.Builders;

namespace HastaKabul.Infrastructure.Persistence.Configurations;

public sealed class PatientConfiguration : IEntityTypeConfiguration<Patient>
{
    public void Configure(EntityTypeBuilder<Patient> builder)
    {
        builder.ToTable("patients");
        builder.HasKey(p => p.Id);

        // Esleme yalnizca ozet uzerinden yapilir; duz TC kolonu yok.
        builder.Property(p => p.TcHash).HasMaxLength(64).IsRequired();
        builder.HasIndex(p => p.TcHash).IsUnique();

        builder.Property(p => p.TcEncrypted).IsRequired();

        builder.HasIndex(p => p.PatientRef).IsUnique();
        builder.Property(p => p.FirstName).HasMaxLength(80).IsRequired();
        builder.Property(p => p.LastName).HasMaxLength(80).IsRequired();
        builder.Property(p => p.PhoneMasked).HasMaxLength(20);

        // DisplayName hesaplanan ozelliktir, kolona yazilmaz.
        builder.Ignore(p => p.DisplayName);
    }
}

public sealed class ClinicConfiguration : IEntityTypeConfiguration<Clinic>
{
    public void Configure(EntityTypeBuilder<Clinic> builder)
    {
        builder.ToTable("clinics");
        builder.HasKey(c => c.Id);
        builder.Property(c => c.Code).HasMaxLength(40).IsRequired();
        builder.HasIndex(c => c.Code).IsUnique();
        builder.Property(c => c.Name).HasMaxLength(120).IsRequired();
        // Npgsql List<string> alanlari text[] olarak esler; ek donusum gerekmez.
        builder.Property(c => c.Synonyms);
    }
}

public sealed class DoctorConfiguration : IEntityTypeConfiguration<Doctor>
{
    public void Configure(EntityTypeBuilder<Doctor> builder)
    {
        builder.ToTable("doctors");
        builder.HasKey(d => d.Id);
        builder.Property(d => d.FullName).HasMaxLength(160).IsRequired();
        builder.Property(d => d.Room).HasMaxLength(20).IsRequired();
        builder.Ignore(d => d.DisplayName);

        builder.HasOne(d => d.Clinic)
            .WithMany()
            .HasForeignKey(d => d.ClinicId)
            .OnDelete(DeleteBehavior.Restrict);
    }
}

public sealed class AppointmentSlotConfiguration : IEntityTypeConfiguration<AppointmentSlot>
{
    public void Configure(EntityTypeBuilder<AppointmentSlot> builder)
    {
        builder.ToTable("appointment_slots");
        builder.HasKey(s => s.Id);
        builder.Property(s => s.StartTimeUtc).IsRequired();
        builder.Property(s => s.DurationMinutes).IsRequired();
        builder.Ignore(s => s.EndTimeUtc);

        builder.HasOne(s => s.Doctor)
            .WithMany()
            .HasForeignKey(s => s.DoctorId)
            .OnDelete(DeleteBehavior.Restrict);

        // Bos saat sorgusu: poliklinik + gun + alinmamis.
        builder.HasIndex(s => new { s.ClinicId, s.StartTimeUtc, s.IsTaken });
    }
}

public sealed class AppointmentConfiguration : IEntityTypeConfiguration<Appointment>
{
    public void Configure(EntityTypeBuilder<Appointment> builder)
    {
        builder.ToTable("appointments");
        builder.HasKey(a => a.Id);

        builder.Property(a => a.Status).HasConversion<string>().HasMaxLength(20).IsRequired();
        builder.Property(a => a.AccessCode).HasMaxLength(6).IsRequired();
        builder.HasIndex(a => a.AccessCode).IsUnique();

        builder.HasOne(a => a.Patient)
            .WithMany()
            .HasForeignKey(a => a.PatientId)
            .OnDelete(DeleteBehavior.Restrict);

        builder.HasOne(a => a.Doctor)
            .WithMany()
            .HasForeignKey(a => a.DoctorId)
            .OnDelete(DeleteBehavior.Restrict);

        builder.HasOne(a => a.Slot)
            .WithMany()
            .HasForeignKey(a => a.SlotId)
            .OnDelete(DeleteBehavior.Restrict);

        // Bir slota yalnizca bir randevu.
        builder.HasIndex(a => a.SlotId).IsUnique();

        // Kiosk eslestirme: hasta + gun + durum.
        builder.HasIndex(a => new { a.PatientId, a.ScheduledAtUtc, a.Status });

        // Panel: hekim + gun.
        builder.HasIndex(a => new { a.DoctorId, a.ScheduledAtUtc });
    }
}

public sealed class ClinicalEntryConfiguration : IEntityTypeConfiguration<ClinicalEntry>
{
    public void Configure(EntityTypeBuilder<ClinicalEntry> builder)
    {
        builder.ToTable("clinical_entries");
        builder.HasKey(e => e.Id);
        builder.Property(e => e.Diagnosis).HasMaxLength(400).IsRequired();
        builder.Property(e => e.Medications);

        builder.HasOne(e => e.Appointment)
            .WithMany()
            .HasForeignKey(e => e.AppointmentId)
            .OnDelete(DeleteBehavior.Cascade);
    }
}

public sealed class SimplifiedSummaryConfiguration : IEntityTypeConfiguration<SimplifiedSummary>
{
    public void Configure(EntityTypeBuilder<SimplifiedSummary> builder)
    {
        builder.ToTable("simplified_summaries");
        builder.HasKey(s => s.Id);

        builder.Property(s => s.Status).HasConversion<string>().HasMaxLength(20).IsRequired();
        builder.Property(s => s.RequestId).HasMaxLength(80).IsRequired();
        builder.Property(s => s.DiagnosisPlain).HasMaxLength(2000);
        builder.Property(s => s.FailureCode).HasMaxLength(40);

        // medications[] yapili bir liste oldugu icin jsonb kolonunda tutulur;
        // duz metin listeleri Postgres text[] olarak saklanir.
        builder.OwnsMany(s => s.Medications, m => m.ToJson());
        builder.Property(s => s.Warnings);
        builder.Property(s => s.UnknownTerms);
        builder.Property(s => s.SourceIds);

        builder.HasOne(s => s.Appointment)
            .WithMany()
            .HasForeignKey(s => s.AppointmentId)
            .OnDelete(DeleteBehavior.Cascade);

        builder.HasIndex(s => s.AppointmentId);
    }
}

public sealed class VoiceSessionConfiguration : IEntityTypeConfiguration<VoiceSession>
{
    public void Configure(EntityTypeBuilder<VoiceSession> builder)
    {
        builder.ToTable("voice_sessions");
        builder.HasKey(s => s.Id);
        builder.Property(s => s.Status).HasConversion<string>().HasMaxLength(20).IsRequired();
        builder.Ignore(s => s.Verified);
    }
}

public sealed class KbEntryConfiguration : IEntityTypeConfiguration<KbEntry>
{
    public void Configure(EntityTypeBuilder<KbEntry> builder)
    {
        builder.ToTable("kb_entries");
        builder.HasKey(e => e.Id);

        builder.Property(e => e.Term).HasMaxLength(160).IsRequired();
        builder.Property(e => e.Title).HasMaxLength(240).IsRequired();
        builder.Property(e => e.Body).IsRequired();
        builder.Property(e => e.Kind).HasConversion<string>().HasMaxLength(20).IsRequired();
        builder.Property(e => e.Synonyms);

        // P0'da anahtar kelime eslesmesi yeter; embedding nullable kalir, P1'de doldurulur.
        // Kolon tipi (vector(1536) / real[]) DatabaseFeatures.UsePgvector'e gore
        // HastaKabulDbContext.OnModelCreating icinde belirlenir.
        builder.Property(e => e.Embedding).IsRequired(false);

        builder.HasIndex(e => e.Term);
    }
}

public sealed class AuditLogEntryConfiguration : IEntityTypeConfiguration<AuditLogEntry>
{
    public void Configure(EntityTypeBuilder<AuditLogEntry> builder)
    {
        builder.ToTable("audit_logs");
        builder.HasKey(e => e.Id);
        builder.Property(e => e.Action).HasMaxLength(80).IsRequired();
        builder.Property(e => e.Actor).HasMaxLength(80).IsRequired();
        builder.Property(e => e.EntityType).HasMaxLength(80).IsRequired();
        builder.Property(e => e.RequestId).HasMaxLength(80).IsRequired();
        builder.Property(e => e.Detail).HasMaxLength(400);

        builder.HasIndex(e => e.RequestId);
    }
}
