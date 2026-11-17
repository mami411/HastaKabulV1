"""Mobil cep sağlık asistanı: şikâyet → poliklinik önerisi, acil durum, sohbet, mevcut randevu, mobil metinler.

Hepsi kural + şablon: bu testlerde LLM'e giden tek bir çağrı bile hata sayılır (sahte LLM kayıt tutar).
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import BUGUN, ayarlar
from ortak.llm import SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.anlama import kural_tabanli_anla
from test_denetim_duzeltmeleri import onay_adimina_gel

BELIRSIZ_LLM = json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
                           "saat_bitis": None})
LISTE = alan.poliklinik_listesi_metni()


def ad(kod: str) -> str:
    return alan.poliklinik_bul(kod).ad


def hazirla(kur):
    llm = SahteSaglayici(varsayilan=BELIRSIZ_LLM)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    r = k.orkestre(oid, event="TC_VERIFIED").json()
    return k, oid, r, llm


def randevu_post(k):
    return [c for c in k.api.cagrilar if c[0] == "POST" and c[1].endswith("/appointments")]


# ------------------------------------------------------------------ tablo: gerçek kullanımda sık cümleler
# Beklenen: poliklinik kodu (öneri), "LISTE" (doğru bölüm söylenemez → dört poliklinik), "ACIL" (112),
# "TIBBI" (yorum yok), "GECMIS"/"ILAC" (geçmiş muayene), "TAVSIYE" (ilaç tavsiyesi reddi),
# "SECIM:<kod>" (kullanıcı polikliniği kendisi söyledi), "YOK" (acil/şikâyet SAYILMAMALI).
TABLO = [
    # Göz
    ("gözüm bulanık görüyor", "GOZ"), ("gozum bulanik goruyor", "GOZ"), ("gözlerim kaşınıyor", "GOZ"),
    ("gözümde kızarıklık var", "GOZ"), ("göz kızarıklığı", "GOZ"), ("gözüm ağrıyor", "GOZ"),
    ("gözlüğümün numarası değişti galiba", "GOZ"), ("gözlük yaptırmak istiyorum", "GOZ"),
    ("göz tansiyonum var", "GOZ"), ("uzağı net göremiyorum", "GOZ"), ("gözlerim sulanıyor", "GOZ"),
    ("katarakt olabilir mi diye baktırmak istiyorum", "GOZ"), ("gözümün kapağı şişti", "GOZ"),
    # Kardiyoloji
    ("kalbim için bakılsın", "KARDIYOLOJI"), ("kalbim ağrıyor", "KARDIYOLOJI"), ("kalbimde çarpıntı var", "KARDIYOLOJI"),
    ("kalbim agriyor", "KARDIYOLOJI"), ("çarpıntım oluyor", "KARDIYOLOJI"), ("tansiyonum yüksek", "KARDIYOLOJI"),
    ("göğüs ağrım var", "KARDIYOLOJI"), ("merdiven çıkınca nefes darlığı oluyor", "KARDIYOLOJI"),
    ("tansiyonum çok düştü", "KARDIYOLOJI"), ("kalp kontrolü yaptırmak istiyorum", "KARDIYOLOJI"),
    ("efor testi için", "KARDIYOLOJI"), ("kalp krizi geçirmiştim kontrol lazım", "KARDIYOLOJI"),
    # Ortopedi
    ("belim ağrıyor", "ORTOPEDI"), ("belim agriyor", "ORTOPEDI"), ("dizlerim ağrıyor", "ORTOPEDI"),
    ("dizim şişti", "ORTOPEDI"), ("boynum tutuldu", "ORTOPEDI"), ("omzum ağrıyor", "ORTOPEDI"),
    ("kolum kırıldı", "ORTOPEDI"), ("ayak bileğimi burktum", "ORTOPEDI"), ("bel fıtığım var", "ORTOPEDI"),
    ("eklemlerim ağrıyor", "ORTOPEDI"), ("kas ağrım geçmiyor", "ORTOPEDI"), ("sırtım ağrıyor", "ORTOPEDI"),
    ("topuğum ağrıyor", "ORTOPEDI"), ("dizi izlerken boynum ağrıdı", "ORTOPEDI"),
    # Dahiliye
    ("midem ağrıyor", "DAHILIYE"), ("midem bulanıyor", "DAHILIYE"), ("şeker hastasıyım", "DAHILIYE"),
    ("şekerim yüksek çıktı", "DAHILIYE"), ("tiroidim için kontrol", "DAHILIYE"), ("halsizim sürekli yorgunum", "DAHILIYE"),
    ("ateşim var", "DAHILIYE"), ("grip oldum", "DAHILIYE"), ("kansızlığım var", "DAHILIYE"),
    ("genel kontrol yaptırmak istiyorum", "DAHILIYE"), ("check-up istiyorum", "DAHILIYE"),
    ("üç gündür öksürüyorum", "DAHILIYE"), ("ishal oldum", "DAHILIYE"), ("kolesterolüm yüksek", "DAHILIYE"),
    # Birden çok polikliniğe işaret eden / listede olmayan
    ("başım dönüyor", "LISTE"), ("gözüm ve dizim ağrıyor", "LISTE"), ("dişim ağrıyor", "LISTE"),
    # MedTech kataloğundaki yeni poliklinikler (şikâyetten öneri)
    ("cildimde döküntü var", "DERMATOLOJI"), ("sivilcelerim çoğaldı", "DERMATOLOJI"),
    ("kulağım ağrıyor", "KBB"), ("boğazım ağrıyor", "KBB"), ("burnum tıkalı nefes alamıyorum", "KBB"),
    ("hamileyim", "KADIN_DOGUM"), ("çocuğumun ateşi var", "COCUK_SAGLIGI"),
    ("migrenim var", "NOROLOJI"), ("idrarda yanma var", "UROLOJI"),
    # Acil
    ("göğsümde şiddetli ağrı var", "ACIL"), ("nefes alamıyorum", "ACIL"), ("bayılacak gibiyim", "ACIL"),
    ("babam felç geçiriyor galiba", "ACIL"), ("kanama durmuyor", "ACIL"), ("intihar etmeyi düşünüyorum", "ACIL"),
    ("göğsüm sıkışıyor", "ACIL"), ("annem bayıldı uyanmıyor", "ACIL"), ("yüzüm kaydı konuşamıyorum", "ACIL"),
    ("kalp krizi mi geçiriyorum", "ACIL"), ("ambulans çağırın", "ACIL"), ("nefes alamiyorum", "ACIL"),
    # Tavsiye / geçmiş korunur
    ("kalp ilacımı ne zaman alacağım", "ILAC"), ("tansiyon ilacımı bırakabilir miyim", "ILAC"),
    ("tansiyon ilacımın yerine başka ilaç alabilir miyim", "TAVSIYE"),
    ("son muayenemde hangi ilaçlar yazıldı", "GECMIS"), ("başım ağrıyor hangi ilacı almalıyım", "TIBBI:NOROLOJI"),
    ("ne ilaç içmeliyim", "TIBBI"),
    ("Metformin kullanıyorum", "TIBBI"),
    # Kullanıcı polikliniği de söyledi: poliklinik esas
    ("kalbim ağrıyor kardiyolojiye randevu", "SECIM:KARDIYOLOJI"), ("gözüm bulanık ortopedi olsun", "SECIM:ORTOPEDI"),
    ("göz doktoruna gitmek istiyorum gözüm kaşınıyor", "SECIM:GOZ"), ("göze yarın", "SECIM:GOZ"), ("kalp", "SECIM:KARDIYOLOJI"),
    ("göz doktoruna randevu", "SECIM:GOZ"), ("kbb", "SECIM:KBB"), ("kabebe randevu", "SECIM:KBB"),
    ("cildiye", "SECIM:DERMATOLOJI"), ("kadın doğum", "SECIM:KADIN_DOGUM"), ("jinekolog", "SECIM:KADIN_DOGUM"),
    ("çocuk doktoru", "SECIM:COCUK_SAGLIGI"), ("pediatri", "SECIM:COCUK_SAGLIGI"),
    ("nöroloji yarın", "SECIM:NOROLOJI"), ("üroloji", "SECIM:UROLOJI"), ("genel cerrahi", "SECIM:GENEL_CERRAHI"),
    # Olumsuz örnekler: günlük ifadeler acil/şikâyet sayılmaz
    ("acil randevu istiyorum", "YOK"), ("acele etmem lazım", "YOK"), ("acil servis nerede", "YOK"),
    ("belki yarın", "YOK"), ("kasım ayında olsun", "YOK"), ("randevu almak istiyorum", "YOK"),
    ("dizi izliyordum", "YOK"), ("boyunca bekledim", "YOK"), ("bu kadar mı", "YOK"),
]


def test_tablo_en_az_altmis_cumle():
    assert len(TABLO) >= 60


@pytest.mark.parametrize("cumle,beklenen", TABLO)
def test_sikayet_tablosu_kural(cumle, beklenen):
    a = kural_tabanli_anla(cumle, BUGUN, alan, adim="POLIKLINIK_BEKLENIYOR")
    if beklenen in alan.POLIKLINIK_KODLARI:
        assert a.niyet == "sikayet" and a.oneri == beklenen and a.poliklinik is None, a
    elif beklenen == "LISTE":
        assert a.niyet == "sikayet" and a.oneri is None, a
    elif beklenen == "ACIL":
        assert a.niyet == "acil", a
    elif beklenen == "TIBBI":
        assert a.niyet == "tibbi_soru" and a.oneri is None, a
    elif beklenen.startswith("TIBBI:"):
        assert a.niyet == "tibbi_soru" and a.oneri == beklenen.split(":")[1], a
    elif beklenen == "GECMIS":
        assert a.niyet == "gecmis_muayene", a
    elif beklenen == "ILAC":
        assert a.niyet in ("ilac_sorusu", "gecmis_muayene") and a.oneri is None, a
    elif beklenen == "TAVSIYE":
        assert a.niyet == "ilac_tavsiye", a
    elif beklenen.startswith("SECIM:"):
        assert a.niyet == "poliklinik_ve_zaman" and a.poliklinik == beklenen.split(":")[1] and a.oneri is None, a
    else:
        assert a.niyet not in ("acil", "sikayet", "tibbi_soru") and a.oneri is None, a


@pytest.mark.parametrize("cumle,beklenen", [t for t in TABLO if t[1] not in ("YOK",)])
def test_sikayet_tablosu_orkestrator_sablon_ve_llm_yok(kur, cumle, beklenen):
    """Uçtan uca: doğru şablon, kaynak=kural, LLM'e hiç gidilmez, randevu oluşmaz."""
    k, oid, _, llm = hazirla(kur)
    r = k.orkestre(oid, userText=cumle)
    g = r.json()
    metin = g["replyText"]
    assert llm.cagrilar == [], cumle
    assert r.headers.get("X-Anlama-Kaynagi") in ("kural", None)
    assert randevu_post(k) == []
    if beklenen in alan.POLIKLINIK_KODLARI:
        assert metin.startswith(alan.ONERI_SOR.format(poliklinik=ad(beklenen))), metin
        assert g["data"]["expect"] == "poliklinik" and g["data"]["choices"][0] == ad(beklenen)
        assert g["data"]["suggestion"] == {"clinic": beklenen, "clinicName": ad(beklenen)}
        assert r.headers["X-Anlama-Kaynagi"] == "kural"
    elif beklenen == "LISTE":
        assert metin == alan.SIKAYET_BELIRSIZ.format(liste=LISTE), metin
        assert "suggestion" not in g["data"]
    elif beklenen == "ACIL":
        assert metin == alan.ACIL_YANIT and g["data"]["emergency"] is True
    elif beklenen == "TIBBI":
        assert metin == alan.TIBBI_YANIT
    elif beklenen.startswith("TIBBI:"):
        kod = beklenen.split(":")[1]
        assert metin.startswith(alan.TIBBI_YORUM_YOK) and alan.ONERI_SOR.format(poliklinik=ad(kod)) in metin
        assert g["data"]["suggestion"]["clinic"] == kod
    elif beklenen == "TAVSIYE":
        assert metin == alan.ILAC_TAVSIYE_RET
    elif beklenen in ("GECMIS", "ILAC"):
        assert "history" in g["data"]
    elif beklenen.startswith("SECIM:"):
        assert "suggestion" not in g["data"] and not metin.startswith("Bu şikâyet")


