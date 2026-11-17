"""Çalıştırma: ``.venv\\Scripts\\python.exe -m ses_servisi`` (proje kökünden)."""

from __future__ import annotations

import logging

import uvicorn

from ortak.ayarlar import env_yukle

from .app import uygulama_olustur
from .ayarlar import SesAyarlari
from .gunluk import log_maskesini_kur


def main() -> None:
    env_yukle()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log_maskesini_kur()  # oturum kimlikleri (UUID) log satırlarında maskelenir (O6)
    # httpx INFO satırları URL'deki oturum kimliğini yazar; yalnız uyarılar kalsın.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    for _ad in ("httpx2", "httpcore2", "huggingface_hub", "faster_whisper"):
        logging.getLogger(_ad).setLevel(logging.WARNING)
    ayarlar = SesAyarlari.ortamdan()
    app = uygulama_olustur(ayarlar)
    uvicorn.run(app, host=ayarlar.host, port=ayarlar.port, access_log=False, log_config=None)


if __name__ == "__main__":
    main()
