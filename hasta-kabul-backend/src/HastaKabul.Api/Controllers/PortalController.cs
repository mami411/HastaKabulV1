using HastaKabul.Application.Contracts.Portal;
using HastaKabul.Application.Services;
using Microsoft.AspNetCore.Mvc;

namespace HastaKabul.Api.Controllers;

/// <summary>
/// Hasta portali. Demo girisi randevu basina 6 haneli erisim kodudur ve
/// yalnizca hekim onayli ozet doner.
/// </summary>
[ApiController]
[Route("api/v1/portal")]
public sealed class PortalController(PortalService portal) : ControllerBase
{
    [HttpPost("summary")]
    public Task<PortalSummaryResponse> GetSummaryAsync(
        [FromBody] PortalAccessRequest request, CancellationToken ct)
        => portal.GetByAccessCodeAsync(request, ct);
}
