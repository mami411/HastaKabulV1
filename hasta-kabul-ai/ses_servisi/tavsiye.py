"""Şikâyet için TAVSİYE isteği yolu: "midem bulanıyor ne yapabilirim", "randevu değil, doktora gitmeden ne
yapabilirim", "çay iyi gelir mi".

Bu modül yalnız KARAR verir (kural, LLM'siz); yanıtı orkestratör (``_Baglam._tavsiye_yolu``) kurar:

- ``sikayet_konusu``: cümledeki şikâyet konusu, kapalı listeden (``TAVSIYE_KONULARI``) ya da konuşma tanıma
  toleransıyla (``TAVSIYE_STT_KALIPLARI``: "midem çok kullanıyor" → bulantı). Konu kodu dışında bir şey dönmez.
- ``tavsiye_istegi``: güçlü / zayıf tavsiye işareti ("doktora gitmeden", "ne iyi gelir" / "ne yapabilirim").
- ``randevu_reddi``: "randevu değil", "doktora gitmek istemiyorum".
- ``tavsiye_engeli``: bu yolun devreye girmemesi gereken cümle (ciddi belirti, ilaç/doz/teşhis/tahlil, çocuk,
  gebelik, doktor/poliklinik tavsiyesi) → mevcut akış.
- ``alt_sorular``: sorulan ev yöntemi ("çay", "zencefil") → koddan sabit alt soru.

KVKK: LLM'e giden soru ``llm_siniri.saglik_tavsiye_yuku_kur`` ile YALNIZ bu kapalı listelerden kurulur; kullanıcının
cümlesi, hasta kaydı ya da kimlik bilgisi yüke girmez. Acil ifadeler bu yoldan ÖNCE yakalanır (``anlama.acil_mi``).
"""

from __future__ import annotations

import re
from types import ModuleType

from ortak.turkce import ifade_gecer_mi

from .anlama import metindeki_poliklinik


_DOLGU = frozenset({"da", "de", "cok", "biraz", "hep", "surekli", "yine", "gene", "fena", "bayagi", "epey",
                    "asiri", "hafif", "bugun", "sabahtan", "beri", "dunden", "simdi"})


def _gecer(n: str, ifadeler: tuple[str, ...]) -> bool:
    return any(ifade_gecer_mi(n, i) for i in ifadeler)


def konu_kaydi(kod: str | None, alan: ModuleType) -> tuple[str, str, str | None, tuple[str, ...]] | None:
    return next((k for k in getattr(alan, "TAVSIYE_KONULARI", ()) if k[0] == kod), None) if kod else None


def konu_adi(kod: str | None, alan: ModuleType) -> str | None:
    kayit = konu_kaydi(kod, alan)
    return kayit[1] if kayit else None


def konu_poliklinigi(kod: str | None, alan: ModuleType) -> str | None:
    kayit = konu_kaydi(kod, alan)
    return kayit[2] if kayit else None


def sikayet_konusu(n: str, alan: ModuleType) -> str | None:
    """Normalize cümledeki şikâyet konusu kodu (ilk eşleşen; özelden genele) ya da None."""
    if not n:
        return None
    # Organ ile fiil arasındaki dolgu kelimeleri ("başım da çok ağrıyor") kalıbı bozmasın.
    n = " ".join(k for k in n.split() if k not in _DOLGU)
    for desen, kod in getattr(alan, "TAVSIYE_STT_KALIPLARI", ()):
        if re.search(desen, n):
            return kod
    for kod, _, _, kaliplar in getattr(alan, "TAVSIYE_KONULARI", ()):
        if _gecer(n, kaliplar):
            return kod
    return None


def kisisel_sikayet_var(n: str, alan: ModuleType) -> bool:
    """Birinci şahıs şikâyet anlatımı ("midem bulanıyor", "başım ağrıyor"; STT bozuk biçimi dahil)."""
    if _gecer(n, getattr(alan, "SAGLIK_KISISEL_SIKAYET", ())) or _gecer(n, getattr(alan, "TAVSIYE_KISISEL_IFADELER", ())):
        return True
    return any(re.search(d, n) for d, _ in getattr(alan, "TAVSIYE_STT_KALIPLARI", ())[:1])


def tavsiye_istegi(n: str, alan: ModuleType) -> str | None:
    """"guclu" | "zayif" | None."""
    if _gecer(n, alan.TAVSIYE_GUCLU_IFADELER):
        return "guclu"
    if _gecer(n, alan.TAVSIYE_ZAYIF_IFADELER):
        return "zayif"
    return None


def randevu_reddi(n: str, alan: ModuleType) -> bool:
    return _gecer(n, alan.RANDEVU_RET_IFADELERI)


def tavsiye_engeli(n: str, alan: ModuleType, ilac_var: bool) -> bool:
    """Bu cümle tavsiye yolunda yanıtlanmamalı mı? (Mevcut akış: öneri + 112 notu, "tıbbi yorum yapamam",
    sabit yönlendirme, ilaç akışı.)"""
    if ilac_var:
        return True
    if _gecer(n, alan.TAVSIYE_ENGEL_IFADELERI):
        return True
    if _gecer(n, alan.SAGLIK_CIDDI_BELIRTILER):
        return True
    # İlaç önerisi ("hangi ilacı", "ne almalıyım"), teşhis ("ne olabilir", "ciddi mi") ve ölçüm yorumu ("normal
    # mi") isteği. "Ne içmeliyim" burada sayılmaz (bulantıda içecek sorusu); ilaç kelimesi ayrıca engeldir.
    oneri = tuple(i for i in alan.SAGLIK_SPESIFIK_ONERI if i != "ne içmeliyim")
    if _gecer(n, (*oneri, *alan.SAGLIK_SPESIFIK_TESHIS, *alan.SAGLIK_SPESIFIK_TAHLIL)):
        return True
    if _gecer(n, ("ilaç", "ilac", "hap", "tablet", "şurup", "kapsül", "iğne", "antibiyotik", "ağrı kesici",
                  "ağrıkesici", "reçete")):
        return True
    # Poliklinik adı söylendiyse randevu konuşmasıdır ("dahiliye olsun"); organ adı ("göz") sayılmaz.
    return metindeki_poliklinik(n, alan, organ_haric=True) is not None


def alt_sorular(n: str, alan: ModuleType) -> tuple[str, ...]:
    bulunan: list[str] = []
    for desen, soru in getattr(alan, "TAVSIYE_ALT_SORULARI", ()):
        if re.search(desen, n) and soru not in bulunan:
            bulunan.append(soru)
        if len(bulunan) >= getattr(alan, "TAVSIYE_ALT_AZAMI", 2):
            break
    return tuple(bulunan)


def tavsiye_sorusu(konu_kodu: str, alt: tuple[str, ...], alan: ModuleType) -> str | None:
    """LLM'e gidecek soru: YALNIZ kapalı listelerden (konu adı + sabit alt sorular)."""
    ad = konu_adi(konu_kodu, alan)
    if ad is None:
        return None
    gecerli = {s for _, s in getattr(alan, "TAVSIYE_ALT_SORULARI", ())}
    ilk = alan.TAVSIYE_SORU.format(konu=ad)
    bas = {"i": "İ", "ı": "I"}.get(ilk[:1], ilk[:1].upper())
    return " ".join([bas + ilk[1:], *(s for s in alt if s in gecerli)])
