using System.Globalization;
using HastaKabul.Api.Infrastructure;
using HastaKabul.Application.Contracts.Slots;
using HastaKabul.Application.Services;
using HastaKabul.Domain.Common;
using Microsoft.AspNetCore.Mvc;

namespace HastaKabul.Api.Controllers;

/// <summary>
/// Uygun saat sorgusu. Ses orkestratoru cagirdigi icin X-Service-Key ister.
/// </summary>
[ApiController]
[Route("api/v1/slots")]
public sealed class SlotsController(SlotService slots) : ControllerBase
{
    [HttpGet]
    [RequireServiceKey]
    public Task<IReadOnlyList<SlotView>> GetAsync(
        [FromQuery] string clinic, [FromQuery] string date, CancellationToken ct)
    {
        if (!DateOnly.TryParseExact(date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var day))
            throw new DomainException(ErrorCodes.ValidationFailed, "date parametresi YYYY-AA-GG (ör. 2026-11-17) biçiminde olmalıdır.");

        return slots.GetAvailableAsync(clinic, day, ct);
    }
}
