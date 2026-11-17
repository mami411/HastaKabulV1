using HastaKabul.Application.Abstractions;
using HastaKabul.Infrastructure.Security;

namespace HastaKabul.Api.Infrastructure;

/// <summary>
/// Her istekte tasinan X-Request-Id; yoksa uretilir. Hekim kimligi JWT'den okunur.
/// </summary>
public sealed class HttpRequestContext(IHttpContextAccessor accessor) : IRequestContext
{
    public const string HeaderName = "X-Request-Id";

    public string RequestId
    {
        get
        {
            var context = accessor.HttpContext;
            if (context is null)
                return Guid.NewGuid().ToString("N");

            if (context.Items.TryGetValue(HeaderName, out var existing) && existing is string value)
                return value;

            var header = context.Request.Headers[HeaderName].FirstOrDefault();
            var requestId = string.IsNullOrWhiteSpace(header) ? Guid.NewGuid().ToString("N") : header!;
            context.Items[HeaderName] = requestId;
            return requestId;
        }
    }

    public Guid? DoctorId
    {
        get
        {
            var claim = accessor.HttpContext?.User.FindFirst(DoctorTokenService.DoctorIdClaim)?.Value;
            return Guid.TryParse(claim, out var id) ? id : null;
        }
    }
}