def test_kardiyoloji_onerisinde_112_notu_var(kur):
    k, oid, _, _ = hazirla(kur)
    metin = k.orkestre(oid, userText="kalbim ağrıyor").json()["replyText"]
    assert alan.ONERI_ACIL_NOTU["KARDIYOLOJI"] in metin
    # Teşhis dili yok
    for yasak in ("teşhis", "muhtemelen", "kalp krizi", "hastalığınız"):
        assert yasak not in metin


def test_sikayet_ve_tavsiye_sorusu_yorum_yok_ama_oneri_var(kur):
    k, oid, _, llm = hazirla(kur)
    metin = k.orkestre(oid, userText="midem ağrıyor hangi ilacı almalıyım").json()["replyText"]
    assert metin.startswith(alan.TIBBI_YORUM_YOK) and "Dahiliye polikliniğinden" in metin and llm.cagrilar == []


def test_hangi_bolume_gitmeliyim_sikayet_sorar(kur):
    k, oid, _, llm = hazirla(kur)
    g = k.orkestre(oid, userText="hangi bölüme gitmeliyim").json()
    assert g["replyText"] == alan.YONLENDIRME_SOR.format(liste=LISTE) and llm.cagrilar == []


# ------------------------------------------------------------------ tam akış: şikâyetle başlayan randevu

