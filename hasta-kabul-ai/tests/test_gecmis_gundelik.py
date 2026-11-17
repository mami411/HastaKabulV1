"""Geçmiş muayene / reçete sorularının gündelik ve konuşma tanıma (STT) biçimleri (2026-11-16).

Hata: "Ya en son randevu, bu doktor bana ne yazmıştı?" → "Kayıtlarınızda bu ilaç görünmüyor…". Kök neden:
``gecmis.ilac_adayi_var_mi`` 5+ harfli, soru kökü olmayan her kelimeyi ilaç adayı sayıyordu; "randevu" kelimesi
aday sayıldı ve kayıtta eşleşme olmadığından ``ILAC_BULUNAMADI`` okundu. Ayrıca "demişti" bulanık eşleşmeyle
"Demir sülfat" sanılıyordu. Düzeltme: gündelik/dolgu kelimeleri ve fiil çekimleri ilaç adayı değildir; geçmiş
sorusunun gündelik biçimleri kuralla tanınır (LLM'e gitmez).

Demo geçmiş (tests/sahte_api.demo_ziyaretleri): 20 Temmuz Dahiliye (Mehmet D.; Metformin, Demir sülfat),
30 Eylül Kardiyoloji (Elif K.; Amlodipin) — ikisi hekim onaylı; Ortopedi onaysız (hiç dönmez).
"""

from __future__ import annotations

import itertools

import pytest

from conftest import BUGUN
from ortak.llm import SahteSaglayici
from ortak.turkce import ascii_katla, normalize
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.anlama import kural_tabanli_anla
from ses_servisi.gecmis import gundelik_mi, ilac_adayi_var_mi, ilac_bul, ziyaretleri_ayristir
from sahte_api import demo_ziyaretleri
from test_denetim_duzeltmeleri import onay_adimina_gel

KULLANICI_CUMLESI = "Ya en son randevu, bu doktor bana ne yazmıştı?"

LISTE_YANITI = ("30 Eylül tarihli Kardiyoloji muayenenizde 1 ilaç yazılmış. Amlodipin: Damarlarınızı gevşeterek "
                "tansiyonunuzu düşürür. Günde bir kez, her gün aynı saatte bir tablet alın. Önceki muayenelerinizde "
                "de yazılanlar: Metformin, Demir sülfat; ayrıntısı ekranda. Sorunuz olursa hekiminize danışın.")
ZIYARET_YANITI = ("Hekim onaylı kayıtlarınıza göre son muayeneniz 30 Eylül tarihinde Kardiyoloji polikliniğinde, "
                  "Elif K. ile yapılmış. Bu muayenede yazılan ilaçlar: Amlodipin. Önceki muayeneleriniz: 20 Temmuz "
                  "Dahiliye; ayrıntısı ekranda. Sorunuz olursa hekiminize danışın.")
OZET_BASI = "30 Eylül tarihli Kardiyoloji muayenenizin özeti: Tansiyonunuz sürekli olarak yüksek seyrediyor"
KARDIYOLOJI_YANITI = ("30 Eylül tarihli Kardiyoloji muayenenizde 1 ilaç yazılmış. Amlodipin: Damarlarınızı "
                      "gevşeterek tansiyonunuzu düşürür. Günde bir kez, her gün aynı saatte bir tablet alın. "
                      "Sorunuz olursa hekiminize danışın.")
DAHILIYE_BASI = "20 Temmuz tarihli Dahiliye muayenenizde 2 ilaç yazılmış. Metformin:"


def hazirla(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    return k, oid


def sor(kur, cumle: str) -> tuple[dict, object]:
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText=cumle)
    assert r.status_code == 200
    return r.json(), k


# ---------------------------------------------------------------- kullanıcının cümlesi (birebir)

def test_kullanici_cumlesi_birebir_son_muayene_ilaclari(kur):
    govde, k = sor(kur, KULLANICI_CUMLESI)
    assert govde["replyText"] == LISTE_YANITI
    assert govde["replyText"] != alan.ILAC_BULUNAMADI
    gecmis = govde["data"]["history"]
    assert [v["clinicName"] for v in gecmis["visits"]] == ["Kardiyoloji", "Dahiliye"]
    assert gecmis["focus"] == [] and gecmis["spoken"] is True
    assert k.llm.cagrilar == []  # kural anladı; LLM'e hiç gidilmedi


