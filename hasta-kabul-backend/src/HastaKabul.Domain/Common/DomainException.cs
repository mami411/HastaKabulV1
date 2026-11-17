namespace HastaKabul.Domain.Common;

/// <summary>
/// Is kuralinin ihlal edildigi durumlarda atilir. Kod, API katmaninda
/// { "error": { "code", "message", "requestId" } } zarfina donusur.
/// </summary>
public class DomainException : Exception
{
    public string Code { get; }

    public DomainException(string code, string message) : base(message)
    {
        Code = code;
    }
}

/// <summary>Gecersiz durum gecisi: API 409 doner.</summary>
public sealed class InvalidStatusTransitionException : DomainException
{
    public InvalidStatusTransitionException(string from, string to)
        : base(ErrorCodes.InvalidStatusTransition, $"'{ToWire(from)}' durumundan '{ToWire(to)}' durumuna geçilemez.")
    {
    }

    /// <summary>Enum adini istemcinin gordugu bicime cevirir: InExam -> IN_EXAM.</summary>
    public static string ToWire(string name)
    {
        var sb = new System.Text.StringBuilder(name.Length + 4);
        for (var i = 0; i < name.Length; i++)
        {
            if (i > 0 && char.IsUpper(name[i]) && !char.IsUpper(name[i - 1]))
                sb.Append('_');
            sb.Append(char.ToUpperInvariant(name[i]));
        }
        return sb.ToString();
    }
}
