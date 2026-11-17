"""Son bağımsız denetim (2026-11-16) regresyon testleri.

Kök neden: yeniden doğrulanan oturumda CONFIRM'ün sürekli 503 API_UNAVAILABLE ("Şu an sisteme ulaşamıyorum")
dönmesi. Backend 409 APPOINTMENT_CONFLICT döndürüyordu (demo hastanın aynı gün aynı poliklinikte / aynı saatte
önceki randevusu); çalışan ses servisi süreci kodun bu kodu iş kuralı listesine eklediği sürümden ÖNCE
başlatılmıştı (log: 01:45 süreç, 01:56 kod; "randevu oluşturulamadı (sistem hatası): API 409
APPOINTMENT_CONFLICT"). İş kuralı olarak tanınmayan her 409 sistem hatasına çevriliyor ve onay sorusu açık
bırakıldığı için her "evet" aynı slotla aynı reddi alıyordu (yapışkan döngü).

Bu dosya: (1) backend'in 409 döndürdüğü her kodun orkestratörde iş kuralı sayıldığını backend kaynağından
okuyarak denetler, (2) randevu ucunun döndürebileceği her iş kuralı kodu için yanıtın "sisteme ulaşamıyorum"
olmadığını ve aynı onayın tekrar denenmediğini, (3) ekranda gösterilmeyen yedek saat listesinden sesle seçim
yapılamadığını sınar.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ortak.llm import SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan

KOK = Path(__file__).resolve().parents[2]
HATA_YAZICI = KOK / "hasta-kabul-backend" / "src" / "HastaKabul.Api" / "Middleware" / "ErrorResponseWriter.cs"
HATA_KODLARI = KOK / "hasta-kabul-backend" / "src" / "HastaKabul.Domain" / "Common" / "ErrorCodes.cs"
# Orkestratörün hiç çağırmadığı uçlara özgü 409'lar (hasta portalı / hekim paneli).
ORKESTRATOR_DISI_409 = {"SUMMARY_NOT_APPROVED"}


def llm_json(**alanlar):
    temel = {"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None, "saat_bitis": None}
    temel.update(alanlar)
    return json.dumps(temel, ensure_ascii=False)


def onay_bekleyen(kur, llm=None):
    k = kur(llm if llm is not None else SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="dahiliye yarın").json()
    secilen = r["data"]["slots"][0]["slotId"]
    assert k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": secilen}).json()["action"] == "CONFIRM"
    return k, oid, secilen


def randevu_postlari(k) -> int:
    return sum(1 for y, p in k.api.cagrilar if y == "POST" and p == "/api/v1/appointments")


# ------------------------------------------------------------------ backend sözleşmesiyle eşzamanlılık

@pytest.mark.skipif(not HATA_YAZICI.exists() or not HATA_KODLARI.exists(), reason="backend kaynağı yok")
def test_backendin_409_dondurdugu_her_kod_is_kurali_listesinde():
    sabitler = dict(re.findall(r'public const string (\w+) = "([A-Z_]+)";',
                               HATA_KODLARI.read_text(encoding="utf-8")))
    yazici = HATA_YAZICI.read_text(encoding="utf-8")
    kodlar_409 = {sabitler[ad] for ad in re.findall(r"ErrorCodes\.(\w+) => StatusCodes\.Status409Conflict", yazici)}
    assert {"APPOINTMENT_CONFLICT", "SLOT_IN_PAST", "SLOT_TAKEN"} <= kodlar_409  # ayrıştırma çalışıyor
    eksik = kodlar_409 - ORKESTRATOR_DISI_409 - alan.IS_MANTIGI_HATALARI
    assert not eksik, f"Backend 409 döndürüyor ama orkestratör sistem hatası sayacak: {sorted(eksik)}"


# ------------------------------------------------------------------ randevu ucunun her iş kuralı kodu

@pytest.mark.parametrize("durum,kod,mesaj", [
    (409, "APPOINTMENT_CONFLICT", "Aynı gün bu poliklinikte zaten randevunuz var; lütfen başka bir gün seçin."),
    (409, "APPOINTMENT_CONFLICT", "Bu saatte başka bir randevunuz var; lütfen farklı bir saat seçin."),
    (409, "APPOINTMENT_CONFLICT", ""),
    (409, "SLOT_IN_PAST", "Seçilen saat geçmişte kaldı."),
    (409, "SLOT_TAKEN", "Seçilen saat başka bir hasta tarafından alındı."),
    (404, "NOT_FOUND", "Seçilen saat bulunamadı."),
    (409, "INVALID_STATUS_TRANSITION", "Geçiş yapılamaz."),
])
def test_is_kurali_reddi_sistem_hatasi_degil_ve_onay_yapiskan_degil(kur, durum, kod, mesaj):
    k, oid, secilen = onay_bekleyen(kur)
    k.api.randevu_hata_kodu = (durum, kod, mesaj)
    r = k.orkestre(oid, userText="evet onaylıyorum")
    govde = r.json()
    assert r.status_code != 503
    assert govde["data"].get("errorCode") != "API_UNAVAILABLE"
    assert alan.SISTEM_HATASI not in govde["replyText"]
    assert govde["action"] == "OFFER_SLOTS"           # onay kartı kapanır, saat seçimine dönülür
    assert secilen not in [s["slotId"] for s in govde["data"]["slots"]]
    # Yapışkan döngü yok: ikinci "evet" aynı slotla yeniden POST /appointments göndermez.
    once = randevu_postlari(k)
    r2 = k.orkestre(oid, userText="evet onaylıyorum").json()
    assert randevu_postlari(k) == once
    assert r2["action"] != "DONE" and r2["data"].get("errorCode") != "API_UNAVAILABLE"


def test_ozel_sablonu_olmayan_is_kurali_kodu_genel_ret_sablonu_ve_baska_saatle_tamamlanir(kur):
    k, oid, secilen = onay_bekleyen(kur)
    k.api.randevu_hata_kodu = (409, "INVALID_STATUS_TRANSITION", "x")
    govde = k.orkestre(oid, userText="evet").json()
    assert govde["replyText"].startswith(alan.RANDEVU_OLUSTURULAMADI)
    assert govde["error"]["code"] == "INVALID_STATUS_TRANSITION" and govde["data"]["expect"] == "secim"
    k.api.randevu_hata_kodu = None
    yeni = govde["data"]["slots"][0]["slotId"]
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": yeni})
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"


def test_is_kurali_reddinde_oturum_suresi_dolmussa_yeniden_giris(kur):
    k, oid, _ = onay_bekleyen(kur)
    k.api.randevu_hata_kodu = (409, "INVALID_STATUS_TRANSITION", "Oturumun süresi doldu")
    k.api.sure_doldur(oid)
    govde = k.orkestre(oid, event="CONFIRM", data={"slotId": k.api.slotlar[0].id})
    # CONFIRM yanlış slotla: önce seçim uyuşmazlığı; doğru slotla yeniden.
    secili = govde.json()["data"].get("slotId")
    govde = k.orkestre(oid, event="CONFIRM", data={"slotId": secili}).json()
    assert govde["action"] == "HANDOFF" and govde["data"].get("restart") is True


def test_gercek_sistem_hatasi_hala_sistem_hatasi(kur):
    """Kapalı liste korunur: 500 ve ağ hatası "dolu/yok" ya da iş kuralı gibi sunulmaz."""
    k, oid, _ = onay_bekleyen(kur)
    k.api.randevu_hata_modu = "ag"
    r = k.orkestre(oid, userText="evet")
    assert r.status_code == 503 and r.json()["replyText"] == alan.SISTEM_HATASI


# ------------------------------------------------------------------ gizli ekran yedeğinden sesle seçim yok

def test_poliklinik_sorulurken_sira_sayisi_gizli_listeden_secmez(kur):
    llm = SahteSaglayici([llm_json(niyet="secim", sira=2)] * 3)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    r = k.orkestre(oid, event="TC_VERIFIED").json()
    assert r["data"]["expect"] == "poliklinik" and r["data"]["slots"]  # ekran yedeği gönderiliyor
    r = k.orkestre(oid, userText="ikincisi").json()
    assert r["action"] == "OFFER_SLOTS" and r["data"]["expect"] == "poliklinik"
    assert alan.POLIKLINIK_SORU_TEKRAR in r["replyText"]


def test_gun_sorulurken_sira_sayisi_gizli_listeden_secmez(kur):
    llm = SahteSaglayici([llm_json(niyet="secim", sira=1)] * 3)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="kardiyoloji").json()
    assert r["data"]["expect"] == "gun"
    r = k.orkestre(oid, userText="ilkini alayım").json()
    assert r["action"] == "OFFER_SLOTS" and r["data"]["expect"] == "gun"


def test_poliklinik_secilmeden_saat_soylenince_rastgele_poliklinik_secilmez(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    r = k.orkestre(oid, event="TC_VERIFIED").json()
    assert r["data"]["slots"]
    r = k.orkestre(oid, userText="yarın saat 9").json()  # yedek listede her poliklinikten 09:00 var
    assert r["action"] == "OFFER_SLOTS" and r["data"]["expect"] == "poliklinik"
    assert "Tercihinizi not aldım" in r["replyText"]
    # Poliklinik söylenince not alınan gün/saat kullanılır (tam eşleşme → onay sorusu).
    r = k.orkestre(oid, userText="kardiyoloji").json()
    assert r["action"] == "CONFIRM" and "Kardiyoloji" in r["replyText"]


def test_secim_adiminda_saat_soylemek_hala_secer(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="dahiliye yarın sabah").json()
    assert r["data"]["expect"] == "secim"
    ikinci = r["data"]["slots"][1]["label"].split()[-1]
    r = k.orkestre(oid, userText=f"saat {ikinci}").json()
    assert r["action"] == "CONFIRM"


# ------------------------------------------------------------------ STT toleransı (provada ölçülen yanlış duymalar)

@pytest.mark.parametrize("duyulan", ["Yerin sabah", "Yer'in sabah", "yerin öğleden sonra", "Yırın sabah"])
def test_yerin_sabah_yarin_sayilir(duyulan):
    from datetime import date, timedelta

    from ses_servisi.anlama import kural_tabanli_anla
    bugun = date(2026, 11, 16)
    k = kural_tabanli_anla(duyulan, bugun, alan, adim="GUN_BEKLENIYOR")
    assert k.niyet == "poliklinik_ve_zaman" and k.gun == bugun + timedelta(days=1)


@pytest.mark.parametrize("metin", ["yerinde kalsın", "yerine başka saat olsun"])
def test_yerin_toleransi_baska_kelimeleri_yarin_yapmaz(metin):
    from datetime import date

    from ses_servisi.anlama import kural_tabanli_anla
    assert kural_tabanli_anla(metin, date(2026, 11, 16), alan, adim="GUN_BEKLENIYOR").gun is None


@pytest.mark.parametrize("duyulan,beklenen", [
    ("Metormil'i ne zaman kullanacağım?", ["Metformin"]),
    ("Metormen'in ne zaman kullanacağım?", ["Metformin"]),
    ("Parol'ü ne zaman alacağım", []),          # tolerans dar kalır: Parasetamol değil
])
def test_kesme_isaretli_stt_ilac_adi(duyulan, beklenen):
    from datetime import date

    from sahte_api import demo_ziyaretleri
    from ses_servisi.gecmis import ilac_bul, ziyaretleri_ayristir
    z = ziyaretleri_ayristir({"visits": demo_ziyaretleri(date(2026, 11, 16))})
    assert [i.ad for _, i in ilac_bul(duyulan, z)] == beklenen


def test_randevu_alinmis_oturumda_yeni_randevu_istegi_ne_yapilacagini_soyler(kur):
    k, oid, _ = onay_bekleyen(kur)
    assert k.orkestre(oid, userText="evet onaylıyorum").json()["action"] == "DONE"
    r = k.orkestre(oid, userText="yarın kardiyolojiden de randevu istiyorum").json()
    assert r["action"] == "DONE" and alan.YENI_RANDEVU_ICIN in r["replyText"]
    assert randevu_postlari(k) == 1


@pytest.mark.parametrize("duyulan", ["mefes alamıyorum", "Nefes alamıyorum"])
def test_stt_bozuk_nefes_alamiyorum_randevu_alinmis_oturumda_da_acil(kur, duyulan):
    """Prova 3. turda Whisper "mefes alamıyorum" yazdı; acil kaçıp "randevunuz oluşturuldu" okundu."""
    k, oid, _ = onay_bekleyen(kur)
    assert k.orkestre(oid, userText="evet onaylıyorum").json()["action"] == "DONE"
    r = k.orkestre(oid, userText=duyulan).json()
    assert "112" in r["replyText"] and "Erişim kodunuz" not in r["replyText"]


def test_burnumdan_mefes_alamiyorum_acil_degil():
    from ses_servisi.anlama import acil_mi, normalize
    assert not acil_mi(normalize("burnumdan mefes alamıyorum"), alan)