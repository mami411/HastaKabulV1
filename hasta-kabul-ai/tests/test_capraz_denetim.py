"""Çapraz denetim düzeltmeleri (2026-11-16):

1. KEEPALIVE: hasta uygulamasında asistan dışındaki sayfalarda gezinmek orkestratörde boşta kalma sayılmaz.
2. KVKK: geçmiş belleğe alınmadan önce, dağarcıkta olmayan küçük harfli ilaç adı genel anlama yolunda LLM'e
   gitmez (hastanın kayıtlı ilaçları önce adla maskelenir / LLM'siz çözülür; ilaç biçimli kelimeler gizlenir).
3. LLM konu etiketi: ayrı şemada alan zorunlu; yol varsayılan kapalı (ILAC_KONU_LLM).
4. Konu kuralı: "spor yapabilir miyim" yan etki sorusu değildir.
5. Dağarcık: doz/sıklık içeren ve kişiye karar veren cümleler temkinli hâle getirildi.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest

from conftest import BUGUN, SIMDI
from ortak.llm import SahteSaglayici
from ortak.turkce import normalize
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.alanlar.ilac_dagarcigi import ILACLAR
from ses_servisi.ilac_bilgisi import dagarcik_kaydi, konu_bul
from ses_servisi.llm_siniri import llm_icin_maskele, llm_yuku_kur
from test_denetim_duzeltmeleri import Saat, SaatliKurulum

BELIRSIZ = json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
                       "saat_bitis": None})


def _kullanici_yukleri(llm: SahteSaglayici) -> list[str]:
    return [c["mesajlar"][-1].icerik for c in llm.cagrilar]


# ------------------------------------------------------------------ 1. KEEPALIVE

def _gecmise_kadar(k, oid):
    k.orkestre(oid, event="START")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="Son muayenemde hangi ilaçlar yazıldı")
    assert r.status_code == 200 and r.json()["data"].get("history")


def test_keepalive_yokken_5_dk_sonra_gecmis_sorusu_SESSION_IDLE_ile_kapanir():
    """Hatanın kendisi: asistanı açıp başka sayfalarda 5 dk'dan uzun gezinen kullanıcı."""
    saat = Saat(SIMDI)
    k = SaatliKurulum(saat)
    try:
        oid = k.api.oturum_ac("VERIFIED")
        _gecmise_kadar(k, oid)
        saat.t = SIMDI + timedelta(seconds=301)
        r = k.orkestre(oid, userText="Metformini ne zaman kullanacağım")
        assert r.status_code == 409 and r.json()["data"]["reason"] == "SESSION_IDLE"
    finally:
        k.kapat()


def test_keepalive_ile_uygulamada_gezinmek_bosta_sayilmaz():
    saat = Saat(SIMDI)
    k = SaatliKurulum(saat)
    try:
        oid = k.api.oturum_ac("VERIFIED")
        _gecmise_kadar(k, oid)
        api_cagri = len(k.api.cagrilar)
        for dakika in range(1, 8):  # 7 dk boyunca her dakika (ön yüz 60 sn'de bir gönderir)
            saat.t = SIMDI + timedelta(minutes=dakika)
            r = k.orkestre(oid, event="KEEPALIVE")
            assert r.status_code == 200 and r.json()["data"]["alive"] is True
            assert r.json()["action"] is None and r.json()["replyText"] == ""
        assert len(k.api.cagrilar) == api_cagri  # KEEPALIVE API'ye gitmez
        saat.t = SIMDI + timedelta(minutes=7, seconds=30)
        r = k.orkestre(oid, userText="Metformini ne zaman kullanacağım")
        assert r.status_code == 200 and "Metformin" in r.json()["replyText"]
    finally:
        k.kapat()


def test_keepalive_durumu_degistirmez_onay_sorusu_ve_secim_korunur():
    saat = Saat(SIMDI)
    k = SaatliKurulum(saat)
    try:
        oid = k.api.oturum_ac("VERIFIED")
        slotlar = k.orkestre(oid, event="TC_VERIFIED").json()["data"]["slots"]
        k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": slotlar[0]["slotId"]})
        saat.t = SIMDI + timedelta(seconds=200)
        k.orkestre(oid, event="KEEPALIVE")
        saat.t = SIMDI + timedelta(seconds=400)
        k.orkestre(oid, event="KEEPALIVE")
        saat.t = SIMDI + timedelta(seconds=450)
        r = k.orkestre(oid, userText="evet onaylıyorum")
        assert r.json()["action"] == "DONE" and len(k.api.randevular) == 1
    finally:
        k.kapat()


