"""Bağımsız denetim (KVKK sınırı, yanlış kişi/yanlış içerik, onay güvenliği) regresyon testleri.

Her test, denetimde kanıtlanan bir kusuru düzeltmeden önce kırmızıya düşürür:
- S1: kuralın çözemediği, kullanıcının kendi sağlık verisini taşıyan cümleler LLM'e gidiyordu.
- S2: adsız tetikleyicisiz ad-soyad ("Ayşe Yılmaz için…"), erişim kodu (6 hane), adres LLM yükündeydi.
- S3: kayıtta olmayan ilaç adı, adı "çıkarmak" için LLM'e zorla gönderiliyordu.
- G1: bulanık ilaç eşleşmesi farklı ilacı eşliyordu (Paroksetin→Parol, Klonopin→Klonidin).
- G2: END'den sonra gelen gecikmeli istek oturumu API'den yeniden kurup geçmişi yeniden belleğe alıyordu.
- G3: boşta kalmış (kiosk sıfırlanmamış) oturumda geçmiş muayene yeniden getirilip okunuyordu.
- O1: onay adımında araya başka yanıt girdikten sonra söylenen "evet" seçim yeniden okunmadan randevu
  oluşturuyordu.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from conftest import BUGUN, SIMDI
from ortak.llm import SahteSaglayici
from ortak.turkce import normalize
from sahte_api import demo_ziyaretleri
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.anlama import kural_tabanli_anla
from ses_servisi.gecmis import Ilac, Ziyaret, _ad_eslesir_mi, ilac_bul, ziyaretleri_ayristir
from ses_servisi.llm_siniri import llm_icin_maskele, llm_yuku_kur
from ses_servisi.orkestrator import Orkestrator
from test_denetim_duzeltmeleri import Saat, SaatliKurulum, onay_adimina_gel

BELIRSIZ_LLM = json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
                           "saat_bitis": None})


def _yuk(llm: SahteSaglayici) -> str:
    return "\n".join(m.icerik for c in llm.cagrilar for m in c["mesajlar"])


def _gecmis_cagrilari(k):
    return [c for c in k.api.cagrilar if c[1].endswith("/medical-history")]


# ------------------------------------------------------------------ S1 sağlık verisi LLM'e gitmez

@pytest.mark.parametrize("cumle", [
    "şeker hastasıyım", "HIV pozitifim", "hamileyim", "Metformin kullanıyorum",
    "kalbim için bir bakılmasını rica ediyorum", "Lustral içiyorum bir şey sorabilir miyim",
    "depresyon tedavisi görüyorum", "astımım var", "dün ameliyat oldum",
])
def test_S1_saglik_verisi_tasiyan_cumle_llme_gitmez(kur, cumle):
    llm = SahteSaglayici(varsayilan=BELIRSIZ_LLM)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText=cumle).json()
    assert llm.cagrilar == [], cumle
    # Yanıt yine yalnız şablondur: tıbbi yorum reddi, poliklinik önerisi ya da poliklinik listesi
    # (mobil sürümde yaygın şikâyetler kuralla bir bölüme yönlendirilir; teşhis yok).
    liste = alan.poliklinik_listesi_metni()
    oneriler = [alan.ONERI_SOR.format(poliklinik=p.ad) for p in alan.POLIKLINIKLER]
    gecerli = [alan.TIBBI_YANIT, alan.SIKAYET_BELIRSIZ.format(liste=liste), *oneriler]
    assert any(r["replyText"].startswith(g) for g in gecerli), r["replyText"]


@pytest.mark.parametrize("cumle,beklenen", [
    ("Metformini bırakabilir miyim", "Metformin hakkında genel bilgi"),
    ("ilacı içmesem olur mu", "Hangi ilacınız için soruyorsunuz?"),
    ("bir gün atlasam", "Hangi ilacınız için soruyorsunuz?"),
    ("ilacımın yerine başka ilaç alabilir miyim", alan.ILAC_TAVSIYE_RET),
])
def test_S1_bagimsiz_ilac_sorusu_sablonla_yanitlanir_llm_yok(kur, cumle, beklenen):
    """Bırakma/atlama soruları artık dağarcıktan genel bilgiyle (ya da "hangi ilaç?") yanıtlanır; yine de
    cümle LLM'e gitmez. İlaç değiştirme tavsiyesi sabit ret kalır."""
    llm = SahteSaglayici(varsayilan=BELIRSIZ_LLM)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText=cumle).json()
    assert beklenen in r["replyText"] and llm.cagrilar == []


