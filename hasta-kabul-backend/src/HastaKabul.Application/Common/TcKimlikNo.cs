namespace HastaKabul.Application.Common;

/// <summary>
/// TC kimlik numarasi bicim denetimi. Yalnizca bicim kontrol edilir; deger hicbir yere
/// loglanmaz ve bu siniftan disariya cikmaz.
/// </summary>
public static class TcKimlikNo
{
    public const int Length = 11;

    public static bool IsWellFormed(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return false;

        var tc = value.Trim();
        if (tc.Length != Length) return false;
        if (tc[0] == '0') return false;

        var digits = new int[Length];
        for (var i = 0; i < Length; i++)
        {
            if (!char.IsAsciiDigit(tc[i])) return false;
            digits[i] = tc[i] - '0';
        }

        var oddSum = digits[0] + digits[2] + digits[4] + digits[6] + digits[8];
        var evenSum = digits[1] + digits[3] + digits[5] + digits[7];

        var tenth = ((oddSum * 7) - evenSum) % 10;
        if (tenth < 0) tenth += 10;
        if (tenth != digits[9]) return false;

        var firstTenSum = digits.Take(10).Sum();
        return firstTenSum % 10 == digits[10];
    }
}
