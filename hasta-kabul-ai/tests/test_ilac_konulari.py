"""İlaç soru konuları: konu ayrımı (kural), STT toleransı, takip sorusu, bilinmeyen ilaç/konu, acil bağı ve
KVKK sınırı (LLM'e ilaç adı/sağlık ifadesi gitmez).

Yanıt içeriği LLM'den gelmez: ``ses_servisi/alanlar/ilac_dagarcigi.py`` (genel bilgi) + hekim onaylı özet
(doz/sıklık) + konu modülü şablonları. Demo geçmişi: Kardiyoloji (Amlodipin, en yeni), Dahiliye (Metformin,
Demir sülfat).
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import BUGUN
from ortak.llm import SahteSaglayici
from ortak.turkce import normalize, turkce_kucuk
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.alanlar import ilac_dagarcigi
from ses_servisi.alanlar.ilac_dagarcigi import AZAMI_KONU_METNI, ILACLAR
from ses_servisi.anlama import acil_mi, kural_tabanli_anla
from ses_servisi.gecmis import Ilac, Ziyaret, ziyaretleri_ayristir
from ses_servisi.ilac_bilgisi import dagarcik_kaydi, konu_bul, konu_metni
from ses_servisi.llm_siniri import llm_icin_maskele, llm_yuku_kur
from sahte_api import demo_ziyaretleri

KULLANICI_CUMLESI = "amlodipin kullanırken bir alerjimin olmaması gerekiyor mu"
KONULAR = ("yan_etki", "alerji", "yemek", "alkol", "unutma", "hamilelik", "etkilesim", "sure")
BELIRSIZ_LLM = json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
                           "saat_bitis": None})


def hazirla(kur, llm=None, **ayar):
    k = kur(llm if llm is not None else SahteSaglayici(varsayilan=BELIRSIZ_LLM), **ayar)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    return k, oid


def amlodipin_bilgisi(konu: str) -> str:
    return dagarcik_kaydi("Amlodipin").konular[konu]


def _yuk(llm: SahteSaglayici) -> str:
    return "\n".join(m.icerik for c in llm.cagrilar for m in c["mesajlar"])


# ------------------------------------------------------------------ kullanıcının bildirdiği hata

def test_kullanicinin_cumlesi_alerji_sorusu_olarak_yanitlanir(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText=KULLANICI_CUMLESI).json()
    metin = r["replyText"]
    assert metin.startswith("30 Eylül tarihli Kardiyoloji muayenenizde yazılan Amlodipin hakkında genel bilgi:")
    assert amlodipin_bilgisi("alerji") in metin and metin.endswith(alan.ILAC_KONU_KAPANIS)
    assert "Damarlarınızı gevşeterek" not in metin  # eski hazır açıklama değil
    assert r["data"]["history"]["topic"] == "alerji" and r["data"]["history"]["focus"] == ["Amlodipin"]
    assert k.llm.cagrilar == []


# ------------------------------------------------------------------ konu ayrımı (kural)

@pytest.mark.parametrize("cumle,konu", [
    (KULLANICI_CUMLESI, "alerji"),
    ("amlodipini kimler kullanmamalı", "alerji"),
    ("alerjim varsa amlodipin içebilir miyim", "alerji"),
    ("amlodipinin yan etkisi var mı", "yan_etki"),
    ("metformin ishal yapar mı", "yan_etki"),
    ("metformin aç karnına mı alınır", "yemek"),
    ("demir ilacını çayla içebilir miyim", "yemek"),
    ("amlodipinle greyfurt suyu içilir mi", "yemek"),
    ("amlodipin içerken alkol alabilir miyim", "alkol"),
    ("metformin kullanırken içki içilir mi", "alkol"),
    ("metformini unutursam ne olur", "unutma"),
    ("bir gün atlasam", "unutma"),
    ("hamileyken amlodipin kullanılır mı", "hamilelik"),
    ("emzirirken demir ilacı içebilir miyim", "hamilelik"),
    ("amlodipini başka ilaçlarla birlikte alabilir miyim", "etkilesim"),
    ("ilacımla birlikte ağrı kesici alabilir miyim", "etkilesim"),
    ("metformini ne kadar süre kullanacağım", "sure"),
    ("tansiyon ilacımı bırakabilir miyim", "sure"),
    ("Metforminin dozunu artırayım mı", "doz"),
    ("metformini fazla aldım", "doz"),
])
def test_konu_ayrimi_kural(cumle, konu):
    a = kural_tabanli_anla(cumle, BUGUN, alan, adim="SLOT_SECIMI")
    assert a.niyet == "ilac_sorusu" and a.ilac_konu == konu, a


@pytest.mark.parametrize("cumle,konu", [
    ("amlodipinin yanetkisi nedir", "yan_etki"),         # bitişik yazım
    ("amlodipin yan tesiri", "yan_etki"),
    ("metformın ac karnina mi", "yemek"),                # Türkçe karaktersiz
    ("amlodipin aleji yapar mı", "alerji"),              # STT: "alerji" → "aleji"
    ("Amlodipin'i içerken içki içebilir miyim", "alkol"),
    ("lanzoprazol tok karnına mı", "yemek"),
    ("metformini unuttum ne yapayim", "unutma"),
    ("metformin icerken icki icilir mi", "alkol"),
])
def test_stt_ve_gundelik_yazim_toleransi(cumle, konu):
    a = kural_tabanli_anla(cumle, BUGUN, alan, adim="SLOT_SECIMI")
    assert a.niyet == "ilac_sorusu" and a.ilac_konu == konu, a


@pytest.mark.parametrize("cumle", [
    "hamileyim", "alerjim var", "biraz daha erken var mı", "eşimle birlikte gelebilir miyim",
    "erişim kodumu unuttum", "yarın sabah mı", "randevum ne zaman", "kardiyoloji yarın sabah",
    "kaç gün sonra randevu var", "alerji testi için randevu alabilir miyim",
])
def test_ilac_disi_cumleler_ilac_sorusu_sayilmaz(cumle):
    a = kural_tabanli_anla(cumle, BUGUN, alan, adim="SLOT_SECIMI")
    assert a.ilac_konu is None, a


def test_ilac_degistirme_konu_degil_tavsiye_reddi():
    assert kural_tabanli_anla("Amlodipin yerine başka ilaç alabilir miyim", BUGUN, alan).niyet == "ilac_tavsiye"


# ------------------------------------------------------------------ uçtan uca: her konu

@pytest.mark.parametrize("konu,soru", [
    ("yan_etki", "Amlodipinin yan etkisi var mı?"),
    ("alerji", "Alerjim varsa amlodipin içebilir miyim?"),
    ("yemek", "Amlodipin aç karnına mı içilir?"),
    ("alkol", "Amlodipin içerken alkol alabilir miyim?"),
    ("unutma", "Amlodipini unutursam ne olur?"),
    ("hamilelik", "Hamileyken amlodipin kullanılır mı?"),
    ("etkilesim", "Amlodipini başka ilaçlarla birlikte alabilir miyim?"),
    ("sure", "Amlodipini ne kadar süre kullanacağım?"),
])
def test_her_konu_dagarciktan_yanitlanir(kur, konu, soru):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText=soru).json()
    metin = r["replyText"]
    assert amlodipin_bilgisi(konu) in metin, metin
    assert metin.endswith(alan.ILAC_KONU_KAPANIS)
    if konu in alan.ILAC_TALIMATLI_KONULAR:
        # Hekim talimatı genel bilgiden ÖNCE okunur (doz/sıklık hekim onaylı özetten).
        talimat = "Hekiminizin talimatı: Günde bir kez, her gün aynı saatte bir tablet alın."
        assert talimat in metin and metin.index(talimat) < metin.index(amlodipin_bilgisi(konu))
    assert r["data"]["history"]["topic"] == konu and r["action"] == "OFFER_SLOTS"
    assert {"expect", "listen", "choices"} <= set(r["data"])  # yanıt sözleşmesi korunur
    assert k.llm.cagrilar == []


@pytest.mark.parametrize("soru", ["Metformin ne işe yarıyor", "Metformini nasıl kullanacağım?"])
def test_ne_ise_yarar_ve_kullanim_hekim_onayli_ozettir(kur, soru):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText=soru).json()
    assert r["replyText"] == ("20 Temmuz tarihli Dahiliye muayenenizde yazılan Metformin: Kan şekerinizi dengeler. "
                              "Günde iki kez, sabah ve akşam yemekle birlikte birer tablet alın. "
                              "Sorunuz olursa hekiminize danışın.")


def test_demir_yemek_sorusunda_hekim_talimati_ve_genel_bilgi_celismez(kur):
    k, oid = hazirla(kur)
    metin = k.orkestre(oid, userText="demir ilacımı sütle içsem olur mu").json()["replyText"]
    assert "süt ve çayla birlikte almayın" in metin and "bunlarla birlikte almayın" in metin


# ------------------------------------------------------------------ takip sorusu (zamir → son ilaç)

def test_zamirli_takip_sorulari_son_konusulan_ilaca_baglanir(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="son muayenemde hangi ilaçlar yazıldı").json()
    assert "Amlodipin" in r["replyText"]
    sirali = [("peki yan etkisi?", "yan_etki"), ("bunu içerken alkol alabilir miyim", "alkol"),
              ("aç karnına mı", "yemek"), ("unutursam ne olur", "unutma"), ("ne kadar süre kullanacağım", "sure")]
    for soru, konu in sirali:
        r = k.orkestre(oid, userText=soru).json()
        assert amlodipin_bilgisi(konu) in r["replyText"], (soru, r["replyText"])
        assert r["data"]["history"]["focus"] == ["Amlodipin"]
    # Başka ilaca geçilince bağlam da geçer
    k.orkestre(oid, userText="Metformin ne işe yarıyor")
    r = k.orkestre(oid, userText="peki alkol?").json()
    assert dagarcik_kaydi("Metformin").konular["alkol"] in r["replyText"]
    assert k.llm.cagrilar == []


def test_ilac_belli_degilse_sorar_sonra_adla_tamamlar(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="yan etkisi var mı").json()
    assert r["replyText"] == alan.ILAC_HANGISI.format(ilaclar="Amlodipin, Metformin ve Demir sülfat")
    assert r["data"]["history"]["focus"] == []
    r = k.orkestre(oid, userText="Metformin").json()
    assert dagarcik_kaydi("Metformin").konular["yan_etki"] in r["replyText"]
    # Bekleyen konu yalnız bir tur geçerli
    r = k.orkestre(oid, userText="Amlodipin").json()
    assert "hakkında genel bilgi" not in r["replyText"] and "Damarlarınızı gevşeterek" in r["replyText"]


def test_takip_olmayan_cumle_ilaca_baglanmaz(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="Amlodipin ne işe yarıyor")
    r = k.orkestre(oid, userText="eşimle birlikte gelebilir miyim").json()
    assert "Amlodipin" not in r["replyText"] and "history" not in r["data"]


def test_randevu_sonrasi_da_konu_sorulur(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    k.orkestre(oid, userText="ilki")
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"
    r = k.orkestre(oid, userText=KULLANICI_CUMLESI).json()
    assert r["action"] == "DONE" and amlodipin_bilgisi("alerji") in r["replyText"]


# ------------------------------------------------------------------ bilinmeyen ilaç / konu

@pytest.mark.parametrize("soru", ["aspirinin yan etkisi var mı", "lustral ile alkol alınır mı",
                                  "ibuprofen aç karnına içilir mi"])
def test_kayitta_olmayan_ilac_durustce_soylenir(kur, soru):
    """Dağarcıkta olsa bile (ibuprofen) hastanın kaydında olmayan ilaç için KAYIT bilgisi okunmaz (genel bilgi
    yolu kapalıyken; açıkken genel bilgi verilir, son ilaca yine bağlanmaz: test_saglik_bilgisi)."""
    k, oid = hazirla(kur, SAGLIK_BILGI_LLM="0")
    k.orkestre(oid, userText="Amlodipin ne işe yarıyor")  # son ilaç Amlodipin: yine de ona bağlanmamalı
    r = k.orkestre(oid, userText=soru).json()
    assert r["replyText"] == alan.ILAC_BULUNAMADI and k.llm.cagrilar == []


def test_dagarcikta_olmayan_kayitli_ilac_icin_bilgi_uydurulmaz(kur):
    k, oid = hazirla(kur)
    k.api.ziyaretler[1]["medications"].append(
        {"name": "Zorbatil", "purpose": "Hekim açıklaması.", "usage": "Günde bir kez alın."})
    r = k.orkestre(oid, userText="Zorbatilin yan etkisi var mı").json()
    assert r["replyText"] == alan.ILAC_KONU_BILGI_YOK.format(ad="Zorbatil")
    r = k.orkestre(oid, userText="Zorbatili unutursam ne olur").json()
    assert r["replyText"] == ("Hekiminizin talimatı: Günde bir kez alın. "
                              + alan.ILAC_KONU_BILGI_YOK.format(ad="Zorbatil"))


def test_konu_anlasilmazsa_llme_yalniz_maskeli_cumle_gider(kur):
    # Yol isteğe bağlıdır (ILAC_KONU_LLM, varsayılan kapalı); açıkken yalnız maskeli cümle gider.
    llm = SahteSaglayici(varsayilan=json.dumps({"ilac_konu": "yan_etki"}))
    k, oid = hazirla(kur, llm, ILAC_KONU_LLM="1")
    k.orkestre(oid, userText="ilaçlarım neler")  # geçmiş bellekte
    r = k.orkestre(oid, userText="Metforminle araba kullanabilir miyim").json()
    assert dagarcik_kaydi("Metformin").konular["yan_etki"] in r["replyText"]
    assert len(llm.cagrilar) == 1
    kullanici = llm.cagrilar[0]["mesajlar"][-1].icerik
    assert kullanici == "[ilaç] araba kullanabilir miyim"
    assert "Metformin" not in _yuk(llm) and "metformin" not in _yuk(llm).lower()


def test_llm_konu_veremezse_durust_sablon(kur):
    k, oid = hazirla(kur)  # sahte LLM "belirsiz" döner
    r = k.orkestre(oid, userText="Metforminle araba kullanabilir miyim").json()
    assert r["replyText"] == alan.ILAC_KONU_TANIMSIZ.format(ad="Metformin")


def test_llm_yokken_de_konu_sorulari_calisir(kur):
    k = kur(None)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    assert amlodipin_bilgisi("alerji") in k.orkestre(oid, userText=KULLANICI_CUMLESI).json()["replyText"]
    r = k.orkestre(oid, userText="Metforminle araba kullanabilir miyim").json()
    assert r["replyText"] == alan.ILAC_KONU_TANIMSIZ.format(ad="Metformin")


def test_saglik_ifadeli_konu_cumlesi_llme_hic_gitmez(kur):
    # Anlama (niyet) LLM'i için. Genel bilgi yolu açıkken soru o yola gider: test_saglik_bilgisi.
    k, oid = hazirla(kur, SAGLIK_BILGI_LLM="0")
    k.orkestre(oid, userText="Metforminle şekerim düşer mi araba kullanırken")
    k.orkestre(oid, userText="Metformin ilacımla araba kullanabilir miyim")
    assert k.llm.cagrilar == []


# ------------------------------------------------------------------ KVKK: LLM yükünde ilaç adı/sağlık ifadesi yok

@pytest.mark.parametrize("cumle,gizliler", [
    ("amlodipin ile greyfurt", ["amlodipin"]),
    ("Metforminle araba kullanabilir miyim", ["Metformin"]),
    ("lanzoprazolü sabah mı", ["lanzoprazol"]),
    ("alerjim var mı amlodipine", ["alerji", "amlodipin"]),
    ("hamileyim demir ilacı", ["hamile", "demir", "ilacı"]),
    ("şeker hastasıyım tansiyonum yüksek", ["şeker", "hastas", "tansiyon"]),
])
def test_llm_yukunde_ilac_adi_ve_saglik_ifadesi_yok(cumle, gizliler):
    for adim in ("SLOT_SECIMI", "ILAC_KONUSU"):
        yuk = llm_yuku_kur(cumle, alan=alan, bugun=BUGUN, simdi_metni="19:36", adim=adim)
        kullanici = yuk.mesajlar[-1].icerik.lower()
        for g in gizliler:
            assert g.lower() not in kullanici, (cumle, g, kullanici)


def test_llm_maskesi_alan_kelimelerini_bozmaz():
    assert llm_icin_maskele("Kardiyolojiye yarın sabah", alan) == "Kardiyolojiye yarın sabah"
    assert llm_icin_maskele("[ilaç] araba kullanabilir miyim", alan) == "[ilaç] araba kullanabilir miyim"


def test_sistem_istemi_ilac_konusu_adiminda_dagarcik_ya_da_kayit_tasimaz():
    yuk = llm_yuku_kur("[ilaç] araba kullanabilir miyim", alan=alan, bugun=BUGUN, simdi_metni="19:36",
                       adim="ILAC_KONUSU").tum_metin()
    for kayit in ILACLAR:
        assert kayit.ad not in yuk.split("Niyetler:")[0]  # istemde ilaç adı yok (yalnız niyet açıklamaları)
        for metin in kayit.konular.values():
            assert metin not in yuk


# ------------------------------------------------------------------ acil bağı

@pytest.mark.parametrize("cumle", ["amlodipin içtim yüzüm şişti", "metformin içtim nefes darlığı oldu",
                                   "ilacı aldıktan sonra dilim şişti", "demir hapını içince nefesim daraldı"])
def test_ilac_sonrasi_ciddi_belirti_acil(kur, cumle):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText=cumle).json()
    assert r["replyText"] == alan.ACIL_YANIT and r["data"]["emergency"] is True
    assert k.llm.cagrilar == []


@pytest.mark.parametrize("cumle", ["amlodipin nefes darlığı yapar mı", "nefes darlığım var",
                                   "metformin bayıltır mı"])
def test_soru_bicimi_ya_da_ilacsiz_belirti_acil_degil(cumle):
    assert not acil_mi(normalize(cumle), alan)


# ------------------------------------------------------------------ mahremiyet ve sesli sınır

def test_sesli_okuma_kapaliysa_ilac_adi_okunmaz(kur):
    k, oid = hazirla(kur, GECMIS_SESLI_OKUMA="0")
    r = k.orkestre(oid, userText=KULLANICI_CUMLESI).json()
    assert r["replyText"] == alan.GECMIS_EKRANDA and r["data"]["history"]["spoken"] is False
    k2, oid2 = hazirla(kur, GECMIS_SESLI_OKUMA="0")
    r = k2.orkestre(oid2, userText="yan etkisi var mı").json()
    assert r["replyText"] == alan.ILAC_HANGISI_EKRANDA and "Amlodipin" not in r["replyText"]


def test_bosta_kalma_ve_end_ilac_baglamini_siler(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="Amlodipin ne işe yarıyor")
    oturum = k.app.state.orkestrator.depo.getir(oid)
    assert oturum.son_ilac is not None
    oturum.saglik_verisini_sil()
    assert oturum.gecmis is None and oturum.son_ilac is None and oturum.bekleyen_konu is None


def _demo_ziyaretler() -> list[Ziyaret]:
    return ziyaretleri_ayristir({"visits": [{k: v for k, v in z.items() if k != "onayli"}
                                            for z in demo_ziyaretleri(BUGUN) if z["onayli"]]})


@pytest.mark.parametrize("konu", [*KONULAR, "doz"])
def test_seed_ilaclarinda_her_yanit_sesli_sinira_sigar(konu):
    for z in _demo_ziyaretler():
        for i in z.ilaclar:
            metin = konu_metni(alan, [(z, i)], konu)
            assert len(metin) <= alan.ILAC_KONU_AZAMI_SESLI <= 600, (i.ad, konu, len(metin))
            assert "{" not in metin and "}" not in metin


# ------------------------------------------------------------------ dağarcık bütünlüğü

SEED_ILACLARI = ("Amlodipin", "Metformin", "Demir sülfat", "Parasetamol")


@pytest.mark.parametrize("ad", [*SEED_ILACLARI, "Lansoprazol", "Famotidin", "Amoksisilin", "Levotiroksin",
                                "İbuprofen"])
def test_dagarcik_her_konuyu_kapsar(ad):
    kayit = dagarcik_kaydi(ad)
    assert kayit is not None, ad
    assert set(KONULAR) | {"amac"} <= set(kayit.konular), ad


def test_dagarcik_metinleri_kisa_dozsuz_ve_kisiye_ozel_degil():
    for kayit in ILACLAR:
        for konu, metin in kayit.konular.items():
            assert len(metin) <= AZAMI_KONU_METNI, (kayit.ad, konu)
            assert not re.search(r"\d+\s*(mg|mcg|ml|gram)\b", metin, re.I), (kayit.ad, konu)
            for yasak in ("sizin için uygun", "güvenle kullanabilirsiniz", "rahatlıkla", "sorun olmaz"):
                assert yasak not in metin.lower(), (kayit.ad, konu, yasak)


def test_dagarcik_basinda_demo_uyarisi_var():
    bas = turkce_kucuk((ilac_dagarcigi.__doc__ or "")[:600])
    assert "demo" in bas and "hekim" in bas and "eczacı" in bas and "gözden geçir" in bas


def test_dagarcik_eslesmesi_kesin_benzer_ad_baska_ilacin_bilgisini_almaz():
    assert dagarcik_kaydi("Amlodipin 5 mg tablet").ad == "Amlodipin"
    assert dagarcik_kaydi("Demir sülfat").ad == "Demir sülfat"
    assert dagarcik_kaydi("Demir (III) hidroksit polimaltoz") is None
    assert dagarcik_kaydi("Metformin XR").ad == "Metformin"
    assert dagarcik_kaydi("Metoprolol") is None


def test_konu_bul_sirasi():
    assert konu_bul(normalize("hamileyken alkol alabilir miyim"), alan)[0] == "alkol"
    assert konu_bul(normalize("süt ve çayla birlikte içilir mi"), alan)[0] == "yemek"
    assert konu_bul(normalize("dozu unutursam"), alan)[0] == "unutma"


def test_iki_ilac_tek_yanitta_sinir_asmaz():
    z = _demo_ziyaretler()[1]  # Dahiliye: Metformin + Demir sülfat
    metin = konu_metni(alan, [(z, z.ilaclar[0]), (z, z.ilaclar[1])], "alkol")
    assert len(metin) <= alan.ILAC_KONU_AZAMI_SESLI and "Metformin" in metin


def test_bilinmeyen_kayit_ilaci_konu_metni():
    z = Ziyaret(_demo_ziyaretler()[0].zaman, "Dahiliye", "", "", (Ilac("Zorbatil", "a", "Günde bir kez alın."),), ())
    assert konu_metni(alan, [(z, z.ilaclar[0])], "alkol") == alan.ILAC_KONU_BILGI_YOK.format(ad="Zorbatil")
