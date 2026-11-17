"""HastaKabul API istemcisi (yalnız bu servis kullanır; servis anahtarı tarayıcıya gitmez).

Uçlar (hasta-kabul-backend):
- GET  /api/v1/voice-sessions/{id}            → {sessionId, verified, attempts, maxAttempts, status}
- GET  /api/v1/slots?clinic=&date=YYYY-MM-DD   (X-Service-Key) → [{slotId, startTime(UTC), doctorDisplayName}]
- POST /api/v1/appointments {voiceSessionId, slotId} (X-Service-Key)
       → {appointmentId, slotId, scheduledAt, doctorDisplayName, clinicName, status, accessCode}
- GET  /api/v1/voice-sessions/{id}/medical-history?limit=3 (X-Service-Key) → {visits:[…]}
Hata zarfı: {error:{code,message,requestId}}.

Hata sınıflandırması (halisaha ``api_client.sistem_hatasi_mi`` uyarlaması): iş-mantığı
kodları kapalı bir listedir (konu modülünde); ağ hatası, zaman aşımı, 5xx, 401 ve
bilinmeyen her kod SİSTEM hatasıdır ve kullanıcıya "dolu/yok" diye yansıtılmaz.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class ApiHatasi(Exception):
    durum: int            # HTTP durum kodu; ağ hatasında 0
    kod: str              # backend hata kodu ya da NETWORK_ERROR / TIMEOUT / UNKNOWN
    mesaj: str = ""
    istek_kimligi: str = ""
    sistem_hatasi: bool = True
    zaman_asimi: bool = False

    def __str__(self) -> str:  # mesajda hasta verisi yok; yalnız kod ve durum
        return f"API {self.durum} {self.kod}"


@dataclass
class ApiOlcum:
    cagri_sayisi: int = 0
    toplam_ms: float = 0.0
    ayrinti: list[tuple[str, float]] = field(default_factory=list)


class HastaKabulApi:
    def __init__(self, taban_url: str, servis_anahtari: str | None, *, zaman_asimi_sn: float = 8.0,
                 is_mantigi_hatalari: frozenset[str] = frozenset(),
                 transport: httpx.AsyncBaseTransport | None = None):
        self.taban_url = taban_url.rstrip("/")
        self._anahtar = servis_anahtari
        self._is_mantigi = is_mantigi_hatalari
        self._istemci = httpx.AsyncClient(base_url=self.taban_url, timeout=zaman_asimi_sn,
                                          transport=transport)

    async def kapat(self) -> None:
        await self._istemci.aclose()

    async def _istek(self, yontem: str, yol: str, *, istek_kimligi: str, servis_anahtari: bool = False,
                     olcum: ApiOlcum | None = None, **kw: Any) -> Any:
        basliklar = {"X-Request-Id": istek_kimligi, "Accept": "application/json"}
        if servis_anahtari:
            if not self._anahtar:
                # Yapılandırma hatası: istek gönderilmez, sistem hatası sayılır.
                raise ApiHatasi(0, "SERVICE_KEY_MISSING", "SERVICE_KEY tanımlı değil.", istek_kimligi)
            basliklar["X-Service-Key"] = self._anahtar
        baslangic = time.perf_counter()
        try:
            yanit = await self._istemci.request(yontem, yol, headers=basliklar, **kw)
        except httpx.TimeoutException as exc:
            raise ApiHatasi(0, "TIMEOUT", "API zaman aşımı.", istek_kimligi, zaman_asimi=True) from exc
        except httpx.TransportError as exc:
            raise ApiHatasi(0, "NETWORK_ERROR", "API'ye ulaşılamadı.", istek_kimligi) from exc
        finally:
            if olcum is not None:
                gecen = (time.perf_counter() - baslangic) * 1000
                olcum.cagri_sayisi += 1
                olcum.toplam_ms += gecen
                olcum.ayrinti.append((f"{yontem} {yol.split('?')[0]}", gecen))
        if yanit.status_code >= 400:
            kod, mesaj, rid = "UNKNOWN", "", istek_kimligi
            try:
                govde = yanit.json()
                if isinstance(govde, dict) and "error" not in govde and "title" in govde:
                    # ASP.NET ProblemDetails (model doğrulama 400 / Content-Type yok 415): hata zarfı
                    # değildir; iş kuralı sayılmaz, sistem hatası olarak ele alınır.
                    kod, mesaj = "PROBLEM_DETAILS", str(govde.get("title") or "")[:200]
                    rid = str(govde.get("traceId") or rid)
                else:
                    # Güncel backend her hatada (401/403/404/405/415 ve beklenmeyen 500 INTERNAL_ERROR dahil)
                    # {error:{code,message,requestId}} zarfı döner; eski ProblemDetails desteği yukarıda kalır.
                    hata = govde.get("error")
                    if not isinstance(hata, dict):
                        hata = {}
                    kod = str(hata.get("code") or kod)
                    mesaj = str(hata.get("message") or "")
                    rid = str(hata.get("requestId") or rid)
            except (ValueError, AttributeError):
                pass
            sistem = kod not in self._is_mantigi or yanit.status_code >= 500
            raise ApiHatasi(yanit.status_code, kod, mesaj, rid, sistem_hatasi=sistem)
        if yanit.status_code == 204:
            return None
        try:
            return yanit.json()
        except ValueError as exc:
            raise ApiHatasi(yanit.status_code, "INVALID_BODY", "API yanıtı JSON değil.", istek_kimligi) from exc

    async def oturum_getir(self, oturum_id: str, *, istek_kimligi: str,
                           olcum: ApiOlcum | None = None) -> dict[str, Any]:
        veri = await self._istek("GET", f"/api/v1/voice-sessions/{oturum_id}",
                                 istek_kimligi=istek_kimligi, olcum=olcum)
        if not isinstance(veri, dict) or "status" not in veri:
            raise ApiHatasi(200, "INVALID_BODY", "Oturum yanıtı beklenen biçimde değil.", istek_kimligi)
        return veri

    async def slotlar(self, poliklinik: str, gun_iso: str, *, istek_kimligi: str,
                      olcum: ApiOlcum | None = None) -> list[dict[str, Any]]:
        veri = await self._istek("GET", "/api/v1/slots", params={"clinic": poliklinik, "date": gun_iso},
                                 istek_kimligi=istek_kimligi, servis_anahtari=True, olcum=olcum)
        if not isinstance(veri, list):
            raise ApiHatasi(200, "INVALID_BODY", "Slot yanıtı liste değil.", istek_kimligi)
        return [s for s in veri if isinstance(s, dict) and s.get("slotId") and s.get("startTime")]

    async def gecmis_getir(self, oturum_id: str, *, limit: int = 3, istek_kimligi: str,
                           olcum: ApiOlcum | None = None) -> dict[str, Any]:
        """Hekim onaylı geçmiş muayene özetleri (en yeniden eskiye). Oturum VERIFIED/COMPLETED olmalı.

        Yanıt: {visits:[{appointmentId, scheduledAt(UTC), clinicName, doctorDisplayName, diagnosisPlain,
        medications:[{name,purpose,usage}], warnings:[...]}]}. Gövde loglanmaz.
        """
        veri = await self._istek("GET", f"/api/v1/voice-sessions/{oturum_id}/medical-history",
                                 params={"limit": limit}, istek_kimligi=istek_kimligi, servis_anahtari=True,
                                 olcum=olcum)
        if not isinstance(veri, dict) or not isinstance(veri.get("visits"), list):
            raise ApiHatasi(200, "INVALID_BODY", "Geçmiş yanıtı beklenen biçimde değil.", istek_kimligi)
        return veri

    async def randevu_olustur(self, oturum_id: str, slot_id: str, *, istek_kimligi: str,
                              olcum: ApiOlcum | None = None) -> dict[str, Any]:
        veri = await self._istek("POST", "/api/v1/appointments",
                                 json={"voiceSessionId": oturum_id, "slotId": slot_id},
                                 istek_kimligi=istek_kimligi, servis_anahtari=True, olcum=olcum)
        if not isinstance(veri, dict) or not veri.get("accessCode"):
            raise ApiHatasi(200, "INVALID_BODY", "Randevu yanıtı beklenen biçimde değil.", istek_kimligi)
        return veri