def test_keepalive_suresi_asilmis_oturumu_kurtaramaz_ve_bilinmeyen_oturumu_kurmaz():
    saat = Saat(SIMDI)
    k = SaatliKurulum(saat)
    try:
        oid = k.api.oturum_ac("VERIFIED")
        _gecmise_kadar(k, oid)
        saat.t = SIMDI + timedelta(seconds=301)
        assert k.orkestre(oid, event="KEEPALIVE").json()["data"]["alive"] is False
        r = k.orkestre(oid, userText="Metformini ne zaman kullanacağım")
        assert r.status_code == 409 and r.json()["data"]["reason"] == "SESSION_IDLE"

        yabanci = k.api.oturum_ac("VERIFIED")  # asistan hiç açılmadı
        onceki = len(k.api.cagrilar)
        r = k.orkestre(yabanci, event="KEEPALIVE")
        assert r.status_code == 200 and r.json()["data"]["alive"] is False
        assert len(k.api.cagrilar) == onceki
        assert k.app.state.orkestrator.depo.getir(yabanci) is None
    finally:
        k.kapat()


def test_end_sonrasi_keepalive_oturumu_canlandirmaz(kur):
    k = kur(None)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    k.orkestre(oid, event="END")
    r = k.orkestre(oid, event="KEEPALIVE")
    assert r.status_code == 409 and r.json()["error"]["code"] == "VOICE_SESSION_ENDED"


# ------------------------------------------------------------------ 2. KVKK: geçmişten önce ilaç adı LLM'e gitmez

def test_gecmisten_once_dagarcikta_olmayan_kayitli_ilac_adi_llme_gitmez(kur):
    llm = SahteSaglayici(varsayilan=BELIRSIZ)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    k.api.ziyaretler[1]["medications"].append(
        {"name": "Zorbatil", "purpose": "Hekim açıklaması.", "usage": "Günde bir kez alın."})
    for cumle in ("zorbatil nedir", "zorbatilim hakkında bilgi verir misiniz", "zorbatil"):
        r = k.orkestre(oid, userText=cumle).json()
        assert "Zorbatil" in r["replyText"], (cumle, r["replyText"])
    yuk = "\n".join(_kullanici_yukleri(llm)).lower()
    assert "zorbatil" not in yuk


def test_gecmisten_once_ilac_bicimli_kelimeler_maskelenir(kur):
    llm = SahteSaglayici(varsayilan=BELIRSIZ)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    for cumle in ("atorvastatin nedir", "aspirin", "eliquis bana iyi gelir mi", "glifor hakkında",
                  "klopidogrel", "ramiprilim"):
        k.orkestre(oid, userText=cumle)
    # Anlama (niyet) yükü: ilaç adları maskeli. Genel bilgi yoluna yalnız soru gider (ilaç adı açık, kasıtlı;
    # kayıt bilgisi yok) — o yük ayrıca test_saglik_bilgisi'nde denetlenir.
    niyet_cagrilari = [c for c in llm.cagrilar if c["mesajlar"][0].icerik != alan.SAGLIK_BILGI_ISTEMI]
    assert niyet_cagrilari, "anlama LLM'i de çağrılmalı (ör. 'aspirin', 'klopidogrel' tek kelime)"
    yuk = "\n".join(c["mesajlar"][-1].icerik for c in niyet_cagrilari).lower()
    for ad in ("atorvastatin", "aspirin", "eliquis", "glifor", "klopidogrel", "ramipril"):
        assert ad not in yuk, (ad, yuk)


def test_gecmis_okunamazsa_ilac_adayli_cumle_llme_hic_gitmez(kur):
    llm = SahteSaglayici(varsayilan=BELIRSIZ)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    k.api.gecmis_hata_modu = "500"
    k.orkestre(oid, userText="zorbatil nedir")
    assert llm.cagrilar == []


@pytest.mark.parametrize("cumle", [
    "Kardiyolojiye yarın sabah", "annemle gelebilir miyim", "randevumu öğleden sonraya alabilir misiniz",
    "teşekkürler görüşürüz", "pazartesi uygun mu", "bilmiyorum anlamadım", "eşimle birlikte gelebilir miyim",
    "programım belli değil", "hesabım", "çevirin", "telefonumu unuttum", "istasyondan geleceğim",
])
def test_ilac_bicim_maskesi_gundelik_kelimeleri_bozmaz(cumle):
    assert "[ilaç]" not in llm_icin_maskele(cumle, alan), cumle


def test_kayitli_ilac_adlari_yalniz_maske_icin_kullanilir_yuke_girmez():
    yuk = llm_yuku_kur("zorbatilimi sabah mı alayım", alan=alan, bugun=BUGUN, simdi_metni="10:00",
                       adim="SLOT_SECIMI", ek_ilac_adlari=("Zorbatil", "Amlodipin")).tum_metin().lower()
    assert "zorbatil" not in yuk and "amlodipin" not in yuk and "[ilaç]" in yuk


# ------------------------------------------------------------------ 3. LLM konu etiketi

