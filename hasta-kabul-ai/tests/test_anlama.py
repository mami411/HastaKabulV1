"""Serbest metin anlama: kural tabanlı çözümleyici, LLM birleştirme, maskeleme, yedek."""

import asyncio
import json
from datetime import date, time

import pytest

from ortak.llm import LLMBaglantiHatasi, SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.anlama import anla, kural_tabanli_anla

BUGUN = date(2026, 11, 15)  # Pazar


def kural(metin):
    return kural_tabanli_anla(metin, BUGUN, alan)


@pytest.mark.parametrize("metin,poliklinik,gun,bas,bit", [
    ("Kardiyolojiye yarın sabah randevu istiyorum", "KARDIYOLOJI", date(2026, 11, 16), time(8), time(12)),
    ("kalp polikliniği", "KARDIYOLOJI", None, None, None),
    ("İç hastalıkları cuma öğleden sonra", "DAHILIYE", date(2026, 11, 20), time(12), time(18)),
    ("Göz doktoru cumartesi", "GOZ", date(2026, 11, 21), None, None),
    ("ORTOPEDİ PAZARTESİ SAAT 10:30", "ORTOPEDI", date(2026, 11, 16), time(10, 30), time(10, 30)),
    ("ortopedi saat on bir buçuk", "ORTOPEDI", None, time(11, 30), time(11, 30)),
    ("dahiliye saat iki", "DAHILIYE", None, time(14), time(14)),
    ("dahiliye dokuz buçukta", "DAHILIYE", None, time(9, 30), time(9, 30)),
    ("yarın", None, date(2026, 11, 16), None, None),
    ("20 kasım dahiliye", "DAHILIYE", date(2026, 11, 20), None, None),
])
def test_kural_poliklinik_ve_zaman(metin, poliklinik, gun, bas, bit):
    a = kural(metin)
    assert a.niyet == "poliklinik_ve_zaman"
    assert (a.poliklinik, a.gun, a.saat_baslangic, a.saat_bitis) == (poliklinik, gun, bas, bit)


@pytest.mark.parametrize("metin,niyet", [
    ("Onaylıyorum", "onay"), ("evet", "onay"), ("TAMAM", "onay"),
    ("hayır", "ret"), ("vazgeçtim", "ret"), ("onaylamıyorum", "ret"),
    ("başka saat var mı", "baska_saat"), ("hayır başka bir saat", "baska_saat"),
    ("midem ağrıyor hangi ilacı almalıyım", "tibbi_soru"),
    ("TC numaramı söyleyeyim mi", "kimlik"), ("benim numaram 10000000146", "kimlik"),
    ("bugün hava nasıl olacak acaba", "poliklinik_ve_zaman"),  # "bugün" → gün bilgisi
    ("asdf qwer", "belirsiz"),
])
def test_kural_niyetler(metin, niyet):
    assert kural(metin).niyet == niyet


def test_tarih_icerikli_cumle_onay_sayilmaz():
    # "evet ama yarın sabah olsun" onay DEĞİL; zaman değişikliğidir.
    assert kural("evet ama yarın sabah olsun").niyet == "poliklinik_ve_zaman"


def anla_calistir(metin, llm, adim="SLOT_SECIMI"):
    return asyncio.run(anla(metin, alan=alan, llm=llm, bugun=BUGUN, simdi_metni="19:36", adim=adim))


def llm_json(**alanlar):
    temel = {"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
             "saat_bitis": None, "yanit": ""}
    temel.update(alanlar)
    return json.dumps(temel, ensure_ascii=False)


def test_llm_sonucu_kullanilir_ve_istem_takvim_icerir():
    # Kural bu cümleyi çözemez (poliklinik adı yok, gün/saat yok) → LLM'e gider.
    llm = SahteSaglayici([llm_json(niyet="poliklinik_ve_zaman", poliklinik="KARDIYOLOJI", gun="2026-11-20",
                                   saat_baslangic="13:00", saat_bitis="17:00")])
    # ("kalbim için …" artık LLM'e gitmez: kendi sağlık verisini taşır; bkz. test_saglik_verisi_llme_gitmez.)
    a = anla_calistir("kardiyak bölüme bir bakılmasını rica ediyorum", llm)
    assert a.kaynak == "llm"
    # Metinde dayanağı olmayan gün/saat atılır (temellendirme); "kardi" kökü poliklinik dayanağıdır.
    assert (a.poliklinik, a.gun, a.saat_baslangic) == ("KARDIYOLOJI", None, None)
    sistem = llm.cagrilar[0]["mesajlar"][0].icerik
    assert "2026-11-20 = 20 Kasım Cuma" in sistem       # haftanın günü istemde
    assert "15 Kasım Pazar 2026" in sistem
    assert llm.cagrilar[0]["json_semasi"] == alan.ANLAMA_SEMASI


