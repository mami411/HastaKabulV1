"""Türkçe yardımcılar: küçük harf, saat okuma, sayı okuma, TC maskeleme."""

import pytest

from ortak.turkce import (
    ascii_katla,
    herhangi_gecer_mi,
    rakamlari_tek_tek_oku,
    saat_oku,
    saatleri_oku,
    sayi_oku,
    tc_maskele,
    turkce_kucuk,
)
from ses_servisi.alanlar.hasta_kabul import tts_on_isleme


def test_turkce_kucuk_i_harfleri():
    assert turkce_kucuk("ONAYLANDI") == "onaylandı"
    assert turkce_kucuk("KESİNLEŞTİ") == "kesinleşti"
    assert "̇" not in turkce_kucuk("İSTANBUL")
    # str.lower() bu farkı yapmaz (rapordaki büyük harf açığı):
    assert "ONAYLANDI".lower() != "onaylandı"


def test_ascii_katla():
    assert ascii_katla("Göz Hastalıkları ÇOK İYİ") == "goz hastaliklari cok iyi"


def test_kelime_basi_eslesme():
    assert herhangi_gecer_mi("Kardiyoloji'ye gitmek istiyorum", ("kardiyoloji",)) == "kardiyoloji"
    assert herhangi_gecer_mi("RANDEVUNUZ ONAYLANDI", ("onaylandı",)) == "onaylandı"
    assert herhangi_gecer_mi("randevunuz onaylandi", ("onaylandı",)) == "onaylandı"
    # kelime ortası rastlantı yakalanmaz
    assert herhangi_gecer_mi("polikliniğe", ("lini",)) is None


@pytest.mark.parametrize("sayi,okunus", [
    (0, "sıfır"), (7, "yedi"), (14, "on dört"), (23, "yirmi üç"), (45, "kırk beş"), (100, "yüz"),
    (246, "iki yüz kırk altı"), (1000, "bin"), (2026, "iki bin yirmi altı"), (246802, "iki yüz kırk altı bin sekiz yüz iki"),
])
def test_sayi_oku(sayi, okunus):
    assert sayi_oku(sayi) == okunus


@pytest.mark.parametrize("metin,beklenen", [
    ("Saat 14:30 uygun", "Saat on dört buçuk uygun"),
    ("09:00 ve 09:15 boş", "dokuz ve dokuz on beş boş"),
    ("saat 20.00 olsun", "saat yirmi olsun"),
    ("14:30'da görüşürüz", "on dört otuzda görüşürüz"),
    ("09:00'da gelin", "dokuzda gelin"),
    ("23:45", "yirmi üç kırk beş"),
])
def test_saatleri_oku_saatleri_cevirir(metin, beklenen):
    assert saatleri_oku(metin) == beklenen


@pytest.mark.parametrize("metin", [
    "Muayene ücreti 3.50 TL",          # fiyat (raporda "üç elli TL" oluyordu)
    "Tarih 15.09 olarak kayıtlı",       # tarih (raporda "on beş dokuz")
    "Toplam 45.00 TL",                  # fiyat (raporda "kırk beş")
    "Kurulum bedeli 2.500 TL",          # binlik ayraç
    "Doğum tarihi 15.09.2026",          # tam tarih
    "Süre 12:30:45",                    # saniyeli zaman damgası
    "Oran 3,14:30 gibi garip",          # ayraçlı sayı dizisi
    "25:00 geçersiz saat",              # aralık dışı
])
def test_saatleri_oku_saat_olmayan_sayilara_dokunmaz(metin):
    assert saatleri_oku(metin) == metin


def test_saat_oku():
    assert saat_oku(9, 0) == "dokuz"
    assert saat_oku(14, 30) == "on dört buçuk"
    assert saat_oku(14, 30, ekli=True) == "on dört otuz"


def test_rakamlari_tek_tek_oku():
    assert rakamlari_tek_tek_oku("246802") == "iki dört altı sekiz sıfır iki"


def test_tts_on_isleme_kod_ve_saat():
    metin = tts_on_isleme("Randevunuz 16 Kasım saat 09:30. Erişim kodunuz 246802.")
    assert "dokuz buçuk" in metin
    assert "iki dört altı sekiz sıfır iki" in metin
    assert "16 Kasım" in metin  # tarih bozulmaz


@pytest.mark.parametrize("metin", [
    "kimliğim 10000000146",
    "1 0 0 0 0 0 0 0 1 4 6",
    "100-000-001-46",
    "TC:10000000146.",
    "123456789012",                 # 11'den uzun bitişik dizi de gizlenir (denetim O1)
])
def test_tc_maskele(metin):
    maskeli, var = tc_maskele(metin)
    assert var
    assert "10000000146" not in maskeli.replace(" ", "").replace("-", "")
    assert "[gizli numara]" in maskeli


@pytest.mark.parametrize("metin", ["telefon 0532 123 45", "kod 246802", "1234567890"])
def test_tc_maskele_11_hane_olmayani_birakir(metin):
    assert tc_maskele(metin) == (metin, False)