def test_ilac_konu_llm_varsayilan_kapali_llm_cagrilmaz(kur):
    llm = SahteSaglayici(varsayilan=json.dumps({"ilac_konu": "alkol"}))
    k = kur(llm, SAGLIK_BILGI_LLM="0")  # genel bilgi yolu açıkken: test_saglik_bilgisi
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    k.orkestre(oid, userText="ilaçlarım neler")
    r = k.orkestre(oid, userText="Metforminle araba kullanabilir miyim").json()
    assert r["replyText"] == alan.ILAC_KONU_TANIMSIZ.format(ad="Metformin")
    assert llm.cagrilar == []


def test_ilac_konu_llm_acikken_konu_alani_zorunlu_semayla_istenir(kur):
    llm = SahteSaglayici(varsayilan=json.dumps({"ilac_konu": "yemek"}))
    k = kur(llm, ILAC_KONU_LLM="1")
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    k.orkestre(oid, userText="ilaçlarım neler")
    r = k.orkestre(oid, userText="Metforminle araba kullanabilir miyim").json()
    assert dagarcik_kaydi("Metformin").konular["yemek"] in r["replyText"]
    sema = llm.cagrilar[-1]["json_semasi"]
    assert sema is not None and "ilac_konu" in json.dumps(sema)
    assert "ilac_konu" in alan.ILAC_KONU_SEMASI["required"]


# ------------------------------------------------------------------ 4. konu kuralı

@pytest.mark.parametrize("cumle,konu", [
    ("amlodipin ile spor yapabilir miyim", None),
    ("yemek yapabilir miyim", None),
    ("amlodipin uyku yapar mı", "yan_etki"),
    ("metformin ishal yapabilir mi", "yan_etki"),
    ("amlodipinin yan etkisi var mı", "yan_etki"),
    ("bu ilaç baş ağrısı yapıyor mu", "yan_etki"),
])
def test_birinci_sahis_yapmak_fiili_yan_etki_sayilmaz(cumle, konu):
    assert konu_bul(normalize(cumle), alan)[0] == konu


def test_spor_sorusu_yan_etki_bilgisi_okumaz(kur):
    k = kur(SahteSaglayici(varsayilan=BELIRSIZ))
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="amlodipin ile spor yapabilir miyim").json()
    assert dagarcik_kaydi("Amlodipin").konular["yan_etki"] not in r["replyText"]


# ------------------------------------------------------------------ 5. dağarcık içeriği

def test_dagarcikta_siklik_ve_doz_araligi_yok():
    """Doz/sıklık hekim talimatındadır; dağarcık "günde bir kez", "en az 4 saat" gibi sayı vermez."""
    for kayit in ILACLAR:
        for konu, metin in kayit.konular.items():
            kucuk = metin.lower()
            assert not re.search(r"günde (bir|iki|üç|\d)", kucuk), (kayit.ad, konu)
            assert not re.search(r"en az \d+ saat", kucuk), (kayit.ad, konu)


def test_alerji_ve_kontrendikasyon_metinleri_ilaci_kendiniz_birakmayin_der():
    """Reçeteli hastaya "X olanlar kullanmamalıdır" demek kendi kararıyla bırakmaya yol açabilir."""
    for kayit in ILACLAR:
        metin = kayit.konular["alerji"].lower()
        assert "hekiminize" in metin and ("bırakmadan" in metin or "kullanmadan önce" in metin), kayit.ad


# ------------------------------------------------------------------ 6. İlaç listesi tutarlılığı

def test_ilac_listesi_onceki_muayenelerin_ilaclarini_da_anar(kur):
    """İlaçlarım sayfası tüm onaylı muayenelerin ilaçlarını gösterir; asistan yalnız son muayeneyi okursa
    hasta diğer ilaçlarını bırakılmış sanabilir. Önceki muayenelerin ilaç adları da anılır."""
    k = kur(SahteSaglayici(varsayilan=BELIRSIZ))
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="START")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="ilaçlarım neler").json()
    ziyaretler = r["data"]["history"]["visits"]
    assert len(ziyaretler) >= 2
    son_adlar = {i["name"].casefold() for i in ziyaretler[0]["medications"]}
    onceki = [i["name"] for z in ziyaretler[1:] for i in z["medications"] if i["name"].casefold() not in son_adlar]
    assert onceki, "demo verisinde önceki muayenede farklı ilaç olmalı"
    for ad in onceki:
        assert ad in r["replyText"]
    assert "Önceki muayenelerinizde" in r["replyText"]



def test_onceki_ilac_adlari_son_muayenedekini_tekrarlamaz():
    from types import SimpleNamespace as N
    from ses_servisi.gecmis import _onceki_ilac_adlari
    son = N(ilaclar=[N(ad="Amlodipin")])
    onceki = [N(ilaclar=[N(ad="Metformin"), N(ad="amlodipin")]), N(ilaclar=[N(ad="Metformin"), N(ad="Demir sülfat")])]
    assert _onceki_ilac_adlari(son, onceki) == ["Metformin", "Demir sülfat"]
    assert _onceki_ilac_adlari(son, []) == []
