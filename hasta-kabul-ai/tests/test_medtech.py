"""MedTech Özel Hastanesi özelleştirmesi: kurum ayarı, yeni poliklinikler, hekim seçimi, kurum soruları, TTS.

Kurum değerleri ``kurum.json``'dan gelir (ortak/kurum.py); şablonlar bu değerlerle kurulur. Kurum soruları ve
hekim seçimi kural + sabit şablonla yanıtlanır, LLM'e gitmez.
"""

from __future__ import annotations

import json

import pytest

from ortak.kurum import KURUM, Kurum, kurum_yukle, telefon_okunusu
from ortak.llm import SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.anlama import kural_tabanli_anla
from conftest import BUGUN

BELIRSIZ_LLM = json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
                           "saat_bitis": None, "yanit": ""})


def hazirla(kur):
    llm = SahteSaglayici(varsayilan=BELIRSIZ_LLM)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    r = k.orkestre(oid, event="TC_VERIFIED").json()
    return k, oid, r, llm


# ------------------------------------------------------------------ kurum ayarı

def test_varsayilan_kurum_medtech():
    assert KURUM.tam_ad == "MedTech Özel Hastanesi" and KURUM.kisa_ad == "MedTech"
    assert KURUM.uygulama_adi == "MedTech Cep" and KURUM.asistan_adi == "MedTech Asistan"
    assert KURUM.cagri_merkezi == "0850 000 00 00"


def test_kurum_dosyasi_okunur_eksik_alan_varsayilana_duser(tmp_path):
    dosya = tmp_path / "kurum.json"
    dosya.write_text(json.dumps({"tam_ad": "Örnek Klinik", "kisa_ad": "Örnek", "cagri_merkezi": "  ",
                                 "okunuslar": {"Örnek": "Ör nek"}}), encoding="utf-8")
    k = kurum_yukle(ortam={"KURUM_DOSYASI": str(dosya)})
    assert k.tam_ad == "Örnek Klinik" and k.kisa_ad == "Örnek"
    assert k.cagri_merkezi == Kurum().cagri_merkezi  # boş alan → varsayılan
    assert k.asistan_adi == "MedTech Asistan" and k.okunuslar == {"Örnek": "Ör nek"}


@pytest.mark.parametrize("icerik", ["{bozuk", "[1, 2]", ""])
def test_bozuk_kurum_dosyasi_servisi_dusurmez(tmp_path, icerik):
    dosya = tmp_path / "kurum.json"
    dosya.write_text(icerik, encoding="utf-8")
    assert kurum_yukle(dosya) == Kurum()
    assert kurum_yukle(tmp_path / "yok.json") == Kurum()


def test_sablonlar_kurum_adini_ayardan_alir():
    assert alan.KURUM_TAM_AD == KURUM.tam_ad and alan.ASISTAN_ADI == KURUM.asistan_adi
    assert KURUM.asistan_adi in alan.SELAMLAMA and KURUM.tam_ad in alan.SELAMLAMA
    assert KURUM.tam_ad in alan.VEDA_YANIT
    for ad in ("SISTEM_HATASI", "SISTEM_HATASI_SAATLER", "DEVIR", "BEKLENMEYEN_HATA"):
        assert KURUM.cagri_merkezi in getattr(alan, ad), ad
    # Devlet hastanesi / MHRS dili kalmadı
    for adi, deger in vars(alan).items():
        if adi.isupper() and isinstance(deger, str) and adi != "SISTEM_ISTEMI":
            for yasak in ("mhrs", "182", "devlet hastanesi", "hastaneyi arayabilirsiniz"):
                assert yasak not in deger.lower(), (adi, yasak)


def test_telefon_okunusu():
    assert telefon_okunusu("0850 000 00 00") == "sıfır sekiz yüz elli, sıfır sıfır sıfır, sıfır sıfır, sıfır sıfır"
    assert telefon_okunusu("0212 123 45 67") == "sıfır iki yüz on iki, yüz yirmi üç, kırk beş, altmış yedi"


