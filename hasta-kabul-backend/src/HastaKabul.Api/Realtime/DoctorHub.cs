using HastaKabul.Infrastructure.Security;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;

namespace HastaKabul.Api.Realtime;

/// <summary>
/// Hekim bazli kanal. Baglanti JWT ile dogrulanir ve istemci yalnizca kendi
/// doctor:{doctorId} grubuna eklenir; boylece herkes herkesin olayini almaz.
/// </summary>
[Authorize]
public sealed class DoctorHub : Hub
{
    public static string GroupName(Guid doctorId) => $"doctor:{doctorId}";

    public override async Task OnConnectedAsync()
    {
        var doctorId = Context.User?.FindFirst(DoctorTokenService.DoctorIdClaim)?.Value;

        if (Guid.TryParse(doctorId, out var id))
            await Groups.AddToGroupAsync(Context.ConnectionId, GroupName(id));

        await base.OnConnectedAsync();
    }
}
