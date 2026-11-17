using HastaKabul.Domain.Common;

namespace HastaKabul.Domain.Patients;

/// <summary>
/// KVKK siniri: TC asla duz metin tutulmaz. Sema bugunden tc_hash (HMAC-SHA256 + pepper)
/// ve tc_encrypted kolonlarini tasir; duz TC kolonu hic acilmaz.
/// AI servisleri yalnizca <see cref="PatientRef"/> gorur, TC'den turetilmez.
/// </summary>
public class Patient : Entity
{
    private Patient() { }

    public Patient(string tcHash, byte[] tcEncrypted, string firstName, string lastName, string? phoneMasked = null)
    {
        TcHash = tcHash;
        TcEncrypted = tcEncrypted;
        FirstName = firstName;
        LastName = lastName;
        PhoneMasked = phoneMasked;
        PatientRef = Guid.NewGuid();
    }

    /// <summary>Takma adli kimlik. AI servislerine ve olaylara yalnizca bu deger gider.</summary>
    public Guid PatientRef { get; private set; }

    public string TcHash { get; private set; } = null!;
    public byte[] TcEncrypted { get; private set; } = null!;

    public string FirstName { get; private set; } = null!;
    public string LastName { get; private set; } = null!;
    public string? PhoneMasked { get; private set; }

    /// <summary>
    /// Ekranda ve olaylarda gosterilen ad: "Ayşe H.". Tam ad arayuze ve loglara hic dusmez.
    /// </summary>
    public string DisplayName =>
        string.IsNullOrWhiteSpace(LastName)
            ? FirstName
            : $"{FirstName} {char.ToUpperInvariant(LastName[0])}.";
}
