namespace HastaKabul.Application.Contracts.Voice;

/// <summary>
/// Ses oturumu durumu. Orkestrator TC istegini hic gormez; dogrulama sonucunu
/// yalnizca bu yanittan okur. attempts sayacini API tutar.
/// </summary>
public sealed record VoiceSessionView(
    string SessionId,
    bool Verified,
    int Attempts,
    int MaxAttempts,
    string Status);

/// <summary>Arayuz TC'yi dogrudan API'ye gonderir: POST /voice-sessions/{id}/verify.</summary>
public sealed record VerifyVoiceSessionRequest(string Tc);

public sealed record VoiceVerificationResponse(
    string SessionId,
    bool Verified,
    int Attempts,
    int MaxAttempts,
    string Status,
    bool Handoff);
