using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;

namespace HastaKabul.Domain.Voice;

/// <summary>
/// Sesli randevu oturumu. TC el sikismasi: arayuz TC'yi dogrudan API'ye gonderir,
/// orkestrator bu istegi hic gormez; sonucu GET /voice-sessions/{id} ile okur.
/// Deneme sayacini API tutar, HANDOFF kararini orkestrator verir.
/// </summary>
public class VoiceSession : Entity
{
    /// <summary>Basarisizlikta en fazla 2 tekrar, yani toplam 3 deneme.</summary>
    public const int MaxAttempts = 3;

    private VoiceSession() { }

    public VoiceSession(TimeSpan lifetime)
    {
        Status = VoiceSessionStatus.Active;
        ExpiresAtUtc = CreatedAtUtc.Add(lifetime);
    }

    public VoiceSessionStatus Status { get; private set; }
    public bool Verified => Status is VoiceSessionStatus.Verified or VoiceSessionStatus.Completed;

    /// <summary>Dogrulama sonrasi eslesen hasta. Oturum uzerinden okunur, orkestratore verilmez.</summary>
    public Guid? PatientId { get; private set; }
    public Guid? PatientRef { get; private set; }

    public int Attempts { get; private set; }
    public DateTime ExpiresAtUtc { get; private set; }

    public bool IsExpired(DateTime nowUtc) => nowUtc >= ExpiresAtUtc;

    /// <summary>
    /// Omur dolduysa ACTIVE ya da VERIFIED oturumu EXPIRED yapar. Dogrulanmis oturum
    /// randevu ve saglik gecmisine kapi actigi icin o da sure dolunca kapanir.
    /// COMPLETED/HANDED_OFF kapanmis durumlardir, degistirilmez.
    /// </summary>
    /// <returns>Durum bu cagrida EXPIRED olduysa true.</returns>
    public bool ExpireIfElapsed(DateTime nowUtc)
    {
        if (Status is not (VoiceSessionStatus.Active or VoiceSessionStatus.Verified) || !IsExpired(nowUtc))
            return false;

        Status = VoiceSessionStatus.Expired;
        return true;
    }

    /// <summary>Basarisiz TC denemesi. Hak bittiginde oturum HANDOFF durumuna gecer.</summary>
    public void RegisterFailedAttempt()
    {
        EnsureActive();

        Attempts++;
        if (Attempts >= MaxAttempts)
            Status = VoiceSessionStatus.HandedOff;
    }

    /// <summary>Basarili dogrulama deneme hakkindan dusmez; Attempts yalniz basarisiz denemeleri sayar.</summary>
    public void MarkVerified(Guid patientId, Guid patientRef)
    {
        EnsureActive();

        PatientId = patientId;
        PatientRef = patientRef;
        Status = VoiceSessionStatus.Verified;
    }

    /// <summary>Randevu olusturuldu; oturum kapanir.</summary>
    public void Complete()
    {
        if (Status is not VoiceSessionStatus.Verified)
            throw new DomainException(
                ErrorCodes.VoiceSessionNotVerified,
                "Randevu oluşturmak için oturumun doğrulanmış olması gerekir.");

        Status = VoiceSessionStatus.Completed;
    }

    public void Expire() => Status = VoiceSessionStatus.Expired;

    /// <summary>
    /// Hasta cikis yapti (ya da sayfa kapandi): oturum hemen kapanir, kimligini bilen biri hasta
    /// uclarini artik cagiramaz. ACTIVE/VERIFIED/COMPLETED oturum EXPIRED olur ve omru simdiye cekilir;
    /// HANDED_OFF ve EXPIRED oldugu gibi kalir (tekrar cagri zararsizdir).
    /// </summary>
    /// <returns>Durum bu cagrida degistiyse true.</returns>
    public bool End(DateTime nowUtc)
    {
        if (Status is not (VoiceSessionStatus.Active or VoiceSessionStatus.Verified or VoiceSessionStatus.Completed))
            return false;

        Status = VoiceSessionStatus.Expired;
        if (ExpiresAtUtc > nowUtc)
            ExpiresAtUtc = nowUtc;
        return true;
    }

    public void HandOff() => Status = VoiceSessionStatus.HandedOff;

    /// <summary>
    /// Oturum TC beklemiyorsa (hak bitti, suresi doldu, zaten dogrulandi) ilgili hatayi atar.
    /// Bicim denetiminden once cagrilir ki kapali oturuma 400 yerine dogru kod donsun.
    /// </summary>
    public void EnsureAwaitingVerification() => EnsureActive();

    private void EnsureActive()
    {
        if (Status is VoiceSessionStatus.HandedOff)
            throw new DomainException(
                ErrorCodes.VoiceSessionExhausted,
                "Doğrulama deneme hakkı doldu; görüşme bir görevliye aktarılmalı.");

        if (Status is VoiceSessionStatus.Expired)
            throw new DomainException(
                ErrorCodes.InvalidStatusTransition,
                "Oturumun süresi doldu; lütfen yeni bir oturum başlatın.");

        if (Status is VoiceSessionStatus.Verified or VoiceSessionStatus.Completed)
            throw new DomainException(
                ErrorCodes.InvalidStatusTransition,
                "Bu oturumda kimlik zaten doğrulandı.");

        if (Status is not VoiceSessionStatus.Active)
            throw new InvalidStatusTransitionException(Status.ToString(), VoiceSessionStatus.Active.ToString());
    }
}