def test_kullanici_cumlesi_mevcut_son_muayene_yanitiyla_ayni(kur):
    """Beklenen yol: "son muayenemde hangi ilaçlar yazıldı" yanıtıyla birebir aynı metin ve kart."""
    referans, _ = sor(kur, "Son muayenemde hangi ilaçlar yazıldı?")
    govde, _ = sor(kur, KULLANICI_CUMLESI)
    assert govde["replyText"] == referans["replyText"]
    assert govde["data"]["history"] == referans["data"]["history"]
    assert govde["action"] == referans["action"]


def test_kok_neden_randevu_ilac_adayi_degil():
    assert not ilac_adayi_var_mi(KULLANICI_CUMLESI, alan)
    assert not ilac_adayi_var_mi("randevu", alan)
    assert gundelik_mi("randevu") and gundelik_mi("randevumda")


# ---------------------------------------------------------------- gündelik / STT biçimleri

LISTE_CUMLELERI = [
    "en son randevumda doktor bana ne yazmıştı",
    "geçen sefer doktor ne verdi",
    "son muayenede ne yazıldı",
    "doktor bana hangi ilaçları yazmıştı",
    "bana ne yazdılar",
    "reçetemde ne var",
    "geçen gittiğimde ne ilaç verdiler",
    "önceki muayenelerimde ne yazıldı",
    "ilaçlarımı nasıl kullanacaktım",
    "geçen randevuda ne yazıldı",
    "en son muayenemde doktor ne yazdı",
    "son gittiğimde doktor ne yazdı",
    "doktorum bana ne yazmıştı",
    "geçen ay doktor ne verdi",
    "geçen hafta doktora gittim ne yazdı",
    "daha önce bana ne ilaç verilmişti",
    "en son randevuda ne verdiler",
    "en son randevu ne yazılmıştı",
    "doktorun yazdığı ilaçlar neydi",
    "reçetemi okur musun",
    "reçetemde neler var",
    "son muayene de ne yazıldı",
    "ilaçlarım neydi",
    "hangi ilaçları kullanıyordum",
    "ne ilaç kullanıyordum",
    "bu doktor bana ne yazmıştı",
    "doktor bana ne vermişti hani şu",
    "hani şey, en son doktor bana ne yazmıştı ya",
    "ya bu doktor ne yazmıştı bana şimdi hatırlamıyorum",
    "Ya en son randevu bu doktor bana ne yazmıştı",
    "ya en son randevu bu doktor bana ne yazmisti",  # Türkçe karaktersiz yazım
    "YA EN SON RANDEVU, BU DOKTOR BANA NE YAZMIŞTI?",
    "en son randevumda ne yazmışlardı",
    "geçen randevumda bana hangi ilaçları verdiler",
]


@pytest.mark.parametrize("cumle", LISTE_CUMLELERI)
def test_gundelik_gecmis_sorusu_son_muayene_ilaclarini_okur(kur, cumle):
    govde, k = sor(kur, cumle)
    assert govde["replyText"] == LISTE_YANITI, cumle
    assert [v["clinicName"] for v in govde["data"]["history"]["visits"]] == ["Kardiyoloji", "Dahiliye"]
    assert k.llm.cagrilar == []


ZIYARET_CUMLELERI = [
    "en son ne zaman muayene oldum",
    "en son hangi doktora gittim",
    "en son ne zaman doktora gittim",
    "en son hangi bölüme gittim",
    "son muayenem ne zamandı",
]


@pytest.mark.parametrize("cumle", ZIYARET_CUMLELERI)
def test_son_ziyaret_tarihi_ve_hekimi(kur, cumle):
    govde, k = sor(kur, cumle)
    assert govde["replyText"] == ZIYARET_YANITI
    assert govde["data"]["history"]["visits"][0]["doctorDisplayName"] == "Elif K."
    assert k.llm.cagrilar == []


@pytest.mark.parametrize("cumle", ["doktor ne demişti", "son randevumda doktor ne dedi",
                                   "en son randevumda ne dediler", "doktor bana ne söylemişti"])