def test_sikayetle_baslayan_tam_randevu_akisi(kur):
    k, oid, _, llm = hazirla(kur)
    r = k.orkestre(oid, userText="gözüm bulanık görüyor").json()
    assert r["data"]["suggestion"]["clinic"] == "GOZ"
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] == "OFFER_SLOTS" and r["data"]["expect"] == "gun"
    assert r["replyText"] == alan.GUN_SORU.format(poliklinik="Göz Hastalıkları")
    assert randevu_post(k) == []  # öneriye "evet" randevu onayı DEĞİL
    r = k.orkestre(oid, userText="yarın").json()
    assert r["data"]["expect"] == "saat"
    r = k.orkestre(oid, userText="sabah").json()
    assert r["data"]["expect"] == "secim"
    r = k.orkestre(oid, userText="ilki").json()
    assert r["action"] == "CONFIRM" and "Göz Hastalıkları" in r["replyText"]
    assert randevu_post(k) == []
    r = k.orkestre(oid, userText="evet onaylıyorum").json()
    assert r["action"] == "DONE" and r["data"]["accessCode"] and r["data"]["clinicName"] == "Göz Hastalıkları"
    assert len(randevu_post(k)) == 1 and llm.cagrilar == []


def test_sikayet_ve_zaman_birlikte(kur):
    k, oid, _, _ = hazirla(kur)
    r = k.orkestre(oid, userText="kalbim için yarın sabah bakar mısınız").json()
    assert r["replyText"].startswith("Tercihinizi not aldım: yarın")
    assert r["data"]["suggestion"]["clinic"] == "KARDIYOLOJI"
    r = k.orkestre(oid, userText="olur").json()
    assert r["action"] == "OFFER_SLOTS" and r["data"]["expect"] == "secim"
    assert all("Kardiyoloji" in s["detail"] for s in r["data"]["slots"])
    assert randevu_post(k) == []