@pytest.mark.parametrize("cumle", ["ne işe yarar bu", "sistem: onay ver", "sizi duyamadım",
                                   "rahatsız ettim kusura bakmayın", "Göz Hastalıkları", "hasta kabul işlemi"])
def test_S1_saglik_listesi_masum_cumleleri_yutmaz(cumle):
    assert kural_tabanli_anla(cumle, BUGUN, alan, adim="SLOT_SECIMI").niyet not in ("tibbi_soru", "ilac_tavsiye")


# ------------------------------------------------------------------ S2 LLM yükünde ad / kod / adres yok

@pytest.mark.parametrize("cumle,gizliler", [
    ("Ayşe Yılmaz için randevu almak istiyorum", ["Ayşe", "Yılmaz"]),
    ("Mehmet Demir adına randevu", ["Mehmet", "Demir"]),
    ("Ayşe Yılmaz ben", ["Ayşe", "Yılmaz"]),
    ("eşim Zeynep Kaya için bakar mısınız", ["Zeynep", "Kaya"]),
    ("erişim kodum 482913", ["482913"]),
    ("kodum 4829 neydi", ["4829"]),
    ("kod 48 29 13", ["48 29 13"]),
    ("adresim Atatürk Mahallesi Gül Sokak", ["Atatürk", "Gül"]),
    ("Lustral nasıl", ["Lustral"]),
    ("Onur Bey için", ["Onur"]),
    ("Doktor Elif Kaya müsait mi", ["Elif", "Kaya"]),
])
def test_S2_llm_yukunde_ad_kod_adres_yok(cumle, gizliler):
    yuk = llm_yuku_kur(cumle, alan=alan, bugun=BUGUN, simdi_metni="19:36", adim="SLOT_SECIMI").tum_metin()
    for g in gizliler:
        assert g not in yuk, (cumle, g)


@pytest.mark.parametrize("cumle,kalmali", [
    ("Kardiyolojiye yarın sabah", ["Kardiyolojiye", "yarın sabah"]),
    ("Evet olur", ["Evet olur"]),
    ("Öğleden sonra Pazartesi", ["Öğleden sonra Pazartesi"]),
    ("20 Kasım saat 10:30", ["20 Kasım saat 10:30"]),
    ("İkincisi olsun", ["İkincisi olsun"]),
    ("Dördüncü saat", ["Dördüncü"]),
    ("Yarın Saat Onda", ["Yarın Saat Onda"]),
])
def test_S2_alan_kelimeleri_llm_icin_gizlenmez(cumle, kalmali):
    m = llm_icin_maskele(cumle, alan)
    for x in kalmali:
        assert x in m, m


def test_S2_ucdan_uca_llm_yukunde_tetikleyicisiz_ad_yok(kur):
    llm = SahteSaglayici(varsayilan=BELIRSIZ_LLM)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="Ayşe Yılmaz için randevu almak istiyorum")
    # Adlı randevu cümlesi artık kuralda kalır (canlı bulgu 4): LLM'e hiç gitmez.
    assert r.headers["X-Anlama-Kaynagi"] == "kural" and llm.cagrilar == []
    k.orkestre(oid, userText="erişim kodum 482913 bir bakılmasını rica ediyorum")
    assert len(llm.cagrilar) == 1
    yuk = _yuk(llm)
    for g in ("Ayşe", "Yılmaz", "482913"):
        assert g not in yuk


# ------------------------------------------------------------------ S3 ilaç adı LLM'e zorla gönderilmez

