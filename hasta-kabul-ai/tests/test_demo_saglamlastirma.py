"""Backend demo sağlamlaştırmasına (2026-11-16) uyum testleri.

- 409 APPOINTMENT_CONFLICT (saat çakışması / aynı gün aynı poliklinik / ipucusuz genel) → şablon, seçim
  kaldırılır, gün ya da saat yeniden sorulur, onay döngüsü yok, başka slotla tamamlanabilir.
- 409 SLOT_IN_PAST → SLOT_EXPIRED ile aynı davranış.
- Eşzamanlı rezervasyon artık 409 SLOT_TAKEN.
- Hata zarfı {error:{code,message,requestId}} her durumda; ProblemDetails desteği sürer.
- Randevu ufku (RANDEVU_UFUK_GUN, varsayılan 8): ötesi dürüst şablonla reddedilir.
- Sahte API verify sözleşmesi: biçimsiz TC 400 (sayaç artmaz), süresi dolmuşta INVALID_STATUS_TRANSITION.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import httpx
import pytest

from conftest import BUGUN, YARIN, ayarlar
from ortak.llm import SahteSaglayici
from sahte_api import SahteApi
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.api_istemcisi import ApiHatasi, HastaKabulApi

OBUR_GUN = date(2026, 11, 17)


def hazirla(kur, **ayar):
    k = kur(SahteSaglayici([]), **ayar)
    # Aynı gün çakışmasından sonra başka gün önerilebilsin diye 17 Kasım slotları da açılır.
    k.api.slotlar += SahteApi([OBUR_GUN]).slotlar
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    return k, oid


def dahiliye_yarin_sec(k, oid, sira: int = 0) -> str:
    r = k.orkestre(oid, userText="dahiliye yarın").json()
    assert r["action"] == "OFFER_SLOTS"
    secilen = r["data"]["slots"][sira]["slotId"]
    assert k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": secilen}).json()["action"] == "CONFIRM"
    return secilen


def slot_idleri(govde: dict) -> list[str]:
    return [s["slotId"] for s in govde["data"]["slots"]]


# ------------------------------------------------------------------ APPOINTMENT_CONFLICT

def test_saat_cakismasi_saat_yeniden_sorulur_ve_baska_saatle_tamamlanir(kur):
    k, oid = hazirla(kur)
    secilen = dahiliye_yarin_sec(k, oid)
    bas = k.api.slot_bul(secilen).baslangic
    # Aynı saatte başka poliklinikte etkin randevu var.
    k.api.onceki_randevular.append({"poliklinik": "KARDIYOLOJI", "baslangic": bas})
    r = k.orkestre(oid, userText="evet")
    govde = r.json()
    assert r.status_code == 409
    assert govde["error"]["code"] == "APPOINTMENT_CONFLICT" and govde["data"]["errorCode"] == "APPOINTMENT_CONFLICT"
    assert govde["action"] == "OFFER_SLOTS" and govde["data"]["expect"] == "saat"
    assert govde["replyText"].startswith(alan.RANDEVU_CAKISMA_SAAT)
    assert "Yarın için sabah mı" in govde["replyText"]
    yeni = slot_idleri(govde)
    assert yeni and secilen not in yeni and not k.api.randevular
    # Onay döngüsü yok: "evet" artık randevu oluşturmaz.
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] != "DONE" and not k.api.randevular
    # Başka saatle tamamlanır.
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": yeni[0]})
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] == "DONE" and len(k.api.randevular) == 1
    assert k.api.randevular[0]["slotId"] == yeni[0]


def test_saat_cakismasi_sonrasi_ayni_saat_yeniden_onerilmez(kur):
    k, oid = hazirla(kur)
    secilen = dahiliye_yarin_sec(k, oid)
    bas = k.api.slot_bul(secilen).baslangic
    k.api.onceki_randevular.append({"poliklinik": "GOZ", "baslangic": bas})
    k.orkestre(oid, userText="evet")
    # Aynı saatteki (başka poliklinikteki) slot da dışlanır.
    r = k.orkestre(oid, userText="kardiyoloji yarın").json()
    baslangiclar = {k.api.slot_bul(s).baslangic for s in slot_idleri(r)}
    assert baslangiclar and bas not in baslangiclar


def test_ayni_gun_ayni_poliklinik_gun_yeniden_sorulur(kur):
    k, oid = hazirla(kur)
    secilen = dahiliye_yarin_sec(k, oid, sira=1)
    bas = k.api.slot_bul(secilen).baslangic
    # Aynı gün, aynı poliklinikte farklı saatte etkin randevu.
    k.api.onceki_randevular.append({"poliklinik": "DAHILIYE", "baslangic": bas + timedelta(hours=2)})
    r = k.orkestre(oid, userText="evet")
    govde = r.json()
    assert r.status_code == 409 and govde["error"]["code"] == "APPOINTMENT_CONFLICT"
    assert govde["data"]["expect"] == "gun" and govde["action"] == "OFFER_SLOTS"
    beklenen = alan.RANDEVU_CAKISMA_GUN.format(gun="Yarın", poliklinik="Dahiliye")
    assert govde["replyText"].startswith(beklenen)
    assert govde["replyText"].endswith(alan.CAKISMA_GUN_SORU)  # örnek olarak "yarın" önerilmez
    yeni = slot_idleri(govde)
    # O gün Dahiliye önerilmez; 17 Kasım önerilir.
    assert yeni and all(k.api.slot_bul(s).baslangic.date() != YARIN for s in yeni)
    assert not k.api.randevular
    # "yarın" denirse yine o gün Dahiliye önerilmez.
    r = k.orkestre(oid, userText="yarın").json()
    assert all(k.api.slot_bul(s).baslangic.date() != YARIN for s in slot_idleri(r)) if r["data"].get("slots") else True
    assert not k.api.randevular
    # Başka günle tamamlanır.
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": yeni[0]})
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] == "DONE" and len(k.api.randevular) == 1


def test_ipucusuz_cakisma_genel_sablon(kur):
    k, oid = hazirla(kur)
    secilen = dahiliye_yarin_sec(k, oid)
    k.api._cakisma = lambda slot: "Çakışma."  # mesaj ipucu vermiyor
    r = k.orkestre(oid, userText="evet")
    govde = r.json()
    assert r.status_code == 409 and govde["error"]["code"] == "APPOINTMENT_CONFLICT"
    assert govde["replyText"].startswith(alan.RANDEVU_CAKISMA) and govde["data"]["expect"] == "gun"
    assert secilen not in slot_idleri(govde) and not k.api.randevular


def test_cakisma_mesaji_ingilizce_de_olsa_kod_esas(kur):
    # Mesaj metniyle karşılaştırma yapılmaz: kod yeter, mesaj yalnız şablon seçimi için ipucu.
    k, oid = hazirla(kur)
    dahiliye_yarin_sec(k, oid)
    k.api._cakisma = lambda slot: "You already have an appointment."
    r = k.orkestre(oid, userText="evet")
    assert r.status_code == 409 and r.json()["replyText"].startswith(alan.RANDEVU_CAKISMA)


# ------------------------------------------------------------------ SLOT_IN_PAST / SLOT_TAKEN

def test_slot_in_past_slot_expired_gibi(kur):
    k, oid = hazirla(kur)
    secilen = dahiliye_yarin_sec(k, oid)
    # Backend saati ileride: slot backend'e göre geçmişte.
    k.api.simdi = k.api.slot_bul(secilen).baslangic + timedelta(minutes=1)
    r = k.orkestre(oid, userText="evet")
    govde = r.json()
    assert r.status_code == 409
    assert govde["error"]["code"] == "SLOT_EXPIRED" and govde["data"]["errorCode"] == "SLOT_EXPIRED"
    assert govde["action"] == "OFFER_SLOTS" and govde["replyText"] == alan.SLOT_GECTI
    assert secilen not in slot_idleri(govde) and not k.api.randevular


def test_eszamanli_409_slot_taken(kur):
    k, oid = hazirla(kur)
    secilen = dahiliye_yarin_sec(k, oid)
    k.api.randevu_hata_modu = "eszamanli"
    r = k.orkestre(oid, userText="evet")
    govde = r.json()
    assert r.status_code == 409 and govde["error"]["code"] == "SLOT_TAKEN"
    assert govde["replyText"] == alan.SLOT_ALINDI_YENI and secilen not in slot_idleri(govde)
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": slot_idleri(govde)[0]})
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"


def test_is_mantigi_listesi():
    for kod in ("APPOINTMENT_CONFLICT", "SLOT_IN_PAST", "SLOT_TAKEN"):
        assert kod in alan.IS_MANTIGI_HATALARI
    assert "INTERNAL_ERROR" not in alan.IS_MANTIGI_HATALARI


# ------------------------------------------------------------------ hata zarfı

def _api_hatasi(yanit: httpx.Response) -> ApiHatasi:
    api = HastaKabulApi("http://x", "anahtar", is_mantigi_hatalari=alan.IS_MANTIGI_HATALARI,
                        transport=httpx.MockTransport(lambda istek: yanit))

    async def calistir():
        try:
            await api.randevu_olustur("o", "s", istek_kimligi="rid")
        finally:
            await api.kapat()
    with pytest.raises(ApiHatasi) as e:
        asyncio.run(calistir())
    return e.value


def _zarf(durum: int, kod: str, mesaj: str = "Türkçe mesaj") -> httpx.Response:
    return httpx.Response(durum, json={"error": {"code": kod, "message": mesaj, "requestId": "req-1"}})


@pytest.mark.parametrize("durum,kod,sistem", [
    (409, "APPOINTMENT_CONFLICT", False), (409, "SLOT_IN_PAST", False), (409, "SLOT_TAKEN", False),
    (401, "UNAUTHORIZED", True), (404, "NOT_FOUND", False), (500, "INTERNAL_ERROR", True),
    (400, "VALIDATION_FAILED", True),
])
def test_hata_zarfi_ayristirilir(durum, kod, sistem):
    h = _api_hatasi(_zarf(durum, kod))
    assert (h.durum, h.kod, h.istek_kimligi, h.sistem_hatasi) == (durum, kod, "req-1", sistem)
    assert h.mesaj == "Türkçe mesaj"


def test_zarf_disi_govdeler_sistem_hatasi():
    for yanit in (httpx.Response(500, json={"error": "metin"}), httpx.Response(500, content=b"<html>"),
                  httpx.Response(404, json=[1, 2])):
        h = _api_hatasi(yanit)
        assert h.kod == "UNKNOWN" and h.sistem_hatasi


def test_problem_details_hala_desteklenir():
    yanit = httpx.Response(400, headers={"Content-Type": "application/problem+json"}, content=json.dumps(
        {"title": "One or more validation errors occurred.", "status": 400, "traceId": "00-abc-01"}).encode())
    h = _api_hatasi(yanit)
    assert h.kod == "PROBLEM_DETAILS" and h.istek_kimligi == "00-abc-01" and h.sistem_hatasi


# ------------------------------------------------------------------ randevu ufku

def test_ufuk_ortam_degiskeni():
    assert ayarlar().randevu_ufuk_gun == 8
    assert ayarlar(RANDEVU_UFUK_GUN="5").randevu_ufuk_gun == 5


def test_ufuk_disi_tarih_poliklinikle(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="dahiliye 30 kasım").json()
    assert r["replyText"].startswith(alan.UFUK_DISI.format(gun_sayisi=8, son_tarih="22 Kasım Pazar"))
    assert alan.UFUK_GUN_SORU in r["replyText"] and r["data"]["expect"] == "gun"
    assert r["action"] != "CONFIRM" and not k.api.randevular
    # Akış sürer: ufuk içindeki bir günle devam edilir.
    r = k.orkestre(oid, userText="yarın").json()
    assert r["action"] in ("OFFER_SLOTS", "CONFIRM") and "Dahiliye" in json.dumps(r, ensure_ascii=False)


def test_ufuk_disi_tarih_poliklinik_yokken(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="30 kasım").json()
    assert r["replyText"].startswith(alan.UFUK_DISI.format(gun_sayisi=8, son_tarih="22 Kasım Pazar"))
    assert "Hangi poliklinikten" in r["replyText"] and r["data"]["expect"] == "poliklinik"


def test_ufuk_son_gunu_kabul_edilir(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="dahiliye 22 kasım").json()
    assert "yalnız önümüzdeki" not in r["replyText"]


def test_ufuk_ayari_sablona_yansir(kur):
    k, oid = hazirla(kur, RANDEVU_UFUK_GUN="3")
    r = k.orkestre(oid, userText="dahiliye 20 kasım").json()
    assert r["replyText"].startswith(alan.UFUK_DISI.format(gun_sayisi=3, son_tarih="17 Kasım Salı"))


def test_ufuk_disi_onay_adiminda_secim_korunur(kur):
    k, oid = hazirla(kur)
    secilen = dahiliye_yarin_sec(k, oid)
    r = k.orkestre(oid, userText="30 kasım olsun").json()
    assert "yalnız önümüzdeki 8 gün" in r["replyText"] and not k.api.randevular
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] == "DONE" and k.api.randevular[0]["slotId"] == secilen


# ------------------------------------------------------------------ sahte API verify sözleşmesi

def _verify(api: SahteApi, oid: str, tc) -> httpx.Response:
    with httpx.Client(transport=api.transport(), base_url="http://sahte") as c:
        return c.post(f"/api/v1/voice-sessions/{oid}/verify", json={"tc": tc})


def test_sahte_verify_bicimsiz_tc_sayac_artmaz():
    api = SahteApi([BUGUN])
    oid = api.oturum_ac()
    r = _verify(api, oid, "123")
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert api.oturumlar[oid]["attempts"] == 0
    r = _verify(api, oid, "12345678901")
    assert r.status_code == 200 and r.json()["attempts"] == 1 and r.json()["verified"] is False
    r = _verify(api, oid, "10000000146")
    assert r.json()["verified"] is True and r.json()["attempts"] == 1


def test_sahte_verify_suresi_dolmus_oturum():
    api = SahteApi([BUGUN])
    oid = api.oturum_ac()
    api.sure_doldur(oid)
    r = _verify(api, oid, "10000000146")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"


def test_sahte_api_bilinmeyen_yol_zarfli_404():
    api = SahteApi([BUGUN])
    with httpx.Client(transport=api.transport(), base_url="http://sahte") as c:
        r = c.get("/api/v1/yok")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
