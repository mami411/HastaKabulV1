"""/orchestrate uçtan uca akışlar (sahte API + sahte LLM, ağ yok)."""

import json
import uuid

from conftest import Kurulum
from ortak.llm import LLMZamanAsimi, SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan


def llm_json(**alanlar):
    temel = {"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
             "saat_bitis": None, "yanit": ""}
    temel.update(alanlar)
    return json.dumps(temel, ensure_ascii=False)


def dogrula(k: Kurulum, oturum: str) -> None:
    """Arayüzün /verify çağrısını taklit eder (TC orkestratöre hiç gelmez)."""
    k.api.oturumlar[oturum]["status"] = "VERIFIED"
    k.api.oturumlar[oturum]["attempts"] += 1


def test_dugme_akisi_llmsiz_tamamlanir(kur):
    llm = SahteSaglayici([])  # hiç çağrılmamalı
    k = kur(llm)
    oid = k.api.oturum_ac()

    r = k.orkestre(oid, event="START")
    assert r.status_code == 200 and r.json()["action"] == "ASK_TC"
    assert r.json()["replyText"] == r.json()["message"] == alan.SELAMLAMA

    dogrula(k, oid)
    r = k.orkestre(oid, event="TC_VERIFIED")
    govde = r.json()
    assert r.status_code == 200 and govde["action"] == "OFFER_SLOTS"
    slotlar = govde["data"]["slots"]
    assert slotlar and {"slotId", "label", "detail"} <= set(slotlar[0])
    # Bugünün 09–12 slotları (şu an 19:36) geçmişte: yalnız yarın sunulur, yerel saatle
    assert all(s["label"].startswith("Pzt 16 Kas") for s in slotlar)
    assert slotlar[0]["label"] == "Pzt 16 Kas 09:00"   # 06:00Z → 09:00 İstanbul
    assert "Z" not in slotlar[0]["label"]
    assert len({s["detail"].split(" · ")[0] for s in slotlar}) >= 4  # birden çok poliklinikten

    secilen = slotlar[0]["slotId"]
    r = k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": secilen})
    govde = r.json()
    assert govde["action"] == "CONFIRM" and govde["data"]["slotId"] == secilen
    assert "16 Kasım Pazartesi saat 09:00" in govde["replyText"]
    assert not k.api.randevular  # seçim yan etkisiz: kilit/rezervasyon yok (D7)

    r = k.orkestre(oid, userText="Onaylıyorum")
    govde = r.json()
    assert r.status_code == 200 and govde["action"] == "DONE"
    assert govde["data"]["accessCode"] == k.api.randevular[0]["accessCode"]
    assert govde["data"]["scheduledAt"].endswith("Z")       # API'nin UTC değeri olduğu gibi
    assert govde["data"]["label"] == "Pzt 16 Kas 09:00"      # gösterim yerel
    assert govde["data"]["accessCode"] in govde["replyText"]
    assert llm.cagrilar == []

    # Tekrar onay: ikinci randevu açılmaz, aynı bilgi döner
    r = k.orkestre(oid, userText="Onaylıyorum")
    assert r.json()["action"] == "DONE" and len(k.api.randevular) == 1
    r = k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": slotlar[1]["slotId"]})
    assert r.json()["action"] == "DONE" and len(k.api.randevular) == 1


def test_serbest_metinle_poliklinik_ve_saat(kur):
    llm = SahteSaglayici([llm_json(niyet="poliklinik_ve_zaman", poliklinik="KARDIYOLOJI", gun="2026-11-16",
                                   saat_baslangic="10:00", saat_bitis="11:00")])
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="kalp polikliniğine yarın saat on ile on bir arası")
    govde = r.json()
    assert r.status_code == 200 and govde["action"] == "OFFER_SLOTS"
    etiketler = [s["label"] for s in govde["data"]["slots"]]
    assert etiketler == ["Pzt 16 Kas 10:00", "Pzt 16 Kas 10:15", "Pzt 16 Kas 10:30", "Pzt 16 Kas 10:45",
                         "Pzt 16 Kas 11:00"]
    assert all(s["detail"].startswith("Kardiyoloji") for s in govde["data"]["slots"])
    # Kural tabanlı çözümleyici cümleyi tam çözdü: LLM'e hiç gidilmez (KVKK: önce kural)
    assert r.headers["X-Anlama-Kaynagi"] == "kural"
    assert "llm;dur=" not in r.headers["Server-Timing"] and "api;dur=" in r.headers["Server-Timing"]
    assert llm.cagrilar == []
    # İlk üç saat sesli okunur, kalanlar için "ekranda" notu
    assert govde["replyText"].startswith("Yarın 10:00, 10:15 ve 10:30 uygun. Hangisini istersiniz?")
    assert govde["data"]["expect"] == "secim" and govde["data"]["listen"] is True