def test_doktor_ne_demisti_yalniz_onayli_ozet(kur, cumle):
    govde, k = sor(kur, cumle)
    metin = govde["replyText"]
    assert metin.startswith(OZET_BASI)
    # Yalnız hekim onaylı özet ve uyarılar; yorum yok, onaysız özet yok.
    assert "Hekiminizin uyarısı: Evde ölçtüğünüz tansiyon değerlerini kaydedin." in metin
    assert metin.endswith(alan.GECMIS_KAPANIS)
    assert "ONAYSIZ" not in metin and "Demir" not in metin  # "demişti" Demir sülfat sanılmaz
    assert govde["data"]["history"]["focus"] == []
    assert k.llm.cagrilar == []


@pytest.mark.parametrize("cumle", ["kardiyolojide ne yazmışlardı", "en son kardiyolojide ne yazdılar",
                                   "kardiyoloji doktoru ne yazmıştı", "kardiyolojide bana ne verdiler"])
def test_poliklinik_sorusu_yalniz_o_muayene(kur, cumle):
    govde, k = sor(kur, cumle)
    assert govde["replyText"] == KARDIYOLOJI_YANITI
    assert [v["clinicName"] for v in govde["data"]["history"]["visits"]] == ["Kardiyoloji"]
    assert k.llm.cagrilar == []


def test_dahiliye_sorusu(kur):
    govde, _ = sor(kur, "dahiliyede bana ne vermişlerdi")
    assert govde["replyText"].startswith(DAHILIYE_BASI)
    assert "Amlodipin" not in govde["replyText"]


def test_kardiyoloji_ne_demisti_o_poliklinigin_ozeti(kur):
    govde, _ = sor(kur, "kardiyolog ne demişti")
    assert govde["replyText"].startswith(OZET_BASI)
    govde, _ = sor(kur, "dahiliye doktoru ne dedi")
    assert govde["replyText"].startswith("20 Temmuz tarihli Dahiliye muayenenizin özeti: Kan şekeriniz")


def test_poliklinik_ziyaret_tarihi(kur):
    govde, _ = sor(kur, "kardiyolojiye en son ne zaman gittim")
    assert govde["replyText"] == ("Hekim onaylı kayıtlarınıza göre son Kardiyoloji muayeneniz 30 Eylül tarihinde, "
                                  "Elif K. ile yapılmış. Bu muayenede yazılan ilaçlar: Amlodipin. Sorunuz olursa "
                                  "hekiminize danışın.")


def test_onayli_kaydi_olmayan_poliklinik_durustce_soylenir(kur):
    govde, _ = sor(kur, "ortopedide ne yazmışlardı")
    assert govde["replyText"] == alan.GECMIS_POLIKLINIK_YOK.format(poliklinik="Ortopedi")
    assert govde["data"]["history"]["visits"] == []
    assert "Parasetamol" not in str(govde)


@pytest.mark.parametrize("cumle,klinik", [("Elif Hanım ne yazmıştı", "Kardiyoloji"),
                                          ("Elif hanımın yazdığı ilaçlar neydi", "Kardiyoloji"),
                                          ("doktor Mehmet bana ne vermişti", "Dahiliye"),
                                          ("Mehmet Bey ne demişti", "Dahiliye")])
def test_hekim_adiyla_soru(kur, cumle, klinik):
    govde, k = sor(kur, cumle)
    assert [v["clinicName"] for v in govde["data"]["history"]["visits"]] == [klinik]
    assert govde["replyText"] != alan.ILAC_BULUNAMADI
    assert k.llm.cagrilar == []


@pytest.mark.parametrize("cumle", ["Zeynep Hanım ne yazmıştı", "doktor Zeynep bana ne vermişti"])
def test_kayitta_olmayan_hekim_durustce_soylenir(kur, cumle):
    govde, k = sor(kur, cumle)
    assert govde["replyText"] == alan.GECMIS_HEKIM_YOK
    assert [v["clinicName"] for v in govde["data"]["history"]["visits"]] == ["Kardiyoloji", "Dahiliye"]
    assert k.llm.cagrilar == []


# ---------------------------------------------------------------- randevu: geçmiş mi, gelecek mi?

@pytest.mark.parametrize("cumle", ["en son randevumda doktor bana ne yazmıştı", "geçen randevuda ne yazıldı",
                                   "son randevumda doktor ne dedi", "önceki randevumda ne verdiler"])
