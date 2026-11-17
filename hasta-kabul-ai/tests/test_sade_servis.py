"""ai-simplify HTTP servisi: girdi doğrulama, 202 + arka plan, callback, yeniden deneme, kalıcı kuyruk, KVKK."""

import logging
import sqlite3
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from ortak.llm import SahteSaglayici
from sahte_backend import ANAHTAR, SahteBackend, YavasSaglayici, bekle, llm_json, sade_ortami
from sade_servisi.app import uygulama_olustur
from sade_servisi.ayarlar import SadeAyarlari
from sade_servisi.backend import BackendIstemcisi
from sade_servisi.kuyruk import Kuyruk

TANSIYON = llm_json("Kan basıncınız, yani tansiyonunuz sürekli yüksek seyrediyor. Tuzu azaltmanız gerekir.",
                    [("i1", "Damarlarınızı gevşeterek tansiyonunuzu düşürür.", "Günde 1 kez 1 tablet alın.")])


def govde(**degisen):
    g = {"patientRef": str(uuid.uuid4()), "diagnosis": "Yüksek tansiyon", "medications": ["Amlodipin 5 mg 1x1"],
         "requestId": uuid.uuid4().hex, "summaryId": str(uuid.uuid4())}
    g.update(degisen)
    return g


def basliklar(anahtar=ANAHTAR):
    return {"X-Service-Key": anahtar, "X-Request-Id": uuid.uuid4().hex}


class Kurulum:
    def __init__(self, tmp_path, llm=None, backend=None, kuyruk=None, isci_baslat=True, **ayar):
        self.backend = backend or SahteBackend()
        self.ayarlar = SadeAyarlari.ortamdan(sade_ortami(tmp_path, **ayar))
        istemci = BackendIstemcisi(self.ayarlar.api_url, self.ayarlar.servis_anahtari, zaman_asimi_sn=2,
                                   transport=self.backend.transport())
        self.app = uygulama_olustur(self.ayarlar, llm=llm, backend=istemci, kuyruk=kuyruk, isci_baslat=isci_baslat)
        self.client = TestClient(self.app)

    def gonder(self, g=None, anahtar=ANAHTAR):
        return self.client.post("/simplify", json=g if g is not None else govde(), headers=basliklar(anahtar))


@pytest.fixture
def kur(tmp_path):
    acik: list[Kurulum] = []

    def _kur(**kw) -> Kurulum:
        k = Kurulum(tmp_path, **kw)
        k.client.__enter__()
        acik.append(k)
        return k

    yield _kur
    for k in acik:
        k.client.__exit__(None, None, None)


# ---------------------------------------------------------------- kimlik ve girdi doğrulama

def test_servis_anahtari_zorunlu(kur):
    k = kur()
    assert k.gonder(anahtar="yanlis").status_code == 401
    assert k.client.post("/simplify", json=govde()).status_code == 401
    r = k.gonder(anahtar="yanlis")
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_anahtar_tanimsizsa_503(kur):
    k = kur(SERVICE_KEY="")
    r = k.gonder()
    assert r.status_code == 503 and r.json()["error"]["code"] == "SERVICE_KEY_MISSING"


@pytest.mark.parametrize("bozuk", [
    {"fazla": "alan"},                                     # extra="forbid"
    {"summaryId": "../../admin"},                          # Guid değil (yol enjeksiyonu)
    {"diagnosis": ""},
    {"diagnosis": "x" * 1001},
    {"medications": ["Parol"] * 201},                     # sert sınır (yumuşak sınır: 30 → SIMPLIFY_FAILED)
    {"medications": ["x" * 501]},
    {"medications": "Parol"},
    {"medications": [None]},
    {"requestId": ""},
    {"patientRef": 12345},
])
def test_gecersiz_govde_400_ve_girdi_yansitilmaz(kur, bozuk):
    k = kur()
    g = govde(diagnosis="GIZLI-TANI-123")
    g.update(bozuk)
    r = k.gonder(g)
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_FAILED"
    assert "GIZLI-TANI-123" not in r.text
    if isinstance(g["patientRef"], str):
        assert g["patientRef"] not in r.text
    assert len(k.app.state.kuyruk) == 0


