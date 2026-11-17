using HastaKabul.Infrastructure.Persistence.Seed;

namespace HastaKabul.Api.Infrastructure;

/// <summary>
/// Demo verisi acikken saatte bir onumuzdeki gunlerin slotlarini tamamlar (idempotent). Backend gun
/// boyu acik kalsa da "yarin" ve sonraki gunler icin her poliklinikte sabah ve ogleden sonra slot olur.
/// Veri silmez; yalniz eksik slot ekler.
/// </summary>
public sealed class DemoSlotTopUpService(IServiceScopeFactory scopes, ILogger<DemoSlotTopUpService> logger)
    : BackgroundService
{
    private static readonly TimeSpan Interval = TimeSpan.FromHours(1);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(Interval);
        while (await timer.WaitForNextTickAsync(stoppingToken))
        {
            try
            {
                await using var scope = scopes.CreateAsyncScope();
                var seeder = scope.ServiceProvider.GetRequiredService<DemoDataSeeder>();
                await seeder.EnsureUpcomingSlotsAsync(DemoDataSeeder.UpcomingSlotDays, stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                logger.LogWarning(ex, "Demo slotlari tamamlanamadi; bir sonraki turda tekrar denenecek.");
            }
        }
    }
}