def test_S3_kayitta_olmayan_ilac_sorusu_llme_gitmez(kur):
    llm = SahteSaglayici(varsayilan=json.dumps({"niyet": "ilac_sorusu", "poliklinik": None, "gun": None,
                                                "saat_baslangic": None, "saat_bitis": None,
                                                "ilac_adi": "Lustral"}))
    k = kur(llm, SAGLIK_BILGI_LLM="0")  # genel bilgi yolu açıkken: test_saglik_bilgisi
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="Lustral nasıl kullanılır").json()
    assert r["replyText"] == alan.ILAC_BULUNAMADI
    assert llm.cagrilar == []


# ------------------------------------------------------------------ G1 bulanık ilaç eşleşmesi

@pytest.mark.parametrize("soylenen,kayit,beklenen", [
    ("paroksetin", "Parol", False), ("klonopin", "Klonidin", False), ("insulin", "Insuman", False),
    ("lansoprazolu", "Lansoprazol", True), ("lanzoprazol", "Lansoprazol", True), ("metformın", "Metformin", True),
    ("majezik", "Magezik", True), ("metformini", "Metformin", True), ("amlodipinle", "Amlodipin", True),
])
def test_G1_ilac_adi_eslesmesi(soylenen, kayit, beklenen):
    assert _ad_eslesir_mi(normalize(soylenen), kayit) is beklenen


def test_G1_benzer_adli_baska_ilacin_talimati_okunmaz():
    z = Ziyaret(SIMDI - timedelta(days=30), "Dahiliye", "", "", (Ilac("Parol", "Ağrı için.", "Günde 3 kez."),
                                                               Ilac("Klonidin", "Tansiyon için.", "Günde 2 kez.")), ())
    assert ilac_bul("Paroksetin nasıl kullanılır", [z]) == []
    assert ilac_bul("Klonopin ne zaman alınır", [z]) == []
    assert [i.ad for _, i in ilac_bul("Parolü ne zaman alayım", [z])] == ["Parol"]


# ------------------------------------------------------------------ G2 END sonrası gecikmeli istek