def test_eksik_alan_ve_bozuk_json(kur):
    k = kur()
    g = govde()
    del g["medications"]
    assert k.gonder(g).status_code == 400
    r = k.client.post("/simplify", content=b"{bozuk", headers={**basliklar(), "Content-Type": "application/json"})
    assert r.status_code == 400


# ---------------------------------------------------------------- uçtan uca (sahte LLM + sahte backend)

def test_202_ve_callback_backende_ulasir(kur):
    k = kur(llm=SahteSaglayici([TANSIYON]))
    g = govde(medications=["Amlodipin 5 mg 1x1", "", "Xyzol 10 mg 2x1"])
    r = k.gonder(g)
    assert r.status_code == 202 and r.json()["accepted"] is True
    assert bekle(lambda: len(k.backend.putlar) == 1)
    put = k.backend.putlar[0]
    assert put["summaryId"] == g["summaryId"]
    assert put["basliklar"]["x-service-key"] == ANAHTAR and put["basliklar"]["x-request-id"] == g["requestId"]
    b = put["govde"]
    assert set(b) == {"diagnosisPlain", "medications", "warnings", "unknownTerms", "sourceIds"}
    assert [m["name"] for m in b["medications"]] == ["Amlodipin", "Xyzol"]   # boş satır atılır
    assert b["unknownTerms"] == ["Xyzol"] and len(b["diagnosisPlain"]) <= 2000
    assert bekle(lambda: len(k.app.state.kuyruk) == 0)                       # iş bitince silinir
    assert g["patientRef"] not in str(put)                                   # patientRef geri gitmez


def test_202_llm_yavasken_de_hemen_doner(kur):
    llm = YavasSaglayici([TANSIYON], gecikme_sn=1.5)
    k = kur(llm=llm)
    bas = time.perf_counter()
    r = k.gonder()
    sure = time.perf_counter() - bas
    assert r.status_code == 202 and sure < 0.5, sure
    r2 = k.gonder(govde(diagnosis="hazımsızlık", medications=[]))   # ikinci istek de beklemez
    assert r2.status_code == 202 and time.perf_counter() - bas < 1.0
    assert bekle(lambda: len(k.backend.putlar) == 2, sure=10)


def test_backend_kb_ucu_varsa_sourceids_backend_guidleri(kur):
    backend = SahteBackend(kb_modu="liste")
    k = kur(llm=SahteSaglayici([TANSIYON]), backend=backend)
    k.gonder()
    assert bekle(lambda: len(backend.putlar) == 1)
    kimlikler = {e["term"]: e["id"] for e in backend.kb}
    assert backend.putlar[0]["govde"]["sourceIds"] == [kimlikler["Hipertansiyon"], kimlikler["Amlodipin"]]
    assert k.client.get("/health").json()["kb"]["kaynak"] == "api"


@pytest.mark.parametrize("mod", ["404", "ag"])
def test_kb_ucu_yoksa_gomulu_kopya(kur, mod):
    backend = SahteBackend(kb_modu=mod)
    k = kur(llm=SahteSaglayici([TANSIYON]), backend=backend)
    k.gonder()
    assert bekle(lambda: len(backend.putlar) == 1)
    assert backend.putlar[0]["govde"]["sourceIds"] == ["gomulu:hipertansiyon", "gomulu:amlodipin"]
    assert k.client.get("/health").json()["kb"]["kaynak"] == "gomulu"


def test_llm_kapali_yollar_callback_ile_biter(kur):
    k = kur(llm=None)
    k.gonder(govde(diagnosis="hazımsızlık", medications=["Lansor 30 mg 1x1"]))
    k.gonder(govde(diagnosis="KOAH", medications=["Lansor 30 mg 1x1"]))
    assert bekle(lambda: len(k.backend.putlar) == 2)
    sablon, hata = (p["govde"] for p in k.backend.putlar)
    assert "diagnosisPlain" in sablon and sablon["unknownTerms"] == []
    assert hata == {"errorCode": "LLM_UNAVAILABLE"}


def test_beklenmeyen_hata_simplify_failed_ile_biter(kur, monkeypatch):
    k = kur(llm=SahteSaglayici([TANSIYON]))

    async def patla(*a, **kw):
        raise RuntimeError("beklenmeyen")
    monkeypatch.setattr(k.app.state.sadelestirici, "uret", patla)
    k.gonder()
    assert bekle(lambda: len(k.backend.putlar) == 1)
    assert k.backend.putlar[0]["govde"] == {"errorCode": "SIMPLIFY_FAILED"}