def test_tts_unvan_telefon_ve_kurum_okunusu():
    sesli = alan.tts_on_isleme("Prof. Dr. Nermin T. ve Uzm. Dr. Mehmet D. · Op. Dr. Selin E., Doç. Dr. Elif K.")
    assert "Profesör Doktor Nermin" in sesli and "Uzman Doktor Mehmet" in sesli
    assert "Operatör Doktor Selin" in sesli and "Doçent Doktor Elif" in sesli
    sesli = alan.tts_on_isleme(alan.SISTEM_HATASI)
    assert "0850" not in sesli and "sıfır sekiz yüz elli" in sesli
    for yazim, okunus in KURUM.okunuslar.items():
        assert okunus in alan.tts_on_isleme(alan.SELAMLAMA) and yazim not in alan.tts_on_isleme(alan.SELAMLAMA)


# ------------------------------------------------------------------ yeni poliklinikler (kural)

@pytest.mark.parametrize("cumle,kod", [
    ("göz doktoruna randevu", "GOZ"), ("kbb", "KBB"), ("kbbye yarın", "KBB"), ("kabebe", "KBB"),
    ("ka be be randevusu", "KBB"), ("kulak burun boğaz", "KBB"), ("cildiye", "DERMATOLOJI"),
    ("cildiyeye randevu", "DERMATOLOJI"), ("dermatalog", "DERMATOLOJI"), ("kadın doğum", "KADIN_DOGUM"),
    ("kadın doğuma randevu", "KADIN_DOGUM"), ("jinekolog", "KADIN_DOGUM"), ("çocuk doktoru", "COCUK_SAGLIGI"),
    ("cocuk doktoru", "COCUK_SAGLIGI"), ("pediatri", "COCUK_SAGLIGI"), ("nöroloji", "NOROLOJI"),
    ("neroloji", "NOROLOJI"), ("NÖROLOJİ", "NOROLOJI"), ("üroloji", "UROLOJI"), ("ÜROLOJİ", "UROLOJI"),
    ("genel cerrahi", "GENEL_CERRAHI"), ("İÇ HASTALIKLARI", "DAHILIYE"), ("kulak", "KBB"), ("cilt", "DERMATOLOJI"),
])
def test_yeni_poliklinik_adlari(cumle, kod):
    a = kural_tabanli_anla(cumle, BUGUN, alan, adim="POLIKLINIK_BEKLENIYOR")
    assert a.niyet == "poliklinik_ve_zaman" and a.poliklinik == kod, a


@pytest.mark.parametrize("cumle,kod", [
    ("gözüm bulanık", "GOZ"), ("gözüm kaşınıyor", "GOZ"), ("kulağım ağrıyor", "KBB"), ("boğazım ağrıyor", "KBB"),
    ("cildimde döküntü var", "DERMATOLOJI"), ("çocuğumun ateşi var", "COCUK_SAGLIGI"),
    ("hamileyim kontrol", "KADIN_DOGUM"), ("migrenim var", "NOROLOJI"), ("idrarda yanma var", "UROLOJI"),
])
def test_sikayetten_yeni_poliklinik_onerisi(cumle, kod):
    a = kural_tabanli_anla(cumle, BUGUN, alan, adim="POLIKLINIK_BEKLENIYOR")
    assert a.niyet == "sikayet" and a.oneri == kod and a.poliklinik is None, a


def test_sikayet_onerisi_orkestratorde_llmsiz(kur):
    k, oid, _, llm = hazirla(kur)
    r = k.orkestre(oid, userText="kulağım ağrıyor").json()
    assert r["replyText"].startswith(alan.ONERI_SOR.format(poliklinik="Kulak Burun Boğaz"))
    assert r["data"]["suggestion"] == {"clinic": "KBB", "clinicName": "Kulak Burun Boğaz"}
    r = k.orkestre(oid, userText="evet").json()
    assert r["data"]["expect"] == "gun" and "Kulak Burun Boğaz" in r["replyText"]
    assert llm.cagrilar == []


def test_poliklinik_sorusu_listesi_kisa_ekran_tam():
    metin = alan.poliklinik_listesi_metni()
    assert f"{len(alan.POLIKLINIKLER)} polikliniğimiz var" in metin
    assert len(metin) < 120  # 11 poliklinik sesli sayılmaz


# ------------------------------------------------------------------ çok hekimli poliklinik

