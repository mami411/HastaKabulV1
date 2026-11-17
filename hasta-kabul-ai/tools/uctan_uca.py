"""Gerçek modellerle uçtan uca deneme ve gecikme ölçümü (Ollama + Whisper + Piper + sahte/gerçek API).

Ön koşul: servis 8002'de, API 3000'de ayakta; ffmpeg PATH'te.
    .venv\\Scripts\\python.exe tools\\uctan_uca.py [--cikti KLASOR] [--llm-kapali-dene]

Ses örnekleri Piper ile üretilir, tarayıcının gönderdiği biçime (webm/opus 48 kHz) ffmpeg ile
çevrilir ve /stt'ye yollanır. Cümleler sentetiktir; gerçek hasta verisi kullanılmaz.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))

from ortak.turkce import normalize  # noqa: E402

SES = os.environ.get("UCTAN_SES_URL", "http://127.0.0.1:8002")
API = os.environ.get("UCTAN_API_URL", "http://localhost:3000")
DEMO_TC = "10000000146"  # backend README'deki sentetik demo hastası

STT_CUMLELERI = [
    "Kardiyolojiye yarın sabah randevu almak istiyorum.",
    "Dahiliye polikliniği için öğleden sonra bir saat var mı?",
    "Göz doktoruna pazartesi saat on buçukta gidebilir miyim?",
    "Evet, onaylıyorum.",
    "Ortopediye yarın saat dokuzda bakar mısınız?",
]
LLM_CUMLELERI = [
    "kardiyolojiye yarın sabah randevu almak istiyorum",
    "dahiliye için öğleden sonra bir saat var mı",
    "göz doktoruna yarın saat on buçukta",
    "kalp polikliniği olsun, en erken saat",
    "otopark ücreti ne kadar",
]


def kelime_hata_orani(referans: str, tahmin: str) -> float:
    r, t = normalize(referans).split(), normalize(tahmin).split()
    d = list(range(len(t) + 1))
    for i, rk in enumerate(r, 1):
        onceki, d[0] = d[0], i
        for j, tk in enumerate(t, 1):
            onceki, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, onceki + (rk != tk))
    return d[len(t)] / max(1, len(r))


def sunucu_zamani(yanit: httpx.Response, ad: str) -> float | None:
    for parca in yanit.headers.get("Server-Timing", "").split(","):
        parca = parca.strip()
        if parca.startswith(ad + ";dur="):
            return float(parca.split("=", 1)[1])
    return None


def webm_yap(wav: bytes, klasor: Path, ad: str) -> bytes:
    giris, cikis = klasor / f"{ad}.wav", klasor / f"{ad}.webm"
    giris.write_bytes(wav)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(giris), "-ar", "48000", "-ac", "1",
                    "-c:a", "libopus", "-b:a", "32k", str(cikis)], check=True)
    return cikis.read_bytes()


def oturum_ac(c: httpx.Client, dogrula: bool = True) -> str:
    oid = c.post(f"{API}/api/v1/voice-sessions").json()["sessionId"]
    if dogrula:  # arayüzün işi: TC doğrudan API'ye gider, orkestratör görmez
        r = c.post(f"{API}/api/v1/voice-sessions/{oid}/verify", json={"tc": DEMO_TC}).json()
        assert r["verified"], r
    return oid


def orkestre(c: httpx.Client, taban: str, oid: str, **govde) -> httpx.Response:
    return c.post(f"{taban}/orchestrate", json={"sessionId": oid, **govde})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cikti", default=tempfile.mkdtemp(prefix="hk-uctan-"))
    ap.add_argument("--llm-kapali-dene", action="store_true", help="LLM'siz ikinci örnek açıp yedek akışı dene")
    args = ap.parse_args()
    klasor = Path(args.cikti)
    klasor.mkdir(parents=True, exist_ok=True)
    rapor: dict = {"cikti": str(klasor)}
    c = httpx.Client(timeout=120)

    saglik = c.get(f"{SES}/health").json()
    rapor["saglik"] = saglik
    c.post(f"{API}/__mock/reset")  # sahte API ise demo verisini sıfırla (gerçek API'de 404, sorun değil)

    # ---------------- TTS
    tts_sureler, tts_ilk = [], None
    for i, cumle in enumerate(["Hoş geldiniz. Randevunuz 16 Kasım Pazartesi saat 09:30. Erişim kodunuz 246802."]
                              + STT_CUMLELERI):
        bas = time.perf_counter()
        r = c.post(f"{SES}/tts", json={"text": cumle})
        sure = (time.perf_counter() - bas) * 1000
        assert r.status_code == 200 and r.content[:4] == b"RIFF", (r.status_code, r.text[:200])
        (klasor / f"tts_{i}.wav").write_bytes(r.content)
        if i == 0:
            tts_ilk = sure
        else:
            tts_sureler.append(sunucu_zamani(r, "tts") or sure)
    tekrar = c.post(f"{SES}/tts", json={"text": STT_CUMLELERI[0]})
    rapor["tts"] = {"ilk_istek_ms": round(tts_ilk or 0), "medyan_ms": round(statistics.median(tts_sureler)),
                    "onbellekten_ms": round(sunucu_zamani(tekrar, "tts") or 0),
                    "onbellek_basligi": tekrar.headers.get("X-Onbellek")}

    # ---------------- STT (Piper sesi → webm/opus → /stt)
    stt = []
    for i, cumle in enumerate(STT_CUMLELERI):
        webm = webm_yap((klasor / f"tts_{i + 1}.wav").read_bytes(), klasor, f"stt_{i}")
        bas = time.perf_counter()
        r = c.post(f"{SES}/stt", files={"file": ("recording.webm", webm, "audio/webm")})
        sure = (time.perf_counter() - bas) * 1000
        assert r.status_code == 200, (r.status_code, r.text[:200])
        metin = r.json()["text"]
        stt.append({"referans": cumle, "metin": metin, "wer": round(kelime_hata_orani(cumle, metin), 3),
                    "sunucu_ms": round(sunucu_zamani(r, "stt") or 0), "toplam_ms": round(sure),
                    "ses_sn": r.json()["durationSec"]})
    rapor["stt"] = {"ornekler": stt, "ortalama_wer": round(statistics.mean(s["wer"] for s in stt), 3),
                    "medyan_ms": round(statistics.median(s["sunucu_ms"] for s in stt))}

    # ---------------- LLM gecikmesi (anlama)
    llm_sureler = []
    for cumle in LLM_CUMLELERI:
        oid = oturum_ac(c)
        orkestre(c, SES, oid, event="TC_VERIFIED")
        r = orkestre(c, SES, oid, userText=cumle)
        llm_sureler.append({"cumle": cumle, "llm_ms": round(sunucu_zamani(r, "llm") or 0),
                            "toplam_ms": round(sunucu_zamani(r, "toplam") or 0), "durum": r.status_code,
                            "action": r.json()["action"], "kaynak": r.headers.get("X-Anlama-Kaynagi"),
                            "yanit": r.json()["replyText"][:120],
                            "slotlar": [s["label"] + " · " + s["detail"] for s in r.json()["data"].get("slots", [])][:3]})
    rapor["llm"] = {"ornekler": llm_sureler, "medyan_ms": round(statistics.median(x["llm_ms"] for x in llm_sureler))}

    # ---------------- Tam akış: START → TC → serbest metin (STT çıktısı) → slot → onay → kod
    akis = []
    oid = oturum_ac(c, dogrula=False)
    r = orkestre(c, SES, oid, event="START"); akis.append(("START", r.status_code, r.json()["action"]))
    c.post(f"{API}/api/v1/voice-sessions/{oid}/verify", json={"tc": DEMO_TC})
    r = orkestre(c, SES, oid, event="TC_VERIFIED"); akis.append(("TC_VERIFIED", r.status_code, r.json()["action"]))
    r = orkestre(c, SES, oid, userText=stt[0]["metin"])
    akis.append(("userText=" + stt[0]["metin"], r.status_code, r.json()["action"]))
    slotlar = r.json()["data"]["slots"]
    assert slotlar, r.json()
    r = orkestre(c, SES, oid, event="SLOT_SELECTED", data={"slotId": slotlar[0]["slotId"]})
    akis.append(("SLOT_SELECTED " + slotlar[0]["label"] + " · " + slotlar[0]["detail"], r.status_code, r.json()["action"]))
    r = orkestre(c, SES, oid, userText="Onaylıyorum")
    akis.append(("Onaylıyorum", r.status_code, r.json()["action"]))
    done = r.json()
    assert done["action"] == "DONE" and done["data"]["accessCode"], done
    rapor["tam_akis"] = {"adimlar": akis, "erisim_kodu": done["data"]["accessCode"],
                         "scheduledAt_utc": done["data"]["scheduledAt"], "etiket_yerel": done["data"]["label"],
                         "yanit": done["replyText"]}

    # ---------------- Hata yolları
    hatalar = {}
    oid = oturum_ac(c)
    s = orkestre(c, SES, oid, event="TC_VERIFIED").json()["data"]["slots"]
    orkestre(c, SES, oid, event="SLOT_SELECTED", data={"slotId": s[0]["slotId"]})
    c.post(f"{API}/__mock/slots/{s[0]['slotId']}/take")  # başka hasta aldı
    r = orkestre(c, SES, oid, userText="Onaylıyorum")
    hatalar["dolu_slot"] = {"http": r.status_code, "code": r.json().get("error", {}).get("code"),
                            "action": r.json()["action"], "yanit": r.json()["replyText"],
                            "eski_slot_listede": s[0]["slotId"] in [x["slotId"] for x in r.json()["data"]["slots"]]}
    oid = oturum_ac(c, dogrula=False)
    orkestre(c, SES, oid, event="START")
    r = orkestre(c, SES, oid, event="TC_VERIFIED")
    hatalar["dogrulanmamis_oturum"] = {"http": r.status_code, "code": r.json().get("error", {}).get("code"),
                                       "action": r.json()["action"], "yanit": r.json()["replyText"]}
    r = orkestre(c, SES, oid, userText="numaram 10000000146")
    hatalar["tc_sesli"] = {"http": r.status_code, "yanit": r.json()["replyText"], "tc_yanitta": DEMO_TC in r.text}
    rapor["hata_yollari"] = hatalar

    # ---------------- LLM kapalı örnek (ayrı süreç, LLM adresi kapalı port)
    if args.llm_kapali_dene:
        ortam = dict(os.environ, SES_PORT="8092", SES_LLM_TABAN_URL="http://127.0.0.1:9/v1", LLM_YENIDEN_DENEME="0",
                     STT_ONCEDEN_YUKLE="0", TTS_ONCEDEN_YUKLE="0", PYTHONIOENCODING="utf-8")
        surec = subprocess.Popen([sys.executable, "-m", "ses_servisi"], cwd=KOK, env=ortam,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            taban = "http://127.0.0.1:8092"
            for _ in range(60):
                try:
                    if c.get(f"{taban}/health").status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(0.5)
            k = {}
            oid = oturum_ac(c)
            orkestre(c, taban, oid, event="TC_VERIFIED")
            r = orkestre(c, taban, oid, userText="göz polikliniği yarın sabah")
            k["anlasilir_metin"] = {"http": r.status_code, "action": r.json()["action"], "degraded": r.json()["data"].get("degraded"),
                                    "kaynak": r.headers.get("X-Anlama-Kaynagi"), "slot_sayisi": len(r.json()["data"]["slots"])}
            r = orkestre(c, taban, oid, userText="şey nasıl desem")
            k["anlasilmaz_metin"] = {"http": r.status_code, "code": r.json().get("error", {}).get("code"),
                                     "action": r.json()["action"], "yanit": r.json()["replyText"]}
            slot = r.json()["data"]["slots"][0]["slotId"]
            orkestre(c, taban, oid, event="SLOT_SELECTED", data={"slotId": slot})
            r = orkestre(c, taban, oid, userText="Onaylıyorum")
            k["dugmeyle_tamamlama"] = {"http": r.status_code, "action": r.json()["action"],
                                       "erisim_kodu_var": bool(r.json()["data"].get("accessCode"))}
            rapor["llm_kapali"] = k
        finally:
            surec.terminate()
            surec.wait(10)

    print(json.dumps(rapor, ensure_ascii=False, indent=2))
    (klasor / "rapor.json").write_text(json.dumps(rapor, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
