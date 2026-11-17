"""Konuşarak randevu ve geçmiş ilaç sorusu: gerçek Whisper + Ollama + Piper ile çok turlu uçtan uca deneme.

Ön koşul: ses servisi (varsayılan 8002) ve sahte API (3000) ayakta; ffmpeg PATH'te.
    .venv\\Scripts\\python.exe tools\\konusma_uctan_uca.py [--cikti KLASOR] [--yakalama-url URL]

Her kullanıcı cümlesi Piper ile seslendirilir, tarayıcının gönderdiği biçime (webm/opus 48 kHz)
çevrilir, /stt'ye gider; çıkan metin /orchestrate'e gönderilir; asistanın yanıtı /tts ile
seslendirilir. Tur başına STT / anlama+iş / TTS süreleri ölçülür. Cümleler sentetiktir.

``--yakalama-url``: LLM'e giden gerçek istek gövdelerini görmek için, LLM adresi kayıt
vekiline (tools dışı, geçici) yönlendirilmiş İKİNCİ bir ses servisi örneğinin adresi. Verilirse
LLM'e düşmesi beklenen metin turları o örneğe gönderilir; gövdeler vekilin dosyasından okunur.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

SES = os.environ.get("UCTAN_SES_URL", "http://127.0.0.1:8002")
API = os.environ.get("UCTAN_API_URL", "http://localhost:3000")
DEMO_TC = "10000000146"  # backend README'deki sentetik demo hastası

RANDEVU_TURLARI = [
    "Kardiyolojiden randevu almak istiyorum.",
    "Yarın olsun.",
    "Sabah olsun.",
    "İkincisi olsun.",
    "Evet, onaylıyorum.",
]
GECMIS_TURLARI = [
    "Son muayenemde hangi ilaçlar yazıldı?",
    "Metformini ne zaman kullanacağım?",
    "Demir ilacımı çayla birlikte alabilir miyim?",
]
# Kuralın çözemeyip LLM'e düşmesi beklenen, kişisel veri gömülü cümleler (maskeleme kanıtı).
LLM_TURLARI = [
    "Ben Ayşe Yılmaz, kalbim için bir bakılmasını rica ediyorum.",
    "Adım Mehmet Kaya, telefonum 123 45 67, şu tansiyon işine bakan birimi istiyorum.",
    "Aspirin nasıl kullanılır?",
]


def zaman(yanit: httpx.Response, ad: str) -> float | None:
    for parca in yanit.headers.get("Server-Timing", "").split(","):
        parca = parca.strip()
        if parca.startswith(ad + ";dur="):
            return round(float(parca.split("=", 1)[1]))
    return None


def webm(c: httpx.Client, metin: str, klasor: Path, ad: str) -> bytes:
    r = c.post(f"{SES}/tts", json={"text": metin})
    assert r.status_code == 200, r.text[:200]
    giris, cikis = klasor / f"{ad}.wav", klasor / f"{ad}.webm"
    giris.write_bytes(r.content)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(giris), "-ar", "48000", "-ac", "1",
                    "-c:a", "libopus", "-b:a", "32k", str(cikis)], check=True)
    return cikis.read_bytes()


def oturum(c: httpx.Client, taban: str) -> str:
    oid = c.post(f"{API}/api/v1/voice-sessions").json()["sessionId"]
    c.post(f"{taban}/orchestrate", json={"sessionId": oid, "event": "START"})
    r = c.post(f"{API}/api/v1/voice-sessions/{oid}/verify", json={"tc": DEMO_TC}).json()  # arayüzün işi
    assert r["verified"], r
    return oid


def sesli_tur(c: httpx.Client, oid: str, cumle: str, klasor: Path, ad: str) -> dict:
    ses = webm(c, cumle, klasor, ad)
    bas = time.perf_counter()
    s = c.post(f"{SES}/stt", files={"file": ("recording.webm", ses, "audio/webm")})
    stt_ms = round((time.perf_counter() - bas) * 1000)
    metin = s.json()["text"]
    bas = time.perf_counter()
    r = c.post(f"{SES}/orchestrate", json={"sessionId": oid, "userText": metin})
    ork_ms = round((time.perf_counter() - bas) * 1000)
    g = r.json()
    bas = time.perf_counter()
    t = c.post(f"{SES}/tts", json={"text": g["replyText"]})
    tts_ms = round((time.perf_counter() - bas) * 1000)
    return {"soylenen": cumle, "duyulan": metin, "http": r.status_code, "action": g["action"],
            "expect": g["data"].get("expect"), "listen": g["data"].get("listen"),
            "kaynak": r.headers.get("X-Anlama-Kaynagi"), "yanit": g["replyText"],
            "slotlar": [x["label"] for x in g["data"].get("slots", [])][:6],
            "erisimKodu": g["data"].get("accessCode"),
            "gecmis_kart": bool(g["data"].get("history")),
            "sure_ms": {"stt": stt_ms, "orkestrasyon": ork_ms, "llm_sunucu": zaman(r, "llm"),
                        "tts": tts_ms, "tts_sunucu": zaman(t, "tts"), "tur_toplam": stt_ms + ork_ms + tts_ms}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cikti", default=tempfile.mkdtemp(prefix="hk-konusma-"))
    ap.add_argument("--yakalama-url", default="")
    args = ap.parse_args()
    klasor = Path(args.cikti)
    klasor.mkdir(parents=True, exist_ok=True)
    c = httpx.Client(timeout=120)
    rapor: dict = {"cikti": str(klasor), "saglik": c.get(f"{SES}/health").json()}
    c.post(f"{API}/__mock/reset")

    oid = oturum(c, SES)
    r = c.post(f"{SES}/orchestrate", json={"sessionId": oid, "event": "TC_VERIFIED"}).json()
    randevu = [{"olay": "TC_VERIFIED", "action": r["action"], "expect": r["data"].get("expect"), "yanit": r["replyText"]}]
    for i, cumle in enumerate(RANDEVU_TURLARI):
        randevu.append(sesli_tur(c, oid, cumle, klasor, f"randevu_{i}"))
    rapor["randevu"] = randevu
    c.post(f"{SES}/orchestrate", json={"sessionId": oid, "event": "END"})

    oid = oturum(c, SES)
    c.post(f"{SES}/orchestrate", json={"sessionId": oid, "event": "TC_VERIFIED"})
    rapor["gecmis"] = [sesli_tur(c, oid, cumle, klasor, f"gecmis_{i}") for i, cumle in enumerate(GECMIS_TURLARI)]
    r = c.post(f"{SES}/orchestrate", json={"sessionId": oid, "event": "END"})
    rapor["end"] = r.json()

    if args.yakalama_url:
        oid = oturum(c, args.yakalama_url)
        c.post(f"{args.yakalama_url}/orchestrate", json={"sessionId": oid, "event": "TC_VERIFIED"})
        c.post(f"{args.yakalama_url}/orchestrate", json={"sessionId": oid, "userText": "son muayenemde ne yazıldı"})
        turlar = []
        for cumle in LLM_TURLARI:
            bas = time.perf_counter()
            r = c.post(f"{args.yakalama_url}/orchestrate", json={"sessionId": oid, "userText": cumle})
            turlar.append({"cumle": cumle, "http": r.status_code, "kaynak": r.headers.get("X-Anlama-Kaynagi"),
                           "yanit": r.json()["replyText"], "ms": round((time.perf_counter() - bas) * 1000)})
        rapor["llm_yakalama_turlari"] = turlar
        c.post(f"{args.yakalama_url}/orchestrate", json={"sessionId": oid, "event": "END"})

    print(json.dumps(rapor, ensure_ascii=False, indent=2))
    (klasor / "rapor.json").write_text(json.dumps(rapor, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
