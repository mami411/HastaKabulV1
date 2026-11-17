"""Şikâyet için tavsiye ("fikir") isteği yolu (``ses_servisi/tavsiye.py``, orkestratörde ``_tavsiye_yolu``).

Kullanıcının 2026-11-16 sesli konuşması (konuşma tanıma hatalarıyla birebir):
  "Midem çok kullanıyor, ne yapabilirim"  → eskiden Dahiliye önerisi + randevu sorusu
  "Randevu bakmak değil de, doktordan önce yapabileceğin bir şey var mı? Doktora gitmeden."
                                            → eskiden "Hangi poliklinikten randevu istiyorsunuz?…"
  "Bide bu lambasına ne iyi gelir, doktora gitmek istemiyorum, önce bir çayda da iyi gelebilir mi, bunları öğrenmek
   istiyorum"                               → eskiden aynı poliklinik sorusu
İstenen: tavsiye isteğinde önce genel öz bakım bilgisi (+ ısrarsız randevu teklifi); randevu reddinde poliklinik
sorusu yok; konu oturumdan hatırlanır; acil her şeyden önce; LLM'e yalnız koddan kurulan konu + soru gider.
"""

from __future__ import annotations

import json

import pytest

from conftest import BUGUN
from ortak.llm import SahteSaglayici
from ortak.turkce import normalize
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.anlama import acil_mi, kural_tabanli_anla
from ses_servisi.llm_siniri import saglik_tavsiye_yuku_kur
from ses_servisi.saglik_bilgisi import yanit_denetle
from ses_servisi.tavsiye import (
    alt_sorular,
    randevu_reddi,
    sikayet_konusu,
    tavsiye_engeli,
    tavsiye_istegi,
    tavsiye_sorusu,
)
from test_denetim_duzeltmeleri import onay_adimina_gel

BELIRSIZ = json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
                       "saat_bitis": None})
TAVSIYE_YANITI = ("Mide bulantısında suyu az az ve sık yudumlamak, yağlı yiyeceklerden uzak durmak genellikle "
                  "rahatlatır. Dinlenmek ve temiz hava almak da iyi gelebilir.")
LISTE = alan.poliklinik_listesi_metni()
POLIKLINIK_SORUSU = "Hangi poliklinikten randevu istiyorsunuz"

T1 = "Midem çok kullanıyor, ne yapabilirim"
T2 = "Randevu bakmak değil de, doktordan önce yapabileceğin bir şey var mı? Doktora gitmeden."
T3 = ("Bide bu lambasına ne iyi gelir, doktora gitmek istemiyorum, önce bir çayda da iyi gelebilir mi, bunları "
      "öğrenmek istiyorum")


def saglik_cagrisi_mi(mesajlar) -> bool:
    return mesajlar[0].icerik == alan.SAGLIK_BILGI_ISTEMI


def akilli_llm(yanit=TAVSIYE_YANITI) -> SahteSaglayici:
    def cevap(mesajlar):
        if saglik_cagrisi_mi(mesajlar):
            return yanit(mesajlar) if callable(yanit) else yanit
        return BELIRSIZ
    return SahteSaglayici(varsayilan=cevap)


def saglik_yukleri(llm: SahteSaglayici) -> list[str]:
    return ["\n".join(m.icerik for m in c["mesajlar"][1:]) for c in llm.cagrilar if saglik_cagrisi_mi(c["mesajlar"])]


def anlama_cagrilari(llm: SahteSaglayici) -> list:
    return [c for c in llm.cagrilar if not saglik_cagrisi_mi(c["mesajlar"])]


def hazirla(kur, llm=None, **ayar):
    llm = llm if llm is not None else akilli_llm()
    k = kur(llm, **ayar)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    return k, oid, llm


def randevu_post(k):
    return [c for c in k.api.cagrilar if c[0] == "POST" and c[1].endswith("/appointments")]


def tavsiye_metni(teklif: str | None = None) -> str:
    parca = [TAVSIYE_YANITI, alan.TAVSIYE_SURERSE, alan.SAGLIK_BILGI_KAPANIS]
    if teklif:
        parca.append(alan.TAVSIYE_RANDEVU_TEKLIFI.format(poliklinik=teklif))
    return " ".join(parca)


