"""ai-simplify HTTP servisi (FastAPI, varsayılan port 8001).

Uçlar:
- POST /simplify   (X-Service-Key) {patientRef, diagnosis, medications[], requestId, summaryId} → 202
                   İş kalıcı kuyruğa yazılır ve HEMEN 202 döner (backend 10 sn bekler, gövdeyi okumaz);
                   özet arka planda üretilip PUT {API}/api/v1/summaries/{summaryId} ile geri gönderilir.
- GET  /health     bileşen durumu (sır ve hasta verisi içermez)

Hata zarfı: {error:{code,message,requestId}} — 400 VALIDATION_FAILED, 401 UNAUTHORIZED,
413 PAYLOAD_TOO_LARGE, 503 SERVICE_KEY_MISSING / QUEUE_FULL. Backend 2xx dışındaki her yanıtta
kendi yedeğine (seed özeti ya da LLM_UNAVAILABLE) düşer.
KVKK: istek gövdesi, patientRef, tanı ve ilaçlar loglanmaz; doğrulama hatası girdiyi geri yansıtmaz.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ortak.ayarlar import env_yukle
from ortak.llm import LLMSaglayici, LLMYapilandirmaHatasi, saglayici_olustur

from .ayarlar import SadeAyarlari
from .backend import BackendIstemcisi
from .bilgi_tabani import BilgiTabani
from .isci import Isci
from .kuyruk import Kuyruk
from .uretici import HATA_GENEL, Sadelestirici, hata_govdesi

log = logging.getLogger("sade_servisi")

ISTEK_KIMLIGI_BASLIGI = "X-Request-Id"
SERVIS_ANAHTARI_BASLIGI = "X-Service-Key"
GUID_DESENI = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
AZAMI_GOVDE_BAYT = 64 * 1024


def _hata(durum: int, kod: str, mesaj: str, rid: str, ayrinti: list[dict[str, Any]] | None = None) -> JSONResponse:
    govde: dict[str, Any] = {"error": {"code": kod, "message": mesaj, "requestId": rid}}
    if ayrinti:
        govde["error"]["details"] = ayrinti
    return JSONResponse(govde, status_code=durum)


def istek_modeli(a: SadeAyarlari) -> type[BaseModel]:
    """Sınırları ayarlardan alan istek modeli. Fazla alan reddedilir (yalnız beyaz liste)."""

    IlacSatiri = Annotated[str, StringConstraints(max_length=a.azami_ilac_uzunlugu)]
    # İlaç SAYISI için sert sınır yumuşak sınırın üstündedir: backend ilaç sayısını sınırlamaz ve 4xx
    # alırsa sessizce kendi yedeğine (girdiyle ilgisiz seed özeti olabilir) düşer. Yumuşak sınır
    # aşılırsa istek kabul edilir ve özet SIMPLIFY_FAILED ile kapanır (hekim görür).

    class SadelestirmeIstegi(BaseModel):
        model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

        patientRef: str = Field(min_length=1, max_length=64)
        diagnosis: str = Field(min_length=1, max_length=a.azami_tani)
        medications: list[IlacSatiri] = Field(max_length=max(a.azami_ilac, 200))
        requestId: str = Field(min_length=1, max_length=128)
        summaryId: str = Field(pattern=GUID_DESENI)

    return SadelestirmeIstegi


def uygulama_olustur(ayarlar: SadeAyarlari | None = None, *, llm: LLMSaglayici | None | str = "ortamdan",
                     backend: BackendIstemcisi | None = None, kuyruk: Kuyruk | None = None,
                     kb_getirici: Any = "backend", isci_baslat: bool = True) -> FastAPI:
    """Bağımlılıklar testlerde dışarıdan verilebilir (sahte LLM, sahte backend, geçici kuyruk)."""
    if ayarlar is None:
        env_yukle()
        ayarlar = SadeAyarlari.ortamdan()
    a = ayarlar

    if llm == "ortamdan":
        llm_nesnesi: LLMSaglayici | None = None
        if a.llm_etkin:
            try:
                llm_nesnesi = saglayici_olustur(a.llm_onek)
            except LLMYapilandirmaHatasi as hata:
                # LLM yoksa servis yine açılır: tüm terimleri bilinen girdiler şablonla özetlenir.
                log.warning("LLM devre dışı: %s", hata)
    else:
        llm_nesnesi = llm  # type: ignore[assignment]

    backend_nesnesi = backend or BackendIstemcisi(a.api_url, a.servis_anahtari, zaman_asimi_sn=a.api_zaman_asimi_sn)
    kuyruk_nesnesi = kuyruk or Kuyruk(a.kuyruk_dosyasi)
    getirici = backend_nesnesi.kb_getir if kb_getirici == "backend" else kb_getirici
    kb = BilgiTabani(getirici, kip=a.kb_kaynagi, yenileme_sn=a.kb_yenileme_sn)
    sadelestirici = Sadelestirici(llm_nesnesi, zaman_asimi_sn=a.uretim_zaman_asimi_sn, azami_token=a.azami_token)
    isci = Isci(kuyruk_nesnesi, sadelestirici, kb, backend_nesnesi, azami_deneme=a.geri_cagri_azami_deneme,
                taban_bekleme_sn=a.geri_cagri_taban_bekleme_sn, azami_bekleme_sn=a.geri_cagri_azami_bekleme_sn)
    Model = istek_modeli(a)

    @asynccontextmanager
    async def omur(app: FastAPI):
        if not a.servis_anahtari:
            log.warning("SERVICE_KEY tanımlı değil: /simplify 503 döner, callback gönderilemez.")
        gorevler: list[asyncio.Task] = []
        if isci_baslat:
            bekleyen = len(kuyruk_nesnesi)
            if bekleyen:
                log.info("kuyrukta yarım kalmış %d iş var, işlenecek", bekleyen)
            gorevler.append(asyncio.create_task(kb.kayitlar()))   # KB'yi önceden yükle (hata gömülüye düşer)
            gorevler.append(asyncio.create_task(isci.calis()))
        yield
        for gorev in gorevler:
            gorev.cancel()
            try:
                await gorev
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await backend_nesnesi.kapat()
        if llm_nesnesi is not None:
            await llm_nesnesi.kapat()
        if kuyruk is None:
            kuyruk_nesnesi.kapat()

    app = FastAPI(title="HastaKabul ai-simplify", version="1.0.0", lifespan=omur,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ayarlar = a
    app.state.isci = isci
    app.state.kuyruk = kuyruk_nesnesi
    app.state.kb = kb
    app.state.sadelestirici = sadelestirici

    @app.middleware("http")
    async def istek_kaydi(request: Request, call_next):
        gelen = request.headers.get(ISTEK_KIMLIGI_BASLIGI, "")
        rid = gelen if gelen.replace("-", "").isalnum() and 8 <= len(gelen) <= 64 else uuid.uuid4().hex
        request.state.istek_kimligi = rid
        baslangic = time.perf_counter()
        try:
            yanit = await call_next(request)
        except Exception:  # noqa: BLE001
            log.error("beklenmeyen hata requestId=%s path=%s", rid, request.url.path)
            yanit = _hata(500, "INTERNAL_ERROR", "Beklenmeyen bir hata oluştu.", rid)
        yanit.headers[ISTEK_KIMLIGI_BASLIGI] = rid
        # Log satırı: yalnız yöntem, yol, durum, süre, istek kimliği (gövde asla).
        log.info("istek method=%s path=%s status=%s ms=%.1f requestId=%s", request.method, request.url.path,
                 yanit.status_code, (time.perf_counter() - baslangic) * 1000, rid)
        return yanit

    @app.get("/health")
    async def saglik() -> dict[str, Any]:
        d = kb.durum
        return {
            "status": "ok",
            "llm": {"etkin": llm_nesnesi is not None, "saglayici": getattr(llm_nesnesi, "ad", None),
                    "model": getattr(llm_nesnesi, "model", None)},
            "kb": {"kaynak": d.kaynak, "kayitSayisi": len(d.kayitlar), "sonHata": d.son_hata},
            "kuyruk": {"bekleyen": len(kuyruk_nesnesi), "durumlar": kuyruk_nesnesi.durumlar()},
            "sayaclar": dict(isci.sayaclar),
            "servisAnahtari": bool(a.servis_anahtari),
        }

    @app.post("/simplify")
    async def sadelestir(request: Request) -> Response:
        rid = request.state.istek_kimligi
        if not a.servis_anahtari:
            return _hata(503, "SERVICE_KEY_MISSING", "Servis anahtarı yapılandırılmamış.", rid)
        verilen = request.headers.get(SERVIS_ANAHTARI_BASLIGI, "")
        if not hmac.compare_digest(verilen.encode("utf-8"), a.servis_anahtari.encode("utf-8")):
            return _hata(401, "UNAUTHORIZED", "Geçersiz servis anahtarı.", rid)

        ham = await request.body()
        if len(ham) > AZAMI_GOVDE_BAYT:
            return _hata(413, "PAYLOAD_TOO_LARGE", "İstek gövdesi çok büyük.", rid)
        try:
            veri = json.loads(ham or b"null")
            istek = Model.model_validate(veri)
        except (ValueError, ValidationError) as exc:
            # Ayrıntıda yalnız alan yolu ve hata türü var; girdi değeri geri yansıtılmaz.
            ayrinti = ([{"field": ".".join(str(p) for p in e["loc"]), "type": e["type"]} for e in exc.errors()]
                       if isinstance(exc, ValidationError) else [{"field": "", "type": "json_invalid"}])
            return _hata(400, "VALIDATION_FAILED", "İstek gövdesi geçersiz.", rid, ayrinti[:20])

        if len(kuyruk_nesnesi) >= a.azami_kuyruk:
            return _hata(503, "QUEUE_FULL", "Kuyruk dolu; daha sonra tekrar deneyin.", rid)
        ilaclar = [m for m in istek.medications if m]
        if len(ilaclar) > a.azami_ilac:
            log.warning("ilaç sayısı sınırı aşıldı, SIMPLIFY_FAILED gönderilecek requestId=%s summaryId=%s adet=%d",
                        rid, istek.summaryId.lower(), len(ilaclar))
            surum = kuyruk_nesnesi.ekle_hazir(istek.summaryId.lower(), istek.requestId, hata_govdesi(HATA_GENEL))
        else:
            surum = kuyruk_nesnesi.ekle(istek.summaryId.lower(), istek.requestId, istek.diagnosis, ilaclar)
        isci.uyandir()
        return JSONResponse({"accepted": True, "summaryId": istek.summaryId.lower(), "requestId": istek.requestId,
                             "version": surum}, status_code=202)

    return app


__all__ = ["uygulama_olustur", "istek_modeli"]
