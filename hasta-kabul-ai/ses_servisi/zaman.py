"""Saat dilimi kuralı: API UTC konuşur, kullanıcıya Europe/Istanbul gösterilir.

- API'den gelen ``startTime`` her zaman UTC kabul edilir ("Z" ya da +00:00). Saat
  dilimi bilgisi olmayan bir değer gelirse de UTC sayılır (API sözleşmesi).
- Kullanıcıya giden etiketler yerel saattir ve ASLA "Z" ile işaretlenmez
  (halisaha raporundaki "yerel saat UTC diye etiketleniyor" hatası).
- ``?date=`` parametresi Istanbul takvim günüdür (backend ``IstanbulTime`` ile aynı).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ortak.turkce import AY_ADLARI, GUN_ADLARI, GUN_KISA

ISTANBUL = ZoneInfo("Europe/Istanbul")

SaatKaynagi = Callable[[], datetime]


def simdi_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_coz(deger: str) -> datetime:
    """ISO 8601 zamanı UTC'ye çevirir. Bilgi yoksa UTC kabul edilir."""
    metin = deger.strip()
    if metin.endswith(("Z", "z")):
        metin = metin[:-1] + "+00:00"
    # .NET 7 haneli kesirli saniye üretebilir; fromisoformat 6 haneye kadar kabul eder.
    if "." in metin:
        bas, _, son = metin.partition(".")
        rakamlar = "".join(ch for ch in son if ch.isdigit())
        ek = son[len(rakamlar):]
        metin = f"{bas}.{rakamlar[:6].ljust(6, '0')}{ek}"
    dt = datetime.fromisoformat(metin)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_iso(dt: datetime) -> str:
    """UTC ISO metni ("…Z"). Yalnız gerçekten UTC olan değerler için kullanılır."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def yerel(dt: datetime) -> datetime:
    return dt.astimezone(ISTANBUL)


def yerel_gun(simdi: datetime) -> date:
    return yerel(simdi).date()


def gun_adi(g: date) -> str:
    return GUN_ADLARI[g.weekday()]


def tarih_metni(g: date) -> str:
    """"16 Kasım Pazartesi" """
    return f"{g.day} {AY_ADLARI[g.month - 1]} {gun_adi(g)}"


def goreli_gun_metni(g: date, bugun: date) -> str:
    fark = (g - bugun).days
    if fark == 0:
        return f"bugün ({tarih_metni(g)})"
    if fark == 1:
        return f"yarın ({tarih_metni(g)})"
    return tarih_metni(g)


def slot_etiketi(baslangic_utc: datetime) -> str:
    """Ekrandaki kısa etiket: "Pzt 16 Kas 09:00" (yerel saat)."""
    y = yerel(baslangic_utc)
    return f"{GUN_KISA[y.weekday()]} {y.day} {AY_ADLARI[y.month - 1][:3]} {y:%H:%M}"


def slot_uzun_etiket(baslangic_utc: datetime) -> str:
    """Cümle içinde: "16 Kasım Pazartesi saat 09:00" (yerel saat)."""
    y = yerel(baslangic_utc)
    return f"{tarih_metni(y.date())} saat {y:%H:%M}"


def takvim_metni(bugun: date, gun_sayisi: int = 7) -> str:
    """LLM istemine giden takvim: tarih + haftanın günü (küçük modeller "cuma"yı
    kendi hesaplayınca yanılıyor — halisaha raporu)."""
    satirlar = []
    for i in range(gun_sayisi):
        g = bugun + timedelta(days=i)
        not_ = " (bugün)" if i == 0 else " (yarın)" if i == 1 else ""
        satirlar.append(f"- {g.isoformat()} = {tarih_metni(g)}{not_}")
    return "\n".join(satirlar)


def hhmm_coz(deger: str | None) -> time | None:
    if not deger:
        return None
    try:
        saat, dakika = deger.strip().split(":")
        return time(int(saat), int(dakika))
    except (ValueError, TypeError):
        return None


def yerel_saat(dt_utc: datetime) -> time:
    return yerel(dt_utc).time().replace(second=0, microsecond=0)
