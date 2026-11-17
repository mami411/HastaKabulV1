using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using HastaKabul.Api.Infrastructure;
using HastaKabul.Api.Middleware;
using HastaKabul.Api.Realtime;
using HastaKabul.Application;
using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Realtime;
using HastaKabul.Application.Common;
using HastaKabul.Infrastructure;
using HastaKabul.Infrastructure.Persistence;
using HastaKabul.Infrastructure.Persistence.Seed;
using HastaKabul.Infrastructure.Security;
using HastaKabul.Domain.Common;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;

var builder = WebApplication.CreateBuilder(args);

// Makineye ozel ayarlar (yerel baglanti dizesi gibi); depoya girmez.
builder.Configuration.AddJsonFile("appsettings.Local.json", optional: true, reloadOnChange: true);

// Ortam degiskenleri .env/compose uzerinden gelir (JWT_SECRET, TC_PEPPER, SERVICE_KEY,
// SIMPLIFY_URL; compose bunlari Jwt__Secret gibi anahtarlara esler).
builder.Configuration.AddEnvironmentVariables();

builder.Services
    .AddControllers()
    .AddJsonOptions(options =>
    {
        // JSON camelCase, veritabani snake_case; donusum yalnizca bu katmanda.
        options.JsonSerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.CamelCase;
        options.JsonSerializerOptions.DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull;
    })
    .ConfigureApiBehaviorOptions(options =>
    {
        // Model dogrulama hatalari da ortak hata zarfinda doner (eskiden ProblemDetails).
        options.InvalidModelStateResponseFactory = ErrorResponseWriter.InvalidModelState;
        // 404/415 gibi istemci hatalari ProblemDetails govdesiyle degil, asagidaki
        // UseStatusCodePages ile ortak zarfta yazilir.
        options.SuppressMapClientErrors = true;
    });

builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();
builder.Services.AddSignalR();
builder.Services.AddHttpContextAccessor();

builder.Services.AddApplication();
builder.Services.AddInfrastructure(builder.Configuration);

builder.Services.AddScoped<IRequestContext, HttpRequestContext>();
builder.Services.AddScoped<IDoctorContext, DoctorContext>();
builder.Services.AddScoped<IRealtimeNotifier, SignalRNotifier>();

// Kiosk (5173), hekim paneli (5174), portal (5175) ve ses ekrani disinda kaynak kabul edilmez.
var allowedOrigins = builder.Configuration.GetSection("Cors:AllowedOrigins").Get<string[]>()
    ?? ["http://localhost:5173", "http://localhost:5174", "http://localhost:5175"];

builder.Services.AddCors(options =>
    options.AddDefaultPolicy(policy => policy
        .WithOrigins(allowedOrigins)
        .AllowAnyHeader()
        .AllowAnyMethod()
        .AllowCredentials()));

var jwtSection = builder.Configuration.GetSection(JwtOptions.SectionName);
var jwtOptions = jwtSection.Get<JwtOptions>() ?? new JwtOptions();

builder.Services
    .AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
    .AddJwtBearer(options =>
    {
        options.TokenValidationParameters = new TokenValidationParameters
        {
            ValidateIssuer = true,
            ValidateAudience = true,
            ValidateLifetime = true,
            ValidateIssuerSigningKey = true,
            ValidIssuer = jwtOptions.Issuer,
            ValidAudience = jwtOptions.Audience,
            IssuerSigningKey = new SymmetricSecurityKey(
                Encoding.UTF8.GetBytes(string.IsNullOrWhiteSpace(jwtOptions.Secret)
                    ? "gelistirme-icin-gecici-anahtar-degistirin-32"
                    : jwtOptions.Secret))
        };

        // WebSocket el sikismasinda token sorgu dizesinden okunur (SignalR kurali).
        options.Events = new JwtBearerEvents
        {
            OnMessageReceived = context =>
            {
                var accessToken = context.Request.Query["access_token"];
                if (!string.IsNullOrEmpty(accessToken) && context.HttpContext.Request.Path.StartsWithSegments("/hubs"))
                    context.Token = accessToken;

                return Task.CompletedTask;
            },

            // Token yok/gecersiz/suresi dolmus: govdesiz 401 yerine ortak hata zarfi.
            OnChallenge = async context =>
            {
                context.HandleResponse();
                var message = context.AuthenticateFailure switch
                {
                    SecurityTokenExpiredException => "Oturumunuzun süresi doldu; lütfen yeniden giriş yapın.",
                    not null => "Oturum anahtarı (token) geçersiz; lütfen yeniden giriş yapın.",
                    _ => ErrorResponseWriter.DescribeStatus(StatusCodes.Status401Unauthorized).Message
                };
                await ErrorResponseWriter.WriteAsync(
                    context.HttpContext, StatusCodes.Status401Unauthorized, ErrorCodes.Unauthorized, message);
                context.Response.Headers.WWWAuthenticate = "Bearer";
            },

            OnForbidden = context => ErrorResponseWriter.WriteAsync(
                context.HttpContext,
                StatusCodes.Status403Forbidden,
                ErrorCodes.Forbidden,
                ErrorResponseWriter.DescribeStatus(StatusCodes.Status403Forbidden).Message)
        };
    });

