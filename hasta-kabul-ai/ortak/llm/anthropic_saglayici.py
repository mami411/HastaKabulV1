"""Anthropic (Claude) sağlayıcısı — opsiyonel.

``anthropic`` paketi kurulu değilse bu modülün içe aktarılması patlamaz; sağlayıcı
yalnızca oluşturulmak istendiğinde ``LLMYapilandirmaHatasi`` verir.
Not: Bu sağlayıcı hasta verisini yurt dışındaki bir buluta gönderir; varsayılan
profil tam yereldir (Ollama). KVKK değerlendirmesi yapılmadan açılmamalı.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

from .temel import (
    AracCagrisi,
    AracSecimi,
    AracTanimi,
    CagriAyarlari,
    LLMBaglantiHatasi,
    LLMHatasi,
    LLMLimitHatasi,
    LLMSaglayici,
    LLMYanit,
    LLMYanitHatasi,
    LLMYapilandirmaHatasi,
    LLMZamanAsimi,
    Mesaj,
)


class AnthropicSaglayici(LLMSaglayici):
    ad = "anthropic"

    def __init__(self, model: str, *, api_anahtari: str | None = None, taban_url: str | None = None,
                 istemci: Any = None, **kw: Any):
        if not model:
            raise LLMYapilandirmaHatasi("LLM modeli tanımlı değil (LLM_MODEL).", saglayici=self.ad)
        super().__init__(model, **kw)
        try:
            import anthropic  # noqa: PLC0415 — bilinçli tembel içe aktarma
        except ImportError as exc:
            if istemci is None:
                raise LLMYapilandirmaHatasi(
                    "anthropic paketi kurulu değil: pip install anthropic", saglayici=self.ad) from exc
            anthropic = None  # type: ignore[assignment]
        self._anthropic = anthropic
        if istemci is not None:
            self._istemci = istemci
        else:
            if not api_anahtari:
                raise LLMYapilandirmaHatasi("Anthropic anahtarı tanımlı değil (LLM_API_ANAHTARI).",
                                            saglayici=self.ad)
            secenekler: dict[str, Any] = {"api_key": api_anahtari, "max_retries": 0,
                                          "timeout": self.zaman_asimi_sn}
            if taban_url:
                secenekler["base_url"] = taban_url
            self._istemci = anthropic.AsyncAnthropic(**secenekler)

    async def kapat(self) -> None:
        kapat = getattr(self._istemci, "close", None)
        if kapat is not None:
            await kapat()

    # ---- Biçim çevirileri ----------------------------------------------------
    @staticmethod
    def _mesajlari_cevir(mesajlar: list[Mesaj]) -> tuple[str, list[dict[str, Any]]]:
        sistem = "\n\n".join(m.icerik for m in mesajlar if m.rol == "system")
        cikti: list[dict[str, Any]] = []
        for m in mesajlar:
            if m.rol == "system":
                continue
            if m.rol == "tool":
                blok = {"type": "tool_result", "tool_use_id": m.arac_cagri_id or "", "content": m.icerik}
                # Ardışık araç sonuçları tek kullanıcı mesajında toplanır (Anthropic kuralı).
                if cikti and cikti[-1]["role"] == "user" and isinstance(cikti[-1]["content"], list) \
                        and cikti[-1]["content"] and cikti[-1]["content"][0].get("type") == "tool_result":
                    cikti[-1]["content"].append(blok)
                else:
                    cikti.append({"role": "user", "content": [blok]})
                continue
            if m.rol == "assistant" and m.arac_cagrilari:
                bloklar: list[dict[str, Any]] = []
                if m.icerik:
                    bloklar.append({"type": "text", "text": m.icerik})
                bloklar += [{"type": "tool_use", "id": c.id, "name": c.ad, "input": c.argumanlar}
                            for c in m.arac_cagrilari]
                cikti.append({"role": "assistant", "content": bloklar})
                continue
            cikti.append({"role": m.rol, "content": m.icerik})
        return sistem, cikti

    def _hata_cevir(self, exc: Exception) -> LLMHatasi:
        a = self._anthropic
        ad = type(exc).__name__
        durum = getattr(exc, "status_code", None)
        if a is not None:
            if isinstance(exc, a.APITimeoutError):
                return LLMZamanAsimi("Anthropic zaman aşımı.", saglayici=self.ad)
            if isinstance(exc, a.APIConnectionError):
                return LLMBaglantiHatasi("Anthropic'e ulaşılamadı.", saglayici=self.ad)
        if durum in (401, 403):
            return LLMYapilandirmaHatasi(f"Anthropic HTTP {durum}", saglayici=self.ad, durum_kodu=durum)
        if durum == 429:
            return LLMLimitHatasi("Anthropic HTTP 429", saglayici=self.ad, durum_kodu=durum)
        if isinstance(durum, int) and durum >= 500:
            return LLMBaglantiHatasi(f"Anthropic HTTP {durum}", saglayici=self.ad, durum_kodu=durum)
        return LLMYanitHatasi(f"Anthropic hatası ({ad})", saglayici=self.ad, durum_kodu=durum)

    async def _sohbet(self, mesajlar: list[Mesaj], *, araclar: Sequence[AracTanimi] | None,
                      arac_secimi: AracSecimi, json_semasi: dict[str, Any] | None,
                      sema_adi: str, ayar: CagriAyarlari) -> LLMYanit:
        sistem, cevrilmis = self._mesajlari_cevir(mesajlar)
        istek: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.azami_token if ayar.azami_token is None else ayar.azami_token,
            "temperature": self.sicaklik if ayar.sicaklik is None else ayar.sicaklik,
            "messages": cevrilmis,
        }
        if sistem:
            istek["system"] = sistem
        arac_listesi = list(araclar or [])
        if json_semasi is not None:
            # Yapılandırılmış çıktı: tek araç tanımlanır ve kullanılması zorunlu tutulur.
            arac_listesi = [AracTanimi(sema_adi or "yanit", "Yanıtı bu şemayla ver.", json_semasi)]
            arac_secimi = sema_adi or "yanit"
        if arac_listesi:
            istek["tools"] = [{"name": a.ad, "description": a.aciklama, "input_schema": a.parametreler}
                              for a in arac_listesi]
            if arac_secimi == "required":
                istek["tool_choice"] = {"type": "any"}
            elif arac_secimi == "none":
                istek["tool_choice"] = {"type": "none"}
            elif arac_secimi != "auto":
                istek["tool_choice"] = {"type": "tool", "name": arac_secimi}
        zaman_asimi = ayar.zaman_asimi_sn or self.zaman_asimi_sn
        baslangic = time.perf_counter()
        try:
            yanit = await self._istemci.messages.create(**istek, timeout=zaman_asimi)
        except LLMHatasi:
            raise
        except Exception as exc:  # SDK hataları tek tipe çevrilir
            raise self._hata_cevir(exc) from exc
        sure_ms = (time.perf_counter() - baslangic) * 1000

        metinler, cagrilar = [], []
        for blok in getattr(yanit, "content", []) or []:
            tip = getattr(blok, "type", None)
            if tip == "text":
                metinler.append(blok.text)
            elif tip == "tool_use":
                cagrilar.append(AracCagrisi(id=blok.id, ad=blok.name, argumanlar=dict(blok.input or {})))
        if json_semasi is not None:
            if not cagrilar:
                raise LLMYanitHatasi("Anthropic yapılandırılmış çıktı vermedi.", saglayici=self.ad)
            metin = json.dumps(cagrilar[0].argumanlar, ensure_ascii=False)
            cagrilar = []
        else:
            metin = "".join(metinler)
        kullanim = getattr(yanit, "usage", None)
        return LLMYanit(
            metin=metin,
            arac_cagrilari=tuple(cagrilar),
            bitis_nedeni=getattr(yanit, "stop_reason", None),
            model=getattr(yanit, "model", self.model),
            sure_ms=sure_ms,
            kullanim={"girdi": kullanim.input_tokens, "cikti": kullanim.output_tokens} if kullanim else None,
        )
