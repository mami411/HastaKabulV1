using HastaKabul.Domain.Common;

namespace HastaKabul.Api.Middleware;

/// <summary>
/// Ortak hata formati: { "error": { "code", "message", "requestId" } }.
/// Arayuz hatayi bu koda bakarak kullaniciya anlamli gosterir.
/// </summary>
public sealed class ErrorHandlingMiddleware(RequestDelegate next, ILogger<ErrorHandlingMiddleware> logger)
{
    public async Task InvokeAsync(HttpContext context)
    {
        try
        {
            await next(context);
        }
        catch (DomainException ex)
        {
            if (ex.Code == ErrorCodes.Internal)
                logger.LogError(ex, "Ic hata. requestId={RequestId}", ErrorResponseWriter.ResolveRequestId(context));

            await ErrorResponseWriter.WriteAsync(context, ErrorResponseWriter.MapStatusCode(ex.Code), ex.Code, ex.Message);
        }
        catch (BadHttpRequestException ex)
        {
            // Kestrel: govde cok buyuk, eksik/bozuk istek. Istemci hatasidir; 500 degil.
            var (_, message) = ErrorResponseWriter.DescribeStatus(ex.StatusCode);
            await ErrorResponseWriter.WriteAsync(context, ex.StatusCode, ErrorCodes.ValidationFailed, message);
        }
        catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested)
        {
            // Istemci baglantiyi kapatti; yazilacak yanit yok, hata logu gereksiz.
            if (!context.Response.HasStarted)
                context.Response.StatusCode = 499;
        }
        catch (Exception ex)
        {
            // Istisna govdesi loglanir; istek govdesi (TC icerebilir) asla loglanmaz.
            logger.LogError(ex, "Beklenmeyen hata. requestId={RequestId}", ErrorResponseWriter.ResolveRequestId(context));
            var (code, message) = ErrorResponseWriter.DescribeStatus(StatusCodes.Status500InternalServerError);
            await ErrorResponseWriter.WriteAsync(context, StatusCodes.Status500InternalServerError, code, message);
        }
    }
}
