using System.Text.Encodings.Web;
using System.Text.Json;
using HastaKabul.Api.Infrastructure;
using HastaKabul.Domain.Common;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.ModelBinding;

namespace HastaKabul.Api.Middleware;

/// <summary>
/// Ortak hata zarfi { "error": { "code", "message", "requestId" } } tek yerden yazilir: is kurali
/// hatalari (middleware), model dogrulama (eski ProblemDetails), JWT 401/403 ve govdesiz durum
/// kodlari (404/405/415) ayni bicimde doner. Turkce karakterler kacislanmadan yazilir.
/// </summary>
public static class ErrorResponseWriter
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
    };

    public static int MapStatusCode(string code) => code switch
    {
        ErrorCodes.ValidationFailed => StatusCodes.Status400BadRequest,
        ErrorCodes.Unauthorized => StatusCodes.Status401Unauthorized,
        ErrorCodes.Forbidden => StatusCodes.Status403Forbidden,
        ErrorCodes.TcNotFound => StatusCodes.Status404NotFound,
        ErrorCodes.NotFound => StatusCodes.Status404NotFound,
        ErrorCodes.NoAppointmentToday => StatusCodes.Status404NotFound,
        ErrorCodes.VoiceSessionNotFound => StatusCodes.Status404NotFound,
        ErrorCodes.SlotTaken => StatusCodes.Status409Conflict,
        ErrorCodes.SlotInPast => StatusCodes.Status409Conflict,
        ErrorCodes.AppointmentConflict => StatusCodes.Status409Conflict,
        ErrorCodes.InvalidStatusTransition => StatusCodes.Status409Conflict,
        ErrorCodes.VoiceSessionNotVerified => StatusCodes.Status409Conflict,
        ErrorCodes.VoiceSessionExhausted => StatusCodes.Status409Conflict,
        ErrorCodes.SummaryNotApproved => StatusCodes.Status409Conflict,
        ErrorCodes.LlmUnavailable => StatusCodes.Status503ServiceUnavailable,
        ErrorCodes.Internal => StatusCodes.Status500InternalServerError,
        _ => StatusCodes.Status400BadRequest
    };

    /// <summary>Govdesiz hata durum kodlari (JWT challenge, bilinmeyen yol, yanlis yontem/icerik turu).</summary>
    public static (string Code, string Message) DescribeStatus(int statusCode) => statusCode switch
    {
        StatusCodes.Status400BadRequest => (ErrorCodes.ValidationFailed, "İstek geçersiz."),
        StatusCodes.Status401Unauthorized => (ErrorCodes.Unauthorized,
            "Bu işlem için geçerli bir hekim oturumu gerekir; lütfen giriş yapın."),
        StatusCodes.Status403Forbidden => (ErrorCodes.Forbidden, "Bu işlem için yetkiniz yok."),
        StatusCodes.Status404NotFound => (ErrorCodes.NotFound, "İstenen adres bulunamadı."),
        StatusCodes.Status405MethodNotAllowed => (ErrorCodes.ValidationFailed,
            "Bu adres bu HTTP yöntemini desteklemiyor."),
        StatusCodes.Status413PayloadTooLarge => (ErrorCodes.ValidationFailed, "İstek gövdesi çok büyük."),
        StatusCodes.Status415UnsupportedMediaType => (ErrorCodes.ValidationFailed,
            "İçerik türü application/json olmalıdır."),
        >= 500 => (ErrorCodes.Internal, "Beklenmeyen bir hata oluştu. Lütfen tekrar deneyin."),
        _ => (ErrorCodes.ValidationFailed, "İstek işlenemedi.")
    };

    public static object Envelope(HttpContext context, string code, string message) => new
    {
        error = new { code, message, requestId = ResolveRequestId(context) }
    };

    public static string Serialize(HttpContext context, string code, string message)
        => JsonSerializer.Serialize(Envelope(context, code, message), JsonOptions);

    public static async Task WriteAsync(HttpContext context, int statusCode, string code, string message)
    {
        if (context.Response.HasStarted)
            return;

        context.Response.Clear();
        // Clear() RequestLoggingMiddleware'in koydugu X-Request-Id basligini da siler; hata
        // yanitlarinda da istek izlenebilsin diye geri yazilir.
        context.Response.Headers[HttpRequestContext.HeaderName] = ResolveRequestId(context);
        context.Response.StatusCode = statusCode;
        context.Response.ContentType = "application/json; charset=utf-8";

        await context.Response.WriteAsync(Serialize(context, code, message));
    }

    /// <summary>
    /// [ApiController] model dogrulamasi (eskiden ProblemDetails): 400 VALIDATION_FAILED zarfi.
    /// Mesaj alan adlarini (camelCase) soyler; cerceve mesajlari (Ingilizce) disari verilmez.
    /// </summary>
    public static IActionResult InvalidModelState(ActionContext actionContext)
    {
        var message = DescribeModelState(actionContext.ModelState);
        return new ContentResult
        {
            StatusCode = StatusCodes.Status400BadRequest,
            ContentType = "application/json; charset=utf-8",
            Content = Serialize(actionContext.HttpContext, ErrorCodes.ValidationFailed, message)
        };
    }

    public static string DescribeModelState(ModelStateDictionary modelState)
    {
        var keys = modelState
            .Where(kv => kv.Value is { Errors.Count: > 0 })
            .Select(kv => kv.Key)
            .ToList();

        if (keys.Any(k => k == "$" || k.StartsWith("$.", StringComparison.Ordinal) || k.StartsWith("$[", StringComparison.Ordinal)))
            return "İstek gövdesi geçerli bir JSON değil.";

        var fields = keys
            .Where(k => k.Length > 0 && k != "request")
            .Select(k => char.ToLowerInvariant(k[0]) + k[1..])
            .Distinct()
            .ToList();

        return fields.Count == 0
            ? "İstek gövdesi eksik ya da okunamadı."
            : $"Eksik ya da hatalı alan: {string.Join(", ", fields)}.";
    }

    public static string ResolveRequestId(HttpContext context) =>
        context.Items.TryGetValue(HttpRequestContext.HeaderName, out var value) && value is string id
            ? id
            : context.TraceIdentifier;
}
