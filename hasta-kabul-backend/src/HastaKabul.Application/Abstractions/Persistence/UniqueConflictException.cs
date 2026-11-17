namespace HastaKabul.Application.Abstractions.Persistence;

/// <summary>Benzersiz indeks ihlalinin hangi kurala takildigi.</summary>
public enum UniqueConflictTarget
{
    /// <summary>appointments.slot_id: ayni slota eszamanli iki randevu.</summary>
    AppointmentSlot,

    /// <summary>appointments.access_code: uretilen erisim kodu zaten var.</summary>
    AccessCode,

    Other
}

/// <summary>
/// Kalicilik katmani benzersiz indeks ihlalini (PostgreSQL 23505) bu istisnaya cevirir; boylece
/// use-case servisleri veritabani surucusunu bilmeden 409 SLOT_TAKEN uretir ya da erisim kodunu
/// yeniden dener. Eslesmeyen ihlal <see cref="UniqueConflictTarget.Other"/> olarak gelir.
/// </summary>
public sealed class UniqueConflictException(UniqueConflictTarget target, Exception? inner = null)
    : Exception($"Benzersizlik ihlali: {target}.", inner)
{
    public UniqueConflictTarget Target { get; } = target;
}
