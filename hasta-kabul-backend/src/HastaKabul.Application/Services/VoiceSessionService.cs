using HastaKabul.Application.Abstractions;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Abstractions.Security;
using HastaKabul.Application.Common;
using HastaKabul.Application.Contracts.Voice;
using HastaKabul.Domain.Audit;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Voice;

namespace HastaKabul.Application.Services;

/// <summary>
/// Ses oturumu yasam dongusu. TC el sikismasi:
/// 1) orkestrator ASK_TC doner, 2) arayuz TC'yi dogrudan buraya gonderir,
/// 3) API oturumu verified + patientRef yapar, 4) orkestrator sonucu GET ile okur,
/// 5) basarisizlikta en fazla 2 tekrar, sonra HANDOFF.
/// </summary>
public sealed class VoiceSessionService(
    IVoiceSessionRepository sessions,
    IPatientRepository patients,
    IAuditLogRepository auditLog,
    IUnitOfWork unitOfWork,
    ITcHasher tcHasher,
    IRequestContext requestContext,
    IClock clock)
{
    public const string InvalidTcFormatMessage =
        "TC kimlik numarası biçimi geçersiz; 11 haneli geçerli bir numara girin. Bu deneme hakkınızdan düşülmedi.";

    /// <summary>Kiosk/ses ekrani oturumu; demo icin 15 dakika yeter.</summary>
    private static readonly TimeSpan Lifetime = TimeSpan.FromMinutes(15);

    public async Task<VoiceSessionView> CreateAsync(CancellationToken ct = default)
    {
        var session = new VoiceSession(Lifetime);
        await sessions.AddAsync(session, ct);
        await unitOfWork.SaveChangesAsync(ct);

        return ToView(session);
    }

    public async Task<VoiceSessionView> GetAsync(Guid sessionId, CancellationToken ct = default)
    {
        var session = await LoadAsync(sessionId, ct);
        return ToView(session);
    }

    /// <summary>
    /// TC dogrulama. Numara bu metottan disariya hic cikmaz; yanitta yalnizca
    /// dogrulandi mi, kacinci deneme ve HANDOFF gerekip gerekmedigi doner.
    /// </summary>
    public async Task<VoiceVerificationResponse> VerifyAsync(
        Guid sessionId, VerifyVoiceSessionRequest request, CancellationToken ct = default)
    {
        var session = await LoadAsync(sessionId, ct);

        // Bicimi gecersiz numara (11 hane degil / kontrol hanesi tutmuyor) yazim hatasidir: deneme
        // hakkindan dusmez, 400 VALIDATION_FAILED doner. Yalniz bicimi gecerli ama kayitli olmayan
        // numara deneme sayilir; basarili dogrulama da deneme sayilmaz.
        session.EnsureAwaitingVerification();
        if (!TcKimlikNo.IsWellFormed(request.Tc))
            throw new DomainException(ErrorCodes.ValidationFailed, InvalidTcFormatMessage);

        var patient = await patients.FindByTcHashAsync(tcHasher.Hash(request.Tc), ct);
        if (patient is null)
        {
            session.RegisterFailedAttempt();
            await PersistAsync(session, "voiceSession.verifyFailed", ct);
            return ToVerificationResponse(session);
        }

        session.MarkVerified(patient.Id, patient.PatientRef);
        await PersistAsync(session, "voiceSession.verified", ct);

        return ToVerificationResponse(session);
    }

    /// <summary>
    /// Cikis: tarayici oturumu kapatir (POST /voice-sessions/{id}/end). Bundan sonra hasta uclari ve
    /// orkestrator ayni oturum kimligiyle veri alamaz (409 VOICE_SESSION_NOT_VERIFIED / EXPIRED).
    /// Tekrar cagri ya da zaten kapali oturum zararsizdir; yanit govdesizdir.
    /// </summary>
    public async Task EndAsync(Guid sessionId, CancellationToken ct = default)
    {
        var session = await sessions.GetByIdAsync(sessionId, ct)
            ?? throw new DomainException(ErrorCodes.VoiceSessionNotFound, "Ses oturumu bulunamadı.");

        if (session.End(clock.UtcNow))
            await PersistAsync(session, "voiceSession.ended", ct);
    }

    private async Task<VoiceSession> LoadAsync(Guid sessionId, CancellationToken ct)
    {
        var session = await sessions.GetByIdAsync(sessionId, ct)
            ?? throw new DomainException(ErrorCodes.VoiceSessionNotFound, "Ses oturumu bulunamadı.");

        // ACTIVE ve VERIFIED oturumlar omur dolunca EXPIRED olur.
        if (session.ExpireIfElapsed(clock.UtcNow))
            await unitOfWork.SaveChangesAsync(ct);

        return session;
    }

    private async Task PersistAsync(VoiceSession session, string action, CancellationToken ct)
    {
        await auditLog.AddAsync(
            new AuditLogEntry(action, "voice", nameof(VoiceSession), session.Id, requestContext.RequestId),
            ct);

        await unitOfWork.SaveChangesAsync(ct);
    }

    private static VoiceSessionView ToView(VoiceSession session) => new(
        session.Id.ToString(),
        session.Verified,
        session.Attempts,
        VoiceSession.MaxAttempts,
        StatusNames.ToWire(session.Status));

    private static VoiceVerificationResponse ToVerificationResponse(VoiceSession session) => new(
        session.Id.ToString(),
        session.Verified,
        session.Attempts,
        VoiceSession.MaxAttempts,
        StatusNames.ToWire(session.Status),
        session.Status is VoiceSessionStatus.HandedOff);
}
