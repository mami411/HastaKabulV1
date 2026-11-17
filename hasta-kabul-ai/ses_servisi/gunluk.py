"""Log güvenliği ve istek gövdesi sınırı.

- ``OturumKimligiMaskesi``: log satırlarındaki UUID'leri (ses oturumu kimliği randevu almaya
  yeten tek yetkidir) "xxxxxxxx-…-1a2b" biçimine indirir. Biçimlenmiş mesaja ve istisna
  metnine (traceback'teki URL'ler dahil) uygulanır; handler'a takılır.
- ``GovdeSiniri``: saf ASGI ara katmanı. ``Content-Length`` sınırı aşıyorsa uç hiç
  çalışmadan 413 döner; başlık yoksa (chunked) okunan baytlar sayılır ve sınır aşılınca
  ``GovdeCokBuyuk`` fırlatılıp 413'e çevrilir.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

# Yalnız tireli UUID (oturum kimliği biçimi); tiresiz 32 hex istek kimlikleri izlenebilirlik için kalır.
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-([0-9a-fA-F]{8})([0-9a-fA-F]{4})\b")


def kimlikleri_maskele(metin: str) -> str:
    return _UUID.sub(lambda m: f"********-…-{m.group(2)}", metin)


class OturumKimligiMaskesi(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            mesaj = record.getMessage()
        except Exception:  # noqa: BLE001 — biçimlenemeyen kayıt olduğu gibi kalır
            return True
        record.msg, record.args = kimlikleri_maskele(mesaj), None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = kimlikleri_maskele(record.exc_text)
        return True


def log_maskesini_kur(logger: logging.Logger | None = None) -> None:
    """Kök logger'ın (ya da verilenin) tüm handler'larına maskeyi takar (bir kez)."""
    hedef = logger or logging.getLogger()
    for h in hedef.handlers:
        if not any(isinstance(f, OturumKimligiMaskesi) for f in h.filters):
            h.addFilter(OturumKimligiMaskesi())


class GovdeCokBuyuk(Exception):
    pass


def _413(istek_kimligi: str) -> bytes:
    return json.dumps({"error": {"code": "VALIDATION_FAILED", "message": "İstek gövdesi çok büyük.",
                                 "requestId": istek_kimligi}}, ensure_ascii=False).encode("utf-8")


class GovdeSiniri:
    def __init__(self, app: Any, sinir: Callable[[str], int | None]):
        self.app = app
        self.sinir = sinir

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        sinir = self.sinir(scope.get("path", ""))
        if sinir is None:
            await self.app(scope, receive, send)
            return
        basliklar = dict(scope.get("headers") or [])
        try:
            uzunluk = int(basliklar.get(b"content-length", b"-1"))
        except ValueError:
            uzunluk = -1
        if uzunluk > sinir:
            await self._gonder_413(send)
            return
        okunan = 0
        baslatildi = False

        async def sayan_receive() -> dict:
            nonlocal okunan
            mesaj = await receive()
            if mesaj.get("type") == "http.request":
                okunan += len(mesaj.get("body", b""))
                if okunan > sinir:
                    raise GovdeCokBuyuk()
            return mesaj

        async def izleyen_send(mesaj: dict) -> None:
            nonlocal baslatildi
            if mesaj.get("type") == "http.response.start":
                baslatildi = True
            await send(mesaj)

        try:
            await self.app(scope, sayan_receive, izleyen_send)
        except GovdeCokBuyuk:
            if not baslatildi:
                await self._gonder_413(send)

    @staticmethod
    async def _gonder_413(send: Callable) -> None:
        govde = _413("")
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(govde)).encode())]})
        await send({"type": "http.response.body", "body": govde})