# ------------------------------------------------------------------ kullanıcının konuşması, birebir

def test_kullanicinin_konusmasi_birebir(kur):
    k, oid, llm = hazirla(kur)
    r1 = k.orkestre(oid, userText=T1).json()
    assert r1["replyText"] == tavsiye_metni("Dahiliye")
    assert r1["data"]["aiGeneralInfo"] is True and r1["action"] == "OFFER_SLOTS"
    assert r1["data"]["suggestion"] == {"clinic": "DAHILIYE", "clinicName": "Dahiliye"}
    assert "ister misiniz" not in r1["replyText"]

    r2 = k.orkestre(oid, userText=T2).json()
    assert r2["replyText"] == tavsiye_metni()  # randevu teklifi tekrarlanmaz
    assert POLIKLINIK_SORUSU not in r2["replyText"] and "suggestion" not in r2["data"]
    assert r2["data"]["aiGeneralInfo"] is True

    r3 = k.orkestre(oid, userText=T3).json()
    assert r3["replyText"] == tavsiye_metni() and POLIKLINIK_SORUSU not in r3["replyText"]
    assert "suggestion" not in r3["data"]

    yukler = saglik_yukleri(llm)
    assert yukler == [
        "Konu: mide bulantısı\nSoru: Mide bulantısı için evde neler yapılabilir?",
        "Konu: mide bulantısı\nSoru: Mide bulantısı için evde neler yapılabilir?",
        "Konu: mide bulantısı\nSoru: Mide bulantısı için evde neler yapılabilir? Bitki çayı iyi gelir mi?",
    ]
    assert anlama_cagrilari(llm) == [] and randevu_post(k) == []


def test_konusmadan_sonra_fikir_degisirse_randevu_onerilen_poliklinikle(kur):
    k, oid, _ = hazirla(kur)
    for c in (T1, T2, T3):
        k.orkestre(oid, userText=c)
    r = k.orkestre(oid, userText="tamam randevu alayım").json()
    assert r["action"] == "OFFER_SLOTS" and r["data"]["expect"] in ("gun", "hekim") and "Dahiliye" in r["replyText"]
    assert randevu_post(k) == []


@pytest.mark.parametrize("evet", ["evet", "olur", "evet bakalım", "tamam randevu alayım", "randevu bak"])
def test_tavsiyeden_sonra_evet_onerilen_polikligi_secer_randevu_olusturmaz(kur, evet):
    k, oid, _ = hazirla(kur)
    k.orkestre(oid, userText="boğazım ağrıyor evde ne yapabilirim")
    r = k.orkestre(oid, userText=evet).json()
    assert r["action"] == "OFFER_SLOTS" and r["data"]["expect"] in ("gun", "hekim"), r["replyText"]
    assert "Kulak Burun Boğaz" in r["replyText"] and randevu_post(k) == []


def test_tavsiyeden_sonra_tam_randevu_akisi_acik_onayla(kur):
    k, oid, _ = hazirla(kur)
    k.orkestre(oid, userText="belim ağrıyor ne iyi gelir")
    r = k.orkestre(oid, userText="evet").json()
    assert "Ortopedi" in r["replyText"]
    r = k.orkestre(oid, userText="yarın sabah").json()
    r = k.orkestre(oid, userText="ilki").json()
    assert r["action"] == "CONFIRM" and randevu_post(k) == []
    r = k.orkestre(oid, userText="evet onaylıyorum").json()
    assert r["action"] == "DONE" and len(randevu_post(k)) == 1


# ------------------------------------------------------------------ 1. şikâyet + tavsiye isteği aynı cümlede

