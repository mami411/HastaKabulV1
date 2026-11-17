using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;

namespace HastaKabul.Application.Common;

/// <summary>
/// Kodda ve JSON'da durum adlari SCREAMING_SNAKE olarak tasinir (SCHEDULED, CHECKED_IN...).
/// Turkce etiketler yalnizca arayuzde, shared/ altindaki tek esleme tablosundan gelir.
/// </summary>
public static class StatusNames
{
    public static string ToWire(AppointmentStatus status) => status switch
    {
        AppointmentStatus.Scheduled => "SCHEDULED",
        AppointmentStatus.CheckedIn => "CHECKED_IN",
        AppointmentStatus.InExam => "IN_EXAM",
        AppointmentStatus.Completed => "COMPLETED",
        AppointmentStatus.NoShow => "NO_SHOW",
        _ => status.ToString().ToUpperInvariant()
    };

    public static AppointmentStatus ParseAppointmentStatus(string? value) => value?.Trim().ToUpperInvariant() switch
    {
        "SCHEDULED" => AppointmentStatus.Scheduled,
        "CHECKED_IN" => AppointmentStatus.CheckedIn,
        "IN_EXAM" => AppointmentStatus.InExam,
        "COMPLETED" => AppointmentStatus.Completed,
        "NO_SHOW" => AppointmentStatus.NoShow,
        _ => throw new DomainException(ErrorCodes.ValidationFailed, $"Geçersiz randevu durumu: '{value}'. Geçerli değerler: CHECKED_IN, IN_EXAM, COMPLETED, NO_SHOW.")
    };

    public static string ToWire(SummaryStatus status) => status switch
    {
        SummaryStatus.Pending => "PENDING",
        SummaryStatus.Ready => "READY",
        SummaryStatus.Approved => "APPROVED",
        SummaryStatus.Rejected => "REJECTED",
        SummaryStatus.Failed => "FAILED",
        _ => status.ToString().ToUpperInvariant()
    };

    public static string ToWire(VoiceSessionStatus status) => status switch
    {
        VoiceSessionStatus.Active => "ACTIVE",
        VoiceSessionStatus.Verified => "VERIFIED",
        VoiceSessionStatus.HandedOff => "HANDED_OFF",
        VoiceSessionStatus.Completed => "COMPLETED",
        VoiceSessionStatus.Expired => "EXPIRED",
        _ => status.ToString().ToUpperInvariant()
    };
}
