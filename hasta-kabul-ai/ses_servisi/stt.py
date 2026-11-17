"""Konuşmadan metne (STT).

Varsayılan: yerel Whisper (faster-whisper / CTranslate2). GPU (CUDA) varsa orada,
yoksa CPU'da int8 çalışır; ses verisi makineden çıkmaz.
Opsiyonel: OpenAI uyumlu ``/audio/transcriptions`` (ör. Groq Whisper) — hibrit profil;
ses buluta gider, KVKK değerlendirmesi yapılmadan açılmamalı.

Kiosk tur tabanlıdır: kayıt bitince tek dosya gelir (webm/opus). Pipecat ses hattı,
VAD ile tur tespiti ve akış (streaming) bu serviste yoktur; faster-whisper'ın kendi
VAD süzgeci yalnız sessizlik kırpmak ve sessizlikte uydurulan metni önlemek için açılır.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ortak.turkce import normalize, tc_maskele

log = logging.getLogger("ses_servisi.stt")

ORNEKLEME_HIZI = 16000


class SesCokUzun(Exception):
    """Çözme sırasında azami süre aşıldı (kayıt sonuna kadar çözülmez)."""

    def __init__(self, azami_sn: float):
        super().__init__(f"en fazla {azami_sn:.0f} sn")
        self.azami_sn = azami_sn


def sesi_coz(veri: bytes, azami_sn: float | None = None) -> Any:
    """Kap/codec fark etmeksizin (webm/opus, ogg, wav, mp3) 16 kHz mono float32 dizisine çevirir.

    faster-whisper'ın ``decode_audio``'su PyAV 19 ile uyumsuz (``metadata_errors`` argümanı
    kaldırıldı); bu yüzden çözme doğrudan PyAV ile yapılır.

    ``azami_sn`` verilirse örnekler çözülürken sayılır ve sınır aşılınca hemen ``SesCokUzun``
    fırlatılır: düşük bit hızlı küçük bir dosya saatlerce ses taşıyıp belleği dolduramaz (O9).
    """
    import av
    import numpy as np

    parcalar = []
    toplam = 0
    sinir = None if azami_sn is None else int(azami_sn * ORNEKLEME_HIZI)
    yeniden = av.AudioResampler(format="s16", layout="mono", rate=ORNEKLEME_HIZI)

    def ekle(dizi: Any) -> None:
        nonlocal toplam
        parcalar.append(dizi)
        toplam += len(dizi)
        if sinir is not None and toplam > sinir:
            raise SesCokUzun(azami_sn or 0)

    with av.open(io.BytesIO(veri), mode="r") as kap:
        if not kap.streams.audio:
            raise ValueError("ses akışı yok")
        for kare in kap.decode(audio=0):
            kare.pts = None
            for k in yeniden.resample(kare):
                ekle(k.to_ndarray().reshape(-1))
        for k in yeniden.resample(None):
            ekle(k.to_ndarray().reshape(-1))
    if not parcalar:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parcalar).astype(np.float32) / 32768.0


class SttHatasi(Exception):
    """durum: önerilen HTTP kodu (400 bozuk ses, 413 çok uzun, 503 servis yok, 504 zaman aşımı)."""

    def __init__(self, mesaj: str, durum: int = 503):
        super().__init__(mesaj)
        self.durum = durum


@dataclass
class SttSonucu:
    metin: str
    maskelendi: bool
    sure_ses_sn: float
    sure_ms: float
    hayalet: bool = False
    cihaz: str = ""


def _cuda_dll_dizinlerini_ekle(ek_dizinler: tuple[str, ...] = ()) -> None:
    """pip'le kurulan nvidia-cublas-cu12 / nvidia-cudnn-cu12 DLL'lerini Windows'ta bulunur yapar.

    ``ek_dizinler`` (STT_CUDA_DLL_DIZINLERI): cuBLAS 12 DLL'leri başka bir yerde kuruluysa
    (ör. CUDA Toolkit ya da Ollama'nın ``lib/ollama/cuda_v12`` klasörü). cuDNN 9 zaten
    ctranslate2 tekerleğinin içinde gelir.
    """
    if sys.platform != "win32":
        return
    dizinler = [Path(d) for d in ek_dizinler if d]
    for kok in map(Path, sys.path):
        nvidia = kok / "nvidia"
        if nvidia.is_dir():
            dizinler.extend(nvidia.glob("*/bin"))
    for bin_dizini in dizinler:
        if not bin_dizini.is_dir():
            log.warning("CUDA DLL dizini bulunamadı: %s", bin_dizini)
            continue
        try:
            os.add_dll_directory(str(bin_dizini))
        except OSError:
            continue
        os.environ["PATH"] = str(bin_dizini) + os.pathsep + os.environ.get("PATH", "")


def hayalet_mi(metin: str, kaliplar: tuple[str, ...]) -> bool:
    """Whisper'ın sessizlikte uydurduğu bilinen kalıplar (ör. "Altyazı M.K.")."""
    n = normalize(metin)
    if not n:
        return True
    return any(normalize(k) and normalize(k) in n for k in kaliplar) and len(n.split()) <= 8


class YerelWhisper:
    def __init__(self, *, model: str, cihaz: str = "auto", hesaplama: str = "auto", dil: str = "tr",
                 isin: int = 1, model_dizini: str | None = None, ipucu: str | None = None,
                 hayalet_kaliplari: tuple[str, ...] = (), cuda_dll_dizinleri: tuple[str, ...] = ()):
        self.model_adi = model
        self.cuda_dll_dizinleri = tuple(cuda_dll_dizinleri)
        self.istenen_cihaz = cihaz
        self.hesaplama = hesaplama
        self.dil = dil
        self.isin = max(1, isin)
        self.model_dizini = model_dizini
        self.ipucu = ipucu
        self.hayalet_kaliplari = hayalet_kaliplari
        self._model: Any = None
        self.cihaz = ""
        self._yukleme_kilidi = threading.Lock()
        self._calisma_kilidi = threading.Lock()  # tek model, sırayla çözümleme

    @property
    def hazir(self) -> bool:
        return self._model is not None

    def yukle(self) -> None:
        with self._yukleme_kilidi:
            if self._model is not None:
                return
            _cuda_dll_dizinlerini_ekle(self.cuda_dll_dizinleri)
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SttHatasi("faster-whisper kurulu değil.") from exc
            adaylar: list[tuple[str, str]] = []
            if self.istenen_cihaz in ("auto", "cuda"):
                try:
                    import ctranslate2
                    if ctranslate2.get_cuda_device_count() > 0:
                        adaylar.append(("cuda", "int8_float16" if self.hesaplama == "auto" else self.hesaplama))
                except Exception:  # noqa: BLE001 — CUDA yoksa CPU'ya düşülür
                    pass
            if self.istenen_cihaz in ("auto", "cpu") or not adaylar:
                adaylar.append(("cpu", "int8" if self.hesaplama == "auto" else self.hesaplama))
            son_hata: Exception | None = None
            for cihaz, hesap in adaylar:
                try:
                    baslangic = time.perf_counter()
                    model = WhisperModel(self.model_adi, device=cihaz, compute_type=hesap,
                                         download_root=self.model_dizini or None)
                    # Isınma: çekirdekleri derler/yükler; CUDA kütüphanesi eksikse burada patlar.
                    import numpy as np
                    list(model.transcribe(np.zeros(ORNEKLEME_HIZI, dtype=np.float32), language=self.dil,
                                          beam_size=1)[0])
                    self._model, self.cihaz = model, f"{cihaz}/{hesap}"
                    log.info("Whisper yüklendi: model=%s cihaz=%s (%.1f sn)", self.model_adi, self.cihaz,
                             time.perf_counter() - baslangic)
                    return
                except Exception as exc:  # noqa: BLE001
                    son_hata = exc
                    log.warning("Whisper %s üzerinde yüklenemedi (%s); sıradaki denenecek.", cihaz,
                                type(exc).__name__)
            raise SttHatasi(f"Whisper modeli yüklenemedi: {type(son_hata).__name__}") from son_hata

    def _cozumle(self, veri: bytes, azami_sn: float) -> SttSonucu:
        baslangic = time.perf_counter()
        try:
            ses = sesi_coz(veri, azami_sn)
        except SesCokUzun as exc:
            raise SttHatasi(f"Kayıt çok uzun; en fazla {azami_sn:.0f} sn.", 413) from exc
        except Exception as exc:  # noqa: BLE001 — PyAV çözülemeyen kap/codec
            raise SttHatasi("Ses dosyası çözülemedi (desteklenen: webm/opus, ogg, wav, mp3).", 400) from exc
        sure_ses = len(ses) / ORNEKLEME_HIZI
        if sure_ses > azami_sn:
            raise SttHatasi(f"Kayıt çok uzun ({sure_ses:.0f} sn); en fazla {azami_sn:.0f} sn.", 413)
        if sure_ses < 0.2:
            return SttSonucu("", False, sure_ses, (time.perf_counter() - baslangic) * 1000, True, self.cihaz)
        self.yukle()
        with self._calisma_kilidi:
            parcalar, _ = self._model.transcribe(
                ses, language=self.dil, beam_size=self.isin, vad_filter=True,
                condition_on_previous_text=False, initial_prompt=self.ipucu,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            metin = " ".join(p.text.strip() for p in parcalar).strip()
        hayalet = hayalet_mi(metin, self.hayalet_kaliplari)
        if hayalet:
            metin = ""
        metin, maskelendi = tc_maskele(metin)
        return SttSonucu(metin, maskelendi, sure_ses, (time.perf_counter() - baslangic) * 1000, hayalet, self.cihaz)

    async def cozumle(self, veri: bytes, *, azami_sn: float, zaman_asimi_sn: float) -> SttSonucu:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._cozumle, veri, azami_sn), zaman_asimi_sn)
        except TimeoutError as exc:
            raise SttHatasi("Ses tanıma zaman aşımına uğradı.", 504) from exc


