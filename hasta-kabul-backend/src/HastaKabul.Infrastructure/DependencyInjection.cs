using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Ai;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Abstractions.Security;
using HastaKabul.Application.Common;
using HastaKabul.Infrastructure.Ai;
using HastaKabul.Infrastructure.Persistence;
using HastaKabul.Infrastructure.Persistence.Repositories;
using HastaKabul.Infrastructure.Persistence.Seed;
using HastaKabul.Infrastructure.Security;
using HastaKabul.Infrastructure.Time;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;

namespace HastaKabul.Infrastructure;

public static class DependencyInjection
{
    /// <summary>
    /// Port'larin gerceklenmesi: veritabani, guvenlik ve AI istemcisi.
    /// Onion'un dis halkasi; Application yalnizca arayuzleri bilir.
    /// </summary>
    public static IServiceCollection AddInfrastructure(this IServiceCollection services, IConfiguration configuration)
    {
        services.Configure<TcProtectionOptions>(configuration.GetSection(TcProtectionOptions.SectionName));
        services.Configure<JwtOptions>(configuration.GetSection(JwtOptions.SectionName));
        services.Configure<SimplifyOptions>(configuration.GetSection(SimplifyOptions.SectionName));
        // Kurum (ozel hastane/klinik) yapilandirmasi; varsayilan MedTech Ozel Hastanesi (bkz. KURUM.md).
        services.Configure<KurumOptions>(configuration.GetSection(KurumOptions.SectionName));

        var connectionString = configuration.GetConnectionString("Default")
            ?? throw new InvalidOperationException("ConnectionStrings:Default tanimli degil (DATABASE_URL).");

        var features = configuration.GetSection(DatabaseFeatures.SectionName).Get<DatabaseFeatures>()
            ?? new DatabaseFeatures();
        services.AddSingleton(features);

        services.AddDbContext<HastaKabulDbContext>(options =>
            options
                .UseNpgsql(connectionString, npgsql =>
                {
                    // pgvector tip eslemesi yalnizca eklenti varken acilir.
                    if (features.UsePgvector)
                        npgsql.UseVector();
                })
                .UseSnakeCaseNamingConvention());

        services.AddScoped<IPatientRepository, PatientRepository>();
        services.AddScoped<IClinicRepository, ClinicRepository>();
        services.AddScoped<IDoctorRepository, DoctorRepository>();
        services.AddScoped<ISlotRepository, SlotRepository>();
        services.AddScoped<IAppointmentRepository, AppointmentRepository>();
        services.AddScoped<IPatientAppointmentReader, AppointmentRepository>();
        services.AddScoped<IClinicalEntryRepository, ClinicalEntryRepository>();
        services.AddScoped<ISummaryRepository, SummaryRepository>();
        services.AddScoped<IVoiceSessionRepository, VoiceSessionRepository>();
        services.AddScoped<IKbEntryRepository, KbEntryRepository>();
        services.AddScoped<IAuditLogRepository, AuditLogRepository>();
        services.AddScoped<IUnitOfWork, UnitOfWork>();

        services.AddSingleton<IClock, SystemClock>();
        services.AddSingleton<ITcHasher, HmacTcHasher>();
        services.AddSingleton<ITcProtector, AesTcProtector>();
        services.AddSingleton<IAccessCodeGenerator, AccessCodeGenerator>();
        services.AddSingleton<IDoctorTokenService, DoctorTokenService>();
        services.AddSingleton<IFallbackSummaryProvider, SeedFallbackSummaryProvider>();

        // Geri cagrisi gelmeyen PENDING ozetin zaman asimi (Simplify:PendingTimeoutSeconds, varsayilan 90).
        var pendingSeconds = configuration.GetValue(
            $"{SimplifyOptions.SectionName}:PendingTimeoutSeconds", SummaryOptions.DefaultPendingTimeoutSeconds);
        services.AddSingleton(new SummaryOptions
        {
            PendingTimeout = TimeSpan.FromSeconds(Math.Max(5, pendingSeconds))
        });

        services.AddHttpClient<ISimplifyClient, SimplifyHttpClient>((provider, client) =>
        {
            var options = configuration.GetSection(SimplifyOptions.SectionName).Get<SimplifyOptions>() ?? new SimplifyOptions();
            client.BaseAddress = new Uri(options.BaseUrl);
            client.Timeout = TimeSpan.FromSeconds(options.TimeoutSeconds);
        });

        services.AddScoped<DemoDataSeeder>();

        return services;
    }
}
