using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Abstractions.Security;
using HastaKabul.Domain.Common;
using Microsoft.AspNetCore.Mvc;

namespace HastaKabul.Api.Controllers;

public sealed record DoctorListItem(string DoctorId, string DisplayName, string ClinicName, string Room);

public sealed record DoctorSignInRequest(string DoctorId);

public sealed record DoctorSignInResponse(string Token, string DoctorId, string DisplayName);

/// <summary>
/// P0'da hekim girisi bir dropdown secimidir; secim kisa omurlu JWT uretir ve
/// ayni token hem REST hem WebSocket icin kullanilir.
/// </summary>
[ApiController]
[Route("api/v1/doctors")]
public sealed class DoctorsController(IDoctorRepository doctors, IDoctorTokenService tokens) : ControllerBase
{
    [HttpGet]
    public async Task<IReadOnlyList<DoctorListItem>> GetAllAsync(CancellationToken ct)
    {
        var all = await doctors.GetAllAsync(ct);

        return all
            .Select(d => new DoctorListItem(
                d.Id.ToString(),
                d.DisplayName,
                d.Clinic?.Name ?? string.Empty,
                d.Room))
            .ToList();
    }

    [HttpPost("sign-in")]
    public async Task<DoctorSignInResponse> SignInAsync([FromBody] DoctorSignInRequest request, CancellationToken ct)
    {
        if (!Guid.TryParse(request.DoctorId, out var doctorId))
            throw new DomainException(ErrorCodes.ValidationFailed, "Geçersiz doctorId.");

        var doctor = await doctors.GetByIdAsync(doctorId, ct)
            ?? throw new DomainException(ErrorCodes.NotFound, "Hekim bulunamadı.");

        return new DoctorSignInResponse(
            tokens.CreateToken(doctor.Id, doctor.DisplayName),
            doctor.Id.ToString(),
            doctor.DisplayName);
    }
}
