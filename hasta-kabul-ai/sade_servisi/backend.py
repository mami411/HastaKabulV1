"""HastaKabul API istemcisi (yalnız bu servis; servis anahtarı başka yere gitmez).

- ``GET  /api/v1/kb``                    (X-Service-Key) → [{id, term, title, body, kind, synonyms}]
- ``PUT  /api/v1/summaries/{summaryId}`` (X-Service-Key) → başarı gövdesi ya da {errorCode}

Callback yanıtı sınıflandırılır:
- 2xx → ``BASARILI``
- 400 (VALIDATION_FAILED) → ``GECERSIZ``: gövdemiz reddedildi; çağıran bir kez SIMPLIFY_FAILED dener
- 404 (özet yok), 409 (özet onaylanmış), diğer 4xx → ``VAZGEC``: tekrar denemenin anlamı yok
- 401/403 (anahtar yanlış/eksik — yapılandırma düzelebilir), 408, 429, 5xx, ağ, zaman aşımı → ``TEKRAR``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx


class GeriCagriSonucu(str, Enum):
    BASARILI = "basarili"
    TEKRAR = "tekrar"
    GECERSIZ = "gecersiz"
    VAZGEC = "vazgec"


@dataclass
class BackendHatasi(Exception):
    durum: int      # HTTP durumu; ağ hatasında 0
    kod: str

    def __str__(self) -> str:  # içerik yok: yalnız durum ve kod
        return f"API {self.durum} {self.kod}"


_ISTEK_KIMLIGI = re.compile(r"[^A-Za-z0-9._:-]")


def istek_kimligi_temizle(rid: str) -> str:
    return _ISTEK_KIMLIGI.sub("", rid)[:64] or "yok"


class BackendIstemcisi:
    def __init__(self, taban_url: str, servis_anahtari: str | None, *, zaman_asimi_sn: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.taban_url = taban_url.rstrip("/")
        self._anahtar = servis_anahtari
        self._istemci = httpx.AsyncClient(base_url=self.taban_url, timeout=zaman_asimi_sn, transport=transport)

    async def kapat(self) -> None:
        await self._istemci.aclose()

    def _basliklar(self, istek_kimligi: str) -> dict[str, str]:
        if not self._anahtar:
            raise BackendHatasi(0, "SERVICE_KEY_MISSING")
        return {"X-Service-Key": self._anahtar, "X-Request-Id": istek_kimligi_temizle(istek_kimligi),
                "Accept": "application/json"}

    @staticmethod
    def _kod(yanit: httpx.Response) -> str:
        try:
            return str((yanit.json().get("error") or {}).get("code") or "UNKNOWN")[:40]
        except (ValueError, AttributeError):
            return "UNKNOWN"

    async def kb_getir(self, istek_kimligi: str = "sade-kb") -> list[dict[str, Any]]:
        try:
            yanit = await self._istemci.get("/api/v1/kb", headers=self._basliklar(istek_kimligi))
        except httpx.TimeoutException as exc:
            raise BackendHatasi(0, "TIMEOUT") from exc
        except httpx.TransportError as exc:
            raise BackendHatasi(0, "NETWORK_ERROR") from exc
        if yanit.status_code != 200:
            raise BackendHatasi(yanit.status_code, self._kod(yanit) if yanit.content else "EMPTY")
        try:
            veri = yanit.json()
        except ValueError as exc:
            raise BackendHatasi(200, "INVALID_BODY") from exc
        if not isinstance(veri, list):
            raise BackendHatasi(200, "INVALID_BODY")
        return veri

    async def ozet_gonder(self, summary_id: str, govde: dict[str, Any],
                          istek_kimligi: str) -> tuple[GeriCagriSonucu, int, str]:
        """Callback gönderir. Dönüş: (sonuç sınıfı, HTTP durumu, hata kodu)."""
        try:
            basliklar = self._basliklar(istek_kimligi)
        except BackendHatasi as hata:
            return GeriCagriSonucu.TEKRAR, 0, hata.kod
        try:
            yanit = await self._istemci.put(f"/api/v1/summaries/{summary_id}", json=govde, headers=basliklar)
        except httpx.TimeoutException:
            return GeriCagriSonucu.TEKRAR, 0, "TIMEOUT"
        except httpx.TransportError:
            return GeriCagriSonucu.TEKRAR, 0, "NETWORK_ERROR"
        kod = yanit.status_code
        if 200 <= kod < 300:
            return GeriCagriSonucu.BASARILI, kod, ""
        hata_kodu = self._kod(yanit)
        if kod in (401, 403, 408, 429) or kod >= 500:
            return GeriCagriSonucu.TEKRAR, kod, hata_kodu
        if kod == 400:
            return GeriCagriSonucu.GECERSIZ, kod, hata_kodu
        return GeriCagriSonucu.VAZGEC, kod, hata_kodu
