using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Voice;

namespace HastaKabul.Application.Services;

/// <summary>
/// Hastaya ait veriye (gecmis muayene, randevular, hasta uygulamasindan randevu) acilan kapinin tek
/// kurali: oturum bulunmali, dogrulanmis (VERIFIED) ya da randevu sonrasi (COMPLETED) olmali ve suresi
/// dolmamis olmali. Hasta, istekte tasinan bir kimlikle degil, oturumdan bulunur.
/// </summary>
internal static class PatientSessionGuard
{
    public const string ExpiredMessage = "Ses oturumunun süresi doldu; kimlik yeniden doğrulanmalı.";

    /// <returns>Oturum ve eslesen hastanin kimligi.</returns>
    public static async Task<(VoiceSession Session, Guid PatientId)> RequireAsync(
        IVoiceSessionRepository sessions,
        IUnitOfWork unitOfWork,
        Guid sessionId,
        DateTime nowUtc,
        string notVerifiedMessage,
        CancellationToken ct)
    {
        var session = await sessions.GetByIdAsync(sessionId, ct)
            ?? throw new DomainException(ErrorCodes.VoiceSessionNotFound, "Ses oturumu bulunamadı.");

        if (session.ExpireIfElapsed(nowUtc))
            await unitOfWork.SaveChangesAsync(ct);

        // Randevu alindiktan sonra (COMPLETED) da okunabilir; ama omur her durumda gecerlidir.
        if (session.Status is VoiceSessionStatus.Expired
            || (session.Status is VoiceSessionStatus.Completed && session.IsExpired(nowUtc)))
            throw new DomainException(ErrorCodes.VoiceSessionNotVerified, ExpiredMessage);

        if (session.Status is not (VoiceSessionStatus.Verified or VoiceSessionStatus.Completed)
            || session.PatientId is null)
            throw new DomainException(ErrorCodes.VoiceSessionNotVerified, notVerifiedMessage);

        return (session, session.PatientId.Value);
    }
}
