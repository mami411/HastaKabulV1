"""ai-voice / orkestratör HTTP servisi (FastAPI, varsayılan port 8002).

Uçlar:
- POST /orchestrate  {sessionId, event?, userText?, data?{slotId}} → {action, replyText, message, data, requestId}
- POST /stt          multipart ``file`` (webm/opus) → {text, masked, durationSec}
- POST /tts          {text} → audio/wav
- GET  /health       bileşen durumu (sır içermez)

Hata yanıtları ortak zarfı taşır: {error:{code,message,requestId}}; /orchestrate hatalarında
ayrıca action/replyText bulunur ki kiosk sessiz kalmasın (D10).
KVKK: istek gövdesi, kullanıcı metni ve transkript loglanmaz.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from types import ModuleType
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from ortak.ayarlar import env_yukle
from ortak.llm import LLMSaglayici, LLMYapilandirmaHatasi, saglayici_olustur

from .alanlar import hasta_kabul
from .api_istemcisi import HastaKabulApi
from .ayarlar import SesAyarlari
from .gunluk import GovdeCokBuyuk, GovdeSiniri
from .orkestrator import Orkestrator, yanit_govdesi
from .stt import BulutStt, SttHatasi, YerelWhisper
from .tts import PiperTts, TtsHatasi
from .zaman import SaatKaynagi, simdi_utc

log = logging.getLogger("ses_servisi")

ISTEK_KIMLIGI_BASLIGI = "X-Request-Id"


def _istek_kimligi(request: Request) -> str:
    return getattr(request.state, "istek_kimligi", None) or uuid.uuid4().hex


def _hata_govdesi(kod: str, mesaj: str, rid: str, yanit_metni: str | None = None) -> dict[str, Any]:
    govde: dict[str, Any] = {"error": {"code": kod, "message": mesaj, "requestId": rid}}
    if yanit_metni:
        govde["replyText"] = yanit_metni
        govde["message"] = yanit_metni
    return govde


def _sunucu_zamanlamasi(zamanlama: dict[str, float]) -> str:
    return ", ".join(f"{ad};dur={ms:.1f}" for ad, ms in zamanlama.items())


def stt_olustur(a: SesAyarlari, alan: ModuleType) -> YerelWhisper | BulutStt:
    if a.stt_saglayici == "bulut":
        if not a.stt_bulut_url:
            raise ValueError("STT_SAGLAYICI=bulut için STT_BULUT_URL gerekli.")
        return BulutStt(taban_url=a.stt_bulut_url, anahtar=a.stt_bulut_anahtar, model=a.stt_model,
                        dil=a.stt_dil, ipucu=alan.STT_ANAHTAR_TERIMLER, hayalet_kaliplari=alan.HAYALET_KALIPLAR)
    return YerelWhisper(model=a.stt_model, cihaz=a.stt_cihaz, hesaplama=a.stt_hesaplama, dil=a.stt_dil,
                        isin=a.stt_isin, model_dizini=a.stt_model_dizini, ipucu=alan.STT_ANAHTAR_TERIMLER,
                        hayalet_kaliplari=alan.HAYALET_KALIPLAR,
                        cuda_dll_dizinleri=a.stt_cuda_dll_dizinleri)


def tts_onbellek_cumleleri(alan: ModuleType) -> frozenset[str]:
    """TTS önbelleğine girebilecek metinler (tts ön işlemesinden sonraki hâliyle): konu modülündeki
    sabit cümleler ve yalnız poliklinik listesiyle doldurulan şablonlar. Hasta/randevu verisi içeren
    hiçbir metin bu kümede olamaz."""
    liste = alan.poliklinik_listesi_metni()
    adaylar: list[str] = []
    for ad, deger in vars(alan).items():
        if not (ad.isupper() and isinstance(deger, str)):
            continue
        try:
            adaylar.append(deger.format(liste=liste) if "{" in deger else deger)
        except (KeyError, IndexError, ValueError):
            continue
    adaylar += ["Sizi tam anlayamadım. " + c for c in list(adaylar)]
    if hasattr(alan, "ANLASILAMADI_EKRAN"):
        adaylar += [alan.ANLASILAMADI_EKRAN.format(ornek=o) for o in alan.ANLASILAMADI_ORNEKLERI.values()]
    return frozenset(alan.tts_on_isleme(c) for c in adaylar)


def uygulama_olustur(ayarlar: SesAyarlari | None = None, *, alan: ModuleType = hasta_kabul,
                     llm: LLMSaglayici | None | str = "ortamdan", api: HastaKabulApi | None = None,
                     stt: Any = None, tts: Any = None, saat: SaatKaynagi = simdi_utc,
                     onceden_yukle: bool | None = None) -> FastAPI:
    """Bağımlılıklar testlerde dışarıdan verilebilir (sahte LLM, sahte API, sahte STT/TTS)."""
    if ayarlar is None:
        env_yukle()
        ayarlar = SesAyarlari.ortamdan()
    a = ayarlar

    if llm == "ortamdan":
        llm_nesnesi: LLMSaglayici | None = None
        if a.llm_etkin:
            try:
                llm_nesnesi = saglayici_olustur(a.llm_onek)
            except LLMYapilandirmaHatasi as hata:
                # D10: LLM yoksa servis yine açılır; akış kural tabanlı anlamayla sürer.
                log.warning("LLM devre dışı: %s", hata)
    else:
        llm_nesnesi = llm  # type: ignore[assignment]
    # Genel sağlık bilgisi için isteğe bağlı ayrı model (SAGLIK_LLM_MODEL); yoksa anlama LLM'i.
    saglik_llm_nesnesi: LLMSaglayici | None = None
    if llm == "ortamdan" and a.saglik_bilgi_llm and a.saglik_llm_model and a.llm_etkin:
        try:
            saglik_llm_nesnesi = saglayici_olustur("SAGLIK_LLM")
        except LLMYapilandirmaHatasi as hata:
            log.warning("Sağlık bilgisi için ayrı LLM kurulamadı, anlama LLM'i kullanılacak: %s", hata)

    api_nesnesi = api or HastaKabulApi(a.api_url, a.servis_anahtari, zaman_asimi_sn=a.api_zaman_asimi_sn,
                                       is_mantigi_hatalari=alan.IS_MANTIGI_HATALARI)
    stt_nesnesi = stt or stt_olustur(a, alan)
    # TTS önbelleğine yalnız konu modülündeki sabit cümleler girer (kişisel veri içermez).
    sabit_cumleler = tts_onbellek_cumleleri(alan)
    tts_nesnesi = tts or PiperTts(model_yolu=a.tts_model_yolu, konusma_hizi=a.tts_konusma_hizi, cuda=a.tts_cuda,
                                  onbellek_suzgeci=sabit_cumleler.__contains__)
    orkestrator = Orkestrator(
        alan=alan, api=api_nesnesi, llm=llm_nesnesi, saat=saat, oturum_omru_sn=a.oturum_omru_dk * 60,
        azami_oturum=a.azami_oturum, arama_gun_sayisi=a.arama_gun_sayisi,
        sunulan_slot_sayisi=a.sunulan_slot_sayisi, slot_min_once_dk=a.slot_min_once_dk,
        azami_metin_uzunlugu=a.azami_metin_uzunlugu, anlama_zaman_asimi_sn=a.anlama_zaman_asimi_sn,
        teshis_logu=a.teshis_logu, bosta_kalma_sn=a.bosta_kalma_sn,
        alternatif_gun_sayisi=a.alternatif_gun_sayisi, randevu_ufuk_gun=a.randevu_ufuk_gun,
        gecmis_sesli=a.gecmis_sesli_okuma,
        gecmis_ikinci_faktor=a.gecmis_ikinci_faktor, gecmis_limit=a.gecmis_limit,
        ilac_konu_llm=a.ilac_konu_llm,
        saglik_bilgi=a.saglik_bilgi_llm, saglik_bilgi_zaman_asimi_sn=a.saglik_bilgi_zaman_asimi_sn,
        saglik_llm=saglik_llm_nesnesi,
    )
    yukle = (a.stt_onceden_yukle or a.tts_onceden_yukle) if onceden_yukle is None else onceden_yukle

    @asynccontextmanager
    async def omur(app: FastAPI):
        if not a.servis_anahtari:
            log.warning("SERVICE_KEY tanımlı değil: slot ve randevu çağrıları yapılamaz.")
        gorevler = []
        if yukle:
            async def arka_planda(ad: str, nesne: Any) -> None:
                try:
                    await asyncio.to_thread(nesne.yukle)
                except Exception as exc:  # noqa: BLE001 — servis açık kalır, uç 503 döner
                    log.error("%s önceden yüklenemedi: %s", ad, exc)
            if a.stt_onceden_yukle:
                gorevler.append(asyncio.create_task(arka_planda("STT", stt_nesnesi)))
            if a.tts_onceden_yukle:
                gorevler.append(asyncio.create_task(arka_planda("TTS", tts_nesnesi)))
        yield
        for g in gorevler:
            g.cancel()
        await api_nesnesi.kapat()
        if llm_nesnesi is not None:
            await llm_nesnesi.kapat()
        if saglik_llm_nesnesi is not None:
            await saglik_llm_nesnesi.kapat()

    app = FastAPI(title="HastaKabul ai-voice", version="1.0.0", lifespan=omur,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.orkestrator = orkestrator
    app.state.ayarlar = a

    # Gövde boyutu sınırı (O9): uç çalışmadan / okuma sırasında 413. CORS'un İÇİNDE durur ki
    # 413 yanıtı da CORS başlıklarını taşısın (yoksa tarayıcı bunu ağ hatası sanar).
    def _govde_siniri(yol: str) -> int | None:
        if yol in ("/orchestrate", "/tts"):
            return a.azami_json_govde_bayt
        if yol == "/stt":
            return a.stt_azami_bayt + 64 * 1024  # multipart zarfı payı
        return None

    app.add_middleware(GovdeSiniri, sinir=_govde_siniri)
    app.add_middleware(
        CORSMiddleware, allow_origins=a.cors_kaynaklari, allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", ISTEK_KIMLIGI_BASLIGI], expose_headers=[ISTEK_KIMLIGI_BASLIGI, "Server-Timing"],
    )

    @app.middleware("http")
    async def istek_kaydi(request: Request, call_next):
        gelen = request.headers.get(ISTEK_KIMLIGI_BASLIGI, "")
        rid = gelen if gelen.isalnum() and 8 <= len(gelen) <= 64 else uuid.uuid4().hex
        request.state.istek_kimligi = rid
        baslangic = time.perf_counter()
        try:
            yanit = await call_next(request)
        except GovdeCokBuyuk:
            raise  # GovdeSiniri 413'e çevirir
        except Exception:
            log.exception("beklenmeyen hata requestId=%s path=%s", rid, request.url.path)
            yanit = JSONResponse(_hata_govdesi("INTERNAL_ERROR", "Beklenmeyen bir hata oluştu.", rid,
                                               getattr(alan, "BEKLENMEYEN_HATA", "Beklenmeyen bir sorun "
                                                       "oluştu. Lütfen tekrar deneyin.")), status_code=500)
        yanit.headers[ISTEK_KIMLIGI_BASLIGI] = rid
        # Log satırı: yalnız yöntem, yol, durum, süre, istek kimliği (gövde asla).
        log.info("istek method=%s path=%s status=%s ms=%.0f requestId=%s", request.method, request.url.path,
                 yanit.status_code, (time.perf_counter() - baslangic) * 1000, rid)
        return yanit

    def _json_degil(request: Request, rid: str) -> JSONResponse | None:
        # text/plain gibi "basit" istekler tarayıcıdan ön uçuşsuz (CSRF) gönderilebilir (O6).
        tur = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if tur != "application/json":
            return JSONResponse(_hata_govdesi("VALIDATION_FAILED", "Content-Type application/json olmalı.", rid,
                                              "İstek anlaşılamadı. Lütfen işlemi yeniden başlatın."),
                                status_code=415)
        return None

    @app.get("/health")
    async def saglik() -> dict[str, Any]:
        return {
            "status": "ok",
            "llm": {"etkin": llm_nesnesi is not None,
                    "saglayici": getattr(llm_nesnesi, "ad", None), "model": getattr(llm_nesnesi, "model", None)},
            "stt": {"hazir": bool(getattr(stt_nesnesi, "hazir", False)), "cihaz": getattr(stt_nesnesi, "cihaz", ""),
                    "model": getattr(stt_nesnesi, "model_adi", "")},
            "tts": {"hazir": bool(getattr(tts_nesnesi, "hazir", False))},
            "servisAnahtari": bool(a.servis_anahtari),
            "gecmis": {"sesliOkuma": a.gecmis_sesli_okuma, "ikinciFaktor": a.gecmis_ikinci_faktor},
            "saglikBilgisi": {"etkin": orkestrator.saglik_llm is not None,
                              "model": getattr(orkestrator.saglik_llm, "model", None)},
            "oturumSayisi": len(orkestrator.depo),
        }

    @app.post("/orchestrate")
    async def orkestre_et(request: Request) -> Response:
        rid = _istek_kimligi(request)
        if (red := _json_degil(request, rid)) is not None:
            return red
        try:
            govde = await request.json()
        except ValueError:
            govde = None
        sonuc = await orkestrator.isle(govde, rid)  # type: ignore[arg-type]
        basliklar = {"Server-Timing": _sunucu_zamanlamasi(sonuc.zamanlama)} if sonuc.zamanlama else {}
        if sonuc.anlama_kaynagi:
            basliklar["X-Anlama-Kaynagi"] = sonuc.anlama_kaynagi
        return JSONResponse(sonuc.govde, status_code=sonuc.durum_kodu, headers=basliklar)

    @app.post("/stt")
    async def konusmadan_metne(request: Request, file: UploadFile | None = File(default=None)) -> Response:
        rid = _istek_kimligi(request)
        anlasilmadi = "Sesinizi yazıya çeviremedim. Lütfen tekrar deneyin ya da mesajınızı yazın."
        if file is None:
            return JSONResponse(_hata_govdesi("VALIDATION_FAILED", "multipart 'file' alanı gerekli.", rid,
                                              anlasilmadi), status_code=400)
        veri = await file.read(a.stt_azami_bayt + 1)
        if len(veri) > a.stt_azami_bayt:
            return JSONResponse(_hata_govdesi("VALIDATION_FAILED", "Ses dosyası çok büyük.", rid, anlasilmadi),
                                status_code=413)
        if not veri:
            return JSONResponse(_hata_govdesi("VALIDATION_FAILED", "Ses dosyası boş.", rid, anlasilmadi),
                                status_code=400)
        try:
            sonuc = await stt_nesnesi.cozumle(veri, azami_sn=a.stt_azami_saniye, zaman_asimi_sn=a.stt_zaman_asimi_sn)
        except SttHatasi as hata:
            log.warning("STT başarısız: durum=%s neden=%s requestId=%s", hata.durum, hata, rid)
            kod = "VALIDATION_FAILED" if hata.durum in (400, 413) else "STT_FAILED"
            return JSONResponse(_hata_govdesi(kod, str(hata), rid, anlasilmadi), status_code=hata.durum)
        return JSONResponse(
            {"text": sonuc.metin, "masked": sonuc.maskelendi, "durationSec": round(sonuc.sure_ses_sn, 2),
             "requestId": rid},
            headers={"Server-Timing": f"stt;dur={sonuc.sure_ms:.1f}"},
        )

    @app.post("/tts")
    async def metinden_sese(request: Request) -> Response:
        rid = _istek_kimligi(request)
        if (red := _json_degil(request, rid)) is not None:
            return red
        try:
            govde = await request.json()
        except ValueError:
            govde = None
        metin = govde.get("text") if isinstance(govde, dict) else None
        if not isinstance(metin, str) or not metin.strip():
            return JSONResponse(_hata_govdesi("VALIDATION_FAILED", "'text' alanı gerekli.", rid), status_code=400)
        if len(metin) > a.tts_azami_karakter:
            return JSONResponse(_hata_govdesi("VALIDATION_FAILED", "Metin çok uzun.", rid), status_code=413)
        hazir_metin = alan.tts_on_isleme(metin.strip())
        try:
            sonuc = await tts_nesnesi.sentezle(hazir_metin, zaman_asimi_sn=a.tts_zaman_asimi_sn)
        except TtsHatasi as hata:
            log.warning("TTS başarısız: durum=%s neden=%s requestId=%s", hata.durum, hata, rid)
            # Kiosk metni ekranda zaten gösterir; burada anlamlı hata döner, sessizlik değil.
            return JSONResponse(_hata_govdesi("TTS_FAILED", "Sesli yanıt şu an üretilemiyor.", rid,
                                              "Sesli yanıt şu an kullanılamıyor; yanıtı ekranda okuyabilirsiniz."),
                                status_code=hata.durum)
        return Response(sonuc.wav, media_type="audio/wav", headers={
            "Server-Timing": f"tts;dur={sonuc.sure_ms:.1f}", "X-Onbellek": "1" if sonuc.onbellekten else "0",
            "Cache-Control": "no-store"})

    return app


__all__ = ["uygulama_olustur", "yanit_govdesi"]