class BulutStt:
    """OpenAI uyumlu ``/audio/transcriptions`` (ör. Groq: https://api.groq.com/openai/v1)."""

    def __init__(self, *, taban_url: str, anahtar: str | None, model: str, dil: str = "tr",
                 ipucu: str | None = None, hayalet_kaliplari: tuple[str, ...] = ()):
        self.taban_url = taban_url.rstrip("/")
        self.anahtar = anahtar
        self.model_adi = model
        self.dil = dil
        self.ipucu = ipucu
        self.hayalet_kaliplari = hayalet_kaliplari
        self.cihaz = "bulut"
        self.hazir = True

    def yukle(self) -> None:
        return None

    async def cozumle(self, veri: bytes, *, azami_sn: float, zaman_asimi_sn: float) -> SttSonucu:
        baslangic = time.perf_counter()
        basliklar = {"Authorization": f"Bearer {self.anahtar}"} if self.anahtar else {}
        alanlar = {"model": self.model_adi, "language": self.dil, "response_format": "json"}
        if self.ipucu:
            alanlar["prompt"] = self.ipucu
        try:
            async with httpx.AsyncClient(timeout=zaman_asimi_sn) as istemci:
                yanit = await istemci.post(f"{self.taban_url}/audio/transcriptions", headers=basliklar,
                                           data=alanlar, files={"file": ("kayit.webm", veri, "audio/webm")})
        except httpx.TimeoutException as exc:
            raise SttHatasi("Ses tanıma zaman aşımına uğradı.", 504) from exc
        except httpx.TransportError as exc:
            raise SttHatasi("Ses tanıma servisine ulaşılamadı.") from exc
        if yanit.status_code >= 400:
            raise SttHatasi(f"Ses tanıma servisi HTTP {yanit.status_code}.",
                            400 if yanit.status_code in (400, 415) else 503)
        metin = str(yanit.json().get("text") or "").strip()
        hayalet = hayalet_mi(metin, self.hayalet_kaliplari)
        metin, maskelendi = tc_maskele("" if hayalet else metin)
        return SttSonucu(metin, maskelendi, 0.0, (time.perf_counter() - baslangic) * 1000, hayalet, self.cihaz)
