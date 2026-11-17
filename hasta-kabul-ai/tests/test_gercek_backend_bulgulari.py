"""Gerçek backend'e geçişte bulunan kusurların regresyon testleri.

Her test bir canlı bulguya karşılık gelir (bkz. hasta-kabul-backend/README.md):
- 404 "poliklinik bulunamadı" "boş saat yok" diye sunuluyordu,
- kayıtta olmayan ilaç / onaysız polikliniğe dair geçmiş sorusu başka kaydı okuyordu,
- adlı cümle ("Ayşe Yılmaz için randevu istiyorum") LLM'e gidiyordu,
- backend kesintisinden sonra eski 503 geri dönüyordu,
- "ayın 20'si" saat 20:00 sanılıyor, geçmiş tarih gelecek yıla atanıyor, tek başına "haftaya salı" teyit edilmiyordu.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from conftest import BUGUN
from ortak.llm import SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.anlama import anla, kural_tabanli_anla
from ses_servisi.gecmis import ilac_adayi_var_mi


def hazirla(kur, llm=None):
    k = kur(llm if llm is not None else SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    return k, oid


# ------------------------------------------------------------------ 2: 404 "boş saat yok" değildir

def test_poliklinik_bulunamadi_404_bos_saat_diye_sunulmaz(kur):
    k, oid = hazirla(kur)
    k.api.cozulemeyen_poliklinikler = {"KARDIYOLOJI"}
    r = k.orkestre(oid, userText="kardiyoloji yarın")
    govde = r.json()
    assert r.status_code == 503 and govde["error"]["code"] == "API_UNAVAILABLE"
    assert govde["replyText"] == alan.SISTEM_HATASI_SAATLER
    assert alan.CAGRI_MERKEZI in govde["replyText"] and "danışma" not in govde["replyText"]
    for yanlis in ("boş saat", "görünmüyor", "yer yok", "dolu"):
        assert yanlis not in govde["replyText"]


def test_ilk_onerilerde_404_sistem_hatasidir(kur):
    k = kur(SahteSaglayici([]))
    k.api.cozulemeyen_poliklinikler = set(alan.POLIKLINIK_KODLARI)
    oid = k.api.oturum_ac("VERIFIED")
    r = k.orkestre(oid, event="TC_VERIFIED")
    assert r.status_code == 503 and r.json()["error"]["code"] == "API_UNAVAILABLE"


def test_validation_failed_de_sistem_hatasidir(kur):
    k, oid = hazirla(kur)
    k.api.hata_modu = "400"
    r = k.orkestre(oid, userText="dahiliye yarın")
    assert r.status_code == 503 and "boş saat" not in r.json()["replyText"]


def test_bos_liste_hala_bos_saat_yok_demektir(kur):
    k, oid = hazirla(kur)
    for s in k.api.slotlar:
        if s.poliklinik == "ORTOPEDI":
            s.alindi = True
    r = k.orkestre(oid, userText="ortopedi")
    assert r.status_code == 200
    assert r.json()["replyText"] == alan.POLIKLINIK_HIC_YOK.format(poliklinik="Ortopedi")


# ------------------------------------------------------------------ 3a: kayıtta olmayan ilaç

@pytest.mark.parametrize("soru", ["aspirin ne için yazılmıştı", "Aspirin ne için yazılmıştı?",
                                  "parol kullanmam söylenmiş miydi", "Parol kullanmam söylenmiş miydi"])
def test_kayitta_olmayan_ilac_sablonla_yanitlanir(kur, soru):
    llm = SahteSaglayici(varsayilan=json.dumps({"niyet": "ilac_sorusu", "poliklinik": None, "gun": None,
                                                "saat_baslangic": None, "saat_bitis": None}))
    k, oid = hazirla(kur, llm)
    r = k.orkestre(oid, userText=soru)
    govde = r.json()
    assert govde["replyText"] == alan.ILAC_BULUNAMADI
    assert "kayıtlarınızda bu ilaç görünmüyor" in govde["replyText"].lower()
    for baska in ("Metformin", "Demir", "Amlodipin"):
        assert baska not in govde["replyText"]
    assert govde["data"]["history"]["focus"] == []
    assert k.llm.cagrilar == []                       # LLM'e hiç gidilmedi
    assert r.headers["X-Anlama-Kaynagi"] == "kural"


def test_stt_hatali_liste_sorusu_ilac_bulunamadi_demez(kur):
    """Canlı kiosk: Whisper "Son muayenemde" yerine "Son vayanemde" yazdı; genel liste isteği okunmalı."""
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="Son vayanemde hangi ilaçlar yazıldı?").json()
    assert r["replyText"] != alan.ILAC_BULUNAMADI
    assert r["replyText"].startswith("30 Eylül tarihli Kardiyoloji muayenenizde 1 ilaç yazılmış.")


@pytest.mark.parametrize(("metin", "aday"), [
    ("aspirin ne için yazılmıştı", True),
    ("Kardiyoaspirin ne için yazılmıştı", True),
    ("son muayenemde hangi ilaçlar yazıldı", False),
    ("ortopedi muayenemde ne yazıldı", False),
    ("dahiliye muayenemde verilen ilaçlar neydi", False),
    ("tansiyon ilacımı nasıl kullanacağım", False),
])
def test_ilac_adayi_alan_kelimelerini_saymaz(metin, aday):
    assert ilac_adayi_var_mi(metin, alan) is aday


# ------------------------------------------------------------------ 3b: belirli poliklinik sorusu

def test_onaysiz_poliklinik_acikca_soylenir(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="ortopedi muayenemde ne yazıldı").json()
    assert r["replyText"] == alan.GECMIS_POLIKLINIK_YOK.format(poliklinik="Ortopedi")
    assert r["replyText"].startswith("Ortopedi için hekim onaylı bir kayıt görünmüyor; onay bekliyor olabilir.")
    assert "Parasetamol" not in json.dumps(r, ensure_ascii=False) and "ONAYSIZ" not in json.dumps(r)
    assert "Amlodipin" not in r["replyText"] and "Metformin" not in r["replyText"]
    assert r["data"]["history"]["visits"] == []
    assert k.llm.cagrilar == []


def test_onayli_poliklinik_yalniz_o_kayit_okunur(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="dahiliye muayenemde ne yazıldı").json()
    assert r["replyText"].startswith("20 Temmuz tarihli Dahiliye muayenenizde 2 ilaç yazılmış.")
    assert "Amlodipin" not in r["replyText"] and "Kardiyoloji" not in r["replyText"]
    assert [v["clinicName"] for v in r["data"]["history"]["visits"]] == ["Dahiliye"]


def test_goz_poliklinigi_turkce_ve_ascii_ad_eslesir(kur):
    k, oid = hazirla(kur)
    k.api.ziyaretler[0]["clinicName"] = "Göz Hastalıkları"
    r = k.orkestre(oid, userText="göz muayenemde hangi ilaçlar yazıldı").json()
    assert r["replyText"].startswith("20 Temmuz tarihli Göz Hastalıkları muayenenizde")
    k2, oid2 = hazirla(kur)
    k2.api.ziyaretler[0]["clinicName"] = "Goz Hastaliklari"   # eski ASCII seed
    r2 = k2.orkestre(oid2, userText="goz muayenemde ne yazildi").json()
    assert "Goz Hastaliklari muayenenizde" in r2["replyText"]


# ------------------------------------------------------------------ 4: adlı cümle kuralda kalır

@pytest.mark.parametrize("cumle", ["Ayşe Yılmaz için randevu istiyorum", "ayşe yılmaz için randevu istiyorum",
                                   "Annem Fatma Demir adına randevu almak istiyorum",
                                   "Ayşe Pazar için randevu istiyorum"])
def test_adli_randevu_cumlesi_llme_gitmez(kur, cumle):
    llm = SahteSaglayici(varsayilan=json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None,
                                                "saat_baslangic": None, "saat_bitis": None}))
    k, oid = hazirla(kur, llm)
    r = k.orkestre(oid, userText=cumle)
    assert r.headers["X-Anlama-Kaynagi"] == "kural"
    assert k.llm.cagrilar == []
    govde = r.json()
    assert govde["action"] == "OFFER_SLOTS" and "Hangi poliklinikten" in govde["replyText"]
    assert "Tercihinizi not aldım" not in govde["replyText"]       # "Pazar" bir ad, gün değil


def test_adli_cumlede_poliklinik_kuralla_cozulur(kur):
    k, oid = hazirla(kur, SahteSaglayici([]))
    r = k.orkestre(oid, userText="Ayşe Yılmaz için kardiyoloji randevusu istiyorum")
    assert r.headers["X-Anlama-Kaynagi"] == "kural" and k.llm.cagrilar == []
    assert "Kardiyoloji" in r.json()["replyText"]


@pytest.mark.parametrize(("metin", "niyet"), [
    ("Ayşe Yılmaz için randevu istiyorum", "randevu_istegi"),
    ("Göz Hastalıkları için randevu", "poliklinik_ve_zaman"),
    ("Sonuncusu olsun", "secim"),
])
def test_ad_benzeri_kelimeler_niyeti_bozmaz(metin, niyet):
    assert kural_tabanli_anla(metin, BUGUN, alan, adim="SECIM_BEKLENIYOR").niyet == niyet


def test_kural_niyet_cikarirsa_llm_cagrilmaz():
    import asyncio
    llm = SahteSaglayici([])
    anlam = asyncio.run(anla("Mehmet Kaya için randevu istiyorum", alan=alan, llm=llm, bugun=BUGUN,
                             simdi_metni="10:00", adim="POLIKLINIK_BEKLENIYOR"))
    assert anlam.kaynak == "kural" and anlam.niyet == "randevu_istegi" and llm.cagrilar == []


# ------------------------------------------------------------------ 7: eski 503 geri dönmez

def test_backend_duzelince_eski_503_donmez(kur):
    k, oid = hazirla(kur)
    k.api.hata_modu = "ag"
    r = k.orkestre(oid, userText="kardiyoloji yarın")
    assert r.status_code == 503
    k.api.hata_modu = None                      # backend geri geldi
    r = k.orkestre(oid, userText="evet, onaylıyorum")
    assert r.status_code != 503 and "error" not in r.json()
    assert r.json()["data"]["slots"]            # güncel saatler yeniden alındı


def test_backend_hala_kapaliysa_503_surer(kur):
    k, oid = hazirla(kur)
    k.api.hata_modu = "ag"
    k.orkestre(oid, userText="kardiyoloji yarın")
    r = k.orkestre(oid, userText="evet, onaylıyorum")
    assert r.status_code == 503 and r.json()["error"]["code"] == "API_UNAVAILABLE"


# ------------------------------------------------------------------ ekler: ayın N'si, geçmiş tarih, haftaya salı

@pytest.mark.parametrize(("metin", "gun"), [
    ("ayın 20'si olsun", date(2026, 11, 20)),
    ("ayın 20'sinde", date(2026, 11, 20)),
    ("ayın yirmisi", date(2026, 11, 20)),
    ("ayın yirmi beşi", date(2026, 11, 25)),
])
def test_ayin_gunu_saat_sanilmaz(metin, gun):
    for adim in ("SECIM_BEKLENIYOR", "SAAT_BEKLENIYOR", "GUN_BEKLENIYOR", None):
        a = kural_tabanli_anla(metin, BUGUN, alan, adim=adim)
        assert a.gun == gun and a.saat_baslangic is None, (metin, adim)


def test_secim_adiminda_ayin_20si_saat_20_secmez(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="dahiliye yarın")
    r = k.orkestre(oid, userText="ayın 20'si olsun").json()
    assert r["action"] != "CONFIRM"
    assert "20:00" not in r["replyText"]


@pytest.mark.parametrize("metin", ["13 Kasım olsun", "ayın 13'ü", "1 kasımda"])
def test_gecmis_tarih_gelecek_yila_atanmaz(metin):
    assert kural_tabanli_anla(metin, BUGUN, alan).niyet == "gecmis_tarih"


def test_uzak_gecmis_ay_gelecek_yil_sayilir():
    a = kural_tabanli_anla("5 ocak", BUGUN, alan)
    assert a.gun == date(2027, 1, 5)


def test_gecmis_tarih_sablonla_sorulur(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="dahiliye")
    r = k.orkestre(oid, userText="13 Kasım olsun")
    assert r.json()["replyText"] == alan.GECMIS_TARIH == "Bu tarih geçmişte kaldı, hangi gün olsun?"
    assert r.headers["X-Anlama-Kaynagi"] == "kural" and k.llm.cagrilar == []
    r = k.orkestre(oid, userText="yarın").json()        # akış sürer; poliklinik unutulmadı
    assert r["action"] in ("OFFER_SLOTS", "CONFIRM") and "Dahiliye" in json.dumps(r, ensure_ascii=False)


def test_haftaya_sali_ufuk_disinda_durust_sablon(kur):
    # Pazar 15 Kasım'dan "haftaya salı" = 24 Kasım; 8 günlük ufkun (son gün 22 Kasım) dışında.
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="haftaya salı").json()
    assert r["replyText"].startswith(alan.UFUK_DISI.format(gun_sayisi=8, son_tarih="22 Kasım Pazar"))
    assert "Hangi poliklinikten" in r["replyText"] and "24 Kasım" not in r["replyText"]
    assert r["action"] != "CONFIRM"


def test_haftaya_sali_hatirlanir_ve_teyit_edilir(kur):
    # Ufuk geniş tutulursa (RANDEVU_UFUK_GUN=14) eski davranış korunur.
    k, oid = hazirla(lambda llm: kur(llm, RANDEVU_UFUK_GUN="14"))
    r = k.orkestre(oid, userText="haftaya salı").json()
    sali = BUGUN + timedelta(days=(1 - BUGUN.weekday()) % 7 + 7)
    assert r["replyText"].startswith("Tercihinizi not aldım: ")
    assert "Kasım" in r["replyText"] and str(sali.day) in r["replyText"]
    assert "Hangi poliklinikten" in r["replyText"]
    r = k.orkestre(oid, userText="dahiliye").json()
    # Ufukta (ARAMA_GUN_SAYISI=3) o gün yok: en yakın gün önerilir ama istenen gün sözde korunur.
    assert f"{sali.day} Kasım" in r["replyText"]
