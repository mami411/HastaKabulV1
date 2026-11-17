"""Güvenlik filtresi: LLM metni kullanıcıya gitmeden önce."""

import pytest

from ortak.guvenlik import FiltreBaglami, filtrele
from ses_servisi.alanlar.hasta_kabul import GUVENLI_YEDEK, GUVENLIK_KURALLARI, SISTEM_HATASI_SAATLER, TC_TUS_TAKIMINDAN, TIBBI_YANIT

K = GUVENLIK_KURALLARI


@pytest.mark.parametrize("metin", [
    "Randevunuz onaylandı.",
    "RANDEVUNUZ ONAYLANDI.",              # büyük harf açığı (I → ı)
    "Randevunuz KESİNLEŞTİ.",             # İ → i
    "randevunuz onaylandi",               # Türkçe karaktersiz
    "Talebiniz ONAYLANMIŞTIR, iyi günler.",
])
def test_kesin_onay_her_zaman_yasak(metin):
    for basari in (False, True):
        sonuc = filtrele(metin, K, FiltreBaglami(basarili_islem=basari))
        assert sonuc.ihlal is not None and sonuc.ihlal.kural == "kesin_onay"
        assert sonuc.metin == GUVENLI_YEDEK


@pytest.mark.parametrize("metin", ["Randevunuz oluşturuldu.", "Talebiniz ALINDI.", "Kaydınız kaydedildi.",
                                   "Erişim kodunuz 123456."])
def test_basari_iddiasi_yalniz_basarisizken_yasak(metin):
    assert filtrele(metin, K, FiltreBaglami(basarili_islem=False)).metin == GUVENLI_YEDEK
    assert filtrele(metin, K, FiltreBaglami(basarili_islem=True)).ihlal is None


def test_dolu_iddiasi_yalniz_sistem_hatasinda_yasak():
    metin = "Maalesef o saat dolu."
    assert filtrele(metin, K, FiltreBaglami(sistem_hatasi=False)).ihlal is None
    assert filtrele(metin, K, FiltreBaglami(sistem_hatasi=True)).metin == SISTEM_HATASI_SAATLER


@pytest.mark.parametrize("metin", ["Size teşhis koyamam ama muhtemelen gastrit.", "Günde 500 mg parasetamol alın.",
                                   "Bu ilacın dozu iki tablettir.", "Hastalığınız ciddi değil."])
def test_tibbi_tavsiye_yasak(metin):
    sonuc = filtrele(metin, K)
    assert sonuc.ihlal is not None and sonuc.ihlal.kural == "tibbi_tavsiye"
    assert sonuc.metin == TIBBI_YANIT


@pytest.mark.parametrize("metin", ["Lütfen kimlik numaranızı söyleyin.", "TC kimlik numaranız nedir?",
                                   "Numaranız 10000000146 olarak alındı."])
def test_kimlik_isteme_ve_tekrarlama_yasak(metin):
    sonuc = filtrele(metin, K, FiltreBaglami(basarili_islem=True))
    assert sonuc.ihlal is not None and sonuc.ihlal.kural == "kimlik_isteme"
    assert sonuc.metin == TC_TUS_TAKIMINDAN
    assert "10000000146" not in sonuc.metin


@pytest.mark.parametrize("metin", ["Kardiyoloji için yarın sabah uygun saatler ekranda.",
                                   "Danışma bankosu bu konuda size yardımcı olabilir.",
                                   "Hangi polikliniğe gitmek istersiniz?"])
def test_masum_metin_gecer(metin):
    sonuc = filtrele(metin, K)
    assert sonuc.ihlal is None and sonuc.metin == metin
