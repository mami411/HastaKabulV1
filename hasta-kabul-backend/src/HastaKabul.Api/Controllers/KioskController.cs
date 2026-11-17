using HastaKabul.Application.Contracts.Kiosk;
using HastaKabul.Application.Services;
using Microsoft.AspNetCore.Mvc;

namespace HastaKabul.Api.Controllers;

/// <summary>
/// Kiosk uc noktalari. TC yalnizca buraya gelir ve buradan ileri gitmez.
/// </summary>
[ApiController]
[Route("api/v1/kiosk")]
public sealed class KioskController(KioskService kiosk) : ControllerBase
{
    /// <summary>Tus takimindan girilen TC'yi dogrular ve karsilama bilgisini doner.</summary>
    [HttpPost("verify")]
    public Task<KioskVerificationResponse> VerifyAsync(
        [FromBody] VerifyTcRequest request, CancellationToken ct)
        => kiosk.VerifyAsync(request, ct);

    /// <summary>"Geldim" onayi; hekim panelindeki satir aninda guncellenir.</summary>
    [HttpPost("check-in")]
    public Task<CheckInResponse> CheckInAsync(
        [FromBody] CheckInRequest request, CancellationToken ct)
        => kiosk.CheckInAsync(request, ct);
}