def test_G2_end_sonrasi_istek_oturumu_yeniden_kurmaz(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    assert k.orkestre(oid, event="END").json()["data"]["ended"] is True
    r = k.orkestre(oid, userText="ilaçlarım neler")
    govde = r.json()
    assert r.status_code == 409 and govde["error"]["code"] == "VOICE_SESSION_ENDED"
    assert govde["action"] == "HANDOFF" and govde["data"]["restart"] is True
    assert "Metformin" not in r.text and "history" not in govde["data"]
    assert _gecmis_cagrilari(k) == []
    assert k.app.state.orkestrator.depo.getir(oid) is None


class _EndYarisiApi:
    """API'den oturum okunurken END gelir (istek, kiosk sıfırlanmadan hemen önce gönderilmişti)."""

    def __init__(self, ork_kutusu: list):
        self.kutu = ork_kutusu
        self.gecmis_cagrisi = 0

    async def oturum_getir(self, oturum_id, **_):
        self.kutu[0].depo.sil(oturum_id)  # END bu arada işlendi
        return {"sessionId": oturum_id, "verified": True, "status": "VERIFIED"}

    async def gecmis_getir(self, oturum_id, **_):
        self.gecmis_cagrisi += 1
        return {"visits": demo_ziyaretleri(BUGUN)}

    async def slotlar(self, *_a, **_k):
        return []


def test_G2_api_beklenirken_gelen_end_oturumu_dirilmez():
    kutu: list = []
    api = _EndYarisiApi(kutu)
    ork = Orkestrator(alan=alan, api=api, llm=None, saat=lambda: SIMDI)
    kutu.append(ork)
    oid = "6f1c2f0e-8f7a-4c3e-9a51-0f8a3b1d2c4e"
    sonuc = asyncio.run(ork.isle({"sessionId": oid, "userText": "ilaçlarım neler"}, "rid12345"))
    assert sonuc.durum_kodu == 409 and sonuc.govde["error"]["code"] == "VOICE_SESSION_ENDED"
    assert api.gecmis_cagrisi == 0 and len(ork.depo) == 0


# ------------------------------------------------------------------ G3 boşta kalan oturumda geçmiş

def test_G3_bosta_kalan_oturumda_gecmis_yeniden_dogrulama_ister():
    saat = Saat(SIMDI)
    k = SaatliKurulum(saat, SahteSaglayici([]), OTURUM_BOSTA_SN="120")
    try:
        oid = k.api.oturum_ac("VERIFIED")
        k.orkestre(oid, event="TC_VERIFIED")
        assert "Amlodipin" in k.orkestre(oid, userText="ilaçlarım neler").json()["replyText"]
        saat.t = SIMDI + timedelta(seconds=121)  # hasta ayrıldı, kiosk sıfırlanmadı, sıradaki kişi soruyor
        r = k.orkestre(oid, userText="ilaçlarım neler")
        govde = r.json()
        assert r.status_code == 409 and govde["error"]["code"] == "SESSION_IDLE"
        assert govde["action"] == "HANDOFF" and govde["data"]["restart"] is True
        assert govde["replyText"] == alan.GECMIS_YENIDEN_DOGRULAMA
        for gizli in ("Amlodipin", "Metformin", "history"):
            assert gizli not in r.text
        assert len([c for c in k.api.cagrilar if c[1].endswith("/medical-history")]) == 1
        # Aynı oturumda sonradan da (boşta kalma süresi dolmadan) geçmiş verilmez
        saat.t += timedelta(seconds=5)
        assert "Metformin" not in k.orkestre(oid, userText="Metformin nasıl kullanılır").text
    finally:
        k.kapat()


# ------------------------------------------------------------------ O1 onay: araya giren yanıttan sonra "evet"

@pytest.mark.parametrize("ara", ["ilaçlarım neler", "kalbim ağrıyor", "hmm şey"])
def test_O1_arada_baska_yanit_varsa_evet_once_secimi_yeniden_okur(kur, ara):
    k = kur(None)
    oid = k.api.oturum_ac("VERIFIED")
    onay_adimina_gel(k, oid)
    k.orkestre(oid, userText=ara)
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] == "CONFIRM" and not k.api.randevular
    assert "Bu randevuyu oluşturayım mı?" in r["replyText"]  # seçim yeniden okundu
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"
    assert len(k.api.randevular) == 1


def test_O1_onay_sorusundan_hemen_sonra_evet_dogrudan_randevu(kur):
    k = kur(None)
    oid = k.api.oturum_ac("VERIFIED")
    onay_adimina_gel(k, oid)
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"


def test_O1_acik_onay_istemi_onay_sorusunu_kapatmaz(kur):
    k = kur(None)
    oid = k.api.oturum_ac("VERIFIED")
    onay_adimina_gel(k, oid)
    assert k.orkestre(oid, userText="tamam").json()["replyText"] == alan.ONAY_ICIN_ACIK
    assert k.orkestre(oid, userText="onaylıyorum").json()["action"] == "DONE"


def test_O1_bosta_kalinca_yeniden_okuma_yolu_onayi_atlatamaz():
    saat = Saat(SIMDI)
    k = SaatliKurulum(saat, OTURUM_BOSTA_SN="120")
    try:
        oid = k.api.oturum_ac("VERIFIED")
        onay_adimina_gel(k, oid)
        k.orkestre(oid, userText="hmm şey")  # onay sorusu kapandı
        saat.t = SIMDI + timedelta(seconds=121)
        r = k.orkestre(oid, userText="evet")
        assert r.status_code == 409 and r.json()["error"]["code"] == "SESSION_IDLE"
        assert not k.api.randevular
    finally:
        k.kapat()


# ------------------------------------------------------------------ D diyalog: tarih / kişisel bilgi

@pytest.mark.parametrize("cumle,gun", [
    ("haftaya salı", "2026-11-24"), ("gelecek hafta salı", "2026-11-24"), ("salı", "2026-11-17"),
    ("bu salı", "2026-11-17"), ("haftaya pazar", "2026-11-22"),
])
def test_D_haftaya_gun_gelecek_haftadir(cumle, gun):
    assert str(kural_tabanli_anla(cumle, BUGUN, alan).gun) == gun  # BUGUN = 15 Kasım 2026 Pazar