@pytest.mark.parametrize("cumle,konu,poliklinik", [
    ("midem bulanıyor ne yapabilirim", "mide bulantısı", "Dahiliye"),
    ("başım ağrıyor ne iyi gelir", "baş ağrısı", "Nöroloji"),
    ("boğazım ağrıyor evde ne yapabilirim", "boğaz ağrısı", "Kulak Burun Boğaz"),
    ("başım ağrıyor ne yapayım", "baş ağrısı", "Nöroloji"),
    ("üç gündür öksürüyorum ne yapmalıyım", "öksürük", "Dahiliye"),
    ("ishal oldum ne yapabilirim", "ishal", "Dahiliye"),
    ("belim ağrıyor evde ne yapabilirim", "bel ağrısı", "Ortopedi"),
    ("midem yanıyor bir şey önerir misin", "mide yanması ve reflü", "Dahiliye"),
])
def test_sikayet_ve_tavsiye_istegi(kur, cumle, konu, poliklinik):
    k, oid, llm = hazirla(kur)
    r = k.orkestre(oid, userText=cumle).json()
    assert r["data"]["aiGeneralInfo"] is True
    assert r["replyText"].endswith(alan.TAVSIYE_RANDEVU_TEKLIFI.format(poliklinik=poliklinik)), r["replyText"]
    assert alan.SAGLIK_BILGI_KAPANIS in r["replyText"] and alan.TAVSIYE_SURERSE in r["replyText"]
    assert "ister misiniz" not in r["replyText"] and r["data"]["suggestion"]["clinicName"] == poliklinik
    assert saglik_yukleri(llm)[0].startswith(f"Konu: {konu}\n")


@pytest.mark.parametrize("cumle", ["midem bulanıyor", "başım ağrıyor", "boğazım ağrıyor"])
def test_yalniz_sikayet_eskisi_gibi_poliklinik_onerisi(kur, cumle):
    k, oid, llm = hazirla(kur)
    r = k.orkestre(oid, userText=cumle).json()
    assert r["replyText"].startswith("Bu şikâyet için genellikle") and "ister misiniz" in r["replyText"]
    assert "aiGeneralInfo" not in r["data"] and llm.cagrilar == []


def test_konu_poliklinigi_yoksa_teklif_yok(kur):
    k, oid, _ = hazirla(kur)
    r = k.orkestre(oid, userText="uyuyamıyorum ne yapabilirim").json()
    assert r["replyText"] == tavsiye_metni() and "suggestion" not in r["data"]


def test_genel_bilgi_sorusu_eski_yolda_kalir(kur):
    """"Baş ağrısına ne iyi gelir" (kişisel şikâyet yok, oturumda konu yok): mevcut genel bilgi yolu, teklif yok."""
    k, oid, llm = hazirla(kur)
    r = k.orkestre(oid, userText="baş ağrısına ne iyi gelir").json()
    assert r["replyText"] == f"{TAVSIYE_YANITI} {alan.SAGLIK_BILGI_KAPANIS}"
    assert saglik_yukleri(llm)[0].startswith("Soru: baş ağrısına ne iyi gelir")


# ------------------------------------------------------------------ 2. randevu reddi + tavsiye

@pytest.mark.parametrize("ret", [
    "randevu değil evde ne yapabilirim", "doktora gitmek istemiyorum ne yapabilirim",
    "gitmeden önce ne yapabilirim", "evde ne yapabilirim", "bir şey önerir misin", "fikir ver",
    "randevu istemiyorum bir tavsiye verir misin",
])
def test_oneriden_sonra_ret_ve_tavsiye(kur, ret):
    k, oid, llm = hazirla(kur)
    k.orkestre(oid, userText="midem bulanıyor")
    r = k.orkestre(oid, userText=ret).json()
    assert r["data"]["aiGeneralInfo"] is True and POLIKLINIK_SORUSU not in r["replyText"], r["replyText"]
    assert saglik_yukleri(llm)[-1].startswith("Konu: mide bulantısı\n")
    assert anlama_cagrilari(llm) == []


@pytest.mark.parametrize("ret", ["istemiyorum", "gerek yok", "randevu istemiyorum", "şimdilik istemiyorum"])
def test_oneriye_istemiyorum_poliklinik_sormaz_tavsiye_teklif_eder(kur, ret):
    k, oid, llm = hazirla(kur)
    k.orkestre(oid, userText="midem bulanıyor")
    r = k.orkestre(oid, userText=ret).json()
    assert r["replyText"] == (f"{alan.RANDEVU_RET_YANIT} "
                              f"{alan.RANDEVU_RET_TAVSIYE_TEKLIFI.format(konu='mide bulantısı')}")
    assert "suggestion" not in r["data"] and llm.cagrilar == []
    r = k.orkestre(oid, userText="evet anlat").json()
    assert r["replyText"] == tavsiye_metni() and r["data"]["aiGeneralInfo"] is True


