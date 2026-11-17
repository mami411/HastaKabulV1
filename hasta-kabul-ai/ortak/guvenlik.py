"""Konudan bağımsız güvenlik filtresi.

LLM'in ürettiği her metin kullanıcıya (ekrana ya da TTS'e) gitmeden önce buradan
geçer. Kural listeleri ve yedek cümleler konu modülünden gelir; bu dosya yalnız
eşleştirme mantığını taşır (halisaha ``_guvenlik_ihlali_fallbacki`` uyarlaması).

Farklar (rapordaki düşük önemli bulgunun düzeltmesi):
- Karşılaştırma Türkçe kurallı küçük harf + ASCII katlama ile yapılır; "ONAYLANDI",
  "Onaylandi" ve "onaylandı" aynı sayılır.
- Eşleşme kelime başından yapılır; kelime ortası rastlantılar takılmaz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .turkce import herhangi_gecer_mi, tc_maskele


class Kosul(str, Enum):
    HER_ZAMAN = "her_zaman"
    BASARI_YOKSA = "basari_yoksa"          # araç gerçekten başarılı dönmediyse yasak
    SISTEM_HATASI_VARSA = "sistem_hatasi"  # bu turda sistem hatası olduysa yasak


@dataclass(frozen=True)
class YasakKurali:
    ad: str
    yedek: str
    ifadeler: tuple[str, ...] = ()
    desenler: tuple[str, ...] = ()  # ham metne uygulanan düzenli ifadeler (büyük/küçük duyarsız)
    kosul: Kosul = Kosul.HER_ZAMAN
    # Doluysa kural yalnız metinde bunlardan biri de geçtiğinde uygulanır ("hazır" tek başına
    # masumdur, "randevunuz hazır" değildir).
    baglam_ifadeleri: tuple[str, ...] = ()


@dataclass
class FiltreBaglami:
    basarili_islem: bool = False
    sistem_hatasi: bool = False


@dataclass(frozen=True)
class Ihlal:
    kural: str
    eslesen: str
    yedek: str


@dataclass
class FiltreSonucu:
    metin: str
    ihlal: Ihlal | None = None
    maskelendi: bool = False
    notlar: list[str] = field(default_factory=list)


def _etkin(kural: YasakKurali, baglam: FiltreBaglami) -> bool:
    if kural.kosul is Kosul.BASARI_YOKSA:
        return not baglam.basarili_islem
    if kural.kosul is Kosul.SISTEM_HATASI_VARSA:
        return baglam.sistem_hatasi
    return True


# Görünmez biçim karakterleri (yumuşak tire, sıfır genişlikli boşluk/birleştirici, yön işaretleri).
_BICIM_KARAKTERLERI = re.compile(
    r"[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180e"
    r"\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\ufe00-\ufe0f]")
# Tek harfleri ayırıcıyla bölünmüş yazım: "o-n-a-y-l-a-n-d-ı", "o n a y".
_HARF_HARF = re.compile(r"(?<!\w)(?:[^\W\d_][\s\-.·_*|/]+){2,}[^\W\d_](?!\w)")


def metin_varyantlari(metin: str) -> list[str]:
    """Denetlenecek biçimler: ham metin, görünmez karakterleri silinmiş ve harf harf
    yazılmış kelimeleri birleştirilmiş hali."""
    temiz = _BICIM_KARAKTERLERI.sub("", metin)
    birlesik = _HARF_HARF.sub(lambda m: re.sub(r"[\s\-.·_*|/]+", "", m.group(0)), temiz)
    varyantlar = [metin]
    for v in (temiz, birlesik):
        if v not in varyantlar:
            varyantlar.append(v)
    return varyantlar


def denetle(metin: str, kurallar: tuple[YasakKurali, ...] | list[YasakKurali],
            baglam: FiltreBaglami | None = None) -> Ihlal | None:
    """İlk ihlali döndürür; yoksa None. Görünmez karakter ve harf harf yazım
    hileleri ayıklanmış biçimler de denetlenir."""
    baglam = baglam or FiltreBaglami()
    for varyant in metin_varyantlari(metin):
        ihlal = _denetle_tek(varyant, kurallar, baglam)
        if ihlal is not None:
            return ihlal
    return None


def _denetle_tek(metin: str, kurallar: tuple[YasakKurali, ...] | list[YasakKurali],
                 baglam: FiltreBaglami) -> Ihlal | None:
    for kural in kurallar:
        if not _etkin(kural, baglam):
            continue
        if kural.baglam_ifadeleri and herhangi_gecer_mi(metin, kural.baglam_ifadeleri) is None:
            continue
        eslesen = herhangi_gecer_mi(metin, kural.ifadeler) if kural.ifadeler else None
        if eslesen is None:
            for desen in kural.desenler:
                m = re.search(desen, metin, flags=re.IGNORECASE)
                if m:
                    eslesen = m.group(0)
                    break
        if eslesen is not None:
            return Ihlal(kural.ad, eslesen, kural.yedek)
    return None


def filtrele(metin: str, kurallar: tuple[YasakKurali, ...] | list[YasakKurali],
             baglam: FiltreBaglami | None = None) -> FiltreSonucu:
    """Metni denetler: ihlal varsa yedek cümleyi, yoksa (TC'si maskelenmiş) metni döndürür."""
    maskeli, maskelendi = tc_maskele(metin)
    ihlal = denetle(maskeli, kurallar, baglam)
    if ihlal is not None:
        return FiltreSonucu(metin=ihlal.yedek, ihlal=ihlal, maskelendi=maskelendi)
    return FiltreSonucu(metin=maskeli, maskelendi=maskelendi)
