"""ai-simplify: bilgi tabanı, Türkçe normalizasyon, ilaç satırı ayrıştırma ve eşleme kuralları."""

import pytest

from ortak.turkce import normalize
from sahte_backend import SEED_KB, seed_kb_json
from sade_servisi.bilgi_tabani import GOMULU_KB, ILAC, TANI, backend_kaydini_hazirla
from sade_servisi.eslesme import esle, ilac_satiri_ayir, kullanim_coz, levenshtein


def _terim(tani: str) -> list[str]:
    return [t.kayit.terim for t in esle(tani, [], GOMULU_KB).tanilar]


def _ilac(satir: str) -> str | None:
    i = esle("x", [satir], GOMULU_KB).ilaclar[0]
    return i.kayit.terim if i.kayit else None


# ---------------------------------------------------------------- bilgi tabanı

def test_gomulu_kb_seed_ile_ayni_16_kayit():
    assert len(GOMULU_KB) == 16
    assert sum(k.tur == TANI for k in GOMULU_KB) == 8 and sum(k.tur == ILAC for k in GOMULU_KB) == 8
    seed = {normalize(t): (normalize(g), k, [normalize(s) for s in es]) for t, _, g, k, es in SEED_KB}
    for k in GOMULU_KB:
        govde, tur, es = seed[normalize(k.terim)]
        assert normalize(k.govde) == govde, k.terim          # içerik aynı, yalnız Türkçe karakter farkı
        assert k.tur == tur
        assert [normalize(s) for s in k.es_anlamlilar] == es
        assert k.sade is not None
    # Doğru Türkçe karakterler
    lansoprazol = next(k for k in GOMULU_KB if k.terim == "Lansoprazol")
    assert "kahvaltıdan yarım saat önce aç karnına alınır" in lansoprazol.govde


def test_backend_kaydi_turkce_karakterle_onarilir_kimlik_korunur():
    kayitlar = [backend_kaydini_hazirla(h) for h in seed_kb_json()]
    assert all(k is not None and k.sade is not None for k in kayitlar)
    demir = next(k for k in kayitlar if normalize(k.terim) == "demir sulfat")
    assert demir.terim == "Demir Sülfat" and "süt ve çay" in demir.govde
    assert len(demir.id) == 36  # backend Guid'i korunur


def test_backend_kaydi_icerigi_farkliysa_oldugu_gibi_kullanilir():
    k = backend_kaydini_hazirla({"id": "a1", "term": "Lansoprazol", "title": "Lansoprazol",
                                 "body": "Yeni metin: gunde iki kez alinir.", "kind": "MEDICATION", "synonyms": []})
    assert k is not None and k.sade is None and k.govde == "Yeni metin: gunde iki kez alinir."
    assert backend_kaydini_hazirla({"id": "x", "term": "A", "body": "b", "kind": "BASKA"}) is None
    assert backend_kaydini_hazirla({"term": "A"}) is None
    assert backend_kaydini_hazirla({"id": "x", "term": "A", "body": "b", "kind": 1, "synonyms": []}).tur == ILAC


# ---------------------------------------------------------------- tanı eşleme

@pytest.mark.parametrize("tani,beklenen", [
    ("yüksek tansiyon", ["Hipertansiyon"]),
    ("YÜKSEK TANSİYON", ["Hipertansiyon"]),
    ("yuksek tansiyon", ["Hipertansiyon"]),
    ("Hipertansiyon", ["Hipertansiyon"]),
    ("HİPERTANSİYON", ["Hipertansiyon"]),
    ("I10 Esansiyel (primer) hipertansiyon", ["Hipertansiyon"]),
    ("Kontrolsüz hipertansiyon", ["Hipertansiyon"]),
    ("hazımsızlık", ["Dispepsi"]),
    ("HAZIMSIZLIK", ["Dispepsi"]),
    ("hazimsizlik", ["Dispepsi"]),
    ("K30 Dispepsi (hazımsızlık)", ["Dispepsi"]),
    ("Dispepsi ve gastrit", ["Dispepsi", "Gastrit"]),
    ("Tip 2 Diyabet, demir eksikliği anemisi", ["Tip 2 Diyabet", "Demir Eksikliği Anemisi"]),
    ("şeker hastalığı", ["Tip 2 Diyabet"]),
    ("Migren atağı", ["Migren"]),
    ("Akut sinüzit", ["Akut Sinüzit"]),
    ("MİGREN", ["Migren"]),
    ("hipertansiyn", ["Hipertansiyon"]),       # yazım hatası (uzaklık 1)
])
def test_tani_eslesir(tani, beklenen):
    assert _terim(tani) == beklenen


