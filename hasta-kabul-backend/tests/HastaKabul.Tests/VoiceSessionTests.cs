using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Voice;
using Xunit;

namespace HastaKabul.Tests;

/// <summary>Deneme sayacini API tutar; hak bitince oturum HANDOFF olur.</summary>
public class VoiceSessionTests
{
    private static VoiceSession CreateSession() => new(TimeSpan.FromMinutes(15));

    [Fact]
    public void Dogrulanan_oturum_hasta_kimligini_tasir()
    {
        var session = CreateSession();
        var patientId = Guid.NewGuid();
        var patientRef = Guid.NewGuid();

        session.MarkVerified(patientId, patientRef);

        Assert.True(session.Verified);
        Assert.Equal(patientId, session.PatientId);
        Assert.Equal(patientRef, session.PatientRef);
        Assert.Equal(0, session.Attempts);   // basarili dogrulama deneme sayilmaz
    }

    [Fact]
    public void Uc_basarisiz_denemeden_sonra_handoff_olur()
    {
        var session = CreateSession();

        session.RegisterFailedAttempt();
        session.RegisterFailedAttempt();
        Assert.Equal(VoiceSessionStatus.Active, session.Status);

        session.RegisterFailedAttempt();

        Assert.Equal(VoiceSessionStatus.HandedOff, session.Status);
    }

    [Fact]
    public void Hak_bittikten_sonra_yeni_deneme_kabul_edilmez()
    {
        var session = CreateSession();
        for (var i = 0; i < VoiceSession.MaxAttempts; i++)
            session.RegisterFailedAttempt();

        var error = Assert.Throws<DomainException>(() => session.RegisterFailedAttempt());

        Assert.Equal(ErrorCodes.VoiceSessionExhausted, error.Code);
    }

    [Fact]
    public void Dogrulanmamis_oturumla_randevu_kapatilamaz()
    {
        var session = CreateSession();

        var error = Assert.Throws<DomainException>(() => session.Complete());

        Assert.Equal(ErrorCodes.VoiceSessionNotVerified, error.Code);
    }
}