def test_oneri_evet_ve_zaman_tek_cumlede(kur):
    k, oid, _, llm = hazirla(kur)
    k.orkestre(oid, userText="belim ağrıyor")
    r = k.orkestre(oid, userText="evet yarın sabah olsun").json()
    assert r["data"]["expect"] == "secim" and all("Ortopedi" in s["detail"] for s in r["data"]["slots"])
    assert llm.cagrilar == [] and randevu_post(k) == []


# ------------------------------------------------------------------ iki "evet" karışmaz

@pytest.mark.parametrize("evet", ["evet", "olur", "bakalım", "tamam", "evet onaylıyorum", "onaylıyorum"])
def test_oneriye_evet_randevu_olusturmaz(kur, evet):
    k, oid, _, _ = hazirla(kur)
    k.orkestre(oid, userText="dizlerim ağrıyor")
    r = k.orkestre(oid, userText=evet).json()
    assert r["action"] == "OFFER_SLOTS" and r["data"]["expect"] == "gun"
    assert "Ortopedi" in r["replyText"] and randevu_post(k) == []


def test_oneri_sonraki_turda_gecersiz_ikinci_evet_randevu_degil(kur):
    k, oid, _, _ = hazirla(kur)
    k.orkestre(oid, userText="midem ağrıyor")
    k.orkestre(oid, userText="evet")             # Dahiliye seçildi, gün soruldu
    r = k.orkestre(oid, userText="evet").json()   # ikinci "evet": öneri yok, onay sorusu yok
    assert r["action"] == "OFFER_SLOTS" and randevu_post(k) == []
    assert r["replyText"] == alan.ONCE_SAAT_SECIN