def test_cok_hekimli_poliklinikte_hekim_sorulur_ve_secilir(kur):
    k, oid, _, llm = hazirla(kur)
    r = k.orkestre(oid, userText="kadın doğum").json()
    assert r["action"] == "OFFER_SLOTS" and r["data"]["expect"] == "hekim"
    assert r["data"]["choices"] == ["Prof. Dr. Nermin T.", "Op. Dr. Selin E.", alan.HEKIM_FARK_ETMEZ_SECENEGI]
    assert r["replyText"] == alan.HEKIM_SORU.format(poliklinik="Kadın Hastalıkları ve Doğum",
                                                     hekimler="Prof. Dr. Nermin T. ve Op. Dr. Selin E.")
    assert r["data"]["slots"] == []
    r = k.orkestre(oid, userText="profesör olsun").json()
    assert r["replyText"].startswith("Tercihinizi not aldım: Prof. Dr. Nermin T. ")
    assert r["data"]["expect"] == "gun"
    assert r["data"]["slots"] and all("Nermin" in s["detail"] for s in r["data"]["slots"])
    k.orkestre(oid, userText="yarın")
    k.orkestre(oid, userText="en erken")
    r = k.orkestre(oid, userText="ilki").json()
    assert r["action"] == "CONFIRM" and "Prof. Dr. Nermin T." in r["replyText"]
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] == "DONE" and r["data"]["clinicName"] == "Kadın Hastalıkları ve Doğum"
    assert llm.cagrilar == []


@pytest.mark.parametrize("yanit,hekim", [("Selin Ergin", "Selin"), ("selin hanım", "Selin"), ("ikincisi", "Selin"),
                                          ("nermin hoca", "Nermin"), ("operatör doktor", "Selin"),
                                          ("Selin Hanım olsun", "Selin"), ("Nermin Hanım", "Nermin"),
                                          ("Doktor Selin Ergin", "Selin")])
def test_hekim_ad_sira_unvan_ile_secilir(kur, yanit, hekim):
    """"Selin Hanım" maskelemede "[ad] hanım" olur; hekim eşlemesi ham metinle kodda yapılır, LLM'e gitmez."""
    k, oid, _, llm = hazirla(kur)
    k.orkestre(oid, userText="kadın doğum")
    r = k.orkestre(oid, userText=yanit).json()
    assert r["data"]["expect"] == "gun", r["replyText"]
    assert all(hekim in s["detail"] for s in r["data"]["slots"])
    assert llm.cagrilar == []


@pytest.mark.parametrize("yanit", ["fark etmez", "farketmez", "hayır", "tercihim yok", "Fark etmez"])
def test_hekim_fark_etmez(kur, yanit):
    k, oid, _, _ = hazirla(kur)
    k.orkestre(oid, userText="çocuk doktoru")
    r = k.orkestre(oid, userText=yanit).json()
    assert r["data"]["expect"] == "gun"
    assert {s["detail"].split(" · ")[1] for s in r["data"]["slots"]} >= {"Uzm. Dr. Burak Ş."}


def test_hekim_sorusuna_anlamsiz_yanit_tekrar_sorar(kur):
    k, oid, _, _ = hazirla(kur)
    k.orkestre(oid, userText="çocuk doktoru")
    r = k.orkestre(oid, userText="evet").json()
    assert r["data"]["expect"] == "hekim"


def test_tek_hekimli_poliklinikte_hekim_sorulmaz(kur):
    k, oid, _, _ = hazirla(kur)
    for ad in ("kbb", "cildiye", "nöroloji", "üroloji", "genel cerrahi", "göz doktoru"):
        oid = k.api.oturum_ac("VERIFIED")
        k.orkestre(oid, event="TC_VERIFIED")
        r = k.orkestre(oid, userText=ad).json()
        assert r["data"]["expect"] == "gun", (ad, r["replyText"])


# ------------------------------------------------------------------ kurum soruları

@pytest.mark.parametrize("cumle,konu", [
    ("çağrı merkezinizin numarası ne", "cagri_merkezi"), ("sizi nasıl arayabilirim", "cagri_merkezi"),
    ("adresiniz nedir", "adres"), ("hastane nerede", "adres"),
    ("cumartesi açık mısınız", "calisma_saatleri"), ("çalışma saatleriniz", "calisma_saatleri"),
    ("muayene ücreti ne kadar", "ucret"), ("kaç para", "ucret"),
    ("sgk geçiyor mu", "sigorta"), ("özel sigortam anlaşmalı mı", "sigorta"),
    ("sigortam muayene ücretini karşılıyor mu", "sigorta"),
])
def test_kurum_sorusu_anlasilir(cumle, konu):
    a = kural_tabanli_anla(cumle, BUGUN, alan, adim="POLIKLINIK_BEKLENIYOR")
    assert a.niyet == "kurum_bilgisi" and a.alt == konu, a


