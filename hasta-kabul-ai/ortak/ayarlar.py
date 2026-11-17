"""Ortam değişkeni okuma yardımcıları.

Kural: model adı, port, zaman aşımı, anahtar gibi hiçbir değer koda gömülmez;
hepsi ortam değişkeninden okunur. ``.env`` dosyası varsa yüklenir ama gerçek
ortam değişkenlerinin üzerine yazmaz.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

_YUKLENDI = False


def env_yukle(dizin: str | Path | None = None) -> None:
    """Proje kökündeki ``.env`` dosyasını (varsa) bir kez yükler."""
    global _YUKLENDI
    if _YUKLENDI:
        return
    _YUKLENDI = True
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv yoksa yalnız gerçek ortam kullanılır
        return
    kok = Path(dizin) if dizin else Path(__file__).resolve().parent.parent
    dosya = kok / ".env"
    if dosya.is_file():
        load_dotenv(dosya, override=False)


class AyarHatasi(ValueError):
    """Bir ortam değişkeni okunamadı ya da geçersiz."""


def _ham(ad: str, ortam: Mapping[str, str] | None) -> str | None:
    kaynak = os.environ if ortam is None else ortam
    deger = kaynak.get(ad)
    if deger is None:
        return None
    deger = deger.strip()
    return deger if deger != "" else None


def ortam_str(ad: str, varsayilan: str | None = None, ortam: Mapping[str, str] | None = None) -> str | None:
    deger = _ham(ad, ortam)
    return varsayilan if deger is None else deger


def ortam_int(ad: str, varsayilan: int, ortam: Mapping[str, str] | None = None) -> int:
    deger = _ham(ad, ortam)
    if deger is None:
        return varsayilan
    try:
        return int(deger)
    except ValueError as exc:
        raise AyarHatasi(f"{ad} tam sayı olmalı.") from exc


def ortam_float(ad: str, varsayilan: float, ortam: Mapping[str, str] | None = None) -> float:
    deger = _ham(ad, ortam)
    if deger is None:
        return varsayilan
    try:
        return float(deger.replace(",", "."))
    except ValueError as exc:
        raise AyarHatasi(f"{ad} sayı olmalı.") from exc


def ortam_bool(ad: str, varsayilan: bool, ortam: Mapping[str, str] | None = None) -> bool:
    deger = _ham(ad, ortam)
    if deger is None:
        return varsayilan
    return deger.lower() in {"1", "true", "evet", "on", "yes", "acik", "açık"}


def ortam_liste(ad: str, varsayilan: list[str], ortam: Mapping[str, str] | None = None) -> list[str]:
    deger = _ham(ad, ortam)
    if deger is None:
        return list(varsayilan)
    return [parca.strip() for parca in deger.split(",") if parca.strip()]


def onekli(onek: str, ad: str, ortam: Mapping[str, str] | None = None, genel_onek: str = "LLM") -> str | None:
    """``{onek}_{ad}`` yoksa ``{genel_onek}_{ad}`` değerini döndürür.

    Örnek: ``onekli("SES_LLM", "MODEL")`` önce ``SES_LLM_MODEL``, yoksa ``LLM_MODEL``.
    Böylece servisler ortak varsayılanı paylaşır, gerekirse kendi değerini verir.
    """
    deger = _ham(f"{onek}_{ad}", ortam)
    if deger is None and onek != genel_onek:
        deger = _ham(f"{genel_onek}_{ad}", ortam)
    return deger