# ---------------------------------------------------------------- callback yeniden deneme

def test_callback_ustel_beklemeyle_yeniden_denenir(kur):
    backend = SahteBackend(put_yanitlari=["ag", 500, 401, 200])
    k = kur(llm=SahteSaglayici([TANSIYON]), backend=backend)
    k.gonder()
    assert bekle(lambda: len(backend.basarili_putlar) == 1, sure=10)
    assert [p["durum"] for p in backend.putlar] == ["ag", 500, 401, 200]
    # Gövde yeniden üretilmedi (LLM bir kez çağrıldı), dört denemede de aynı.
    assert len({str(p["govde"]) for p in backend.putlar}) == 1
    assert bekle(lambda: len(k.app.state.kuyruk) == 0)


def test_bekleme_suresi_ustel_ve_sinirli(kur):
    isci = kur(SADE_GERI_CAGRI_TABAN_BEKLEME_SN="2", SADE_GERI_CAGRI_AZAMI_BEKLEME_SN="300").app.state.isci
    sureler = [isci.bekleme_suresi(d) for d in range(10)]
    assert 1.8 <= sureler[0] <= 2.2 and 3.6 <= sureler[1] <= 4.4 and 7.2 <= sureler[2] <= 8.8
    assert max(sureler) <= 330


def test_azami_deneme_asilinca_vazgecilir(kur):
    backend = SahteBackend(put_yanitlari=[500] * 10)
    k = kur(llm=SahteSaglayici([TANSIYON]), backend=backend, SADE_GERI_CAGRI_AZAMI_DENEME="3")
    k.gonder()
    assert bekle(lambda: len(backend.putlar) == 3 and len(k.app.state.kuyruk) == 0, sure=10)
    time.sleep(0.2)
    assert len(backend.putlar) == 3


def test_400_donerse_bir_kez_simplify_failed_gonderilir(kur):
    backend = SahteBackend(put_yanitlari=[400])
    k = kur(llm=SahteSaglayici([TANSIYON]), backend=backend)
    k.gonder()
    assert bekle(lambda: len(backend.putlar) == 2)
    assert backend.putlar[1]["govde"] == {"errorCode": "SIMPLIFY_FAILED"}


@pytest.mark.parametrize("durum", [404, 409])
def test_404_409_kalici_vazgecilir(kur, durum):
    backend = SahteBackend(put_yanitlari=[durum])
    k = kur(llm=SahteSaglayici([TANSIYON]), backend=backend)
    k.gonder()
    assert bekle(lambda: len(backend.putlar) == 1 and len(k.app.state.kuyruk) == 0)
    time.sleep(0.2)
    assert len(backend.putlar) == 1


# ---------------------------------------------------------------- kalıcı kuyruk

def test_yeniden_baslatmada_bekleyen_is_kaybolmaz(tmp_path):
    ortam = sade_ortami(tmp_path)
    a = SadeAyarlari.ortamdan(ortam)
    g = govde()
    backend = SahteBackend()

    # 1) İş kabul edildi ama işçi çalışmadan süreç kapandı.
    istemci = BackendIstemcisi(a.api_url, a.servis_anahtari, transport=backend.transport())
    with TestClient(uygulama_olustur(a, llm=None, backend=istemci, isci_baslat=False)) as c:
        assert c.post("/simplify", json=g, headers=basliklar()).status_code == 202
    assert backend.putlar == []
    kuyruk = Kuyruk(a.kuyruk_dosyasi)
    assert len(kuyruk) == 1
    sakli = kuyruk.sonraki()
    kuyruk.kapat()
    assert sakli.summary_id == g["summaryId"]
    # Kuyrukta asgari veri: patientRef yok.
    with sqlite3.connect(a.kuyruk_dosyasi) as db:
        ham = str(db.execute("SELECT * FROM isler").fetchall())
    assert g["patientRef"] not in ham

    # 2) Yeni süreç: iş işlenir ve callback gider.
    istemci = BackendIstemcisi(a.api_url, a.servis_anahtari, transport=backend.transport())
    with TestClient(uygulama_olustur(a, llm=SahteSaglayici([TANSIYON]), backend=istemci)) as c:
        assert bekle(lambda: len(backend.putlar) == 1)
        assert bekle(lambda: c.get("/health").json()["kuyruk"]["bekleyen"] == 0)
    assert backend.putlar[0]["summaryId"] == g["summaryId"] and "diagnosisPlain" in backend.putlar[0]["govde"]


