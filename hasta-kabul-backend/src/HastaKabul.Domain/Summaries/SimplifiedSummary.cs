using HastaKabul.Domain.Appointments;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;

namespace HastaKabul.Domain.Summaries;

/// <summary>
/// Sade dil ozeti. Asenkron akis: API PENDING kaydi acar, AI servisi
/// PUT /api/v1/summaries/{summaryId} ile doldurur, kayit READY olur ve summary.ready yayilir.
/// Portal yalnizca APPROVED ozeti gorur.
/// </summary>
public class SimplifiedSummary : Entity
{
    private SimplifiedSummary() { }

    public SimplifiedSummary(Guid appointmentId, Guid clinicalEntryId, Guid patientRef, string requestId)
    {
        AppointmentId = appointmentId;
        ClinicalEntryId = clinicalEntryId;
        PatientRef = patientRef;
        RequestId = requestId;
        Status = SummaryStatus.Pending;
        UpdatedAtUtc = CreatedAtUtc;
    }

    public Guid AppointmentId { get; private set; }
    public Appointment? Appointment { get; private set; }

    public Guid ClinicalEntryId { get; private set; }

    /// <summary>AI servisine giden tek kimlik.</summary>
    public Guid PatientRef { get; private set; }

    public string RequestId { get; private set; } = null!;
    public SummaryStatus Status { get; private set; }
    public DateTime UpdatedAtUtc { get; private set; }

    public string? DiagnosisPlain { get; private set; }
    public List<SimplifiedMedication> Medications { get; private set; } = new();
    public List<string> Warnings { get; private set; } = new();

    /// <summary>Bilgi tabaninda bulunmayan terimler; panelde sari uyari kutusunda gosterilir.</summary>
    public List<string> UnknownTerms { get; private set; } = new();
    public List<string> SourceIds { get; private set; } = new();

    public string? FailureCode { get; private set; }
    public Guid? ApprovedByDoctorId { get; private set; }
    public DateTime? ApprovedAtUtc { get; private set; }

    /// <summary>
    /// AI servisinin callback'i. PENDING, READY (tekrarlanan callback) veya hatali (FAILED: ornegin
    /// zaman asimindan sonra gec gelen sonuc) kayit doldurulabilir. Hekimin karar verdigi kayit
    /// (APPROVED/REJECTED) degismez: reddedilmis ozete gec gelen sonuc onu READY yapamaz; yeniden
    /// uretim yalniz <see cref="Retry"/> ile (kayit once PENDING'e doner) baslar.
    /// </summary>
    public void MarkReady(
        string diagnosisPlain,
        IEnumerable<SimplifiedMedication> medications,
        IEnumerable<string> warnings,
        IEnumerable<string> unknownTerms,
        IEnumerable<string> sourceIds,
        DateTime nowUtc)
    {
        if (Status is SummaryStatus.Approved or SummaryStatus.Rejected)
            throw new InvalidStatusTransitionException(Status.ToString(), SummaryStatus.Ready.ToString());

        DiagnosisPlain = diagnosisPlain;
        Medications = medications.ToList();
        Warnings = warnings.ToList();
        UnknownTerms = unknownTerms.ToList();
        SourceIds = sourceIds.ToList();
        FailureCode = null;
        Status = SummaryStatus.Ready;
        Touch(nowUtc);
    }

    public void MarkFailed(string failureCode, DateTime nowUtc)
    {
        if (Status is SummaryStatus.Approved or SummaryStatus.Rejected)
            throw new InvalidStatusTransitionException(Status.ToString(), SummaryStatus.Failed.ToString());

        FailureCode = failureCode;
        Status = SummaryStatus.Failed;
        Touch(nowUtc);
    }

    /// <summary>Halusinasyon onlemi: portal icerigi yalnizca hekim onayindan sonra acilir.</summary>
    public void Approve(Guid doctorId, DateTime nowUtc)
    {
        if (Status is not SummaryStatus.Ready)
            throw new InvalidStatusTransitionException(Status.ToString(), SummaryStatus.Approved.ToString());

        ApprovedByDoctorId = doctorId;
        ApprovedAtUtc = DateTime.SpecifyKind(nowUtc, DateTimeKind.Utc);
        Status = SummaryStatus.Approved;
        Touch(nowUtc);
    }

    /// <summary>P0 kurali: hekim "yeniden olustur" der, ayni istek tekrar gider.</summary>
    public void Reject(DateTime nowUtc)
    {
        if (Status is not SummaryStatus.Ready)
            throw new InvalidStatusTransitionException(Status.ToString(), SummaryStatus.Rejected.ToString());

        Status = SummaryStatus.Rejected;
        Touch(nowUtc);
    }

    /// <summary>Reddedilen ya da basarisiz ozet icin yeni deneme; kayit PENDING durumuna doner.</summary>
    public void Retry(string requestId, DateTime nowUtc)
    {
        if (Status is not (SummaryStatus.Rejected or SummaryStatus.Failed))
            throw new InvalidStatusTransitionException(Status.ToString(), SummaryStatus.Pending.ToString());

        RequestId = requestId;
        Status = SummaryStatus.Pending;
        FailureCode = null;
        Touch(nowUtc);
    }

    /// <summary>
    /// PENDING kayit zaman asimini asti mi: sadelestirme servisi geri cagri yapmadiysa
    /// demo sonsuza kadar "hazirlaniyor"da kalmasin diye okuma aninda yaslandirilir.
    /// </summary>
    public bool IsPendingTooLong(DateTime nowUtc, TimeSpan timeout)
        => Status is SummaryStatus.Pending && nowUtc - UpdatedAtUtc >= timeout;

    private void Touch(DateTime nowUtc) => UpdatedAtUtc = DateTime.SpecifyKind(nowUtc, DateTimeKind.Utc);
}