# "istemiyorum" 2026-11-16'dan beri randevu reddidir (poliklinik sorulmaz): tests/test_tavsiye.py.
@pytest.mark.parametrize("hayir", ["hayır", "hayır başka", "başka bölüm olsun"])
def test_oneriye_hayir_poliklinik_sorar(kur, hayir):
    k, oid, _, _ = hazirla(kur)
    k.orkestre(oid, userText="kalbim ağrıyor")
    r = k.orkestre(oid, userText=hayir).json()
    assert r["replyText"] == alan.POLIKLINIK_SORU.format(liste=LISTE)
    assert r["data"]["expect"] == "poliklinik" and randevu_post(k) == []


def test_oneriden_sonra_baska_poliklinik_adi_esas(kur):
    k, oid, _, _ = hazirla(kur)
    k.orkestre(oid, userText="kalbim ağrıyor")
    r = k.orkestre(oid, userText="dahiliye olsun").json()
    assert "Dahiliye" in r["replyText"] and r["data"]["expect"] == "gun"


def test_oneriden_sonra_duğmeyle_saat_secimi_ve_onay_dogru_poliklinik(kur):
    """Öneri beklerken ekrandan başka polikliniğin saati seçilirse öneri düşer; "evet" o saatin onayıdır."""
    k, oid, r0, _ = hazirla(kur)
    baska = next(s for s in r0["data"]["slots"] if "Göz" not in s["detail"])
    baska_ad = baska["detail"].split(" · ")[0]
    k.orkestre(oid, userText="gözüm kaşınıyor")
    r = k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": baska["slotId"]}).json()
    assert r["action"] == "CONFIRM"
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] == "DONE" and r["data"]["clinicName"] == baska_ad


def test_onay_beklerken_sikayet_soru_sormaz_evet_secimi_yeniden_okur(kur):
    k = kur(SahteSaglayici(varsayilan=BELIRSIZ_LLM))
    oid = k.api.oturum_ac("VERIFIED")
    onay_adimina_gel(k, oid)
    r = k.orkestre(oid, userText="gözüm bulanık görüyor").json()
    assert r["action"] == "CONFIRM" and "ister misiniz" not in r["replyText"]
    assert r["replyText"].endswith(alan.ONAY_BEKLIYOR_HATIRLAT) and "suggestion" not in r["data"]
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] == "CONFIRM" and "Bu randevuyu oluşturayım mı?" in r["replyText"] and randevu_post(k) == []
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"


# ------------------------------------------------------------------ acil: her adımda öncelikli

ACIL_CUMLELER = ["göğsümde şiddetli ağrı var", "nefes alamıyorum", "bayılacak gibiyim", "felç geçiriyor",
                 "kanama durmuyor", "intihar", "kendime zarar vereceğim"]


@pytest.mark.parametrize("cumle", ACIL_CUMLELER)
def test_acil_kimlik_oncesi_de_oncelikli(kur, cumle):
    llm = SahteSaglayici(varsayilan=BELIRSIZ_LLM)
    k = kur(llm)
    oid = k.api.oturum_ac()
    k.orkestre(oid, event="START")
    r = k.orkestre(oid, userText=cumle)
    assert r.json()["replyText"] == alan.ACIL_YANIT and r.json()["action"] == "ASK_TC"
    assert r.headers["X-Anlama-Kaynagi"] == "kural" and llm.cagrilar == []


