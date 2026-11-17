"""Yönlendirme değerlendirme seti (``tests/yonlendirme_seti.py``): her cümle orkestratör üzerinden koşar, yanıtın
gittiği YOL beklenen yolla karşılaştırılır. LLM gerektiren yollarda sahte LLM kullanılır (anlama → "belirsiz",
sağlık/tavsiye → sabit, denetimden geçen kısa genel bilgi). Ağ yok.

Rapor için: ``.venv\\Scripts\\python.exe tools\\yonlendirme_raporu.py``.
"""

from __future__ import annotations

import json

import pytest

from ortak.llm import SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan
from yonlendirme_seti import VAKALAR, Vaka

BELIRSIZ = json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
                       "saat_bitis": None})
# Denetimden (yanit_denetle) geçen, konudan bağımsız kısa genel bilgi.
GENEL_YANIT = ("Bu konuda dengeli beslenmek, yeterince dinlenmek ve belirtileri izlemek genellikle önerilir. "
               "Belirtiler sürerse bir sağlık kuruluşuna başvurmak gerekir.")
YOLLAR = ("acil", "kurum", "gecmis", "ilac", "spesifik", "tavsiye", "bilgi", "poliklinik", "randevu", "sohbet")


def sahte_llm() -> SahteSaglayici:
    def cevap(mesajlar):
        return GENEL_YANIT if mesajlar[0].icerik == alan.SAGLIK_BILGI_ISTEMI else BELIRSIZ
    return SahteSaglayici(varsayilan=cevap)


def _basla(t: str, *parcalar: str) -> bool:
    return any(t.startswith(p) for p in parcalar if p)


def yol_bul(govde: dict) -> str:
    """Yanıtın gittiği yol (yanıt verisinden ve sabit şablonlardan; LLM metninden değil)."""
    d = govde.get("data") or {}
    t = govde.get("replyText") or ""
    if d.get("emergency"):
        return "acil"
    if d.get("institutionInfo"):
        return "kurum"
    h = d.get("history")
    if h is not None:
        return "ilac" if (h.get("focus") or "topic" in h) else "gecmis"
    if _basla(t, *alan.SAGLIK_SPESIFIK_GIRIS.values(), alan.ILAC_TAVSIYE_RET, alan.TIBBI_YORUM_YOK):
        return "spesifik"
    if d.get("aiGeneralInfo"):
        return "tavsiye" if alan.TAVSIYE_SURERSE in t else "bilgi"
    if alan.SAGLIK_BILGI_KAPANIS in t:
        return "bilgi"  # dağarcıktan genel bilgi (LLM'siz)
    if _basla(t, alan.TAVSIYE_KONU_SOR, alan.RANDEVU_RET_YANIT) or alan.TAVSIYE_KONU_SOR in t:
        return "tavsiye"
    oneri_kaliplari = [alan.ONERI_SOR.split("{")[0], alan.SIKAYET_BELIRSIZ.split("{")[0],
                       alan.YONLENDIRME_SOR.split("{")[0]]
    if d.get("suggestion") or _basla(t, *oneri_kaliplari):
        return "poliklinik"
    if govde.get("action") in ("CONFIRM", "DONE") or d.get("expect") in ("gun", "hekim", "saat", "secim", "onay"):
        return "randevu"
    if _basla(t, alan.POLIKLINIK_SORU.split("{")[0], alan.MEVCUT_RANDEVU_YOK, alan.MEVCUT_RANDEVU_IPTAL,
              alan.GECMIS_TARIH):
        return "randevu"
    if _basla(t, alan.SELAM_YANIT, alan.TESEKKUR_YANIT, alan.VEDA_YANIT, alan.YARDIM, alan.YARDIM_TAMAMLANDI):
        return "sohbet"
    return "diger: " + t[:70]


def vakayi_kos(kur, vaka: Vaka) -> dict:
    k = kur(sahte_llm())
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    for onceki in vaka.once:
        k.orkestre(oid, userText=onceki)
    yanit = k.orkestre(oid, userText=vaka.metin).json()
    yanit["_randevu_post"] = [c for c in k.api.cagrilar if c[0] == "POST" and c[1].endswith("/appointments")]
    return yanit


def test_set_en_az_150_cumle_ve_her_yol_var():
    assert len(VAKALAR) >= 150
    sayim = {y: sum(v.yol == y for v in VAKALAR) for y in YOLLAR}
    assert all(sayim[y] >= 4 for y in YOLLAR if y != "sohbet"), sayim
    assert all(v.yol in YOLLAR for v in VAKALAR)
    assert sum(v.kullanici for v in VAKALAR) >= 6


@pytest.mark.parametrize("vaka", VAKALAR, ids=lambda v: f"{v.yol}:{v.metin[:40]}|{len(v.once)}")
def test_yonlendirme(kur, vaka):
    yanit = vakayi_kos(kur, vaka)
    assert yol_bul(yanit) == vaka.yol, (vaka.metin, yanit["replyText"])
    # Güvenlik: değerlendirme setindeki hiçbir cümle (açık onay dahil, onay sorusundan önce) kendiliğinden randevu
    # oluşturmaz; yalnız "evet onaylıyorum" vakası onay sorusundan sonra oluşturur.
    if vaka.metin != "evet onaylıyorum":
        assert yanit["_randevu_post"] == []