def test_reddettikten_sonra_yeni_sikayette_randevu_sorulmaz(kur):
    k, oid, _ = hazirla(kur)
    k.orkestre(oid, userText="midem bulanıyor")
    k.orkestre(oid, userText="randevu istemiyorum")
    for cumle, bilgi in (("başım ağrıyor", alan.ONERI_BILGI.format(poliklinik="Nöroloji")),
                         ("başım da çok ağrıyor", alan.TAVSIYE_SURERSE)):  # ikincisi şikâyet tablosunda yok
        r = k.orkestre(oid, userText=cumle).json()
        assert "ister misiniz" not in r["replyText"] and POLIKLINIK_SORUSU not in r["replyText"]
        assert "Hangisinden randevu" not in r["replyText"] and r["replyText"].startswith(bilgi), r["replyText"]
        assert "suggestion" not in r["data"]
        assert r["replyText"].endswith(alan.RANDEVU_RET_TAVSIYE_TEKLIFI.format(konu="baş ağrısı"))


def test_hayir_dugmesi_eskisi_gibi_baska_poliklinik_sorar(kur):
    """Ekrandaki "Başka bölüm" düğmesi "hayır" gönderir: başka poliklinik sorusu (değişmedi)."""
    k, oid, _ = hazirla(kur)
    k.orkestre(oid, userText="midem bulanıyor")
    r = k.orkestre(oid, userText="hayır").json()
    assert r["replyText"] == alan.POLIKLINIK_SORU.format(liste=LISTE)


# ------------------------------------------------------------------ 3. bağlam: konu oturumdan; bilinmiyorsa sorulur

@pytest.mark.parametrize("takip", ["doktordan önce yapabileceğim bir şey var mı", "çay iyi gelir mi",
                                   "bu lambasına ne iyi gelir", "peki ne iyi gelir"])
def test_takip_cumlesi_son_sikayeti_kullanir(kur, takip):
    k, oid, llm = hazirla(kur)
    k.orkestre(oid, userText="boğazım ağrıyor")
    r = k.orkestre(oid, userText=takip).json()
    assert r["data"]["aiGeneralInfo"] is True
    assert saglik_yukleri(llm)[-1].startswith("Konu: boğaz ağrısı\nSoru: Boğaz ağrısı için evde neler yapılabilir?")


def test_konu_bilinmiyorsa_hangi_sikayet_sorulur_poliklinik_sorulmaz(kur):
    k, oid, llm = hazirla(kur)
    r = k.orkestre(oid, userText="doktora gitmeden yapabileceğim bir şey var mı").json()
    assert r["replyText"] == alan.TAVSIYE_KONU_SOR and llm.cagrilar == []
    r = k.orkestre(oid, userText="mide bulantısı").json()
    assert r["data"]["aiGeneralInfo"] is True and saglik_yukleri(llm)[0].startswith("Konu: mide bulantısı\n")


def test_teklif_konusmada_bir_kez(kur):
    k, oid, _ = hazirla(kur)
    r1 = k.orkestre(oid, userText="grip oldum ne iyi gelir").json()
    assert r1["replyText"].endswith(alan.TAVSIYE_RANDEVU_TEKLIFI.format(poliklinik="Dahiliye"))
    r2 = k.orkestre(oid, userText="ıhlamur iyi gelir mi").json()
    assert "randevu" not in r2["replyText"] and "suggestion" not in r2["data"]
    r3 = k.orkestre(oid, userText="tamam randevu alayım").json()
    assert "Dahiliye" in r3["replyText"] and r3["data"]["expect"] in ("gun", "hekim")


def test_konuya_ozel_uyari_notu_koddan(kur):
    k, oid, _ = hazirla(kur)
    r = k.orkestre(oid, userText="karnım ağrıyor ne yapayım").json()
    assert alan.TAVSIYE_UYARI_NOTLARI["karin_agrisi"] in r["replyText"]
    assert r["replyText"].index(alan.TAVSIYE_UYARI_NOTLARI["karin_agrisi"]) < r["replyText"].index(alan.TAVSIYE_SURERSE)


