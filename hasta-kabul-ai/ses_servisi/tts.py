"""Metinden konuşmaya (TTS): yerel Piper, Türkçe ses modeli. Çıktı WAV (tarayıcıda çalınır).

Metin TTS'e gitmeden önce konu modülünün ``tts_on_isleme`` işlevinden geçer (saatler
doğal okunuşa, erişim kodu rakam rakam). Aynı cümleler (selamlama vb.) önbellekten döner.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("ses_servisi.tts")


class TtsHatasi(Exception):
    def __init__(self, mesaj: str, durum: int = 503):
        super().__init__(mesaj)
        self.durum = durum


@dataclass
class TtsSonucu:
    wav: bytes
    sure_ms: float
    onbellekten: bool
    sure_ses_sn: float


class PiperTts:
    def __init__(self, *, model_yolu: str, konusma_hizi: float = 1.0, cuda: bool = False,
                 onbellek_boyutu: int = 64, onbellek_suzgeci: Callable[[str], bool] | None = None):
        # KVKK: önbellek yalnız süzgecin izin verdiği (sabit, kişisel veri içermeyen) metinleri tutar.
        # Hasta verili metin (geçmiş muayene, erişim kodu) bellekte kalmaz ve X-Onbellek başlığı
        # "bu metin az önce okundu mu" sorusuna kâhin olamaz.
        self._onbellek_suzgeci = onbellek_suzgeci
        self.model_yolu = model_yolu
        self.konusma_hizi = konusma_hizi
        self.cuda = cuda
        self._ses: Any = None
        self._yukleme_kilidi = threading.Lock()
        self._calisma_kilidi = threading.Lock()
        self._onbellek: OrderedDict[str, bytes] = OrderedDict()
        self._onbellek_boyutu = onbellek_boyutu
        self._onbellek_kilidi = threading.Lock()

    @property
    def hazir(self) -> bool:
        return self._ses is not None

    def yukle(self) -> None:
        with self._yukleme_kilidi:
            if self._ses is not None:
                return
            yol = Path(self.model_yolu)
            if not yol.is_file() or not Path(str(yol) + ".json").is_file():
                raise TtsHatasi(f"Piper ses modeli bulunamadı: {yol.name} (.onnx ve .onnx.json gerekli).")
            try:
                from piper import PiperVoice
            except ImportError as exc:
                raise TtsHatasi("piper-tts kurulu değil.") from exc
            baslangic = time.perf_counter()
            self._ses = PiperVoice.load(str(yol), use_cuda=self.cuda)
            log.info("Piper yüklendi: %s (%.1f sn)", yol.name, time.perf_counter() - baslangic)

    def _sentezle(self, metin: str) -> bytes:
        self.yukle()
        tampon = io.BytesIO()
        with self._calisma_kilidi, wave.open(tampon, "wb") as wav:
            try:
                from piper import SynthesisConfig  # piper-tts >= 1.3
                self._ses.synthesize_wav(metin, wav, syn_config=SynthesisConfig(length_scale=1 / self.konusma_hizi))
            except ImportError:  # eski API
                self._ses.synthesize(metin, wav, length_scale=1 / self.konusma_hizi)
        return tampon.getvalue()

    def _sentezle_onbellekli(self, metin: str) -> TtsSonucu:
        baslangic = time.perf_counter()
        if self._onbellek_suzgeci is not None and not self._onbellek_suzgeci(metin):
            wav = self._sentezle(metin)
            with wave.open(io.BytesIO(wav), "rb") as w:
                sure_ses = w.getnframes() / float(w.getframerate() or 1)
            return TtsSonucu(wav, (time.perf_counter() - baslangic) * 1000, False, sure_ses)
        with self._onbellek_kilidi:
            hazir = self._onbellek.get(metin)
            if hazir is not None:
                self._onbellek.move_to_end(metin)
        if hazir is None:
            hazir = self._sentezle(metin)
            with self._onbellek_kilidi:
                self._onbellek[metin] = hazir
                while len(self._onbellek) > self._onbellek_boyutu:
                    self._onbellek.popitem(last=False)
            onbellekten = False
        else:
            onbellekten = True
        with wave.open(io.BytesIO(hazir), "rb") as w:
            sure_ses = w.getnframes() / float(w.getframerate() or 1)
        return TtsSonucu(hazir, (time.perf_counter() - baslangic) * 1000, onbellekten, sure_ses)

    async def sentezle(self, metin: str, *, zaman_asimi_sn: float) -> TtsSonucu:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._sentezle_onbellekli, metin), zaman_asimi_sn)
        except TimeoutError as exc:
            raise TtsHatasi("Seslendirme zaman aşımına uğradı.", 504) from exc
        except TtsHatasi:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TtsHatasi(f"Seslendirme başarısız ({type(exc).__name__}).") from exc
