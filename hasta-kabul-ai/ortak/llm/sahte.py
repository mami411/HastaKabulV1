"""Testler için sahte sağlayıcı: ağ ve ücret yok, yanıtlar sırayla verilir.

Örnek::

    llm = SahteSaglayici([
        '{"niyet": "onay"}',                 # metin yanıt
        LLMZamanAsimi("zaman aşımı"),         # hata fırlatılır
        lambda mesajlar: '{"niyet": "ret"}',  # çağrılabilir: gelen mesajlara göre yanıt
    ])
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .temel import AracSecimi, AracTanimi, CagriAyarlari, LLMSaglayici, LLMYanit, Mesaj

SahteOge = str | LLMYanit | Exception | Callable[[list[Mesaj]], "str | LLMYanit"]


class SahteSaglayici(LLMSaglayici):
    ad = "sahte"
    bekleme_sn = 0.0

    def __init__(self, yanitlar: Sequence[SahteOge] = (), *, varsayilan: SahteOge | None = None,
                 model: str = "sahte-model", **kw: Any):
        kw.setdefault("yeniden_deneme", 0)
        super().__init__(model, **kw)
        self._yanitlar = list(yanitlar)
        self._varsayilan = varsayilan
        self.cagrilar: list[dict[str, Any]] = []

    def ekle(self, *yanitlar: SahteOge) -> None:
        self._yanitlar.extend(yanitlar)

    async def _sohbet(self, mesajlar: list[Mesaj], *, araclar: Sequence[AracTanimi] | None,
                      arac_secimi: AracSecimi, json_semasi: dict[str, Any] | None,
                      sema_adi: str, ayar: CagriAyarlari) -> LLMYanit:
        self.cagrilar.append({"mesajlar": list(mesajlar), "araclar": list(araclar or []),
                              "arac_secimi": arac_secimi, "json_semasi": json_semasi})
        if self._yanitlar:
            oge = self._yanitlar.pop(0)
        elif self._varsayilan is not None:
            oge = self._varsayilan
        else:
            raise AssertionError("SahteSaglayici: beklenmeyen LLM çağrısı (yanıt listesi boş).")
        if callable(oge) and not isinstance(oge, Exception):
            oge = oge(list(mesajlar))
        if isinstance(oge, Exception):
            raise oge
        if isinstance(oge, LLMYanit):
            return oge
        return LLMYanit(metin=str(oge), model=self.model, sure_ms=1.0)
