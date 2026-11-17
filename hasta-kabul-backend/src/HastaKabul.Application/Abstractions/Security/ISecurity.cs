namespace HastaKabul.Application.Abstractions.Security;

/// <summary>
/// TC yalnizca tus takimi -> API -> DB hattinda gorulur. Hash HMAC-SHA256 + pepper ile
/// uretilir; esleme yalnizca bu ozet uzerinden yapilir.
/// </summary>
public interface ITcHasher
{
    string Hash(string tc);
}

/// <summary>Geri donus gerektiginde kullanilan alan sifrelemesi (tc_encrypted).</summary>
public interface ITcProtector
{
    byte[] Protect(string tc);
    string Unprotect(byte[] cipher);
}

/// <summary>Demo icin randevu basina 6 haneli portal erisim kodu.</summary>
public interface IAccessCodeGenerator
{
    string Generate();
}

/// <summary>Hekim secimi icin kisa omurlu JWT; REST ve WebSocket ayni token'i kullanir.</summary>
public interface IDoctorTokenService
{
    string CreateToken(Guid doctorId, string displayName);
}
