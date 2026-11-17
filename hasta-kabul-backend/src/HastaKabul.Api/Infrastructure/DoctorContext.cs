using HastaKabul.Application.Abstractions;
using HastaKabul.Domain.Common;

namespace HastaKabul.Api.Infrastructure;

/// <summary>
/// Token'daki hekim kimligini verir. P0'da panelde hekim secimi bir dropdown ile
/// yapilir, ama cagrilarin sahibi her zaman token'dan okunur; sorgudaki doctorId
/// yalnizca token yoksa (demo modu) kullanilir.
/// </summary>
public interface IDoctorContext
{
    Guid Require(Guid? fallback);
}

public sealed class DoctorContext(IRequestContext requestContext) : IDoctorContext
{
    public Guid Require(Guid? fallback)
    {
        var doctorId = requestContext.DoctorId ?? fallback;

        return doctorId
            ?? throw new DomainException(ErrorCodes.Unauthorized, "Hekim kimliği bulunamadı; lütfen yeniden giriş yapın.");
    }
}
