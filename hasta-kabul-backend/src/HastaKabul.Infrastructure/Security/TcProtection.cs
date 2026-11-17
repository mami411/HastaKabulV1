using System.Security.Cryptography;
using System.Text;
using HastaKabul.Application.Abstractions.Security;
using Microsoft.Extensions.Options;

namespace HastaKabul.Infrastructure.Security;

public sealed class TcProtectionOptions
{
    public const string SectionName = "TcProtection";

    /// <summary>TC_PEPPER: hash'e karisan, veritabaninda bulunmayan gizli anahtar.</summary>
    public string Pepper { get; set; } = string.Empty;

    /// <summary>Alan sifrelemesi icin 32 baytlik base64 anahtar.</summary>
    public string EncryptionKey { get; set; } = string.Empty;
}

/// <summary>HMAC-SHA256 + pepper. Esleme yalnizca bu ozet uzerinden yapilir.</summary>
public sealed class HmacTcHasher(IOptions<TcProtectionOptions> options) : ITcHasher
{
    private readonly byte[] _pepper = Encoding.UTF8.GetBytes(
        string.IsNullOrWhiteSpace(options.Value.Pepper)
            ? throw new InvalidOperationException("TcProtection:Pepper tanimli degil (TC_PEPPER).")
            : options.Value.Pepper);

    public string Hash(string tc)
    {
        using var hmac = new HMACSHA256(_pepper);
        var bytes = hmac.ComputeHash(Encoding.UTF8.GetBytes(tc.Trim()));
        return Convert.ToHexString(bytes).ToLowerInvariant();
    }
}

/// <summary>
/// AES-GCM ile alan sifrelemesi (tc_encrypted). Cozme yalnizca kurum ici ihtiyac
/// halinde kullanilir; API yanitlarinda TC hicbir zaman donmez.
/// </summary>
public sealed class AesTcProtector : ITcProtector
{
    private const int NonceSize = 12;
    private const int TagSize = 16;

    private readonly byte[] _key;

    public AesTcProtector(IOptions<TcProtectionOptions> options)
    {
        var raw = options.Value.EncryptionKey;
        if (string.IsNullOrWhiteSpace(raw))
            throw new InvalidOperationException("TcProtection:EncryptionKey tanimli degil.");

        _key = Convert.FromBase64String(raw);
        if (_key.Length != 32)
            throw new InvalidOperationException("TcProtection:EncryptionKey 32 bayt (base64) olmalidir.");
    }

    public byte[] Protect(string tc)
    {
        var plain = Encoding.UTF8.GetBytes(tc.Trim());
        var nonce = RandomNumberGenerator.GetBytes(NonceSize);
        var cipher = new byte[plain.Length];
        var tag = new byte[TagSize];

        using var aes = new AesGcm(_key, TagSize);
        aes.Encrypt(nonce, plain, cipher, tag);

        var result = new byte[NonceSize + TagSize + cipher.Length];
        nonce.CopyTo(result, 0);
        tag.CopyTo(result, NonceSize);
        cipher.CopyTo(result, NonceSize + TagSize);
        return result;
    }

    public string Unprotect(byte[] payload)
    {
        var nonce = payload.AsSpan(0, NonceSize);
        var tag = payload.AsSpan(NonceSize, TagSize);
        var cipher = payload.AsSpan(NonceSize + TagSize);
        var plain = new byte[cipher.Length];

        using var aes = new AesGcm(_key, TagSize);
        aes.Decrypt(nonce, cipher, tag, plain);

        return Encoding.UTF8.GetString(plain);
    }
}

/// <summary>Karistirilmasi kolay harfler (0/O, 1/I) disarida birakilir.</summary>
public sealed class AccessCodeGenerator : IAccessCodeGenerator
{
    private const string Alphabet = "23456789";

    public string Generate()
    {
        Span<char> buffer = stackalloc char[6];
        for (var i = 0; i < buffer.Length; i++)
            buffer[i] = Alphabet[RandomNumberGenerator.GetInt32(Alphabet.Length)];

        return new string(buffer);
    }
}