@pytest.mark.parametrize("cumle", ["doğum tarihim 15 Mart 1980", "adresim Gül Sokak 5"])
def test_D_dogum_tarihi_adres_randevu_tarihi_sanilmaz(cumle):
    assert kural_tabanli_anla(cumle, BUGUN, alan).niyet == "kimlik"


# ------------------------------------------------------------------ TTS doz metnini bozmaz

@pytest.mark.parametrize("metin", ["8 saatte bir", "15.09.2026 tarihinde", "günde 3 kez 1 tablet",
                                   "her 12 saatte 1", "5 gün", "3.14:30"])
def test_tts_belirsiz_kaliplara_dokunmaz(metin):
    """Emin olunmayan kalıplar olduğu gibi kalır; saat okuması doz metnine sızmaz."""
    assert alan.tts_on_isleme(metin) == metin


@pytest.mark.parametrize(("metin", "okunus"), [
    ("günde 2 kez 500 mg", "günde 2 kez beş yüz miligram"),
    ("1x1", "bir çarpı bir"),
    ("12.5 mg", "on iki buçuk miligram"),
    ("12,5 mg", "on iki buçuk miligram"),
    ("0,5 mg", "sıfır virgül beş miligram"),
    ("100 mg/5 ml şurup", "yüz miligram bölü beş mililitre şurup"),
    ("250 mcg", "iki yüz elli mikrogram"),
    ("250 µg", "iki yüz elli mikrogram"),
    ("5 g toz", "beş gram toz"),
    ("Demir Sülfat 80 mg tablet 1x1 aç", "Demir Sülfat seksen miligram tablet bir çarpı bir aç"),
    ("2x1 7 gün", "iki çarpı bir 7 gün"),
    ("saat 14:30'da 2,5 tablet", "saat on dört otuzda iki buçuk tablet"),
])
def test_tts_doz_kisaltmalari_yalniz_sesli_kopyada_acilir(metin, okunus):
    assert alan.tts_on_isleme(metin) == okunus


# ------------------------------------------------------------------ G4 TTS önbelleği hasta verisi tutmaz

def test_G4_tts_onbellegi_yalniz_sabit_cumleleri_tutar():
    from conftest import bos_wav
    from ses_servisi.app import tts_onbellek_cumleleri
    from ses_servisi.tts import PiperTts

    sabitler = tts_onbellek_cumleleri(alan)
    assert alan.tts_on_isleme(alan.SELAMLAMA) in sabitler
    assert alan.tts_on_isleme(alan.POLIKLINIK_SOR.format(liste=alan.poliklinik_listesi_metni())) in sabitler
    hasta_metni = alan.tts_on_isleme("20 Temmuz tarihli Dahiliye muayenenizde yazılan Metformin: Günde iki kez.")
    erisim = alan.tts_on_isleme("Randevunuz oluşturuldu: 16 Kasım 09:00, Kardiyoloji, Elif K. Erişim kodunuz 482913.")
    assert hasta_metni not in sabitler and erisim not in sabitler

    t = PiperTts(model_yolu="yok.onnx", onbellek_suzgeci=sabitler.__contains__)
    t._sentezle = lambda _m: bos_wav()  # model yüklemeden
    for metin in (hasta_metni, erisim):
        assert t._sentezle_onbellekli(metin).onbellekten is False
        assert t._sentezle_onbellekli(metin).onbellekten is False  # ikinci kez de: kâhin yok
    sabit = alan.tts_on_isleme(alan.SELAMLAMA)
    t._sentezle_onbellekli(sabit)
    assert t._sentezle_onbellekli(sabit).onbellekten is True
    assert list(t._onbellek) == [sabit]


def test_demo_ziyaretleri_ayristirilir():  # yardımcı veri bütünlüğü (G2 testi bunu kullanır)
    assert ziyaretleri_ayristir({"visits": demo_ziyaretleri(BUGUN)})