@pytest.mark.parametrize("konu,cumle", [("cagri_merkezi", "çağrı merkezi numaranız"), ("adres", "adresiniz nedir"),
                                        ("calisma_saatleri", "pazar açık mı acaba"), ("ucret", "muayene ücreti"),
                                        ("sigorta", "sgk geçiyor mu")])
def test_kurum_sorusu_sabit_yanit_llmsiz(kur, konu, cumle):
    k, oid, _, llm = hazirla(kur)
    r = k.orkestre(oid, userText=cumle).json()
    assert r["replyText"] == alan.KURUM_YANITLARI[konu]
    assert r["data"]["institutionInfo"] == konu and r["action"] == "OFFER_SLOTS"
    assert llm.cagrilar == []


def test_ucret_ve_sigortada_bilgi_uydurulmaz():
    for konu in ("ucret", "sigorta"):
        metin = alan.KURUM_YANITLARI[konu]
        assert KURUM.cagri_merkezi in metin
        assert not any(c in metin for c in ("TL", "lira", "%", "ücretsiz", "karşılanır", "geçerlidir"))


def test_kurum_sorusu_onay_beklerken_secimi_korur(kur):
    k, oid, r0, _ = hazirla(kur)
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": r0["data"]["slots"][0]["slotId"]})
    r = k.orkestre(oid, userText="sgk geçiyor mu").json()
    assert r["replyText"].startswith(alan.KURUM_YANITLARI["sigorta"])
    assert alan.KURUM_ONAY_HATIRLAT in r["replyText"]
    assert not k.api.randevular
    # Araya giren sorudan sonra ilk "evet" seçimi yeniden okur (mevcut güvenlik kuralı), ikincisi oluşturur.
    r = k.orkestre(oid, userText="evet onaylıyorum").json()
    assert r["action"] == "CONFIRM" and not k.api.randevular
    assert k.orkestre(oid, userText="evet onaylıyorum").json()["action"] == "DONE"


def test_kurum_sorusu_tc_oncesi_de_yanitlanir(kur):
    k = kur(SahteSaglayici(varsayilan=BELIRSIZ_LLM))
    oid = k.api.oturum_ac()
    k.orkestre(oid, event="START")
    r = k.orkestre(oid, userText="çağrı merkezinizin numarası ne").json()
    assert r["replyText"].startswith(alan.KURUM_YANITLARI["cagri_merkezi"]) and r["action"] == "ASK_TC"


def test_acil_kurum_sorusundan_once_gelir(kur):
    k, oid, _, _ = hazirla(kur)
    r = k.orkestre(oid, userText="nefes alamıyorum hastane nerede").json()
    assert r["replyText"] == alan.ACIL_YANIT


# ------------------------------------------------------------------ bağımsız denetim (kurum sorusu akış ortasında)

def test_kurum_sorusu_hekim_adiminda_soruyu_yeniden_sorar_ve_secimi_korur(kur):
    """Kurum sorusu hekim sorusunun ortasında gelirse yanıtın sonunda hekim sorusu yeniden sorulur; adım,
    seçenekler ve hekim adayları değişmez, ardından düğme metniyle ("Op. Dr. Selin E.") seçim yapılabilir."""
    k, oid, _, llm = hazirla(kur)
    k.orkestre(oid, userText="kadın doğuma randevu almak istiyorum")
    r = k.orkestre(oid, userText="çağrı merkezinizin numarası ne").json()
    assert r["replyText"] == (f"{alan.KURUM_YANITLARI_AKISTA['cagri_merkezi']} {alan.KURUM_ADIM_HATIRLAT}"
                              f"{alan.HEKIM_SORU_TEKRAR}")
    assert r["data"]["expect"] == "hekim" and r["data"]["institutionInfo"] == "cagri_merkezi"
    assert r["data"]["choices"] == ["Prof. Dr. Nermin T.", "Op. Dr. Selin E.", alan.HEKIM_FARK_ETMEZ_SECENEGI]
    r = k.orkestre(oid, userText="Op. Dr. Selin E.").json()  # ekrandaki düğmenin gönderdiği metin
    assert r["data"]["expect"] == "gun" and all("Selin" in s["detail"] for s in r["data"]["slots"])
    assert llm.cagrilar == []


