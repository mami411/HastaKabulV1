using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Slots;
using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Clinics;
using HastaKabul.Domain.Common;

namespace HastaKabul.Application.Services;

/// <summary>
/// Uygun saat sorgusu: ses asistani (orkestrator goreli tarihi kendisi cozup date=YYYY-MM-DD gonderir)
/// ve hasta uygulamasi ayni kurali kullanir. Donen saatler UTC'dir; baslangici gecmis slot sunulmaz.
/// </summary>
public sealed class SlotService(IClinicRepository clinics, ISlotRepository slots, IClock clock)
{
    public async Task<IReadOnlyList<SlotView>> GetAvailableAsync(
        string clinicNameOrCode, DateOnly localDay, CancellationToken ct = default)
    {
        var clinic = await ResolveClinicAsync(clinicNameOrCode, ct);
        var available = await GetAvailableSlotsAsync(clinic, localDay, 1, ct);

        return available
            .Select(s => new SlotView(
                s.Id.ToString(),
                s.StartTimeUtc,
                s.Doctor?.DisplayName ?? string.Empty,
                s.DoctorId.ToString()))
            .ToList();
    }

    /// <summary>Kod, ad ya da es anlamliyla poliklinigi bulur; yoksa 404 NOT_FOUND.</summary>
    public async Task<Clinic> ResolveClinicAsync(string? clinicNameOrCode, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(clinicNameOrCode))
            throw new DomainException(ErrorCodes.ValidationFailed, "clinic parametresi zorunludur.");

        return await clinics.ResolveAsync(clinicNameOrCode, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, $"Poliklinik bulunamadı: '{clinicNameOrCode}'.");
    }

    /// <summary>
    /// <paramref name="firstLocalDay"/>'den baslayan <paramref name="days"/> Istanbul gunundeki bos ve
    /// baslamamis slotlar, zamana gore.
    /// </summary>
    public async Task<IReadOnlyList<AppointmentSlot>> GetAvailableSlotsAsync(
        Clinic clinic, DateOnly firstLocalDay, int days, CancellationToken ct = default)
    {
        var (fromUtc, _) = IstanbulTime.DayRangeUtc(firstLocalDay);
        var (_, toUtc) = IstanbulTime.DayRangeUtc(firstLocalDay.AddDays(Math.Max(1, days) - 1));
        var now = clock.UtcNow;
        if (fromUtc < now)
            fromUtc = now;
        if (fromUtc >= toUtc)
            return [];

        var available = await slots.GetAvailableAsync(clinic.Id, fromUtc, toUtc, ct);

        return available
            .Where(s => !s.HasStarted(now))
            .OrderBy(s => s.StartTimeUtc)
            .ToList();
    }
}
