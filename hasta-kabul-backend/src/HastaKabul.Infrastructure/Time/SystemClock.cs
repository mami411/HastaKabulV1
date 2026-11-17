using HastaKabul.Application.Abstractions;

namespace HastaKabul.Infrastructure.Time;

public sealed class SystemClock : IClock
{
    public DateTime UtcNow => DateTime.UtcNow;
}
