"""OpenAI uyumlu ``/chat/completions`` sağlayıcısı.

Aynı kod Ollama (``http://localhost:11434/v1``), Groq (``https://api.groq.com/openai/v1``),
Gemini (``https://generativelanguage.googleapis.com/v1beta/openai``) ve OpenAI uyumlu her
uçla çalışır; fark yalnız ortam değişkenlerindeki taban adres, model ve anahtardır.

SDK yerine doğrudan ``httpx`` kullanılır: tek bağımlılık, zaman aşımı ve hata
sınıflandırması elimizde.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import httpx

from .temel import (
    AracCagrisi,
    AracSecimi,
    AracTanimi,
    CagriAyarlari,
    LLMBaglantiHatasi,
    LLMLimitHatasi,
    LLMSaglayici,
    LLMYanit,
    LLMYanitHatasi,
    LLMYapilandirmaHatasi,
    LLMZamanAsimi,
    Mesaj,
    dusunmeyi_temizle,
)

JSON_MODLARI = ("sema", "nesne", "yok")


class OpenAIUyumluSaglayici(LLMSaglayici):
    ad = "openai_uyumlu"

    def __init__(self, model: str, *, taban_url: str, api_anahtari: str | None = None,
                 json_modu: str = "sema", ek_govde: dict[str, Any] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, **kw: Any):
        if not model:
            raise LLMYapilandirmaHatasi("LLM modeli tanımlı değil (LLM_MODEL).", saglayici=self.ad)
        if not taban_url:
            raise LLMYapilandirmaHatasi("LLM taban adresi tanımlı değil (LLM_TABAN_URL).", saglayici=self.ad)
        if json_modu not in JSON_MODLARI:
            raise LLMYapilandirmaHatasi(f"LLM_JSON_MODU şunlardan biri olmalı: {JSON_MODLARI}", saglayici=self.ad)
        super().__init__(model, **kw)
        self.taban_url = taban_url.rstrip("/")
        self.json_modu = json_modu
        self.ek_govde = dict(ek_govde or {})
        basliklar = {"Content-Type": "application/json"}
        if api_anahtari:
            basliklar["Authorization"] = f"Bearer {api_anahtari}"
        self._istemci = httpx.AsyncClient(headers=basliklar, transport=transport,
                                          timeout=self.zaman_asimi_sn)

    async def kapat(self) -> None:
        await self._istemci.aclose()

    # ---- Biçim çevirileri ----------------------------------------------------
    @staticmethod
    def _mesaj_cevir(m: Mesaj) -> dict[str, Any]:
        if m.rol == "tool":
            return {"role": "tool", "tool_call_id": m.arac_cagri_id or "", "content": m.icerik}
        sonuc: dict[str, Any] = {"role": m.rol, "content": m.icerik}
        if m.rol == "assistant" and m.arac_cagrilari:
            sonuc["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.ad, "arguments": json.dumps(c.argumanlar, ensure_ascii=False)}}
                for c in m.arac_cagrilari
            ]
        return sonuc

    @staticmethod
    def _arac_cevir(a: AracTanimi) -> dict[str, Any]:
        return {"type": "function",
                "function": {"name": a.ad, "description": a.aciklama, "parameters": a.parametreler}}

    @staticmethod
    def _arac_secimi_cevir(secim: AracSecimi) -> Any:
        if secim in ("auto", "required", "none"):
            return secim
        return {"type": "function", "function": {"name": secim}}

    def _govde(self, mesajlar: list[Mesaj], araclar: Sequence[AracTanimi] | None,
               arac_secimi: AracSecimi, json_semasi: dict[str, Any] | None, sema_adi: str,
               ayar: CagriAyarlari) -> dict[str, Any]:
        govde: dict[str, Any] = {
            "model": self.model,
            "messages": [self._mesaj_cevir(m) for m in mesajlar],
            "temperature": self.sicaklik if ayar.sicaklik is None else ayar.sicaklik,
            "max_tokens": self.azami_token if ayar.azami_token is None else ayar.azami_token,
            "stream": False,
        }
        if araclar:
            govde["tools"] = [self._arac_cevir(a) for a in araclar]
            govde["tool_choice"] = self._arac_secimi_cevir(arac_secimi)
        if json_semasi is not None:
            if self.json_modu == "sema":
                govde["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": sema_adi or "yanit", "schema": json_semasi, "strict": False},
                }
            elif self.json_modu == "nesne":
                govde["response_format"] = {"type": "json_object"}
        govde.update(self.ek_govde)
        return govde

    # ---- İstek ------------------------------------------------------------------
    async def _sohbet(self, mesajlar: list[Mesaj], *, araclar: Sequence[AracTanimi] | None,
                      arac_secimi: AracSecimi, json_semasi: dict[str, Any] | None,
                      sema_adi: str, ayar: CagriAyarlari) -> LLMYanit:
        govde = self._govde(mesajlar, araclar, arac_secimi, json_semasi, sema_adi, ayar)
        zaman_asimi = ayar.zaman_asimi_sn or self.zaman_asimi_sn
        baslangic = time.perf_counter()
        try:
            yanit = await self._istemci.post(f"{self.taban_url}/chat/completions", json=govde,
                                             timeout=zaman_asimi)
        except httpx.TimeoutException as exc:
            raise LLMZamanAsimi(f"LLM {zaman_asimi:.0f} sn içinde yanıt vermedi.", saglayici=self.ad) from exc
        except httpx.TransportError as exc:
            raise LLMBaglantiHatasi(f"LLM sunucusuna ulaşılamadı ({type(exc).__name__}).",
                                    saglayici=self.ad) from exc
        sure_ms = (time.perf_counter() - baslangic) * 1000
        self._durum_denetle(yanit)
        try:
            veri = yanit.json()
            secim = veri["choices"][0]
            mesaj = secim["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMYanitHatasi("LLM yanıtı beklenen biçimde değil.", saglayici=self.ad) from exc

        cagrilar = []
        for i, c in enumerate(mesaj.get("tool_calls") or []):
            fonk = c.get("function") or {}
            argumanlar = fonk.get("arguments") or "{}"
            if isinstance(argumanlar, str):
                try:
                    argumanlar = json.loads(argumanlar) if argumanlar.strip() else {}
                except json.JSONDecodeError as exc:
                    raise LLMYanitHatasi("Araç argümanları geçerli JSON değil.", saglayici=self.ad) from exc
            cagrilar.append(AracCagrisi(id=c.get("id") or f"cagri_{i}", ad=fonk.get("name", ""),
                                        argumanlar=argumanlar if isinstance(argumanlar, dict) else {}))
        kullanim = veri.get("usage")
        return LLMYanit(
            metin=dusunmeyi_temizle(mesaj.get("content") or ""),
            arac_cagrilari=tuple(cagrilar),
            bitis_nedeni=secim.get("finish_reason"),
            model=veri.get("model", self.model),
            sure_ms=sure_ms,
            kullanim={k: v for k, v in kullanim.items() if isinstance(v, int)} if isinstance(kullanim, dict) else None,
        )

    def _durum_denetle(self, yanit: httpx.Response) -> None:
        kod = yanit.status_code
        if kod < 400:
            return
        # Sağlayıcının hata mesajı kısaltılarak alınır; istem içeriği eklenmez.
        try:
            ayrinti = str((yanit.json().get("error") or {}).get("message", ""))[:200]
        except (ValueError, AttributeError):
            ayrinti = ""
        mesaj = f"LLM HTTP {kod}" + (f": {ayrinti}" if ayrinti else "")
        if kod in (401, 403):
            raise LLMYapilandirmaHatasi(mesaj, saglayici=self.ad, durum_kodu=kod)
        if kod == 429:
            raise LLMLimitHatasi(mesaj, saglayici=self.ad, durum_kodu=kod)
        if kod in (408, 504):
            raise LLMZamanAsimi(mesaj, saglayici=self.ad, durum_kodu=kod)
        if kod >= 500:
            raise LLMBaglantiHatasi(mesaj, saglayici=self.ad, durum_kodu=kod)
        if kod == 404:
            raise LLMYapilandirmaHatasi(mesaj + " (model adı ya da taban adres yanlış olabilir)",
                                        saglayici=self.ad, durum_kodu=kod)
        raise LLMYanitHatasi(mesaj, saglayici=self.ad, durum_kodu=kod)