@pytest.mark.parametrize("cumle", ACIL_CUMLELER[:3])
def test_acil_onay_adiminda_randevu_olusturmaz_secimi_korur(kur, cumle):
    k = kur(SahteSaglayici(varsayilan=BELIRSIZ_LLM))
    oid = k.api.oturum_ac("VERIFIED")
    secilen = onay_adimina_gel(k, oid)
    g = k.orkestre(oid, userText=cumle).json()
    assert g["replyText"] == alan.ACIL_YANIT and g["action"] == "CONFIRM" and g["data"]["slotId"] == secilen
    assert k.orkestre(oid, userText="evet").json()["action"] == "CONFIRM"  # önce seçim yeniden okunur
    assert randevu_post(k) == []


def test_acil_tamamlanan_ve_devredilen_oturumda_da(kur):
    k, oid, _, _ = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    k.orkestre(oid, userText="ilki")
    assert k.orkestre(oid, userText="evet onaylıyorum").json()["action"] == "DONE"
    assert k.orkestre(oid, userText="nefes alamıyorum").json()["replyText"] == alan.ACIL_YANIT
    k2 = kur(SahteSaglayici(varsayilan=BELIRSIZ_LLM))
    oid2 = k2.api.oturum_ac("HANDED_OFF")
    k2.orkestre(oid2, event="START")
    assert k2.orkestre(oid2, userText="kanama durmuyor").json()["replyText"] == alan.ACIL_YANIT


def test_acil_tc_iceren_cumlede_de_once_acil(kur):
    k, oid, _, llm = hazirla(kur)
    g = k.orkestre(oid, userText="10000000146 nefes alamıyorum").json()
    assert g["replyText"] == alan.ACIL_YANIT and llm.cagrilar == []
    assert "10000000146" not in json.dumps(g)


@pytest.mark.parametrize("cumle", ["acil randevu istiyorum", "acele lazım", "acil olarak kardiyoloji",
                                   "acil servis nerede", "nefes darlığım var", "kalp krizi geçirmiştim",
                                   "burnum tıkalı nefes alamıyorum", "göğüs ağrım var"])
def test_acil_listesi_gunluk_ifadeleri_yakalamaz(cumle):
    assert kural_tabanli_anla(cumle, BUGUN, alan, adim="SLOT_SECIMI").niyet != "acil"


# ------------------------------------------------------------------ sızıntı: bu cümlelerin hiçbiri LLM'e gitmez

def test_sikayet_acil_sohbet_cumleleri_llme_gitmez(kur):
    k, oid, _, llm = hazirla(kur)
    cumleler = [c for c, b in TABLO if b != "YOK"] + [
        "merhaba", "ne yapabilirsin", "yardım", "teşekkürler", "randevularım neler", "randevumu iptal et",
        "hangi bölüme gitmeliyim", "evet", "hayır"]
    for c in cumleler:
        k.orkestre(oid, userText=c)
    assert llm.cagrilar == []


# ------------------------------------------------------------------ karşılama, yardım, sohbet, hızlı eylemler

def test_giris_sonrasi_karsilama_ne_yapabildigini_soyler(kur):
    _, _, r, _ = hazirla(kur)
    assert r["replyText"] == alan.POLIKLINIK_SOR.format(liste=LISTE)
    assert "Randevu alabilir, geçmiş muayenelerinizde yazılan ilaçları sorabilirsiniz" in r["replyText"]
    assert r["data"]["expect"] == "poliklinik"


