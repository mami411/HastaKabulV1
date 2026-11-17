"""Ollama model seçimi için kısa ölçüm: Türkçe anlama doğruluğu (JSON), araç çağrısı, gecikme.

Kullanım (proje kökünden, Ollama açıkken):
    .venv\\Scripts\\python.exe tools\\llm_olcum.py qwen3:4b qwen2.5:3b gemma3:4b

Ağ: yalnız yerel Ollama (http://localhost:11434/v1). Örnek cümleler sentetiktir; gerçek hasta verisi yok.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ortak.llm import AracTanimi, LLMHatasi, Mesaj, OpenAIUyumluSaglayici  # noqa: E402
from ses_servisi.alanlar import hasta_kabul as alan  # noqa: E402
from ses_servisi.anlama import _birlestir, _istem, _llm_ciktisini_dogrula, _temellendir, kural_tabanli_anla  # noqa: E402
from ses_servisi.zaman import simdi_utc, yerel, yerel_gun  # noqa: E402

TABAN = "http://localhost:11434/v1"


def vakalar(bugun):
    g = lambda n: bugun + timedelta(days=n)  # noqa: E731
    cuma = bugun + timedelta(days=(4 - bugun.weekday()) % 7)
    return [
        # (cümle, adım, beklenen niyet, beklenen poliklinik, beklenen gün)
        ("Kardiyolojiye yarın sabah randevu almak istiyorum", "SLOT_SECIMI", "poliklinik_ve_zaman", "KARDIYOLOJI", g(1)),
        ("kalp doktoruna cuma günü bakabilir miyiz", "SLOT_SECIMI", "poliklinik_ve_zaman", "KARDIYOLOJI", cuma),
        ("Dahiliye için öğleden sonra bir saat var mı", "SLOT_SECIMI", "poliklinik_ve_zaman", "DAHILIYE", None),
        ("göz polikliniği olsun", "SLOT_SECIMI", "poliklinik_ve_zaman", "GOZ", None),
        ("ortopediye yarın saat on buçuk", "SLOT_SECIMI", "poliklinik_ve_zaman", "ORTOPEDI", g(1)),
        ("iç hastalıkları bölümüne bugün", "SLOT_SECIMI", "poliklinik_ve_zaman", "DAHILIYE", g(0)),
        ("evet onaylıyorum", "ONAY_BEKLENIYOR", "onay", None, None),
        ("tamam bu saat uygun, alalım", "ONAY_BEKLENIYOR", "onay", None, None),
        ("yok vazgeçtim bunu istemiyorum", "ONAY_BEKLENIYOR", "ret", None, None),
        ("başka saatler de göster", "SLOT_SECIMI", "baska_saat", None, None),
        ("midem çok ağrıyor hangi ilacı almalıyım", "SLOT_SECIMI", "tibbi_soru", None, None),
        ("göğsüm sıkışıyor hangi bölüme gitmeliyim", "SLOT_SECIMI", "tibbi_soru", None, None),
        ("otopark ücreti ne kadar", "SLOT_SECIMI", "konu_disi", None, None),
        ("kimlik numaramı söylememi ister misiniz", "SLOT_SECIMI", "kimlik", None, None),
        ("hmm şey", "SLOT_SECIMI", "belirsiz", None, None),
        ("aslında yarın değil cuma olsun", "ONAY_BEKLENIYOR", "poliklinik_ve_zaman", None, cuma),
    ]


async def anlama_olc(model: str, ek_govde: dict | None) -> dict:
    llm = OpenAIUyumluSaglayici(model, taban_url=TABAN, zaman_asimi_sn=60, sicaklik=0.1, azami_token=256,
                                ek_govde=ek_govde, yeniden_deneme=0)
    simdi = simdi_utc()
    bugun = yerel_gun(simdi)
    # Isınma (model yükleme süresi ölçüme girmesin)
    try:
        await llm.metin_uret("Merhaba", azami_token=5)
    except LLMHatasi as h:
        await llm.kapat()
        return {"model": model, "hata": str(h)}
    sureler, dogru_niyet, dogru_alan, hatalar, ayrinti = [], 0, 0, 0, []
    birlesik = 0  # servisin gerçekte kullandığı: LLM + temellendirme + kural birleştirme
    for cumle, adim, niyet, pol, gun in vakalar(bugun):
        sistem = _istem(alan, bugun, f"{yerel(simdi):%H:%M}", adim, "", 7)
        bas = time.perf_counter()
        try:
            veri = await llm.json_uret([Mesaj.kullanici(cumle)], sistem=sistem, sema=alan.ANLAMA_SEMASI,
                                       sema_adi="anlam")
            a = _llm_ciktisini_dogrula(veri, bugun, 7, alan)
        except LLMHatasi as h:
            hatalar += 1
            ayrinti.append(f"  HATA {cumle!r}: {h.kod}")
            continue
        finally:
            sureler.append((time.perf_counter() - bas) * 1000)
        b = _birlestir(_temellendir(a, cumle, alan), kural_tabanli_anla(cumle, bugun, alan))
        birlesik += b.niyet == niyet and (pol is None or b.poliklinik == pol) and (gun is None or b.gun == gun)
        ok_n = a.niyet == niyet
        ok_a = (pol is None or a.poliklinik == pol) and (gun is None or a.gun == gun)
        dogru_niyet += ok_n
        dogru_alan += ok_n and ok_a
        if not (ok_n and ok_a):
            ayrinti.append(f"  YANLIŞ {cumle!r}: {a.niyet}/{a.poliklinik}/{a.gun} (beklenen {niyet}/{pol}/{gun})")
    # Araç çağrısı
    arac_ok = 0
    araclar = [AracTanimi(a["ad"], a["aciklama"], a["parametreler"]) for a in alan.ARAC_SEMALARI]
    arac_vakalari = [("Kardiyolojiye yarın randevu istiyorum", "uygun_saatleri_ara", "KARDIYOLOJI"),
                     ("göz polikliniğine bakar mısın", "uygun_saatleri_ara", "GOZ"),
                     ("evet bu saati onaylıyorum", "randevuyu_onayla", None)]
    for cumle, beklenen, pol in arac_vakalari:
        sistem = (f"Hastane randevu asistanısın. Bugün {bugun.isoformat()}. Kullanıcının isteğine uygun aracı çağır. "
                  "Ekranda seçili bir randevu var; kullanıcı onaylarsa randevuyu_onayla çağır.")
        try:
            y = await llm.sohbet([Mesaj.kullanici(cumle)], sistem=sistem, araclar=araclar, arac_secimi="required")
            c = y.arac_cagrilari[0] if y.arac_cagrilari else None
            if c and c.ad == beklenen and (pol is None or c.argumanlar.get("poliklinik") == pol):
                arac_ok += 1
            else:
                ayrinti.append(f"  ARAÇ {cumle!r}: {c}")
        except LLMHatasi as h:
            ayrinti.append(f"  ARAÇ HATA {cumle!r}: {h.kod}")
    await llm.kapat()
    n = len(vakalar(bugun))
    return {"model": model, "niyet": f"{dogru_niyet}/{n}", "tam": f"{dogru_alan}/{n}", "birlesik": f"{birlesik}/{n}", "arac": f"{arac_ok}/3",
            "hata": hatalar, "medyan_ms": round(statistics.median(sureler)), "p90_ms": round(sorted(sureler)[int(0.9 * len(sureler)) - 1]),
            "ayrinti": ayrinti}


async def main() -> None:
    import json
    modeller = sys.argv[1:] or ["qwen3:4b"]
    for m in modeller:
        ek = {"reasoning_effort": "none"} if m.startswith("qwen3") else None
        sonuc = await anlama_olc(m, ek)
        print(json.dumps({k: v for k, v in sonuc.items() if k != "ayrinti"}, ensure_ascii=False))
        for satir in sonuc.get("ayrinti", []):
            print(satir)


if __name__ == "__main__":
    asyncio.run(main())
