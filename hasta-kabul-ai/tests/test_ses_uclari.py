"""/stt ve /tts uçları (sahte STT/TTS), hata yedekleri, hayalet transkript, KVKK logları."""

import logging

from conftest import SahteStt, SahteTts
from ortak.llm import SahteSaglayici
from ses_servisi.stt import SttHatasi, hayalet_mi
from ses_servisi.tts import TtsHatasi


def test_stt_metni_dondurur_ve_tc_maskeler(kur):
    k = kur(None, stt=SahteStt("kimliğim 10000000146 kardiyoloji"))
    r = k.client.post("/stt", files={"file": ("recording.webm", b"\x1aE\xdf\xa3sahte", "audio/webm")})
    assert r.status_code == 200
    assert r.json()["masked"] is True
    assert "10000000146" not in r.json()["text"] and "kardiyoloji" in r.json()["text"]
    assert r.headers["Server-Timing"].startswith("stt;dur=")


def test_stt_hatalari_anlamli_yanit(kur):
    k = kur(None, stt=SahteStt(hata=SttHatasi("model yok", 503)))
    r = k.client.post("/stt", files={"file": ("r.webm", b"x", "audio/webm")})
    assert r.status_code == 503 and r.json()["error"]["code"] == "STT_FAILED"
    assert r.json()["replyText"]
    k2 = kur(None, stt=SahteStt(hata=SttHatasi("çözülemedi", 400)))
    r = k2.client.post("/stt", files={"file": ("r.webm", b"x", "audio/webm")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert k2.client.post("/stt").status_code == 400
    assert k2.client.post("/stt", files={"file": ("r.webm", b"", "audio/webm")}).status_code == 400


def test_stt_boyut_siniri(kur):
    k = kur(None, STT_AZAMI_BAYT="10")
    r = k.client.post("/stt", files={"file": ("r.webm", b"x" * 11, "audio/webm")})
    assert r.status_code == 413


def test_hayalet_transkript():
    from ses_servisi.alanlar.hasta_kabul import HAYALET_KALIPLAR
    assert hayalet_mi("Altyazı M.K.", HAYALET_KALIPLAR)
    assert hayalet_mi("İzlediğiniz için teşekkürler.", HAYALET_KALIPLAR)
    assert hayalet_mi("", HAYALET_KALIPLAR)
    assert not hayalet_mi("Kardiyolojiye yarın sabah randevu istiyorum.", HAYALET_KALIPLAR)


def test_tts_wav_ve_on_isleme(kur):
    tts = SahteTts()
    k = kur(None, tts=tts)
    r = k.client.post("/tts", json={"text": "Randevunuz saat 14:30. Erişim kodunuz 246802."})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"
    assert "on dört buçuk" in tts.metinler[0]
    assert "iki dört altı sekiz sıfır iki" in tts.metinler[0]


def test_tts_hatalari(kur):
    k = kur(None, tts=SahteTts(hata=TtsHatasi("model yok")))
    r = k.client.post("/tts", json={"text": "Merhaba"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "TTS_FAILED" and r.json()["replyText"]
    assert k.client.post("/tts", json={}).status_code == 400
    assert k.client.post("/tts", json={"text": "a" * 601}).status_code == 413


def test_istek_kimligi_tasinir(kur):
    k = kur(None)
    r = k.client.get("/health", headers={"X-Request-Id": "abc12345def"})
    assert r.headers["X-Request-Id"] == "abc12345def"
    assert r.json()["status"] == "ok"
    assert "test-servis-anahtari" not in r.text  # servis anahtarı hiçbir yanıtta yok


def test_loglarda_hasta_sozu_yok(kur, caplog):
    llm = SahteSaglayici(varsayilan='{"niyet":"konu_disi","poliklinik":null,"gun":null,'
                                    '"saat_baslangic":null,"saat_bitis":null,"yanit":""}')
    k = kur(llm, stt=SahteStt("Ayşe Hanımeli 10000000146"))
    caplog.set_level(logging.DEBUG)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    k.orkestre(oid, userText="Benim adım Ayşe Hanımeli, midem ağrıyor")
    k.orkestre(oid, userText="hava nasıl Ayşe")
    k.client.post("/stt", files={"file": ("r.webm", b"x", "audio/webm")})
    k.client.post("/tts", json={"text": "Ayşe Hanım merhaba"})
    tum_log = "\n".join(r.getMessage() for r in caplog.records)
    for yasak in ("Ayşe", "Hanımeli", "10000000146", "midem", "test-servis-anahtari"):
        assert yasak not in tum_log
    assert "path=/orchestrate" in tum_log  # istek satırı yine de var


def test_sesi_coz_wav_ve_bozuk_veri():
    """Gerçek PyAV çözücü (faster-whisper decode_audio PyAV 19 ile uyumsuz olduğu için kendi çözücümüz)."""
    import io
    import struct
    import wave

    import pytest

    from ses_servisi.stt import sesi_coz

    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(struct.pack("<hh", 16384, 16384) * 48000)  # 1 sn stereo 48 kHz
    ses = sesi_coz(tampon.getvalue())
    assert abs(len(ses) / 16000 - 1.0) < 0.05
    assert ses.dtype.name == "float32" and 0.4 < float(abs(ses).max()) <= 1.0
    with pytest.raises(Exception):
        sesi_coz(b"bozuk veri")