def test_kalp_onerisine_istemiyorum_112_notu_korunur(kur):
    k, oid, llm = hazirla(kur)
    k.orkestre(oid, userText="kalbim ağrıyor")
    r = k.orkestre(oid, userText="istemiyorum").json()
    assert r["replyText"] == " ".join([alan.RANDEVU_RET_YANIT, alan.ONERI_ACIL_NOTU["KARDIYOLOJI"],
                                       alan.RANDEVU_RET_GENEL])
    assert llm.cagrilar == []


def test_kendi_sikayeti_icin_ilac_karari_sabit_yonlendirme(kur):
    k, oid, llm = hazirla(kur)
    r = k.orkestre(oid, userText="başım ağrıyor parol içsem iyi gelir mi").json()
    assert r["replyText"].startswith(alan.SAGLIK_SPESIFIK_GIRIS["kisisel_ilac"]) and saglik_yukleri(llm) == []


def test_ev_yontemi_sorulmadiysa_konusuz_tedavi_cumlesi_atilir():
    soru = saglik_tavsiye_yuku_kur("bulanti", (), alan=alan).mesajlar[0].icerik
    ham = TAVSIYE_YANITI + " Bazı kişilerde rahatlatıcı bulabilirler ama bunlar bir tedavi yerine geçmez."
    assert yanit_denetle(ham, soru, alan, tavsiye=True)[0] == TAVSIYE_YANITI
    soru2 = saglik_tavsiye_yuku_kur("bulanti", ("Bitki çayı iyi gelir mi?",), alan=alan).mesajlar[0].icerik
    ham2 = TAVSIYE_YANITI + " Bitki çaylarını bazı kişiler rahatlatıcı bulur ama bir tedavi yerine geçmez."
    assert yanit_denetle(ham2, soru2, alan, tavsiye=True)[0] == ham2


def test_tavsiye_en_cok_uc_cumle():
    soru = saglik_tavsiye_yuku_kur("bulanti", (), alan=alan).mesajlar[0].icerik
    ham = " ".join(f"Bu {s} cümle genellikle iyi gelir." for s in ("birinci", "ikinci", "üçüncü", "dördüncü"))
    metin = yanit_denetle(ham, soru, alan, tavsiye=True)[0]
    assert metin is not None and metin.count(".") == alan.TAVSIYE_AZAMI_CUMLE


def test_ret_ve_konu_yok(kur):
    k, oid, llm = hazirla(kur)
    r = k.orkestre(oid, userText="randevu istemiyorum").json()
    assert r["replyText"] == f"{alan.RANDEVU_RET_YANIT} {alan.RANDEVU_RET_GENEL}" and llm.cagrilar == []


def test_konu_bellegi_vedada_silinir(kur):
    k, oid, llm = hazirla(kur)
    k.orkestre(oid, userText="boğazım ağrıyor")
    k.orkestre(oid, userText="görüşürüz")
    r = k.orkestre(oid, userText="doktora gitmeden ne yapabilirim").json()
    assert r["replyText"] == alan.TAVSIYE_KONU_SOR


# ------------------------------------------------------------------ 4. STT toleransı (dar)

@pytest.mark.parametrize("cumle,konu", [
    ("midem çok kullanıyor", "bulanti"), ("Midem kullanıyor", "bulanti"), ("midem bulunuyor", "bulanti"),
    ("midem sürekli bulanıyo", "bulanti"), ("bulantısına ne iyi gelir", "bulanti"), ("başım ağrıyor", "bas_agrisi"),
    ("bas agrim var", "bas_agrisi"), ("bogazim agriyor", "bogaz_agrisi"), ("midem kötü", "mide"),
])
def test_konu_bulma_ve_stt_toleransi(cumle, konu):
    assert sikayet_konusu(normalize(cumle), alan) == konu


def test_ilacli_cumle_bulanti_sayilmaz_ve_tavsiye_engelli():
    n = normalize("midem için ilaç kullanıyorum")
    assert sikayet_konusu(n, alan) != "bulanti" and tavsiye_engeli(n, alan, ilac_var=False)


@pytest.mark.parametrize("cumle", ["lamba aldım", "bu lambasına ne iyi gelir",
                                   "araba kullanıyorum", "kullanıyor musun", "yarın evde olmayacağım"])
def test_rastgele_kelime_sikayete_cevrilmez(cumle):
    assert sikayet_konusu(normalize(cumle), alan) is None


