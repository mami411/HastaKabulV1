namespace HastaKabul.Application.Abstractions;

/// <summary>
/// Her istekte tasinan X-Request-Id ve (varsa) hekim kimligi.
/// Tum servisler ayni requestId'yi loglar ve ileri tasir.
/// </summary>
public interface IRequestContext
{
    string RequestId { get; }
    Guid? DoctorId { get; }
}
