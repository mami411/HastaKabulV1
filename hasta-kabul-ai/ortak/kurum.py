"""Kurum (özel hastane / klinik) yapılandırması — asistanın tek kurum ayar noktası.

Değerler ``hasta-kabul-ai/kurum.json`` dosyasından okunur (varsayılan MedTech Özel Hastanesi, kurgusal demo
kurumu). Başka bir dosya için süreç ortamında ``KURUM_DOSYASI=<yol>`` verilir (``.env`` buraya etki etmez:
şablonlar modül yüklenirken kurulur). Dosya ya da alan yoksa MedTech varsayılanı kullanılır.

Kural: değerler cümlede EKSİZ geçer ("{tam_ad} çağrı merkezi numarası {cagri_merkezi}"); "MedTech'e" gibi
ünlü uyumuna bağlı ekler kurum adına göre değişeceği için şablonlarda kullanılmaz. Ayrıntı: kökteki KURUM.md.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .turkce import sayi_oku

VARSAYILAN_DOSYA = Path(__file__).resolve().parent.parent / "kurum.json"


@dataclass(frozen=True)
class Kurum:
    tam_ad: str = "MedTech Özel Hastanesi"
    kisa_ad: str = "MedTech"
    uygulama_adi: str = "MedTech Cep"
    asistan_adi: str = "MedTech Asistan"
    cagri_merkezi: str = "0850 000 00 00"  # demo numarası, gerçek değil
    adres: str = "Örnek Mahallesi, Şifa Sokağı No: 1, İstanbul"
    calisma_saatleri: str = ("Polikliniklerimiz hafta içi 09:00 ile 17:00 arasında, cumartesi ve pazar 09:00 ile "
                             "13:00 arasında hasta kabul eder; hafta içi öğle arası 12:00 ile 13:00 arasıdır.")
    # TTS okunuş eşlemesi: yazım → sesli okunuş (ör. "MedTech" → "Med tek"). Yalnız sesli kopyaya uygulanır.
    okunuslar: dict[str, str] = field(default_factory=lambda: {"MedTech": "Med tek"})


_METIN_ALANLARI = ("tam_ad", "kisa_ad", "uygulama_adi", "asistan_adi", "cagri_merkezi", "adres", "calisma_saatleri")


def kurum_yukle(yol: str | Path | None = None, ortam: dict[str, str] | None = None) -> Kurum:
    """Kurum dosyasını okur. Bozuk/eksik dosya ya da boş alan varsayılana düşer (servis yine açılır)."""
    kaynak = os.environ if ortam is None else ortam
    dosya = Path(yol or kaynak.get("KURUM_DOSYASI") or VARSAYILAN_DOSYA)
    varsayilan = Kurum()
    try:
        ham: Any = json.loads(dosya.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return varsayilan
    if not isinstance(ham, dict):
        return varsayilan
    degerler: dict[str, Any] = {}
    for ad in _METIN_ALANLARI:
        deger = ham.get(ad)
        if isinstance(deger, str) and deger.strip():
            degerler[ad] = deger.strip()
    okunuslar = ham.get("okunuslar")
    if isinstance(okunuslar, dict):
        degerler["okunuslar"] = {str(k): str(v) for k, v in okunuslar.items() if str(k).strip() and str(v).strip()}
    return Kurum(**{**varsayilan.__dict__, **degerler})


def telefon_okunusu(numara: str) -> str:
    """"0850 000 00 00" → "sıfır sekiz yüz elli, sıfır sıfır sıfır, sıfır sıfır, sıfır sıfır".
    Her grup: baştaki sıfırlar tek tek, kalan sayı bütün okunur (Türkçe telefon okuma alışkanlığı)."""
    parcalar = []
    for grup in re.findall(r"\d+", numara):
        sifirlar = len(grup) - len(grup.lstrip("0"))
        kalan = grup.lstrip("0")
        parcalar.append(" ".join(["sıfır"] * sifirlar + ([sayi_oku(int(kalan))] if kalan else [])))
    return ", ".join(parcalar)


KURUM: Kurum = kurum_yukle()

__all__ = ["KURUM", "Kurum", "kurum_yukle", "telefon_okunusu"]