def test_kural_cozdugunde_llme_gidilmez():
    llm = SahteSaglayici([])  # çağrılırsa AssertionError
    a = anla_calistir("kalbim için cuma öğleden sonra bakar mısınız", llm)
    assert a.kaynak == "kural" and a.gun == date(2026, 11, 20) and a.saat_baslangic == time(12)
    assert llm.cagrilar == []


def test_llm_gecersiz_alanlari_atilir():
    llm = SahteSaglayici([llm_json(niyet="poliklinik_ve_zaman", poliklinik="PSIKIYATRI", gun="2027-01-01",
                                   saat_baslangic="99:99")])
    a = anla_calistir("astroloji", llm)
    assert a.poliklinik is None and a.gun is None and a.saat_baslangic is None
    assert a.niyet == "belirsiz"


def test_kural_acik_kelimede_llm_ile_celisirse_kazanir():
    llm = SahteSaglayici([llm_json(niyet="poliklinik_ve_zaman", poliklinik="DAHILIYE")])
    a = anla_calistir("ortopedi yarın", llm)
    assert a.poliklinik == "ORTOPEDI" and a.gun == date(2026, 11, 16)


def test_tc_iceren_metin_llme_gitmez():
    llm = SahteSaglayici([])
    a = anla_calistir("kimlik numaram 1 0 0 0 0 0 0 0 1 4 6 kardiyoloji", llm)
    assert a.niyet == "kimlik"
    assert llm.cagrilar == []


def test_onay_adiminda_kisa_onay_llmsiz():
    llm = SahteSaglayici([])
    assert anla_calistir("Onaylıyorum", llm, adim="ONAY_BEKLENIYOR").niyet == "onay"
    assert anla_calistir("hayır", llm, adim="ONAY_BEKLENIYOR").niyet == "ret"
    assert llm.cagrilar == []


def test_llm_hatasinda_kurala_duser():
    llm = SahteSaglayici([LLMBaglantiHatasi("kapalı")])
    a = anla_calistir("şey nasıl desem bilemedim", llm)
    assert a.kaynak == "kural_yedek" and a.llm_hatasi == "LLM_BAGLANTI" and a.niyet == "belirsiz"
    # Kuralın çözdüğü cümle LLM'e hiç gitmez (LLM kapalı olsa da etkilenmez)
    a = anla_calistir("dahiliye yarın", SahteSaglayici([LLMBaglantiHatasi("kapalı")]))
    assert a.kaynak == "kural" and a.poliklinik == "DAHILIYE" and a.gun == date(2026, 11, 16)


def test_llm_bozuk_json_kurala_duser():
    llm = SahteSaglayici(["bu json değil"])
    a = anla_calistir("hmm bilmiyorum ki", llm)
    assert a.kaynak == "kural_yedek" and a.niyet == "belirsiz"


@pytest.mark.parametrize("metin,bas,bit", [
    ("Göz Doktoruna Pazartesi saat 10.30'da gidebilir miyim?", time(10, 30), time(10, 30)),  # Whisper yazımı
    ("Ortopediye yarın saat 9'da bakar mısınız?", time(9), time(9)),
    ("kalp polikliniği olsun, en erken saat", None, None),  # sabah aralığı değil
    ("yarın sabah erken saatte", time(8), time(12)),
])
def test_kural_stt_yazimi_ve_en_erken(metin, bas, bit):
    a = kural(metin)
    assert (a.saat_baslangic, a.saat_bitis) == (bas, bit)


def test_llm_uydurdugu_tam_saat_atilir():
    # Uçtan uca denemede görüldü: "en erken saat" → model 08:00 tam saat verdi.
    llm = SahteSaglayici([llm_json(niyet="poliklinik_ve_zaman", poliklinik="KARDIYOLOJI",
                                   saat_baslangic="08:00", saat_bitis="08:00")])
    a = anla_calistir("kalp polikliniği olsun, en erken saat", llm)
    assert a.poliklinik == "KARDIYOLOJI" and a.saat_baslangic is None and a.saat_bitis is None


def test_llm_tam_saati_kuralla_celisirse_rakam_kazanir():
    llm = SahteSaglayici([llm_json(niyet="poliklinik_ve_zaman", poliklinik="GOZ",
                                   saat_baslangic="10:00", saat_bitis="10:00")])
    a = anla_calistir("Göz doktoruna saat 10.30'da", llm)
    assert a.saat_baslangic == time(10, 30) == a.saat_bitis