@pytest.mark.parametrize("cumle,beklenen", [
    ("doktora gitmeden ne yapabilirim", "guclu"), ("ne iyi gelir", "guclu"), ("iyi gelir mi", "guclu"),
    ("bunları öğrenmek istiyorum", "zayif"), ("ne yapabilirim", "zayif"), ("evde", "zayif"),
    ("kardiyoloji yarın sabah", None), ("evet", None),
])
def test_tavsiye_isareti(cumle, beklenen):
    assert tavsiye_istegi(normalize(cumle), alan) == beklenen


@pytest.mark.parametrize("cumle,beklenen", [
    ("randevu bakmak değil de", True), ("doktora gitmek istemiyorum", True), ("randevu istemiyorum", True),
    ("randevu almak istiyorum", False), ("hayır", False), ("başka bölüm", False),
])
def test_randevu_reddi(cumle, beklenen):
    assert randevu_reddi(normalize(cumle), alan) is beklenen


def test_zayif_isaret_konusuz_mevcut_akista(kur):
    k, oid, llm = hazirla(kur)
    r = k.orkestre(oid, userText="ne yapabilirim").json()
    assert "aiGeneralInfo" not in r["data"] and saglik_yukleri(llm) == []


# ------------------------------------------------------------------ 5. anlaşılmayan cümle: akış dışında nötr

def test_akis_disinda_anlasilmayan_cumle_notr(kur):
    k, oid, _ = hazirla(kur)
    r = k.orkestre(oid, userText="hımm şey").json()
    assert r["replyText"] == alan.ANLASILAMADI_NOTR and POLIKLINIK_SORUSU not in r["replyText"]
    assert r["action"] == "OFFER_SLOTS" and r["data"]["expect"] == "poliklinik"  # sözleşme aynı


def test_akis_disinda_hayir_poliklinik_sormaz(kur):
    k, oid, _ = hazirla(kur)
    r = k.orkestre(oid, userText="hayır").json()
    assert r["replyText"] == alan.RET_NOTR


def test_randevu_istendikten_sonra_anlasilmayan_cumle_eski_davranis(kur):
    k, oid, _ = hazirla(kur)
    r = k.orkestre(oid, userText="randevu almak istiyorum").json()
    assert POLIKLINIK_SORUSU in r["replyText"]
    r = k.orkestre(oid, userText="hımm şey").json()
    assert r["replyText"] == alan.ANLASILAMADI_ONEKI + alan.POLIKLINIK_SORU_TEKRAR


def test_gun_beklenirken_anlasilmayan_cumle_eski_davranis(kur):
    k, oid, _ = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji")
    r = k.orkestre(oid, userText="hımm şey").json()
    assert r["replyText"] == alan.ANLASILAMADI_ONEKI + alan.GUN_SORU_TEKRAR


# ------------------------------------------------------------------ 6. güvenlik: acil önce, engeller, denetim

@pytest.mark.parametrize("cumle", [
    "midem bulanıyor ve göğsüm ağrıyor", "midem bulanıyor göğsüm ağrıyor ne yapabilirim",
    "göğsüm ağrıyor terliyorum", "göğsümde ağrı var sol koluma vuruyor", "kan kusuyorum ne yapabilirim",
    "kanlı kustum", "şiddetli karın ağrım var ne iyi gelir", "karnım çok şiddetli ağrıyor",
    "ensem sertleşti ateşim var", "midem bulanıyor nefes alamıyorum",
])
def test_acil_her_seyden_once(kur, cumle):
    k, oid, llm = hazirla(kur)
    r = k.orkestre(oid, userText=cumle).json()
    assert r["replyText"] == alan.ACIL_YANIT and r["data"]["emergency"] is True and llm.cagrilar == []


@pytest.mark.parametrize("cumle", ["göğüs ağrım var", "midem bulanıyor", "karnım ağrıyor", "başım ağrıyor",
                                   "nefes darlığım var", "kusuyorum"])
def test_acil_birlikte_tek_basina_tetiklenmez(cumle):
    assert not acil_mi(normalize(cumle), alan)


