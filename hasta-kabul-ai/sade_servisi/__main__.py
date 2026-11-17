r"""Çalıştırma: ``.venv\Scripts\python.exe -m sade_servisi`` (proje kökünden)."""

from __future__ import annotations

import logging

import uvicorn

from ortak.ayarlar import env_yukle

from .app import uygulama_olustur
from .ayarlar import SadeAyarlari


def main() -> None:
    env_yukle()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx INFO satırları URL'yi yazar; yalnız uyarılar kalsın.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    ayarlar = SadeAyarlari.ortamdan()
    app = uygulama_olustur(ayarlar)
    uvicorn.run(app, host=ayarlar.host, port=ayarlar.port, access_log=False, log_config=None)


if __name__ == "__main__":
    main()
