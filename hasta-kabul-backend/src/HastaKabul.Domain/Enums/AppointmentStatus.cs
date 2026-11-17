namespace HastaKabul.Domain.Enums;

/// <summary>
/// Kodda Ingilizce sabitler; Turkce etiketler shared/ altindaki tek esleme tablosunda tutulur
/// (Randevulu, Geldi ve Bekliyor, Muayenede, Tamamlandi, Gelmedi).
/// </summary>
public enum AppointmentStatus
{
    Scheduled = 0,
    CheckedIn = 1,
    InExam = 2,
    Completed = 3,
    NoShow = 4
}