def test_gecmis_zamanli_randevu_gecmis_muayenedir(cumle):
    assert kural_tabanli_anla(cumle, BUGUN, alan).niyet in ("gecmis_muayene", "ilac_sorusu")


@pytest.mark.parametrize("cumle,niyet", [("randevum ne zaman", "mevcut_randevu"),
                                         ("yaklaşan randevum var mı", "mevcut_randevu"),
                                         ("randevumu iptal etmek istiyorum", "mevcut_randevu"),
                                         ("randevu almak istiyorum", "randevu_istegi"),
                                         ("yarın kardiyolojiye randevu", "poliklinik_ve_zaman")])
def test_gelecek_randevu_gecmis_sayilmaz(cumle, niyet):
    assert kural_tabanli_anla(cumle, BUGUN, alan).niyet == niyet


def test_randevum_ne_zaman_yaniti_degismedi(kur):
    govde, _ = sor(kur, "randevum ne zaman")
    assert govde["replyText"].startswith(alan.MEVCUT_RANDEVU_YOK)
    assert "history" not in govde["data"]


@pytest.mark.parametrize("cumle", ["ne dediniz", "hangi doktora gitmeliyim", "hangi bölüme gitmeliyim",
                                   "doktor bana randevu verdi mi", "doktora gitmeden ne yapabilirim"])
def test_gecmis_olmayan_cumleler(cumle):
    assert kural_tabanli_anla(cumle, BUGUN, alan).niyet not in ("gecmis_muayene", "ilac_sorusu")


# ---------------------------------------------------------------- "ilaç adayı" özelliği (geniş dolgu listesi)

_DOLGU = ["ya", "bu", "hani", "şey", "yani", "şimdi", "acaba", "galiba", "herhalde", "işte", "falan", "tamam",
          "lütfen", "bir de", "peki", "evet", "sanırım", "hatırlamıyorum", "unuttum", "bilmiyorum"]
_ZAMAN = ["en son", "geçen sefer", "geçen gün", "geçenlerde", "önceki", "son", "daha önce", "geçen hafta",
          "geçen ay", "en son randevumda", "geçen randevuda", "son randevuda", "en son randevu", "son gittiğimde"]
_KIM = ["doktor", "bu doktor", "doktorum", "hekim", "hekimim", "doktor bey", "doktor hanım", "hocam",
        "kardiyolog", "doktor bana", "bana", "bize"]
_SORU = ["ne yazmıştı", "ne yazdı", "ne yazmışlardı", "ne verdi", "ne vermişti", "ne verdiler", "ne demişti",
         "ne dedi", "ne söylemişti", "hangi ilaçları yazmıştı", "ne yazılmıştı", "ne ilaç verdiler",
         "neler yazdı", "ne önermişti"]


def _dolgu_cumleleri() -> list[str]:
    cumleler = []
    for d, z, k, s in itertools.product(_DOLGU[:6], _ZAMAN, _KIM, _SORU):
        cumleler.append(f"{d} {z} {k} {s}")
    for d, z, k, s in itertools.product(_DOLGU[6:], _ZAMAN[:4], _KIM[:4], _SORU[:4]):
        cumleler.append(f"{z} {k} {s} {d}")
    return cumleler


def test_dolgu_cumlelerinde_ilac_adayi_yok():
    """İlaç adayı olmayan cümle: dolgu + zaman + kişi + fiil birleşimlerinin hiçbiri ilaç adı taşımaz."""
    ziyaretler = ziyaretleri_ayristir({"visits": [z for z in demo_ziyaretleri(BUGUN) if z["onayli"]]})
    cumleler = _dolgu_cumleleri()
    assert len(cumleler) > 3000
    adayli = [c for c in cumleler if ilac_adayi_var_mi(c, alan)]
    assert adayli == []
    # Bulanık eşleşme de olmaz ("demişti" ≠ "Demir sülfat", "verdi" ≠ …).
    eslesen = [c for c in cumleler if ilac_bul(c, ziyaretler)]
    assert eslesen == []