@pytest.mark.parametrize("cumle", [
    "midem ağrıyor hangi ilacı almalıyım", "başım ağrıyor parol içsem iyi gelir mi", "çocuğumun ateşi var ne yapabilirim",
    "hamileyim midem bulanıyor ne iyi gelir", "kalbim çarpıyor ne yapabilirim", "başım ağrıyor ciddi mi",
    "midem bulanıyor ne olabilir", "boğazım ağrıyor antibiyotik iyi gelir mi",
])
def test_engelli_cumleler_tavsiye_yoluna_girmez(kur, cumle):
    k, oid, llm = hazirla(kur)
    r = k.orkestre(oid, userText=cumle).json()
    assert alan.TAVSIYE_SURERSE not in r["replyText"], (cumle, r["replyText"])
    assert all(not y.startswith("Konu:") for y in saglik_yukleri(llm))


def test_tavsiye_engeli_ilac_adi():
    assert tavsiye_engeli(normalize("midem bulanıyor"), alan, ilac_var=True)
    assert not tavsiye_engeli(normalize("midem bulanıyor ne içmeliyim"), alan, ilac_var=False)


@pytest.mark.parametrize("ham,neden", [
    ("Zencefil bulantıyı kesinlikle geçirir. Bol su için.", "riskli_iddia"),
    ("Bitki çayı bulantıyı tedavi eder ve rahatlatır.", "riskli_iddia"),
    ("Metoklopramid bulantıya iyi gelir, eczaneden alabilirsiniz.", "izin"),
    ("Metoklopramid bulantıyı azaltabilen bir ilaçtır.", "baska_ilac"),
    ("Günde 2 litre su için ve dinlenin, bu genellikle iyi gelir.", "doz"),
    ("Sizde muhtemelen gastrit var, hafif beslenin.", "teshis"),
])
def test_tavsiye_ciktisi_denetlenir(ham, neden):
    soru = saglik_tavsiye_yuku_kur("bulanti", (), alan=alan).mesajlar[0].icerik
    metin, n = yanit_denetle(ham, soru, alan, tavsiye=True)
    assert metin is None and n == neden, (ham, n)


def test_tavsiyede_modelin_basvuru_cumlesi_atilir_kodunki_kalir(kur):
    k, oid, _ = hazirla(kur, akilli_llm(TAVSIYE_YANITI + " Bulantı geçmezse doktora başvurun."))
    r = k.orkestre(oid, userText="midem bulanıyor ne yapabilirim").json()
    assert r["replyText"] == tavsiye_metni("Dahiliye")


def test_denetimden_gecmeyen_yanitta_sabit_metin_ve_etiket_yok(kur):
    k, oid, _ = hazirla(kur, akilli_llm("Parasetamol 500 mg alın."))
    r = k.orkestre(oid, userText="başım ağrıyor ne yapayım").json()
    assert "Parasetamol" not in r["replyText"] and r["replyText"].startswith(alan.SAGLIK_BILGI_VEREMIYORUM)
    assert "aiGeneralInfo" not in r["data"]


def test_yanit_sesli_sinirin_altinda(kur):
    uzun = " ".join(["Suyu az az ve sık yudumlamak genellikle rahatlatır."] * 8)
    k, oid, _ = hazirla(kur, akilli_llm(uzun))
    r = k.orkestre(oid, userText="midem bulanıyor ne yapabilirim").json()
    assert len(r["replyText"]) <= alan.SAGLIK_BILGI_AZAMI_SESLI and r["data"]["aiGeneralInfo"] is True


def test_saglik_llm_kapaliyken_eski_davranis(kur):
    k, oid, llm = hazirla(kur, akilli_llm(), SAGLIK_BILGI_LLM="0")
    r = k.orkestre(oid, userText="başım ağrıyor ne yapayım").json()
    assert r["replyText"].startswith(alan.TIBBI_YORUM_YOK) and saglik_yukleri(llm) == []


# ------------------------------------------------------------------ KVKK: yük yalnız koddan

