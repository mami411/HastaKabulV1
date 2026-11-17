"""Kayıtlı ilacı TARİFLE sorma ("tansiyonumu düşüren ilaç", "şeker hapım", "kardiyolojide yazılan ilaç").

Tarif, hastanın hekim onaylı kayıtlarındaki ilaçlarla kodda eşleştirilir (amaç, poliklinik, sıra, zaman): tek ilaca
uyarsa o ilacın konu yanıtı verilir ve adı söylenir; birden çoğuna ya da hiçbirine uyarsa hangisi olduğu sorulur.
Tarif cümlesi ve kayıtlar LLM'e gitmez. Sahte API demo geçmişi: Dahiliye (Metformin, Demir sülfat), Kardiyoloji
(Amlodipin).
"""

from __future__ import annotations

import json

import pytest

from ortak.llm import SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan

KULLANICI_CUMLESI = ("Bu tansiyonumu düşünen ilacı kullandığım zaman, dikkat etmem, yememem gereken şeyler var mı?")
KONULAR = {"yan_etki", "alerji", "yemek", "alkol", "unutma", "hamilelik", "etkilesim", "sure", "doz", "amac",
           "kullanim", "tanimsiz", None}


def _llm() -> SahteSaglayici:
    belirsiz = json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
                           "saat_bitis": None})
    return SahteSaglayici(varsayilan=belirsiz)


def _sor(kur, *cumleler: str, dogrulanmis: bool = True):
    llm = _llm()
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED" if dogrulanmis else "ACTIVE")
    if dogrulanmis:
        k.orkestre(oid, event="TC_VERIFIED")
    g = None
    for c in cumleler:
        g = k.orkestre(oid, userText=c).json()
    return g, llm


def test_kullanici_cumlesi_amlodipin_yemek_bilgisi(kur):
    g, llm = _sor(kur, KULLANICI_CUMLESI)
    t, d = g["replyText"], g["data"]
    assert "Amlodipin" in t and "Greyfurt" in t and "Hekiminizin talimatı" in t, t
    assert "Kardiyoloji polikliniğinden randevu" not in t and "suggestion" not in d
    assert d["history"]["focus"] == ["Amlodipin"] and d["history"]["topic"] == "yemek"
    assert llm.cagrilar == []  # tarif ve kayıt LLM'e gitmez


@pytest.mark.parametrize("cumle, ilac", [
    ("tansiyon ilacımla greyfurt yiyebilir miyim", "Amlodipin"),
    ("tansyon hapımı içerken nelere dikkat etmeliyim", "Amlodipin"),          # STT bozuk yazımı
    ("şeker ilacımın yan etkisi var mı", "Metformin"),
    ("kansızlık için verilen ilacı neyle almamalıyım", "Demir sülfat"),
    ("kardiyolojide yazılan ilacı alkolle içebilir miyim", "Amlodipin"),
    ("son muayenede yazılan ilacın yan etkisi var mı", "Amlodipin"),
])
def test_tek_ilaca_uyan_tarif_adiyla_yanitlanir(kur, cumle, ilac):
    g, llm = _sor(kur, cumle)
    assert g["data"]["history"]["focus"] == [ilac], g["replyText"]
    assert ilac in g["replyText"]
    assert g["data"]["history"].get("topic") in KONULAR
    assert "suggestion" not in g["data"] and not g["data"].get("aiGeneralInfo")
    assert llm.cagrilar == []


@pytest.mark.parametrize("cumle", ["dahiliyede yazılan ilacı nasıl kullanacağım",
                                   "ilk muayenemde verilen ilacın yan etkisi var mı"])
def test_birden_cok_ilaca_uyan_tarif_hangisi_diye_sorulur(kur, cumle):
    g, llm = _sor(kur, cumle)
    t = g["replyText"]
    assert "Metformin" in t and "Demir sülfat" in t and "Hangisini" in t, t
    assert g["data"]["history"]["focus"] == []
    assert llm.cagrilar == []


def test_kayitta_olmayan_tarif_durustce_soylenir_ve_ilaclar_sayilir(kur):
    g, llm = _sor(kur, "kolesterol ilacımın yan etkisi var mı")
    t = g["replyText"]
    assert t.startswith("Hekim onaylı kayıtlarınızda") and "görünmüyor" in t, t
    assert all(a in t for a in ("Amlodipin", "Metformin", "Demir sülfat"))
    assert "Kardiyoloji" not in t.split(".")[0]  # Kardiyoloji muayenesi okunmaz
    assert llm.cagrilar == []


def test_ilac_baglaminda_hastalik_kelimesi_sikayet_sayilmaz(kur):
    g, _ = _sor(kur, "şeker hapımı aç karnına mı alayım")
    assert "suggestion" not in g["data"] and g["data"]["history"]["focus"] == ["Metformin"], g["replyText"]


def test_tarif_ve_randevu_istegi_randevu_akisidir(kur):
    g, _ = _sor(kur, "şeker ilacım için randevu almak istiyorum")
    assert "history" not in g["data"]
    assert g["data"]["suggestion"]["clinic"] == "DAHILIYE"


def test_ilac_degistirme_tavsiyesi_tarifle_de_reddedilir(kur):
    g, _ = _sor(kur, "tansiyon ilacımın yerine başka ilaç alabilir miyim")
    assert g["replyText"] == alan.ILAC_TAVSIYE_RET


def test_kimlik_oncesi_tarif_sorusu_kayit_okumaz(kur):
    g, llm = _sor(kur, KULLANICI_CUMLESI, dogrulanmis=False)
    assert "Amlodipin" not in g["replyText"] and "history" not in g["data"]
    assert "kimli" in g["replyText"], g["replyText"]
    assert llm.cagrilar == []
