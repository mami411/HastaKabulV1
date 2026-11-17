"""Geçmiş muayene ve ilaç soruları: yanıt tamamen şablon + backend verisi.

Demo geçmiş (tests/sahte_api.demo_ziyaretleri, gerçek backend seed'iyle aynı içerik):
20 Temmuz Dahiliye (Metformin, Demir sülfat; 3 uyarı), 30 Eylül Kardiyoloji (Amlodipin) — ikisi hekim
onaylı; 6 Kasım Ortopedi (Parasetamol) onaysız, hiç dönmez.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import pytest

from conftest import SIMDI, BUGUN
from ortak.llm import SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.anlama import kural_tabanli_anla
from ses_servisi.gecmis import ilac_bul, ziyaretleri_ayristir
from sahte_api import demo_ziyaretleri
from test_denetim_duzeltmeleri import Saat, SaatliKurulum

METFORMIN = ("Metformin: Kan şekerinizi dengeler. "
             "Günde iki kez, sabah ve akşam yemekle birlikte birer tablet alın.")
DEMIR = ("Demir sülfat: Azalan demir deponuzu doldurur. Günde bir kez, sabah aç karnına, portakal suyu gibi "
         "C vitamini içeren bir içecekle alın; süt ve çayla birlikte almayın.")


def gecmis_cagrilari(k):
    return [c for c in k.api.cagrilar if c[1].endswith("/medical-history")]


def hazirla(kur, llm=None, **ayar):
    k = kur(llm if llm is not None else SahteSaglayici([]), **ayar)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    return k, oid


def test_ilac_listesi_son_muayene(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="Eski muayenemde hangi ilaçlar yazıldı, ne zaman kullanacağım?")
    govde = r.json()
    assert r.status_code == 200 and govde["action"] == "OFFER_SLOTS"  # bulunulan adım korunur
    # "Son muayene" en yenisidir: 30 Eylül Kardiyoloji
    assert govde["replyText"] == ("30 Eylül tarihli Kardiyoloji muayenenizde 1 ilaç yazılmış. Amlodipin: Damarlarınızı "
                                  "gevşeterek tansiyonunuzu düşürür. Günde bir kez, her gün aynı saatte bir tablet "
                                  "alın. Önceki muayenelerinizde de yazılanlar: Metformin, Demir sülfat; ayrıntısı "
                                  "ekranda. Sorunuz olursa hekiminize danışın.")
    gecmis = govde["data"]["history"]
    assert [v["clinicName"] for v in gecmis["visits"]] == ["Kardiyoloji", "Dahiliye"]  # en yeni önce
    assert gecmis["spoken"] is True
    assert gecmis["visits"][1]["date"] == "20 Temmuz 2026" and gecmis["visits"][1]["warnings"]
    # Onaysız özet hiçbir yerde görünmez
    assert "Parasetamol" not in r.text and "ONAYSIZ" not in r.text
    assert k.llm.cagrilar == []  # kural anladı; LLM'e hiç gidilmedi
    assert len(gecmis_cagrilari(k)) == 1
    # İkinci soru oturum belleğinden yanıtlanır (API'ye tekrar gidilmez)
    k.orkestre(oid, userText="ilaçlarımı tekrar söyler misiniz")
    assert len(gecmis_cagrilari(k)) == 1


@pytest.mark.parametrize("soru", ["Metformini nasıl kullanacağım?", "metformın ne zaman alınır",
                                  "metformin ilacım ne için"])
def test_tek_ilac_sorusu_yalniz_o_ilac(kur, soru):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText=soru).json()
    assert r["replyText"] == ("20 Temmuz tarihli Dahiliye muayenenizde yazılan Metformin: Kan şekerinizi dengeler. "
                              "Günde iki kez, sabah ve akşam yemekle birlikte birer tablet alın. "
                              "Sorunuz olursa hekiminize danışın.")
    assert "Demir" not in r["replyText"]
    assert r["data"]["history"]["focus"] == ["Metformin"]
    assert k.llm.cagrilar == []


def test_iki_kelimelik_ilac_adi(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="demir ilacımı nasıl kullanacağım").json()
    assert r["replyText"].startswith("20 Temmuz tarihli Dahiliye muayenenizde yazılan Demir sülfat:")
    assert "Metformin" not in r["replyText"]


def test_onceki_muayenenin_ilaclari_tum_liste(kur):
    """Eski ziyaretteki ilaçlar ekrandaki kartta; ilaç adıyla sorulunca sesli de okunur."""
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="Metformin ve demir ilacımı nasıl kullanacağım").json()
    assert METFORMIN in r["replyText"] and DEMIR in r["replyText"]


def test_eski_muayenedeki_ilac_ve_bellekten_ad_eslesmesi(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="ilaçlarım neler")  # geçmiş belleğe alınır
    # Kural "amlodipin ne işe yarıyor"u tek başına çözemez; bellekteki kayıtla KODDA eşleşir, LLM'e gidilmez
    r = k.orkestre(oid, userText="Amlodipin ne işe yarıyordu").json()
    assert r["replyText"].startswith("30 Eylül tarihli Kardiyoloji muayenenizde yazılan Amlodipin: Damarlarınızı")
    assert k.llm.cagrilar == []


def test_uyari_sorusu(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="son muayenemde uyarı var mıydı").json()
    assert "Günlük tuz tüketiminizi azaltın" in r["replyText"]
    assert r["replyText"].endswith(alan.GECMIS_KAPANIS)


def test_kayit_yoksa_acikca_soylenir(kur):
    k, oid = hazirla(kur)
    k.api.ziyaretler = [z for z in k.api.ziyaretler if not z["onayli"]]  # yalnız onay bekleyen var
    r = k.orkestre(oid, userText="geçmiş muayenelerim").json()
    assert r["replyText"] == alan.GECMIS_YOK and r["data"]["history"]["visits"] == []


def test_dogrulanmamis_oturumda_once_kimlik(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("ACTIVE")
    k.orkestre(oid, event="START")
    r = k.orkestre(oid, userText="son muayenemde hangi ilaçlar yazıldı").json()
    assert r["action"] == "ASK_TC" and r["replyText"] == alan.GECMIS_ONCE_KIMLIK
    assert gecmis_cagrilari(k) == [] and "history" not in r["data"]


def test_suresi_dolan_oturumda_gecmis_verilmez_kiosk_yeniden_baslar(kur):
    k, oid = hazirla(kur)
    k.api.sure_doldur(oid)
    r = k.orkestre(oid, userText="ilaçlarım")
    govde = r.json()
    assert r.status_code == 409 and govde["error"]["code"] == "VOICE_SESSION_NOT_VERIFIED"
    assert govde["action"] == "HANDOFF" and govde["replyText"] == alan.OTURUM_SURESI_DOLDU
    assert govde["data"]["reason"] == "SESSION_EXPIRED" and govde["data"]["restart"] is True
    assert "Metformin" not in r.text and "history" not in govde["data"]
    assert k.app.state.orkestrator.depo.getir(oid).gecmis is None


def test_tamamlanan_oturumun_suresi_dolunca_gecmis_reddedilir(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    k.orkestre(oid, userText="ilki")
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"
    k.api.sure_doldur(oid)
    r = k.orkestre(oid, userText="son muayenemde ne yazılmıştı").json()
    assert r["replyText"] == alan.OTURUM_SURESI_DOLDU and r["data"]["restart"] is True


def test_suresi_dolan_oturumda_randevu_olusmaz(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    k.orkestre(oid, userText="ilki")
    k.api.sure_doldur(oid)
    r = k.orkestre(oid, userText="evet onaylıyorum")
    assert r.status_code == 409 and r.json()["replyText"] == alan.OTURUM_SURESI_DOLDU
    assert r.json()["data"]["restart"] is True and k.api.randevular == []


@pytest.mark.parametrize("soru,konu", [("ilacımı bırakabilir miyim", "sure"),
                                       ("bu ilaçla birlikte ağrı kesici alabilir miyim", "etkilesim"),
                                       ("ilacın yan etkisi var mı", "yan_etki")])
def test_ilac_adi_soylenmeyen_konu_sorusu_hangi_ilac_diye_sorar(kur, soru, konu):
    """Eskiden sabit ret alan konu soruları artık yanıtlanır; ilaç belli değilse hangisi olduğu sorulur."""
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText=soru).json()
    assert r["replyText"] == alan.ILAC_HANGISI.format(ilaclar="Amlodipin, Metformin ve Demir sülfat")
    assert r["data"]["history"]["topic"] == konu and k.llm.cagrilar == []


def test_doz_sorusu_hekim_talimatini_okur(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="Metforminin dozunu artırayım mı").json()
    assert "Hekiminizin talimatı: Günde iki kez, sabah ve akşam yemekle birlikte birer tablet alın." in r["replyText"]
    assert alan.ILAC_DOZ_GENEL in r["replyText"] and k.llm.cagrilar == []


def test_iki_ilacin_birlikte_kullanimi_uydurulmaz(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="Metformini Amlodipinle beraber alabilir miyim").json()
    assert r["replyText"] == alan.ILAC_ETKILESIM_CIFT.format(adlar="Amlodipin ve Metformin")


def test_ilac_degistirme_tavsiyesi_sabit_ret(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="Amlodipin yerine başka bir ilaç kullanabilir miyim").json()
    assert r["replyText"] == alan.ILAC_TAVSIYE_RET and k.llm.cagrilar == []


def test_bellekteki_ilac_adiyla_birakma_sorusu_genel_bilgi(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="ilaçlarım neler")
    r = k.orkestre(oid, userText="Amlodipini kesebilir miyim").json()
    assert "Amlodipin hakkında genel bilgi" in r["replyText"] and "danışmadan bırakmayın" in r["replyText"]


def test_kayitta_olmayan_ilac_llme_hic_gitmez(kur):
    """Bağımsız denetim: eskiden kayıtta bulunmayan ilacın adını çıkarmak için cümle LLM'e zorla
    gönderiliyordu (kullanıcının kullandığı ilacın adı = sağlık verisi). LLM'in döndüreceği ad zaten
    metinde geçmek zorunda olduğundan kazanç yoktu; artık LLM'e gidilmez, yanıt yine 'bulunamadı'.
    (Genel bilgi yolu kapalıyken. Açıkken genel bilgi sorusu olarak yanıtlanır: test_saglik_bilgisi.)"""
    llm = SahteSaglayici([json.dumps({"niyet": "ilac_sorusu", "poliklinik": None, "gun": None,
                                      "saat_baslangic": None, "saat_bitis": None, "ilac_adi": "Aspirin"})])
    k, oid = hazirla(kur, llm, SAGLIK_BILGI_LLM="0")
    for soru in ("Aspirin nasıl kullanılır", "Lustral ne zaman alınır", "Aspirini nasıl alacağım"):
        r = k.orkestre(oid, userText=soru).json()
        assert r["replyText"] == alan.ILAC_BULUNAMADI, soru
    assert llm.cagrilar == []


def test_mahremiyet_bayragi_yalniz_ekranda(kur):
    k, oid = hazirla(kur, GECMIS_SESLI_OKUMA="0")
    r = k.orkestre(oid, userText="ilaçlarım neler").json()
    assert r["replyText"] == alan.GECMIS_EKRANDA and "Metformin" not in r["replyText"]
    assert r["data"]["history"]["spoken"] is False
    assert r["data"]["history"]["visits"][1]["medications"][0]["name"] == "Metformin"


def test_ikinci_faktor_bayragi_kapali_basarisiz(kur):
    k, oid = hazirla(kur, GECMIS_IKINCI_FAKTOR="1")
    r = k.orkestre(oid, userText="ilaçlarım neler").json()
    assert r["replyText"] == alan.GECMIS_EK_DOGRULAMA and "history" not in r["data"]
    assert gecmis_cagrilari(k) == []


@pytest.mark.parametrize("mod", ["500", "ag"])
def test_api_hatasi_sistem_hatasi_olarak_soylenir(kur, mod):
    k, oid = hazirla(kur)
    k.api.gecmis_hata_modu = mod
    r = k.orkestre(oid, userText="ilaçlarım neler")
    assert r.status_code == 503 and r.json()["replyText"] == alan.GECMIS_ALINAMADI
    assert r.json()["error"]["code"] == "API_UNAVAILABLE"


def test_randevu_sonrasi_da_sorulabilir(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    k.orkestre(oid, userText="ilki")
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"
    r = k.orkestre(oid, userText="son muayenemde ne yazılmıştı").json()
    assert r["action"] == "DONE" and r["replyText"].startswith("30 Eylül tarihli Kardiyoloji muayenenizde")
    assert r["data"]["accessCode"]  # randevu bilgisi korunur
    r = k.orkestre(oid, userText="hava nasıl").json()
    assert r["action"] == "DONE" and k.llm.cagrilar == []  # tamamlanan oturumda LLM'e gidilmez


def test_bosta_kalan_oturumda_gecmis_bellekten_silinir():
    saat = Saat(SIMDI)
    k = SaatliKurulum(saat, SahteSaglayici([]), OTURUM_BOSTA_SN="120")
    try:
        oid = k.api.oturum_ac("VERIFIED")
        k.orkestre(oid, event="TC_VERIFIED")
        k.orkestre(oid, userText="ilaçlarım neler")
        depo = k.app.state.orkestrator.depo
        assert depo.getir(oid).gecmis
        saat.t = SIMDI + timedelta(seconds=200)
        k.orkestre(oid, userText="kardiyoloji")
        assert depo.getir(oid).gecmis is None
    finally:
        k.kapat()


def test_end_olayi_oturumu_ve_gecmisi_siler(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="ilaçlarım neler")
    r = k.orkestre(oid, event="END")
    assert r.status_code == 200 and r.json()["data"]["ended"] is True
    assert k.app.state.orkestrator.depo.getir(oid) is None


def test_loglarda_saglik_verisi_yok(kur, caplog):
    k, oid = hazirla(kur, TESHIS_LOGU="1")
    with caplog.at_level(logging.DEBUG):
        k.orkestre(oid, userText="Metformini nasıl kullanacağım, ben Ayşe Yılmaz")
        k.orkestre(oid, userText="ilaçlarım neler")
    kayit = "\n".join(r.getMessage() for r in caplog.records)
    for gizli in ("Metformin", "Demir sülfat", "Kan şekeriniz", "Ayşe", "Yılmaz", "Mehmet D."):
        assert gizli not in kayit


def test_ayristirma_bozuk_kayitlari_atlar():
    veri = {"visits": [{"scheduledAt": "bozuk"}, "x",
                       {"scheduledAt": "2026-11-12T07:00:00Z", "clinicName": "Dahiliye",
                        "medications": [{"name": "A\x00b"}, {"purpose": "adsız"}, 5], "warnings": [None, "uyarı"]}]}
    z = ziyaretleri_ayristir(veri)
    assert len(z) == 1 and [i.ad for i in z[0].ilaclar] == ["A b"] and z[0].uyarilar == ("uyarı",)
    with pytest.raises(ValueError):
        ziyaretleri_ayristir({"x": 1})


@pytest.mark.parametrize("metin,beklenen", [("metformini", ["Metformin"]), ("met formin", ["Metformin"]),
                                            ("metformın", ["Metformin"]), ("demir ilacım", ["Demir sülfat"]),
                                            ("amlodipinle", ["Amlodipin"]), ("ilaçlarım", []), ("aspirin", [])])
def test_bulanik_ilac_eslesmesi(metin, beklenen):
    z = ziyaretleri_ayristir({"visits": [{k: v for k, v in x.items() if k != "onayli"}
                                         for x in demo_ziyaretleri(BUGUN) if x["onayli"]]})
    assert [i.ad for _, i in ilac_bul(metin, z)] == beklenen


@pytest.mark.parametrize("metin,niyet", [
    ("son muayenemde hangi ilaçlar yazıldı", "gecmis_muayene"),
    ("ilacımı ne zaman kullanacağım", "ilac_sorusu"),
    ("midem ağrıyor hangi ilacı almalıyım", "tibbi_soru"),   # tavsiye isteği geçmiş sorusu değil
    ("ilacımı bırakabilir miyim", "ilac_sorusu"),        # konu: süre/bırakma (genel bilgi + hekime danış)
    ("ilacımın yerine başka ilaç alabilir miyim", "ilac_tavsiye"),
    ("kardiyolojide muayene olmak istiyorum", "poliklinik_ve_zaman"),
])
def test_kural_gecmis_niyetleri(metin, niyet):
    assert kural_tabanli_anla(metin, BUGUN, alan).niyet == niyet


def test_kayitta_olmayan_ilac_llm_ad_vermese_de_baska_ilac_okunmaz(kur):
    """Gerçek Ollama denemesinde model ilac_adi'nı boş döndürdü: yine de kayıttaki başka ilaç okunmaz."""
    llm = SahteSaglayici([json.dumps({"niyet": "ilac_sorusu", "poliklinik": None, "gun": None,
                                      "saat_baslangic": None, "saat_bitis": None})])
    k, oid = hazirla(kur, llm, SAGLIK_BILGI_LLM="0")  # açık yol: test_saglik_bilgisi
    r = k.orkestre(oid, userText="Aspirin nasıl kullanılır?").json()
    assert r["replyText"] == alan.ILAC_BULUNAMADI and "Amlodipin" not in r["replyText"]


@pytest.mark.parametrize("metin,beklenen", [("Aspirin nasıl kullanılır", True), ("Parol ne işe yarar", True),
                                            ("ilacımı ne zaman alacağım", False), ("nasıl kullanılır", False),
                                            ("Augmentin günde kaç kez", True)])
def test_ilac_adayi(metin, beklenen):
    from ses_servisi.gecmis import ilac_adayi_var_mi
    assert ilac_adayi_var_mi(metin) is beklenen