"""Sağlayıcıdan bağımsız LLM katmanı.

Kullanım::

    from ortak.llm import saglayici_olustur, Mesaj, LLMHatasi

    llm = saglayici_olustur("SADE_LLM")          # SADE_LLM_* yoksa LLM_* okunur
    metin = await llm.metin_uret("Merhaba", sistem="Kısa yanıt ver.")
    veri  = await llm.json_uret([Mesaj.kullanici("...")], sema={...})
    yanit = await llm.sohbet(mesajlar, araclar=[AracTanimi(...)])   # yanit.arac_cagrilari
"""

from .anthropic_saglayici import AnthropicSaglayici
from .fabrika import saglayici_olustur
from .openai_uyumlu import OpenAIUyumluSaglayici
from .sahte import SahteSaglayici
from .temel import (
    AracCagrisi,
    AracTanimi,
    LLMBaglantiHatasi,
    LLMHatasi,
    LLMLimitHatasi,
    LLMSaglayici,
    LLMYanit,
    LLMYanitHatasi,
    LLMYapilandirmaHatasi,
    LLMZamanAsimi,
    Mesaj,
    dusunmeyi_temizle,
    json_ayikla,
    sema_dogrula,
)

__all__ = [
    "AnthropicSaglayici", "AracCagrisi", "AracTanimi", "LLMBaglantiHatasi", "LLMHatasi",
    "LLMLimitHatasi", "LLMSaglayici", "LLMYanit", "LLMYanitHatasi", "LLMYapilandirmaHatasi",
    "LLMZamanAsimi", "Mesaj", "OpenAIUyumluSaglayici", "SahteSaglayici", "dusunmeyi_temizle",
    "json_ayikla", "saglayici_olustur", "sema_dogrula",
]