@pytest.mark.parametrize("cumle", [
    KULLANICI_CUMLESI, "ya en son randevu bu doktor bana ne yazmıştı", "hani şey doktor ne yazmıştı ya",
    "en son randevumda doktor bana ne yazmıştı", "geçen sefer doktor ne verdi", "doktor ne demişti",
    "şimdi bu doktor bana geçen randevuda ne yazmıştı acaba", "galiba en son kardiyolojide bir şey yazmışlardı",
    "sanırım geçen sefer doktor bana bir ilaç vermişti ne olduğunu hatırlamıyorum",
    "tamam peki en son randevumda ne yazdılar", "ya bu randevu meselesi, doktor ne yazmıştı",
    "lütfen en son muayenemde doktorun yazdıklarını söyler misiniz",
])
def test_ilac_adayi_olmayan_cumle_bilinmeyen_ilac_yaniti_almaz(kur, cumle):
    govde, _ = sor(kur, cumle)
    assert govde["replyText"] != alan.ILAC_BULUNAMADI, cumle
    assert "history" in govde["data"]


@pytest.mark.parametrize("cumle", ["aspirin ne için yazılmıştı", "zorbatil ne için yazılmıştı",
                                   "doktor bana coraspin yazmıştı o ne için", "en son randevumda ventolin yazılmıştı"])
def test_kayitta_olmayan_ilac_adi_hala_kayitta_gorunmuyor(kur, cumle):
    govde, k = sor(kur, cumle)
    assert govde["replyText"] == alan.ILAC_BULUNAMADI
    assert k.llm.cagrilar == []


@pytest.mark.parametrize("ad", ["amlodipin", "metformin", "atorvastatin", "lansoprazol", "coraspin", "kerendia",
                                "aspirin", "glifor", "eliquis", "ventolin", "augmentin", "majezik", "arveles",
                                "klonidin", "desloratadin", "levotiroksin", "parol", "zorbatil", "famotidin"])
def test_ilac_adlari_gundelik_sayilmaz(ad):
    assert not gundelik_mi(ascii_katla(ad))


def test_kayitli_ilac_adi_ekli_bicimde_yine_eslesir():
    ziyaretler = ziyaretleri_ayristir({"visits": [z for z in demo_ziyaretleri(BUGUN) if z["onayli"]]})
    assert [i.ad for _, i in ilac_bul("demir ilacımı nasıl kullanacağım", ziyaretler)] == ["Demir sülfat"]
    assert [i.ad for _, i in ilac_bul("metformını ne zaman", ziyaretler)] == ["Metformin"]
    assert ilac_bul("doktor ne demişti", ziyaretler) == []


# ---------------------------------------------------------------- akış ortası ve randevu sonrası

def test_onay_beklerken_gecmis_sorusu_adimi_bozmaz(kur):
    k, oid = hazirla(kur)
    onay_adimina_gel(k, oid)
    r = k.orkestre(oid, userText=KULLANICI_CUMLESI).json()
    assert r["action"] == "CONFIRM"
    assert r["replyText"].startswith("30 Eylül tarihli Kardiyoloji muayenenizde 1 ilaç yazılmış.")
    assert r["data"]["history"]["visits"]
    # Arada başka yanıt verildi: "evet" randevu oluşturmaz, seçim yeniden okunur (mevcut davranış).
    r2 = k.orkestre(oid, userText="evet onaylıyorum").json()
    assert r2["action"] != "DONE"


def test_randevu_alindiktan_sonra_gecmis_sorusu(kur):
    k, oid = hazirla(kur)
    secilen = onay_adimina_gel(k, oid)
    assert k.orkestre(oid, event="CONFIRM", data={"slotId": secilen}).json()["action"] == "DONE"
    r = k.orkestre(oid, userText=KULLANICI_CUMLESI).json()
    assert r["replyText"] == LISTE_YANITI
    r = k.orkestre(oid, userText="Elif Hanım ne yazmıştı").json()
    assert [v["clinicName"] for v in r["data"]["history"]["visits"]] == ["Kardiyoloji"]


def test_hekim_adi_llm_yukune_ve_yanita_ham_girmez(kur):
    """Hekim adı eşlemesi kodda; LLM çağrısı yok, yanıt şablondan (kayıttaki görünen ad dışında ad yok)."""
    govde, k = sor(kur, "Zeynep Hanım ne yazmıştı")
    assert "Zeynep" not in govde["replyText"] and k.llm.cagrilar == []
    assert normalize(govde["replyText"]) == normalize(alan.GECMIS_HEKIM_YOK)