def test_tam_saat_tek_eslesme_dogrudan_onaya_gecer_ama_rezerve_etmez(kur):
    llm = SahteSaglayici([llm_json(niyet="poliklinik_ve_zaman", poliklinik="ORTOPEDI", gun="2026-11-16",
                                   saat_baslangic="10:30", saat_bitis="10:30")])
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="ortopedi yarın on buçuk")
    assert r.json()["action"] == "CONFIRM" and r.json()["data"]["label"] == "Pzt 16 Kas 10:30"
    assert not k.api.randevular
    r = k.orkestre(oid, userText="evet")
    assert r.json()["action"] == "DONE"


def test_slot_taken_kullaniciya_dogru_yansir(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    slotlar = k.orkestre(oid, event="TC_VERIFIED").json()["data"]["slots"]
    secilen = slotlar[0]["slotId"]
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": secilen})
    k.api.slot_bul(secilen).alindi = True  # başka hasta araya girdi
    r = k.orkestre(oid, userText="Onaylıyorum")
    govde = r.json()
    assert r.status_code == 409
    assert govde["error"]["code"] == "SLOT_TAKEN" and govde["data"]["errorCode"] == "SLOT_TAKEN"
    assert govde["action"] == "OFFER_SLOTS" and govde["replyText"] == alan.SLOT_ALINDI_YENI
    yeni = [s["slotId"] for s in govde["data"]["slots"]]
    assert yeni and secilen not in yeni
    # Kullanıcı yeni saat seçip tamamlayabilir
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": yeni[0]})
    assert k.orkestre(oid, userText="Onaylıyorum").json()["action"] == "DONE"


def test_dogrulanmamis_oturum(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("ACTIVE")
    k.orkestre(oid, event="START")
    r = k.orkestre(oid, event="TC_VERIFIED")  # olaya güvenilmez: API hâlâ ACTIVE
    assert r.status_code == 409 and r.json()["action"] == "ASK_TC"
    assert r.json()["error"]["code"] == "VOICE_SESSION_NOT_VERIFIED"
    assert not any(c[1] == "/api/v1/slots" for c in k.api.cagrilar)
    r = k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["action"] == "ASK_TC"
    r = k.orkestre(oid, userText="kardiyoloji")
    assert r.json()["action"] == "ASK_TC" and r.json()["replyText"] == alan.ONCE_KIMLIK


def test_randevuda_oturum_dogrulanmamis_donerse_devir(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    slotlar = k.orkestre(oid, event="TC_VERIFIED").json()["data"]["slots"]
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": slotlar[0]["slotId"]})
    k.api.oturumlar[oid]["status"] = "EXPIRED"
    r = k.orkestre(oid, userText="onaylıyorum")
    assert r.status_code == 409 and r.json()["action"] == "HANDOFF"
    assert r.json()["error"]["code"] == "VOICE_SESSION_NOT_VERIFIED"


def test_handoff_ve_bilinmeyen_oturum(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("HANDED_OFF")
    r = k.orkestre(oid, event="TC_VERIFIED")
    assert r.status_code == 200 and r.json()["action"] == "HANDOFF"
    assert r.json()["data"]["reason"] == "TC_ATTEMPTS_EXHAUSTED"
    r = k.orkestre(str(uuid.uuid4()), event="START")
    assert r.status_code == 404 and r.json()["error"]["code"] == "VOICE_SESSION_NOT_FOUND"


def test_llm_kapaliyken_akis_ve_yedek(kur):
    llm = SahteSaglayici(varsayilan=LLMZamanAsimi("zaman aşımı"))
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    # Kural tabanlı çözümleyici anlıyor → LLM'e hiç gidilmez, 200
    r = k.orkestre(oid, userText="göz polikliniği yarın sabah")
    govde = r.json()
    assert r.status_code == 200 and govde["action"] == "OFFER_SLOTS" and "degraded" not in govde["data"]
    assert all(s["detail"].startswith("Göz") for s in govde["data"]["slots"])
    assert r.headers["X-Anlama-Kaynagi"] == "kural" and llm.cagrilar == []
    # Anlaşılamayan metin → 503 LLM_UNAVAILABLE ama anlamlı Türkçe yanıt ve mevcut adım korunur
    r = k.orkestre(oid, userText="şey nasıl desem")
    assert r.status_code == 503 and r.json()["error"]["code"] == "LLM_UNAVAILABLE"
    assert r.json()["replyText"] == alan.ANLAMA_KULLANILAMIYOR and r.json()["action"] == "OFFER_SLOTS"
    assert r.json()["data"]["slots"]
    # Düğmelerle randevu yine tamamlanır
    slot = r.json()["data"]["slots"][0]["slotId"]
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": slot})
    assert k.orkestre(oid, userText="Onaylıyorum").json()["action"] == "DONE"


def test_llm_hic_yapilandirilmamis(kur):
    k = kur(None)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="dahiliye yarın")
    assert r.status_code == 200 and r.json()["action"] == "OFFER_SLOTS"


def test_api_kapaliyken_dolu_denmez(kur):
    k = kur(None)
    oid = k.api.oturum_ac("VERIFIED")
    k.api.hata_modu = "500"
    r = k.orkestre(oid, event="TC_VERIFIED")
    assert r.status_code == 503  # oturum okunamadı
    k.api.hata_modu = None
    k.orkestre(oid, event="TC_VERIFIED")
    k.api.hata_modu = "ag"
    r = k.orkestre(oid, userText="kardiyoloji yarın")
    govde = r.json()
    assert r.status_code == 503 and govde["error"]["code"] == "API_UNAVAILABLE"
    assert govde["replyText"] == alan.SISTEM_HATASI_SAATLER
    assert "boş saat görünmüyor" not in govde["replyText"]


def test_gercekten_bos_saat_yoksa_soylenir(kur):
    k = kur(None)  # LLM yok: kural tabanlı anlama
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    for s in k.api.slotlar:
        if s.poliklinik == "GOZ":
            s.alindi = True
    r = k.orkestre(oid, userText="göz polikliniği")
    assert r.status_code == 200 and r.json()["action"] == "OFFER_SLOTS"
    assert r.json()["data"]["slots"] == []
    assert "boş saat görünmüyor" in r.json()["replyText"]


def test_randevu_zaman_asimi_sonuc_belirsiz(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    slotlar = k.orkestre(oid, event="TC_VERIFIED").json()["data"]["slots"]
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": slotlar[0]["slotId"]})
    k.api.randevu_hata_modu = "zaman_asimi"
    k.api.randevu_once_kaydet = True  # istek işlendi ama yanıt gelmedi
    r = k.orkestre(oid, userText="Onaylıyorum")
    assert r.status_code == 503 and r.json()["action"] == "HANDOFF"
    assert r.json()["data"]["reason"] == "BOOKING_UNCERTAIN"
    assert "oluşturuldu" not in r.json()["replyText"]


def test_randevu_sistem_hatasi_onayda_kalir(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    slotlar = k.orkestre(oid, event="TC_VERIFIED").json()["data"]["slots"]
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": slotlar[0]["slotId"]})
    k.api.randevu_hata_modu = "ag"
    r = k.orkestre(oid, userText="Onaylıyorum")
    assert r.status_code == 503 and r.json()["action"] == "CONFIRM"
    assert r.json()["replyText"] == alan.SISTEM_HATASI
    k.api.randevu_hata_modu = None
    assert k.orkestre(oid, userText="Onaylıyorum").json()["action"] == "DONE"


def test_llm_metni_kullaniciya_hic_gitmez(kur):
    # LLM şemada olmayan bir "yanit" alanı uydursa da (eski şema) kullanıcıya ASLA gitmez:
    # konu dışı/belirsiz yanıtlar şablondur; "randevunuz onaylandı" türü uydurma kökten kapanır.
    llm = SahteSaglayici([llm_json(niyet="konu_disi", yanit="RANDEVUNUZ ONAYLANDI, iyi günler!"),
                          llm_json(niyet="konu_disi", yanit="Danışma bankosu girişin solunda olabilir.")])
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="randevum oldu mu")
    assert r.json()["replyText"] == alan.KONU_DISI_YANIT
    r = k.orkestre(oid, userText="danışma nerede")
    assert r.json()["replyText"] == alan.KONU_DISI_YANIT
    assert "ONAYLANDI" not in r.text and "solunda" not in r.text


def test_tibbi_soru_sabit_yanit(kur):
    llm = SahteSaglayici([llm_json(niyet="tibbi_soru", yanit="Parasetamol 500 mg alın.")])
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="başım ağrıyor hangi ilacı almalıyım")
    # Tıbbi yorum yok (LLM'in "Parasetamol" önerisi ulaşmaz); şikâyetten yalnız poliklinik önerisi eklenir.
    # ("başım ağrıyor ne yapayım" 2026-11-16'dan beri tavsiye yoludur: tests/test_tavsiye.py.)
    assert r.json()["replyText"].startswith(alan.TIBBI_YORUM_YOK) and r.json()["action"] == "OFFER_SLOTS"
    assert "Parasetamol" not in r.text and r.json()["data"]["suggestion"]["clinic"] == "NOROLOJI"
    r = k.orkestre(oid, userText="ilaç ne içmeliyim")
    assert "Parasetamol" not in r.text


def test_tc_iceren_metin_llme_ve_yanita_gitmez(kur):
    llm = SahteSaglayici([])
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="numaram 10000000146 kardiyoloji")
    assert r.json()["replyText"] == alan.TC_TUS_TAKIMINDAN
    assert "10000000146" not in r.text
    assert llm.cagrilar == []


def test_onayda_ret_ve_baska_saat(kur):
    k = kur(None)
    oid = k.api.oturum_ac("VERIFIED")
    slotlar = k.orkestre(oid, event="TC_VERIFIED").json()["data"]["slots"]
    k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": slotlar[0]["slotId"]})
    r = k.orkestre(oid, userText="hayır")
    assert r.json()["action"] == "OFFER_SLOTS" and r.json()["replyText"] == alan.SECIM_IPTAL
    r = k.orkestre(oid, userText="onaylıyorum")  # seçim yokken onay → randevu yok
    assert r.json()["replyText"] == alan.ONCE_SAAT_SECIN and not k.api.randevular


def test_gecersiz_girdiler(kur):
    k = kur(SahteSaglayici([]))
    assert k.client.post("/orchestrate", json={"sessionId": "abc", "event": "START"}).status_code == 400
    oid = k.api.oturum_ac()
    r = k.orkestre(oid, event="BILINMEYEN")
    assert r.status_code == 400 and r.json()["action"] is None
    assert k.orkestre(oid).status_code == 400
    assert k.orkestre(oid, userText="a" * 501).status_code == 400
    assert k.orkestre(oid, event="SLOT_SELECTED").status_code == 400
    assert k.client.post("/orchestrate", content=b"bozuk", headers={"Content-Type": "application/json"}).status_code == 400


def test_sunulmayan_slot_reddedilir(kur):
    k = kur(SahteSaglayici([]))
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, event="SLOT_SELECTED", data={"slotId": str(uuid.uuid4())})
    assert r.status_code == 400 and r.json()["action"] == "OFFER_SLOTS"


def test_orkestrator_yeniden_baslasa_da_surer(kur):
    k1 = kur(SahteSaglayici([]))
    oid = k1.api.oturum_ac("VERIFIED")
    # Yeni süreç (bellek boş), aynı API
    k2 = kur(None)
    k2.api = k1.api
    from ses_servisi.alanlar import hasta_kabul
    from ses_servisi.api_istemcisi import HastaKabulApi
    k2.app.state.orkestrator.api = HastaKabulApi("http://sahte-api", "test-servis-anahtari",
                                                 is_mantigi_hatalari=hasta_kabul.IS_MANTIGI_HATALARI,
                                                 transport=k1.api.transport())
    r = k2.orkestre(oid, userText="dahiliye yarın")
    assert r.status_code == 200 and r.json()["action"] == "OFFER_SLOTS"


def test_servis_anahtari_yanlissa_sistem_hatasi(kur):
    k = kur(SahteSaglayici([]), SERVICE_KEY="yanlis")
    oid = k.api.oturum_ac("VERIFIED")
    r = k.orkestre(oid, event="TC_VERIFIED")
    assert r.status_code == 503 and r.json()["error"]["code"] == "API_UNAVAILABLE"
    assert "servis" not in r.json()["replyText"].lower()  # iç ayrıntı kullanıcıya gitmez


def test_cors_yalniz_izinli_kaynaklar(kur):
    k = kur(SahteSaglayici([]))
    izinli = k.client.options("/orchestrate", headers={"Origin": "http://localhost:5173",
                                                       "Access-Control-Request-Method": "POST"})
    assert izinli.headers.get("access-control-allow-origin") == "http://localhost:5173"
    yasak = k.client.options("/orchestrate", headers={"Origin": "http://kotu.example",
                                                      "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in yasak.headers
