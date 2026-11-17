"""Ortam değişkenlerinden sağlayıcı oluşturur.

Her ayar önce ``{onek}_{AD}``, yoksa ``LLM_{AD}`` olarak aranır. Böylece ses
servisi ``SES_LLM_MODEL``, sadeleştirme servisi ``SADE_LLM_MODEL`` verebilir;
vermezlerse ortak ``LLM_MODEL`` kullanılır.

| AD              | Açıklama                                               | Varsayılan                 |
|-----------------|--------------------------------------------------------|----------------------------|
| SAGLAYICI       | ``openai_uyumlu`` ya da ``anthropic``                  | openai_uyumlu              |
| TABAN_URL       | OpenAI uyumlu uç (Ollama/Groq/Gemini)                  | http://localhost:11434/v1  |
| MODEL           | Model adı (zorunlu)                                    | —                          |
| API_ANAHTARI    | Bulut sağlayıcı anahtarı (Ollama için gerekmez)        | —                          |
| ZAMAN_ASIMI_SN  | Tek istek zaman aşımı                                  | 20                         |
| SICAKLIK        | Örnekleme sıcaklığı                                    | 0.2                        |
| AZAMI_TOKEN     | Yanıt token sınırı                                     | 512                        |
| YENIDEN_DENEME  | Geçici hatada (429/5xx/bağlantı) ek deneme sayısı      | 1                          |
| JSON_MODU       | ``sema`` / ``nesne`` / ``yok`` (response_format biçimi) | sema                       |
| EK_GOVDE        | İsteğe eklenecek JSON (ör. ``{"reasoning_effort":"none"}``) | —                      |
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from ..ayarlar import onekli
from .temel import LLMSaglayici, LLMYapilandirmaHatasi

VARSAYILAN_TABAN_URL = "http://localhost:11434/v1"


def _sayi(deger: str | None, varsayilan: float, ad: str) -> float:
    if deger is None:
        return varsayilan
    try:
        return float(deger.replace(",", "."))
    except ValueError as exc:
        raise LLMYapilandirmaHatasi(f"{ad} sayı olmalı.") from exc


def saglayici_olustur(onek: str = "LLM", ortam: Mapping[str, str] | None = None) -> LLMSaglayici:
    """Ortamdan bir ``LLMSaglayici`` kurar. Eksik/yanlış ayarda ``LLMYapilandirmaHatasi``."""

    def oku(ad: str) -> str | None:
        return onekli(onek, ad, ortam)

    tur = (oku("SAGLAYICI") or "openai_uyumlu").lower()
    model = oku("MODEL") or ""
    ortak = {
        "zaman_asimi_sn": _sayi(oku("ZAMAN_ASIMI_SN"), 20.0, "ZAMAN_ASIMI_SN"),
        "sicaklik": _sayi(oku("SICAKLIK"), 0.2, "SICAKLIK"),
        "azami_token": int(_sayi(oku("AZAMI_TOKEN"), 512, "AZAMI_TOKEN")),
        "yeniden_deneme": int(_sayi(oku("YENIDEN_DENEME"), 1, "YENIDEN_DENEME")),
    }
    if tur in ("openai_uyumlu", "openai", "ollama", "groq", "gemini"):
        from .openai_uyumlu import OpenAIUyumluSaglayici

        ek_ham = oku("EK_GOVDE")
        try:
            ek_govde = json.loads(ek_ham) if ek_ham else None
        except json.JSONDecodeError as exc:
            raise LLMYapilandirmaHatasi("EK_GOVDE geçerli JSON olmalı.") from exc
        return OpenAIUyumluSaglayici(
            model,
            taban_url=oku("TABAN_URL") or VARSAYILAN_TABAN_URL,
            api_anahtari=oku("API_ANAHTARI"),
            json_modu=(oku("JSON_MODU") or "sema").lower(),
            ek_govde=ek_govde,
            **ortak,
        )
    if tur == "anthropic":
        from .anthropic_saglayici import AnthropicSaglayici

        return AnthropicSaglayici(model, api_anahtari=oku("API_ANAHTARI"),
                                  taban_url=oku("TABAN_URL"), **ortak)
    raise LLMYapilandirmaHatasi(f"Bilinmeyen LLM sağlayıcısı: {tur!r}")
