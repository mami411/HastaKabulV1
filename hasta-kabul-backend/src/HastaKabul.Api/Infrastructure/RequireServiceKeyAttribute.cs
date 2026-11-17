using System.Security.Cryptography;
using System.Text;
using HastaKabul.Domain.Common;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;

namespace HastaKabul.Api.Infrastructure;

/// <summary>
/// Servisler arasi kimlik: AI servisleri API'yi X-Service-Key basligiyla cagirir.
/// API bu baslik olmadan ic endpoint'leri reddeder. Karsilastirma sabit zamanlidir.
/// </summary>
public sealed class RequireServiceKeyAttribute : Attribute, IAsyncActionFilter
{
    public const string HeaderName = "X-Service-Key";

    public async Task OnActionExecutionAsync(ActionExecutingContext context, ActionExecutionDelegate next)
    {
        var configuration = context.HttpContext.RequestServices.GetRequiredService<IConfiguration>();
        var expected = configuration["ServiceKey"];

        if (string.IsNullOrWhiteSpace(expected))
            throw new InvalidOperationException("ServiceKey tanimli degil (SERVICE_KEY).");

        var provided = context.HttpContext.Request.Headers[HeaderName].FirstOrDefault();

        if (!KeysMatch(provided, expected))
            throw new DomainException(ErrorCodes.Unauthorized, "Geçersiz servis anahtarı.");

        await next();
    }

    /// <summary>
    /// Sabit zamanli karsilastirma: iki deger once SHA-256 ile esit uzunluga indirilir, sonra
    /// <see cref="CryptographicOperations.FixedTimeEquals"/> ile karsilastirilir (uzunluk da sizmaz).
    /// </summary>
    public static bool KeysMatch(string? provided, string expected)
    {
        if (string.IsNullOrEmpty(provided))
            return false;

        var a = SHA256.HashData(Encoding.UTF8.GetBytes(provided));
        var b = SHA256.HashData(Encoding.UTF8.GetBytes(expected));
        return CryptographicOperations.FixedTimeEquals(a, b);
    }
}
