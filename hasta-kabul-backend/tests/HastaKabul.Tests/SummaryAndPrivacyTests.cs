using HastaKabul.Application.Common;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Patients;
using HastaKabul.Domain.Summaries;
using Xunit;

namespace HastaKabul.Tests;

public class SummaryTests
{
    private static SimplifiedSummary CreateSummary() =>
        new(Guid.NewGuid(), Guid.NewGuid(), Guid.NewGuid(), "req-1");

    private static void MakeReady(SimplifiedSummary summary) =>
        summary.MarkReady("Sade aciklama", [], [], [], [], DateTime.UtcNow);

    [Fact]
    public void Hazir_olmayan_ozet_onaylanamaz()
    {
        var summary = CreateSummary();

        Assert.Throws<InvalidStatusTransitionException>(() => summary.Approve(Guid.NewGuid(), DateTime.UtcNow));
    }

    [Fact]
    public void Hekim_onayi_kaydi_approved_yapar()
    {
        var summary = CreateSummary();
        MakeReady(summary);
        var doctorId = Guid.NewGuid();

        summary.Approve(doctorId, DateTime.UtcNow);

        Assert.Equal(SummaryStatus.Approved, summary.Status);
        Assert.Equal(doctorId, summary.ApprovedByDoctorId);
    }

    [Fact]
    public void Reddedilen_ozet_yeniden_denenebilir()
    {
        var summary = CreateSummary();
        MakeReady(summary);

        summary.Reject(DateTime.UtcNow);
        Assert.Equal(SummaryStatus.Rejected, summary.Status);

        summary.Retry("req-2", DateTime.UtcNow);

        Assert.Equal(SummaryStatus.Pending, summary.Status);
        Assert.Equal("req-2", summary.RequestId);
    }

    [Fact]
    public void Llm_erisilemezse_kayit_hata_koduyla_isaretlenir()
    {
        var summary = CreateSummary();

        summary.MarkFailed(ErrorCodes.LlmUnavailable, DateTime.UtcNow);

        Assert.Equal(SummaryStatus.Failed, summary.Status);
        Assert.Equal(ErrorCodes.LlmUnavailable, summary.FailureCode);
    }
}

/// <summary>KVKK siniri: disariya cikan ad ve kimlik kontrolleri.</summary>
public class PrivacyTests
{
    [Fact]
    public void Gosterim_adi_soyadi_tek_harfe_indirir()
    {
        var patient = new Patient("hash", [1, 2, 3], "Ayse", "Hanimeli");

        Assert.Equal("Ayse H.", patient.DisplayName);
        Assert.DoesNotContain("Hanimeli", patient.DisplayName);
    }

    [Fact]
    public void Takma_adli_kimlik_tc_den_turetilmez()
    {
        var first = new Patient("ayni-hash", [1], "Ayse", "Hanimeli");
        var second = new Patient("ayni-hash", [1], "Ayse", "Hanimeli");

        Assert.NotEqual(first.PatientRef, second.PatientRef);
    }

    [Theory]
    [InlineData("10000000146", true)]
    [InlineData("12345678901", false)]
    [InlineData("00000000000", false)]
    [InlineData("1234567890", false)]
    [InlineData("1234567890a", false)]
    public void Tc_bicim_denetimi(string value, bool expected)
        => Assert.Equal(expected, TcKimlikNo.IsWellFormed(value));
}