def test_llm_yukune_ad_tc_kayit_girmez(kur):
    k, oid, llm = hazirla(kur)
    k.orkestre(oid, userText="son muayenemde hangi ilaçlar yazıldı")  # geçmiş belleğe alınır
    k.orkestre(oid, userText="Benim adım Zeynep Kara midem bulanıyor evde ne yapabilirim kodum 482913")
    k.orkestre(oid, userText="10000000146 midem bulanıyor ne yapabilirim")
    yukler = saglik_yukleri(llm)
    assert yukler == ["Konu: mide bulantısı\nSoru: Mide bulantısı için evde neler yapılabilir?"]
    tum = json.dumps([[m.icerik for m in c["mesajlar"]] for c in llm.cagrilar], ensure_ascii=False)
    for yasak in ("Zeynep", "Kara", "482913", "10000000146", "Amlodipin", "Metformin", "Ayşe"):
        assert yasak not in tum


def test_yuk_kapali_liste_disini_kabul_etmez():
    assert saglik_tavsiye_yuku_kur("uydurma", (), alan=alan) is None
    yuk = saglik_tavsiye_yuku_kur("bulanti", ("Bitki çayı iyi gelir mi?", "Zeynep Kara 10000000146"), alan=alan)
    assert yuk.mesajlar[0].icerik == ("Konu: mide bulantısı\nSoru: Mide bulantısı için evde neler yapılabilir? "
                                      "Bitki çayı iyi gelir mi?")
    assert yuk.sistem == alan.SAGLIK_BILGI_ISTEMI


def test_alt_sorular_kapali_listeden():
    assert alt_sorular(normalize("önce bir çayda da iyi gelebilir mi zencefil"), alan) == (
        "Bitki çayı iyi gelir mi?", "Zencefil iyi gelir mi?")
    assert alt_sorular(normalize("balgam var"), alan) == ()
    assert tavsiye_sorusu("idrar_yanmasi", (), alan) == "İdrar yaparken yanma için evde neler yapılabilir?"


# ------------------------------------------------------------------ 7. randevu akışı ortasında davranış

def test_onay_beklerken_tavsiye_secimi_korur_evet_yeniden_okur(kur):
    k = kur(akilli_llm())
    oid = k.api.oturum_ac("VERIFIED")
    secilen = onay_adimina_gel(k, oid)
    r = k.orkestre(oid, userText="midem bulanıyor ne yapabilirim").json()
    assert r["action"] == "CONFIRM" and r["data"]["slotId"] == secilen
    assert r["replyText"].endswith(alan.SAGLIK_ONAY_HATIRLAT) and "suggestion" not in r["data"]
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] == "CONFIRM" and randevu_post(k) == []  # önce seçim yeniden okunur
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"


def test_onay_beklerken_randevu_istemiyorum_eski_davranis(kur):
    k = kur(akilli_llm())
    oid = k.api.oturum_ac("VERIFIED")
    onay_adimina_gel(k, oid)
    r = k.orkestre(oid, userText="istemiyorum").json()
    assert r["replyText"] == alan.SECIM_IPTAL


def test_gun_beklenirken_tavsiye_adimi_degistirmez_teklif_yok(kur):
    k, oid, _ = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji")
    r = k.orkestre(oid, userText="başım ağrıyor ne iyi gelir").json()
    assert r["data"]["expect"] == "gun" and "suggestion" not in r["data"]
    assert alan.TAVSIYE_RANDEVU_TEKLIFI.format(poliklinik="Nöroloji") not in r["replyText"]
    r = k.orkestre(oid, userText="yarın").json()
    assert r["data"]["expect"] == "saat"


def test_gun_beklenirken_zayif_isaret_akisi_bozmaz(kur):
    k, oid, llm = hazirla(kur)
    k.orkestre(oid, userText="boğazım ağrıyor")
    k.orkestre(oid, userText="evet")
    r = k.orkestre(oid, userText="yarın evde olmayacağım").json()
    assert alan.TAVSIYE_SURERSE not in r["replyText"] and saglik_yukleri(llm) == []


def test_tamamlanan_oturumda_tavsiye_teklifsiz(kur):
    k, oid, _ = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    k.orkestre(oid, userText="ilki")
    assert k.orkestre(oid, userText="evet onaylıyorum").json()["action"] == "DONE"
    r = k.orkestre(oid, userText="midem bulanıyor ne yapabilirim").json()
    assert r["replyText"] == tavsiye_metni() and r["action"] == "DONE"


def test_kural_anlama_degismedi():
    assert kural_tabanli_anla("midem bulanıyor", BUGUN, alan).oneri == "DAHILIYE"
