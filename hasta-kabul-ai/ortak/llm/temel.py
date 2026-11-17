"""Sağlayıcıdan bağımsız LLM arayüzü.

Konuşma/iş mantığı yalnız bu dosyadaki tiplerle konuşur; hiçbir SDK'yı doğrudan
görmez (halisaha raporunun bir numaralı bulgusu: döngü Anthropic SDK'sına bağlıydı).

Üç kullanım biçimi:
- ``metin_uret``  : düz metin
- ``json_uret``   : JSON / yapılandırılmış çıktı (şema verilebilir, yerelde doğrulanır)
- ``sohbet``      : en genel çağrı; araç (function/tool) tanımları ve araç çağrıları

Hata sınıfları ``LLMHatasi`` altında toplanır; ``gecici`` alanı yeniden denemeye
ya da yedek yanıta karar vermek için kullanılır.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Rol = Literal["system", "user", "assistant", "tool"]


# --------------------------------------------------------------------------
# Veri tipleri
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class AracCagrisi:
    id: str
    ad: str
    argumanlar: dict[str, Any]


@dataclass(frozen=True)
class Mesaj:
    rol: Rol
    icerik: str = ""
    arac_cagrilari: tuple[AracCagrisi, ...] = ()  # rol == "assistant" iken
    arac_cagri_id: str | None = None               # rol == "tool" iken

    @staticmethod
    def sistem(icerik: str) -> Mesaj:
        return Mesaj("system", icerik)

    @staticmethod
    def kullanici(icerik: str) -> Mesaj:
        return Mesaj("user", icerik)

    @staticmethod
    def asistan(icerik: str = "", arac_cagrilari: Sequence[AracCagrisi] = ()) -> Mesaj:
        return Mesaj("assistant", icerik, tuple(arac_cagrilari))

    @staticmethod
    def arac_sonucu(arac_cagri_id: str, icerik: str) -> Mesaj:
        return Mesaj("tool", icerik, arac_cagri_id=arac_cagri_id)


@dataclass(frozen=True)
class AracTanimi:
    ad: str
    aciklama: str
    parametreler: dict[str, Any]  # JSON Schema (type: object)


@dataclass(frozen=True)
class LLMYanit:
    metin: str
    arac_cagrilari: tuple[AracCagrisi, ...] = ()
    bitis_nedeni: str | None = None
    model: str = ""
    sure_ms: float = 0.0
    kullanim: dict[str, int] | None = None


AracSecimi = Literal["auto", "required", "none"] | str  # str: belirli bir aracın adı


# --------------------------------------------------------------------------
# Hatalar
# --------------------------------------------------------------------------

class LLMHatasi(Exception):
    """Tüm LLM hatalarının tabanı. Mesajda istem/kullanıcı metni bulunmaz (KVKK)."""

    kod = "LLM_HATASI"
    gecici = False

    def __init__(self, mesaj: str, *, saglayici: str = "", durum_kodu: int | None = None):
        super().__init__(mesaj)
        self.saglayici = saglayici
        self.durum_kodu = durum_kodu


class LLMYapilandirmaHatasi(LLMHatasi):
    """Eksik model/anahtar, kurulu olmayan paket, 401/403."""
    kod = "LLM_YAPILANDIRMA"


class LLMZamanAsimi(LLMHatasi):
    kod = "LLM_ZAMAN_ASIMI"
    gecici = True


class LLMBaglantiHatasi(LLMHatasi):
    """Sunucuya ulaşılamadı ya da 5xx döndü."""
    kod = "LLM_BAGLANTI"
    gecici = True


class LLMLimitHatasi(LLMHatasi):
    """429 / kota. Ücretsiz katmanlarda sık görülür."""
    kod = "LLM_LIMIT"
    gecici = True


class LLMYanitHatasi(LLMHatasi):
    """Yanıt alındı ama kullanılamıyor: bozuk JSON, şemaya uymayan çıktı, 4xx."""
    kod = "LLM_YANIT"


# --------------------------------------------------------------------------
# JSON yardımcıları
# --------------------------------------------------------------------------

_DUSUNME = re.compile(r"<think>.*?</think>", flags=re.S | re.I)
_KOD_BLOGU = re.compile(r"```(?:json)?\s*(.*?)```", flags=re.S | re.I)


def dusunmeyi_temizle(metin: str) -> str:
    """Bazı modellerin (qwen3 vb.) ``<think>…</think>`` bloğunu siler."""
    return _DUSUNME.sub("", metin or "").strip()


def json_ayikla(metin: str) -> Any:
    """Model çıktısından JSON değeri çıkarır (kod bloğu ya da çevreleyen metin olabilir)."""
    metin = dusunmeyi_temizle(metin)
    adaylar = [metin]
    adaylar += [m.group(1) for m in _KOD_BLOGU.finditer(metin)]
    bas, son = metin.find("{"), metin.rfind("}")
    if 0 <= bas < son:
        adaylar.append(metin[bas: son + 1])
    for aday in adaylar:
        try:
            return json.loads(aday)
        except (json.JSONDecodeError, TypeError):
            continue
    raise LLMYanitHatasi("Model çıktısı geçerli JSON değil.")


_TIP_ESLEME = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def sema_dogrula(deger: Any, sema: dict[str, Any], yol: str = "$") -> None:
    """JSON Schema'nın küçük bir alt kümesiyle doğrular (type, properties, required,
    enum, items, additionalProperties=false). Uymazsa ``LLMYanitHatasi``."""
    tipler = sema.get("type")
    if tipler is not None:
        tip_listesi = tipler if isinstance(tipler, list) else [tipler]
        uygun = False
        for t in tip_listesi:
            beklenen = _TIP_ESLEME.get(t)
            if beklenen is None:
                continue
            if t in ("integer", "number") and isinstance(deger, bool):
                continue
            if isinstance(deger, beklenen):
                uygun = True
                break
        if not uygun:
            raise LLMYanitHatasi(f"Şemaya uymayan tip: {yol}")
    if "enum" in sema and deger not in sema["enum"]:
        raise LLMYanitHatasi(f"İzin verilmeyen değer: {yol}")
    if isinstance(deger, dict):
        ozellikler = sema.get("properties", {})
        for gerekli in sema.get("required", []):
            if gerekli not in deger:
                raise LLMYanitHatasi(f"Eksik alan: {yol}.{gerekli}")
        for ad, alt in deger.items():
            if ad in ozellikler:
                sema_dogrula(alt, ozellikler[ad], f"{yol}.{ad}")
            elif sema.get("additionalProperties") is False:
                raise LLMYanitHatasi(f"Fazla alan: {yol}.{ad}")
    if isinstance(deger, list) and "items" in sema:
        for i, oge in enumerate(deger):
            sema_dogrula(oge, sema["items"], f"{yol}[{i}]")


# --------------------------------------------------------------------------
# Sağlayıcı arayüzü
# --------------------------------------------------------------------------

MesajGirdisi = str | Sequence[Mesaj]


def mesajlari_hazirla(girdi: MesajGirdisi, sistem: str | None = None) -> list[Mesaj]:
    mesajlar = [Mesaj.kullanici(girdi)] if isinstance(girdi, str) else list(girdi)
    if sistem:
        mesajlar = [Mesaj.sistem(sistem)] + [m for m in mesajlar if m.rol != "system"]
    return mesajlar


@dataclass
class CagriAyarlari:
    sicaklik: float | None = None
    azami_token: int | None = None
    zaman_asimi_sn: float | None = None


class LLMSaglayici(ABC):
    """Tüm sağlayıcıların uyduğu ince arayüz."""

    ad: str = "soyut"
    bekleme_sn: float = 0.5  # geçici hatada yeniden denemeden önce (deneme sayısıyla çarpılır)

    def __init__(self, model: str, *, zaman_asimi_sn: float = 30.0, sicaklik: float = 0.2,
                 azami_token: int = 512, yeniden_deneme: int = 1):
        self.model = model
        self.zaman_asimi_sn = zaman_asimi_sn
        self.sicaklik = sicaklik
        self.azami_token = azami_token
        self.yeniden_deneme = max(0, yeniden_deneme)

    # ---- Sağlayıcının yazması gereken tek çağrı ---------------------------
    @abstractmethod
    async def _sohbet(self, mesajlar: list[Mesaj], *, araclar: Sequence[AracTanimi] | None,
                      arac_secimi: AracSecimi, json_semasi: dict[str, Any] | None,
                      sema_adi: str, ayar: CagriAyarlari) -> LLMYanit:
        """Tek bir istek. ``json_semasi`` verilirse sağlayıcı yapılandırılmış çıktı ister."""

    async def kapat(self) -> None:  # bağlantı havuzu vb.
        return None

    # ---- Ortak davranış -------------------------------------------------------
    async def sohbet(self, mesajlar: MesajGirdisi, *, sistem: str | None = None,
                     araclar: Sequence[AracTanimi] | None = None, arac_secimi: AracSecimi = "auto",
                     sicaklik: float | None = None, azami_token: int | None = None,
                     zaman_asimi_sn: float | None = None) -> LLMYanit:
        ayar = CagriAyarlari(sicaklik, azami_token, zaman_asimi_sn)
        return await self._denemeli(mesajlari_hazirla(mesajlar, sistem), araclar=araclar,
                                    arac_secimi=arac_secimi, json_semasi=None, sema_adi="", ayar=ayar)

    async def metin_uret(self, mesajlar: MesajGirdisi, *, sistem: str | None = None,
                         sicaklik: float | None = None, azami_token: int | None = None,
                         zaman_asimi_sn: float | None = None) -> str:
        yanit = await self.sohbet(mesajlar, sistem=sistem, sicaklik=sicaklik,
                                  azami_token=azami_token, zaman_asimi_sn=zaman_asimi_sn)
        return dusunmeyi_temizle(yanit.metin)

    async def json_uret(self, mesajlar: MesajGirdisi, *, sistem: str | None = None,
                        sema: dict[str, Any] | None = None, sema_adi: str = "yanit",
                        sicaklik: float | None = None, azami_token: int | None = None,
                        zaman_asimi_sn: float | None = None) -> Any:
        """JSON üretir ve (şema verildiyse) doğrular. Hata: ``LLMYanitHatasi``."""
        ayar = CagriAyarlari(sicaklik, azami_token, zaman_asimi_sn)
        yanit = await self._denemeli(mesajlari_hazirla(mesajlar, sistem), araclar=None,
                                     arac_secimi="none", json_semasi=sema or {"type": "object"},
                                     sema_adi=sema_adi, ayar=ayar)
        deger = json_ayikla(yanit.metin)
        if sema:
            sema_dogrula(deger, sema)
        return deger

    async def _denemeli(self, mesajlar: list[Mesaj], **kw: Any) -> LLMYanit:
        son_hata: LLMHatasi | None = None
        for deneme in range(self.yeniden_deneme + 1):
            baslangic = time.perf_counter()
            try:
                yanit = await self._sohbet(mesajlar, **kw)
            except LLMZamanAsimi:
                raise  # zaman aşımında yeniden denemek gecikme bütçesini ikiye katlar
            except LLMHatasi as hata:
                son_hata = hata
                if not hata.gecici or deneme >= self.yeniden_deneme:
                    raise
                await asyncio.sleep(self.bekleme_sn * (deneme + 1))
                continue
            if not yanit.sure_ms:
                yanit = LLMYanit(yanit.metin, yanit.arac_cagrilari, yanit.bitis_nedeni,
                                 yanit.model, (time.perf_counter() - baslangic) * 1000, yanit.kullanim)
            return yanit
        assert son_hata is not None
        raise son_hata

    def __repr__(self) -> str:
        return f"<{type(self).__name__} model={self.model!r}>"