@pytest.mark.parametrize("cumle,sablon", [
    ("merhaba", "SELAM_YANIT"), ("Merhaba asistan", "SELAM_YANIT"), ("iyi günler", "SELAM_YANIT"),
    ("ne yapabilirsin", "YARDIM"), ("neler yapabilirsin", "YARDIM"), ("yardım", "YARDIM"),
    ("bana nasıl yardımcı olabilirsin", "YARDIM"),
    ("teşekkürler", "TESEKKUR_YANIT"), ("çok sağ ol", "TESEKKUR_YANIT"), ("tamam teşekkür ederim", "TESEKKUR_YANIT"),
    ("görüşürüz", "VEDA_YANIT"), ("çıkış", "VEDA_YANIT"), ("teşekkürler görüşürüz", "VEDA_YANIT"),
    ("hoşça kal", "VEDA_YANIT"),
])
def test_sohbet_ifadeleri_kuralla_sablonla(kur, cumle, sablon):
    k, oid, _, llm = hazirla(kur)
    r = k.orkestre(oid, userText=cumle)
    assert r.json()["replyText"] == getattr(alan, sablon) and llm.cagrilar == []
    assert r.headers["X-Anlama-Kaynagi"] == "kural"
    if sablon == "VEDA_YANIT":
        assert r.json()["data"]["listen"] is False


def test_kimlik_oncesi_yardim_ve_selam(kur):
    k = kur(SahteSaglayici(varsayilan=BELIRSIZ_LLM))
    oid = k.api.oturum_ac()
    assert k.orkestre(oid, event="START").json()["replyText"] == alan.SELAMLAMA
    for c in ("merhaba", "ne yapabilirsin"):
        g = k.orkestre(oid, userText=c).json()
        assert g["replyText"] == alan.YARDIM_GIRIS_ONCESI and g["action"] == "ASK_TC"


def test_onay_beklerken_tesekkur_onay_sorusunu_kapatmaz(kur):
    k = kur(SahteSaglayici(varsayilan=BELIRSIZ_LLM))
    oid = k.api.oturum_ac("VERIFIED")
    onay_adimina_gel(k, oid)
    g = k.orkestre(oid, userText="teşekkürler").json()
    assert g["action"] == "CONFIRM" and g["replyText"].startswith(alan.TESEKKUR_YANIT)
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"


@pytest.mark.parametrize("cumle,niyet", [
    ("Randevu almak istiyorum", "randevu_istegi"), ("İlaçlarım", "gecmis_muayene"),
    ("Son muayenem", "gecmis_muayene"), ("randevu almak istiyorum", "randevu_istegi"),
    ("ilaclarim", "gecmis_muayene"), ("son muayenem", "gecmis_muayene"),
])
def test_hizli_eylem_metinleri_kesin_anlasilir(kur, cumle, niyet):
    assert kural_tabanli_anla(cumle, BUGUN, alan, adim="POLIKLINIK_BEKLENIYOR").niyet == niyet
    k, oid, _, llm = hazirla(kur)
    r = k.orkestre(oid, userText=cumle)
    assert r.headers["X-Anlama-Kaynagi"] == "kural" and llm.cagrilar == []
    g = r.json()
    if niyet == "randevu_istegi":
        assert g["data"]["expect"] == "poliklinik"
    else:
        assert "history" in g["data"] and g["data"]["history"]["visits"]


# ------------------------------------------------------------------ mevcut randevular: dürüst sabit şablon

@pytest.mark.parametrize("cumle,sablon", [
    ("randevularım neler", "MEVCUT_RANDEVU_YOK"), ("mevcut randevularımı göster", "MEVCUT_RANDEVU_YOK"),
    ("randevum ne zaman", "MEVCUT_RANDEVU_YOK"), ("randevumu iptal et", "MEVCUT_RANDEVU_IPTAL"),
    ("yarınki randevumu iptal etmek istiyorum", "MEVCUT_RANDEVU_IPTAL"), ("randevumu ertele", "MEVCUT_RANDEVU_IPTAL"),
])
def test_mevcut_randevu_sorusu_uydurulmaz(kur, cumle, sablon):
    k, oid, _, llm = hazirla(kur)
    g = k.orkestre(oid, userText=cumle).json()
    assert g["replyText"] == getattr(alan, sablon) and llm.cagrilar == []
    assert not [c for c in k.api.cagrilar if c[0] in ("DELETE", "PUT", "PATCH")] and randevu_post(k) == []