def test_uretilmis_govde_yeniden_baslatmada_llm_siz_gonderilir(tmp_path):
    a = SadeAyarlari.ortamdan(sade_ortami(tmp_path))
    backend = SahteBackend(put_yanitlari=["ag"] * 50)
    istemci = BackendIstemcisi(a.api_url, a.servis_anahtari, transport=backend.transport())
    with TestClient(uygulama_olustur(a, llm=SahteSaglayici([TANSIYON]), backend=istemci)) as c:
        c.post("/simplify", json=govde(), headers=basliklar())
        assert bekle(lambda: len(backend.putlar) >= 1)
    with sqlite3.connect(a.kuyruk_dosyasi) as db:
        satirlar = db.execute("SELECT tani, ilaclar, durum FROM isler").fetchall()
    assert satirlar == [(None, None, "GONDERILECEK")]   # girdi silindi, yalnız üretilmiş gövde kaldı

    backend.put_yanitlari.clear()
    istemci = BackendIstemcisi(a.api_url, a.servis_anahtari, transport=backend.transport())
    llm = SahteSaglayici([])   # çağrılırsa AssertionError
    with TestClient(uygulama_olustur(a, llm=llm, backend=istemci)):
        assert bekle(lambda: len(backend.basarili_putlar) == 1, sure=10)
    assert llm.cagrilar == []


def test_ayni_summaryid_yeni_surumle_degisir(tmp_path):
    k = Kuyruk(tmp_path / "k.sqlite3")
    assert k.ekle("s1", "r1", "a", []) == 1
    assert k.ekle("s1", "r2", "b", ["x"]) == 2
    assert len(k) == 1
    is_ = k.sonraki()
    assert (is_.request_id, is_.tani, is_.ilaclar, is_.surum) == ("r2", "b", ["x"], 2)
    assert not k.govde_kaydet("s1", 1, {"eski": True})    # eski sürümün sonucu yazılmaz
    assert k.govde_kaydet("s1", 2, {"yeni": True})
    k.kapat()


def test_kuyruk_dolu_503(kur):
    k = kur(isci_baslat=False, SADE_AZAMI_KUYRUK="2")
    assert k.gonder().status_code == 202 and k.gonder().status_code == 202
    r = k.gonder()
    assert r.status_code == 503 and r.json()["error"]["code"] == "QUEUE_FULL"


# ---------------------------------------------------------------- KVKK / log

def test_loglarda_hasta_verisi_yok(kur, caplog):
    caplog.set_level(logging.DEBUG)
    k = kur(llm=SahteSaglayici([TANSIYON]), backend=SahteBackend(put_yanitlari=[500]))
    g = govde(patientRef="hasta-ref-GIZLI-77", diagnosis="Yüksek tansiyon, NADIRHASTALIK-GIZLI",
              medications=["Amlodipin 5 mg 1x1", "GIZLIILAC 10 mg 2x1"])
    k.gonder(g)
    k.gonder(govde(diagnosis="x" * 2000, patientRef="hasta-ref-GIZLI-77"))   # doğrulama hatası yolu
    assert bekle(lambda: len(k.backend.basarili_putlar) == 1, sure=10)
    # Sahte backend PUT'u kaydettikten SONRA işçi "callback gönderildi" satırını yazar (isci.py): satırın
    # kendisi beklenir, yoksa iki iş parçacığı arasındaki yarış testi ara sıra düşürüyordu.
    assert bekle(lambda: "callback gönderildi" in caplog.text, sure=10)
    metin = caplog.text
    for gizli in ("hasta-ref-GIZLI-77", "NADIRHASTALIK", "GIZLIILAC", "Amlodipin", "tansiyon", "Lansor"):
        assert gizli not in metin, gizli
    assert g["requestId"] in metin and g["summaryId"] in metin    # izlenebilirlik
    assert "callback gönderildi" in metin and "ms=" in metin


def test_health_sir_icermez(kur):
    r = kur(llm=SahteSaglayici([])).client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert ANAHTAR not in r.text and r.json()["servisAnahtari"] is True