@pytest.mark.parametrize("tani", [
    "Pulmoner hipertansiyon",      # başka hastalık: kapsama kuralı
    "düşük tansiyon",
    "Hipotansiyon",                # hipo/hiper (uzaklık 2) karışmaz
    "Hipertiroidi",
    "kronik gastrit",
    "Tip 1 diyabet",
    "anemi",                       # zayıf (sınıf) eş anlamlı
    "tansiyon",
    "tiroid nodülü",
    "Gastroözofageal reflü",
])
def test_kb_disi_tani_bilinmeyen_olur(tani):
    sonuc = esle(tani, [], GOMULU_KB)
    assert sonuc.tanilar == [] and sonuc.bilinmeyen_tanilar == [tani]


def test_karisik_tanida_bilinen_ve_bilinmeyen_ayrilir():
    s = esle("Hipertansiyon; KOAH, Tanı:", [], GOMULU_KB)
    assert [t.kayit.terim for t in s.tanilar] == ["Hipertansiyon"]
    assert s.bilinmeyen_tanilar == ["KOAH"]          # "Tanı:" anlamsız bölüt, yok sayılır


# ---------------------------------------------------------------- ilaç satırı

def test_ilac_satiri_ad_ve_kullanim_ayrilir():
    s = ilac_satiri_ayir("Lansor 30 mg 1x1")
    assert (s.ad, s.kullanim, s.siklik) == ("Lansor", "30 mg 1x1", 1)
    assert s.kullanim_acik == "Günde 1 kez, her seferinde 1 adet"
    s = ilac_satiri_ayir("Demir sülfat 80 mg tablet sabah aç karnına")
    assert s.ad == "Demir sülfat" and s.kullanim.startswith("80 mg") and s.siklik is None
    s = ilac_satiri_ayir("Metformin XR 1000 mg 2 x 1 tok")
    assert s.ad == "Metformin XR" and s.siklik == 2
    s = ilac_satiri_ayir("Parol")
    assert s.ad == "Parol" and s.kullanim == ""


@pytest.mark.parametrize("metin,beklenen", [
    ("1x1", (1, "Günde 1 kez, her seferinde 1 adet")),
    ("2 x 1", (2, "Günde 2 kez, her seferinde 1 adet")),
    ("1X0,5", (1, "Günde 1 kez, her seferinde 0,5 adet")),
    ("günde 2", (2, "Günde 2 kez")),
    ("GÜNDE İKİ kez", (2, "Günde 2 kez")),
    ("gerektiğinde", (None, None)),
])
def test_kullanim_coz(metin, beklenen):
    assert kullanim_coz(metin) == beklenen


@pytest.mark.parametrize("satir,terim", [
    ("Lansor 30 mg 1x1", "Lansoprazol"),
    ("LANSOR 30 MG 1X1", "Lansoprazol"),
    ("Lansoprazl 30 mg", "Lansoprazol"),        # yazım hatası
    ("Lansoprazole 30 mg", "Lansoprazol"),
    ("Amlodipin 5 mg 1x1", "Amlodipin"),
    ("Parol 500 mg 3x1", "Parasetamol"),
    ("Paracetamol 500mg", "Parasetamol"),
    ("Levotiroxin 50 mcg", "Levotiroksin"),
    ("Levotiroksin sodyum 50 mcg", "Levotiroksin"),
    ("Metformin XR 1000 mg 2x1", "Metformin"),
    ("Demir sülfat 80 mg", "Demir Sülfat"),
    ("DEMİR SÜLFAT", "Demir Sülfat"),
])
def test_ilac_eslesir(satir, terim):
    assert _ilac(satir) == terim


@pytest.mark.parametrize("satir", [
    "Xyzol 10 mg 2x1",               # KB'de yok
    "Antibiyotik 2x1",               # sınıf adı: hangi antibiyotik olduğu uydurulmaz
    "Amlodipin + Valsartan 1x1",     # kombinasyon
    "Pantoprazol 40 mg",             # benzer ama başka etken madde
    "Omeprazol 20 mg",
    "Ramipril 5 mg",
])
def test_kb_disi_ilac_bilinmeyen_olur(satir):
    s = esle("x", [satir], GOMULU_KB)
    assert s.ilaclar[0].kayit is None
    assert s.bilinmeyenler[-1] == ilac_satiri_ayir(satir).ad


def test_kaynak_idleri_koddan_ve_sirali():
    kayitlar = [backend_kaydini_hazirla(h) for h in seed_kb_json()]
    s = esle("hazımsızlık", ["Lansor 30 mg 1x1", "Xyzol 1x1"], kayitlar)
    dispepsi = next(k for k in kayitlar if k.terim == "Dispepsi")
    lansoprazol = next(k for k in kayitlar if k.terim == "Lansoprazol")
    assert s.kaynak_idleri == [dispepsi.id, lansoprazol.id]
    assert s.ilaclar[0].gorunen_ad == "Lansor (Lansoprazol)"
    assert s.bilinmeyenler == ["Xyzol"] and not s.hepsi_bilinen


def test_levenshtein_sinirli():
    assert levenshtein("lansoprazl", "lansoprazol", 1) == 1
    assert levenshtein("hipotansiyon", "hipertansiyon", 1) == 2  # sınır+1
