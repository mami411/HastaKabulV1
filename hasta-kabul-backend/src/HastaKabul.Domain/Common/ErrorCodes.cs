namespace HastaKabul.Domain.Common;

/// <summary>
/// Sprint 0'da dondurulan ortak hata kodu listesi. Butun servisler ayni kodlari kullanir;
/// shared/openapi.yaml ile bire bir ayni kalmalidir.
/// </summary>
public static class ErrorCodes
{
    // Cekirdek akis
    public const string TcNotFound = "TC_NOT_FOUND";
    public const string NoAppointmentToday = "NO_APPOINTMENT_TODAY";
    public const string SlotTaken = "SLOT_TAKEN";
    public const string SummaryNotApproved = "SUMMARY_NOT_APPROVED";

    // Randevu kurallari (demo sertlestirmesi, 2026-11-16)
    /// <summary>Hastanin ayni zaman araliginda ya da ayni gun ayni poliklinikte etkin randevusu var (409).</summary>
    public const string AppointmentConflict = "APPOINTMENT_CONFLICT";
    /// <summary>Secilen slotun baslangici gecmiste kaldi (409); orkestrator guncel saatleri yeniden sunar.</summary>
    public const string SlotInPast = "SLOT_IN_PAST";

    // Durum makinesi
    public const string InvalidStatusTransition = "INVALID_STATUS_TRANSITION";

    // Ses kanali / oturum
    public const string VoiceSessionNotFound = "VOICE_SESSION_NOT_FOUND";
    public const string VoiceSessionNotVerified = "VOICE_SESSION_NOT_VERIFIED";
    public const string VoiceSessionExhausted = "VOICE_SESSION_EXHAUSTED";

    // Yapay zeka katmani
    public const string LlmUnavailable = "LLM_UNAVAILABLE";
    public const string SimplifyFailed = "SIMPLIFY_FAILED";
    public const string IntentUnclear = "INTENT_UNCLEAR";
    public const string SttFailed = "STT_FAILED";
    public const string TtsFailed = "TTS_FAILED";

    // Genel
    public const string ValidationFailed = "VALIDATION_FAILED";
    public const string NotFound = "NOT_FOUND";
    public const string Unauthorized = "UNAUTHORIZED";
    public const string Forbidden = "FORBIDDEN";
    public const string Internal = "INTERNAL_ERROR";
}
