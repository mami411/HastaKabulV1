using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text;
using HastaKabul.Application.Abstractions.Security;
using Microsoft.Extensions.Options;
using Microsoft.IdentityModel.Tokens;

namespace HastaKabul.Infrastructure.Security;

public sealed class JwtOptions
{
    public const string SectionName = "Jwt";

    public string Secret { get; set; } = string.Empty;
    public string Issuer { get; set; } = "hasta-kabul-api";
    public string Audience { get; set; } = "hasta-kabul";

    /// <summary>Demo icin kisa omurlu token.</summary>
    public int LifetimeMinutes { get; set; } = 480;
}

/// <summary>
/// Hekim secimi kisa omurlu JWT uretir; ayni token hem REST hem WebSocket icin kullanilir.
/// Token icinde yalnizca doctorId ve gosterim adi bulunur.
/// </summary>
public sealed class DoctorTokenService(IOptions<JwtOptions> options) : IDoctorTokenService
{
    public const string DoctorIdClaim = "doctorId";

    public string CreateToken(Guid doctorId, string displayName)
    {
        var settings = options.Value;
        if (string.IsNullOrWhiteSpace(settings.Secret))
            throw new InvalidOperationException("Jwt:Secret tanimli degil (JWT_SECRET).");

        var key = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(settings.Secret));
        var credentials = new SigningCredentials(key, SecurityAlgorithms.HmacSha256);

        var token = new JwtSecurityToken(
            issuer: settings.Issuer,
            audience: settings.Audience,
            claims:
            [
                new Claim(JwtRegisteredClaimNames.Sub, doctorId.ToString()),
                new Claim(DoctorIdClaim, doctorId.ToString()),
                new Claim(ClaimTypes.Name, displayName)
            ],
            expires: DateTime.UtcNow.AddMinutes(settings.LifetimeMinutes),
            signingCredentials: credentials);

        return new JwtSecurityTokenHandler().WriteToken(token);
    }
}