builder.Services.AddAuthorization();

// Demo: backend gun boyu acik kalsa da onumuzdeki gunlerin slotlari saatte bir tamamlanir.
if (builder.Configuration.GetValue("Demo:Seed", true))
    builder.Services.AddHostedService<DemoSlotTopUpService>();

var app = builder.Build();

var kurum = builder.Configuration.GetSection(KurumOptions.SectionName).Get<KurumOptions>() ?? new KurumOptions();
app.Logger.LogInformation("Kurum: {Kurum} ({KisaAd}); cagri merkezi {CagriMerkezi}.",
    kurum.TamAd, kurum.KisaAd, kurum.CagriMerkezi);

// Sira onemli: once log satiri ve requestId, sonra hata zarfi.
app.UseMiddleware<RequestLoggingMiddleware>();
app.UseMiddleware<ErrorHandlingMiddleware>();

// Govdesiz hata yanitlari (bilinmeyen yol 404, yanlis yontem 405, icerik turu 415) ortak zarfta.
app.UseStatusCodePages(async context =>
{
    var http = context.HttpContext;
    if (http.Response.HasStarted || http.Response.ContentLength > 0 || !string.IsNullOrEmpty(http.Response.ContentType))
        return;

    var (code, message) = ErrorResponseWriter.DescribeStatus(http.Response.StatusCode);
    await ErrorResponseWriter.WriteAsync(http, http.Response.StatusCode, code, message);
});

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

app.UseCors();
app.UseAuthentication();
app.UseAuthorization();

app.MapControllers();
app.MapHub<DoctorHub>("/hubs/doctor");
app.MapGet("/health", () => Results.Json(new { status = "ok" }));

// Demo akisi: migration'lar uygulanir, istenirse veri sifirlanip yeniden uretilir.
await using (var scope = app.Services.CreateAsyncScope())
{
    var configuration = scope.ServiceProvider.GetRequiredService<IConfiguration>();

    var features = scope.ServiceProvider.GetRequiredService<DatabaseFeatures>();

    if (features.AutoMigrate)
    {
        var db = scope.ServiceProvider.GetRequiredService<HastaKabulDbContext>();

        if (features.UsePgvector)
        {
            await db.Database.MigrateAsync();
        }
        else
        {
            // pgvector'u olmayan yerel makine: migration'lar CREATE EXTENSION vector
            // icerdigi icin sema dogrudan modelden kurulur. Yalnizca deneme yolu.
            await db.Database.EnsureCreatedAsync();
        }
    }

    var reset = args.Contains("--reset-demo") || configuration.GetValue("Demo:Reset", false);
    if (configuration.GetValue("Demo:Seed", true))
    {
        var seeder = scope.ServiceProvider.GetRequiredService<DemoDataSeeder>();

        // Dun (ya da daha once) yuklenmis demo verisi bugun icin bos kalir (kiosk "randevunuz yok",
        // panel bos). Acilista bunu fark edip veriyi bugune gore yeniden uretir (Demo:ResetIfStale).
        if (!reset && configuration.GetValue("Demo:ResetIfStale", true) && await seeder.IsStaleAsync(DateTime.UtcNow))
        {
            app.Logger.LogWarning("Demo verisi bugune ait degil; acilista sifirlaniyor (Demo:ResetIfStale).");
            reset = true;
        }

        await seeder.SeedAsync(reset);
    }
}

// Yalnizca seed amaciyla calistirildiysa servis ayaga kalkmadan cikilir.
if (args.Contains("--seed-only"))
    return;

app.Run();

/// <summary>Entegrasyon testlerinin WebApplicationFactory ile erisebilmesi icin.</summary>
public partial class Program;

internal static class JsonDefaults
{
    public static readonly JsonSerializerOptions Web = new(JsonSerializerDefaults.Web);
}