@pytest.mark.parametrize("hazirlik,beklenen,soru", [
    (["göz doktoru"], "gun", "GUN_SORU_TEKRAR"),
    (["göz doktoru", "yarın"], "saat", "SAAT_SORU_TEKRAR"),
    (["göz doktoru", "yarın", "sabah"], "secim", "SECIM_TEKRAR"),
])
def test_kurum_sorusu_akis_ortasinda_adim_sorusunu_yeniden_sorar(kur, hazirlik, beklenen, soru):
    k, oid, _, _ = hazirla(kur)
    for cumle in hazirlik:
        k.orkestre(oid, userText=cumle)
    r = k.orkestre(oid, userText="cumartesi açık mısınız").json()
    assert r["data"]["expect"] == beklenen
    assert r["replyText"].startswith(alan.KURUM_YANITLARI_AKISTA["calisma_saatleri"])
    assert "poliklinik adını söyleyebilirsiniz" not in r["replyText"]  # poliklinik zaten seçildi
    assert r["replyText"].endswith(alan.KURUM_ADIM_HATIRLAT + getattr(alan, soru))
    if beklenen == "secim":
        assert r["data"]["slots"]  # sunulan saatler korunur


def test_kurum_sorusu_akis_baslamadan_soru_eklemez(kur):
    """Poliklinik henüz seçilmediyse (sohbet başı) yanıt yalnız kurum bilgisidir; ısrarcı soru eklenmez."""
    k, oid, _, _ = hazirla(kur)
    r = k.orkestre(oid, userText="sgk geçiyor mu").json()
    assert r["replyText"] == alan.KURUM_YANITLARI["sigorta"]


def test_hekim_sorusunda_aday_olmayan_ad_llme_gitmez_ve_hekimleri_sayar(kur):
    """2026-11-16 canlı: "Zümrüthan Hanım" maskelenip "[ad] Hanım" olarak LLM'e gidiyor, model kimlik bildirimi
    sanıp "Kimlik numaranızı sesli söylemeyin" diyordu. Artık LLM'e gitmeden hekimler yeniden sayılır; ham ad
    yanıtta yer almaz."""
    k, oid, _, llm = hazirla(kur)
    k.orkestre(oid, userText="kadın doğum")
    r = k.orkestre(oid, userText="Zümrüthan Hanım").json()
    assert r["replyText"] == alan.HEKIM_BULUNAMADI.format(hekimler="Prof. Dr. Nermin T. ve Op. Dr. Selin E.")
    assert r["data"]["expect"] == "hekim" and "Zümrüthan" not in json.dumps(r, ensure_ascii=False)
    assert llm.cagrilar == []
    r = k.orkestre(oid, userText="Selin Hanım olsun").json()
    assert r["data"]["expect"] == "gun"


def test_hekim_sorusunda_kendini_tanitan_cumle_llme_gitmez(kur):
    """Hekim adı ham metinde eşlenir; kişi adı içeren ama adaya uymayan cümle LLM'e hiç gitmez (ham da maskeli de)."""
    k, oid, _, llm = hazirla(kur)
    k.orkestre(oid, userText="kadın doğum")
    r = k.orkestre(oid, userText="Benim adım Zümrüthan Kılıç, bilmiyorum ki hangisi iyi").json()
    assert llm.cagrilar == [] and r["data"]["expect"] == "hekim"
    assert "Zümrüthan" not in json.dumps(r, ensure_ascii=False)


def test_kurum_sorusu_randevu_alindiktan_sonra_yeni_randevu_onermez(kur):
    k, oid, r0, _ = hazirla(kur)
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": r0["data"]["slots"][0]["slotId"]})
    assert k.orkestre(oid, userText="evet onaylıyorum").json()["action"] == "DONE"
    for cumle, konu in (("cumartesi açık mısınız", "calisma_saatleri"), ("çağrı merkezi numaranız", "cagri_merkezi")):
        r = k.orkestre(oid, userText=cumle).json()
        assert r["action"] == "DONE" and r["replyText"] == alan.KURUM_YANITLARI_AKISTA[konu]
        assert "poliklinik adını" not in r["replyText"] and "bana da aldır" not in r["replyText"]
