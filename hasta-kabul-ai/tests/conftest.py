"""Ortak test düzeneği: sabit saat, sahte API, sahte LLM, sahte STT/TTS.

Hiçbir test ağa çıkmaz, ücretli çağrı yapmaz, model indirmez.
"""

from __future__ import annotations

import io
import sys
import wave
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from ortak.llm import SahteSaglayici  # noqa: E402
from sahte_api import ANAHTAR, SahteApi  # noqa: E402
from ses_servisi.alanlar import hasta_kabul  # noqa: E402
from ses_servisi.api_istemcisi import HastaKabulApi  # noqa: E402
from ses_servisi.app import uygulama_olustur  # noqa: E402
from ses_servisi.ayarlar import SesAyarlari  # noqa: E402
from ses_servisi.stt import SttHatasi, SttSonucu  # noqa: E402
from ses_servisi.tts import TtsHatasi, TtsSonucu  # noqa: E402

# 15 Kasım 2026 Pazar 19:36 (İstanbul) = 16:36 UTC → bugünün 09–12 slotları geçmişte kalır.
SIMDI = datetime(2026, 11, 15, 16, 36, tzinfo=timezone.utc)
BUGUN = date(2026, 11, 15)
YARIN = date(2026, 11, 16)


def sabit_saat() -> datetime:
    return SIMDI


class SahteStt:
    hazir = True
    cihaz = "sahte"
    model_adi = "sahte"

    def __init__(self, metin: str = "kardiyoloji yarın sabah", hata: SttHatasi | None = None):
        self.metin = metin
        self.hata = hata
        self.alinanlar: list[bytes] = []

    def yukle(self) -> None:
        return None

    async def cozumle(self, veri: bytes, *, azami_sn: float, zaman_asimi_sn: float) -> SttSonucu:
        self.alinanlar.append(veri)
        if self.hata:
            raise self.hata
        from ortak.turkce import tc_maskele
        metin, maskeli = tc_maskele(self.metin)
        return SttSonucu(metin, maskeli, 1.5, 12.0, False, "sahte")


def bos_wav() -> bytes:
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 2205)
    return tampon.getvalue()


class SahteTts:
    hazir = True

    def __init__(self, hata: TtsHatasi | None = None):
        self.hata = hata
        self.metinler: list[str] = []

    def yukle(self) -> None:
        return None

    async def sentezle(self, metin: str, *, zaman_asimi_sn: float) -> TtsSonucu:
        self.metinler.append(metin)
        if self.hata:
            raise self.hata
        return TtsSonucu(bos_wav(), 5.0, False, 0.1)


def ayarlar(**degisen: str) -> SesAyarlari:
    ortam = {"SERVICE_KEY": ANAHTAR, "HASTAKABUL_API_URL": "http://sahte-api", "STT_ONCEDEN_YUKLE": "0",
             "TTS_ONCEDEN_YUKLE": "0", "ARAMA_GUN_SAYISI": "3", "SUNULAN_SLOT_SAYISI": "6"}
    ortam.update(degisen)
    return SesAyarlari.ortamdan(ortam)


class Kurulum:
    def __init__(self, llm: SahteSaglayici | None, stt: SahteStt | None = None, tts: SahteTts | None = None,
                 **ayar: str):
        self.api = SahteApi([BUGUN, YARIN], simdi=SIMDI)
        self.llm = llm
        self.stt = stt or SahteStt()
        self.tts = tts or SahteTts()
        a = ayarlar(**ayar)
        istemci = HastaKabulApi(a.api_url, a.servis_anahtari, zaman_asimi_sn=2,
                                is_mantigi_hatalari=hasta_kabul.IS_MANTIGI_HATALARI,
                                transport=self.api.transport())
        self.app = uygulama_olustur(a, llm=llm, api=istemci, stt=self.stt, tts=self.tts, saat=sabit_saat,
                                    onceden_yukle=False)
        self.client = TestClient(self.app)

    def orkestre(self, oturum: str, **govde):
        return self.client.post("/orchestrate", json={"sessionId": oturum, **govde})


@pytest.fixture
def kur():
    kurulumlar: list[Kurulum] = []

    def _kur(llm: SahteSaglayici | None = None, **kw) -> Kurulum:
        k = Kurulum(llm, **kw)
        k.client.__enter__()
        kurulumlar.append(k)
        return k

    yield _kur
    for k in kurulumlar:
        k.client.__exit__(None, None, None)