def test_tamamlanan_oturumda_randevum_ne_zaman_bu_oturumdakini_soyler(kur):
    k, oid, _, _ = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    k.orkestre(oid, userText="ilki")
    kod = k.orkestre(oid, userText="evet onaylıyorum").json()["data"]["accessCode"]
    assert kod in k.orkestre(oid, userText="randevum ne zaman").json()["replyText"]
    assert k.orkestre(oid, userText="randevumu iptal et").json()["replyText"] == alan.MEVCUT_RANDEVU_IPTAL


def test_onay_beklerken_randevuyu_iptal_et_secimi_birakir(kur):
    k = kur(SahteSaglayici(varsayilan=BELIRSIZ_LLM))
    oid = k.api.oturum_ac("VERIFIED")
    onay_adimina_gel(k, oid)
    g = k.orkestre(oid, userText="randevuyu iptal et").json()
    assert g["action"] == "OFFER_SLOTS" and g["replyText"] == alan.SECIM_IPTAL and randevu_post(k) == []


# ------------------------------------------------------------------ mobil metinler, ayarlar

_LOBI_IFADELERI = ("danışma", "bankosu", "görevli", "kiosk", "ekrandaki tuş", "lobi")


def test_sablonlarda_lobi_kiosk_dili_yok():
    for adi, deger in vars(alan).items():
        if adi.isupper() and isinstance(deger, str) and adi not in ("SISTEM_ISTEMI", "STT_ANAHTAR_TERIMLER"):
            for ifade in _LOBI_IFADELERI:
                assert ifade not in deger.lower(), (adi, ifade)
    assert "kiosk" not in alan.SISTEM_ISTEMI.lower()
    assert alan.TC_TUS_TAKIMINDAN == "Kimlik numaranızı sesli söylemeyin; uygulamadaki tuş takımından girin."
    assert alan.SISTEM_HATASI.startswith("Şu an sisteme ulaşamıyorum")
    assert alan.CAGRI_MERKEZI in alan.SISTEM_HATASI and alan.CAGRI_MERKEZI == "0850 000 00 00"


def test_handoff_reason_kodlari_degismedi(kur):
    k = kur(SahteSaglayici(varsayilan=BELIRSIZ_LLM))
    oid = k.api.oturum_ac("HANDED_OFF")
    g = k.orkestre(oid, event="START").json()
    assert g["action"] == "HANDOFF" and g["data"]["reason"] == "TC_ATTEMPTS_EXHAUSTED"
    assert g["replyText"] == alan.DEVIR_TC


def test_bosta_kalma_varsayilani_mobil_ve_sesli_gecmis():
    a = ayarlar()
    assert a.bosta_kalma_sn == 300.0 and a.gecmis_sesli_okuma is True


# ------------------------------------------------------------------ TTS: eğik çizgili doz yalnız sesli kopyada

@pytest.mark.parametrize("metin,okunus", [
    ("12,5 mg/5 ml", "on iki buçuk miligram bölü beş mililitre"),
    ("Parasetamol şurup 120 mg/5 ml, günde 3 kez", "yüz yirmi miligram bölü beş mililitre"),
    ("250 mg/5ml", "iki yüz elli miligram bölü beş mililitre"),
    ("5 mg/ml damla", "beş miligram bölü bir mililitre damla"),
    ("100 mcg / 2 ml", "yüz mikrogram bölü iki mililitre"),
])
def test_egik_cizgili_doz_bolu_ile_acilir(metin, okunus):
    assert okunus in alan.tts_on_isleme(metin)


@pytest.mark.parametrize("metin", ["100 IU/ml", "40 mg/gün", "2 mg/kg", "15/11/2026", "1/2 tablet"])
def test_emin_olunmayan_egik_kaliplara_dokunulmaz(metin):
    assert "bölü" not in alan.tts_on_isleme(metin)


def test_egik_doz_ekrandaki_metni_degistirmez(kur):
    """Sesli kopya dışında hiçbir yerde açılım yok: şablon metni ham kalır."""
    assert re.search(r"mg/5 ml", alan.GECMIS_ILAC.format(ad="X", amac="", kullanim="12,5 mg/5 ml"))
