using System.Diagnostics;
using System.Text.Json;
using HastaKabul.Api.Infrastructure;

namespace HastaKabul.Api.Middleware;

/// <summary>
/// JSON satir log: service, requestId, path, status, ms.
/// Kural tek: TC hic loglanmaz, maskeli bile. Bu yuzden istek govdesi ve sorgu
/// dizesi log satirina girmez.
/// </summary>
public sealed class RequestLoggingMiddleware(RequestDelegate next, ILogger<RequestLoggingMiddleware> logger)
{
    private const string ServiceName = "api";

    public const int MaxRequestIdLength = 64;

    /// <summary>Yalniz [A-Za-z0-9._:-] tutulur, en fazla 64 karakter (ai servisleriyle ayni kural).</summary>
    public static string SanitizeRequestId(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return string.Empty;

        return new string(value
            .Where(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_' or '.' or ':')
            .Take(MaxRequestIdLength)
            .ToArray());
    }

    public async Task InvokeAsync(HttpContext context)
    {
        // X-Request-Id yoksa uret ve yanitta geri ver; tum servisler ayni kimligi tasir.
        // Gelen deger temizlenir ve kirpilir: audit/ozet tablolarindaki request_id 80 karakterdir;
        // uzun ya da garip bir baslik veritabani hatasina (500) ve log enjeksiyonuna yol acmasin.
        var requestId = SanitizeRequestId(context.Request.Headers[HttpRequestContext.HeaderName].FirstOrDefault());
        if (string.IsNullOrEmpty(requestId))
            requestId = Guid.NewGuid().ToString("N");

        context.Items[HttpRequestContext.HeaderName] = requestId;
        context.Response.Headers[HttpRequestContext.HeaderName] = requestId;

        var stopwatch = Stopwatch.StartNew();
        try
        {
            await next(context);
        }
        finally
        {
            stopwatch.Stop();

            var line = JsonSerializer.Serialize(new
            {
                service = ServiceName,
                requestId,
                path = context.Request.Path.Value,
                method = context.Request.Method,
                status = context.Response.StatusCode,
                ms = stopwatch.ElapsedMilliseconds
            });

            logger.LogInformation("{LogLine}", line);
        }
    }
}
