"""Arka plan işçisi: kuyruktan iş alır, özeti üretir, callback'i gönderir, gerekirse yeniden dener.

Her kabul edilen iş bir callback ile sonlanır:
- üretim başarılı → başarı gövdesi; LLM yok ve bilinmeyen terim var → ``{errorCode: LLM_UNAVAILABLE}``;
  beklenmeyen hata → ``{errorCode: SIMPLIFY_FAILED}``
- backend gövdeyi 400 ile reddederse bir kez ``SIMPLIFY_FAILED`` gönderilir
- ağ/5xx/401/zaman aşımı → üstel bekleme (taban × 2^deneme, üst sınırlı) ile yeniden denenir;
  azami deneme aşılırsa iş silinir ve hata loglanır (özet backend'de PENDING kalır — bkz. README)
- 404/409 → iş silinir (özet yok ya da hekim onaylamış)
- üretim ``AZAMI_URETIM_DENEME`` kez başlayıp sonuçlanmadıysa (süreç üretim sırasında çöktü):
  yeniden üretilmez, ``SIMPLIFY_FAILED`` gönderilir (zehirli girdi sonsuz çökme döngüsü yapmaz)
- işleme sırasında beklenmeyen hata (SQLite, kodlama…) → iş ertelenir; aynı iş kuyruğun başını
  tıkayıp saniyede bir yeniden denenmez, sayaç dolunca silinir

Log satırları yalnız requestId, summaryId, yol/sonuç, sayılar ve süre taşır; tanı, ilaç ve
patientRef asla loglanmaz.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable

from .backend import BackendIstemcisi, GeriCagriSonucu, istek_kimligi_temizle
from .bilgi_tabani import BilgiTabani
from .kuyruk import BEKLIYOR, Is, Kuyruk
from .uretici import HATA_GENEL, Sadelestirici, hata_govdesi

log = logging.getLogger("sade_servisi.isci")

AZAMI_URETIM_DENEME = 3


class Isci:
    def __init__(self, kuyruk: Kuyruk, sadelestirici: Sadelestirici, kb: BilgiTabani, backend: BackendIstemcisi,
                 *, azami_deneme: int = 12, taban_bekleme_sn: float = 2.0, azami_bekleme_sn: float = 300.0,
                 saat: Callable[[], float] = time.time):
        self.kuyruk = kuyruk
        self.sadelestirici = sadelestirici
        self.kb = kb
        self.backend = backend
        self.azami_deneme = max(1, azami_deneme)
        self.taban_bekleme_sn = taban_bekleme_sn
        self.azami_bekleme_sn = azami_bekleme_sn
        self._saat = saat
        self._uyandir = asyncio.Event()
        self.sayaclar = {"uretilen": 0, "gonderilen": 0, "vazgecilen": 0}

    def uyandir(self) -> None:
        self._uyandir.set()

    def bekleme_suresi(self, deneme: int) -> float:
        temel = min(self.azami_bekleme_sn, self.taban_bekleme_sn * (2 ** deneme))
        return temel * random.uniform(0.9, 1.1)

    async def calis(self) -> None:
        while True:
            try:
                self._uyandir.clear()
                is_ = self.kuyruk.sonraki()
                if is_ is None:
                    yakin = self.kuyruk.en_yakin_zaman()
                    bekle = None if yakin is None else max(0.02, yakin - self._saat())
                    try:
                        await asyncio.wait_for(self._uyandir.wait(), timeout=bekle)
                    except TimeoutError:
                        pass
                    continue
                try:
                    await self.isle(is_)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — iş ertelenir, kuyruk başı tıkanmaz
                    self._isleme_hatasi(is_, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — döngü ölmemeli
                log.error("işçi döngüsü hatası tur=%s", type(exc).__name__)
                await asyncio.sleep(1.0)

    def _isleme_hatasi(self, is_: Is, exc: Exception) -> None:
        rid = istek_kimligi_temizle(is_.request_id)
        try:
            deneme = self.kuyruk.ertele(is_.summary_id, is_.surum, self.bekleme_suresi(is_.deneme))
            if deneme >= self.azami_deneme:
                self.kuyruk.sil(is_.summary_id, is_.surum)
                self.sayaclar["vazgecilen"] += 1
                log.error("iş %d kez hatayla sonuçlandı, silindi requestId=%s summaryId=%s tur=%s",
                          deneme, rid, is_.summary_id, type(exc).__name__)
                return
        except Exception as ic:  # noqa: BLE001
            log.error("iş ertelenemedi requestId=%s summaryId=%s tur=%s", rid, is_.summary_id, type(ic).__name__)
            raise
        log.error("iş işlenirken hata, ertelendi requestId=%s summaryId=%s tur=%s deneme=%d",
                  rid, is_.summary_id, type(exc).__name__, deneme)

    async def _uret(self, is_: Is, rid: str) -> dict | None:
        if self.kuyruk.uretim_basladi(is_.summary_id, is_.surum) > AZAMI_URETIM_DENEME:
            log.error("üretim %d kez tamamlanamadı (süreç çökmüş olabilir), SIMPLIFY_FAILED gönderilecek "
                      "requestId=%s summaryId=%s", AZAMI_URETIM_DENEME, rid, is_.summary_id)
            govde = hata_govdesi(HATA_GENEL)
            return govde if self.kuyruk.govde_kaydet(is_.summary_id, is_.surum, govde) else None
        try:
            kb = await self.kb.kayitlar()
            sonuc = await self.sadelestirici.uret(is_.tani, is_.ilaclar, kb.kayitlar)
            govde = sonuc.govde
            log.info("üretim requestId=%s summaryId=%s yol=%s neden=%s kb=%s eslesen=%d bilinmeyen=%d "
                     "llm_deneme=%d ihlal=%s ms=%.0f", rid, is_.summary_id, sonuc.yol, sonuc.neden or "-",
                     kb.kaynak, sonuc.eslesen, sonuc.bilinmeyen, sonuc.llm_deneme,
                     ",".join(sonuc.ihlaller) or "-", sonuc.sure_ms)
        except Exception as exc:  # noqa: BLE001 — iş yine de callback ile sonlanmalı
            log.error("üretim hatası requestId=%s summaryId=%s tur=%s", rid, is_.summary_id, type(exc).__name__)
            govde = hata_govdesi(HATA_GENEL)
        self.sayaclar["uretilen"] += 1
        if not self.kuyruk.govde_kaydet(is_.summary_id, is_.surum, govde):
            log.info("iş yeni sürümle değişti, eski sonuç bırakıldı requestId=%s summaryId=%s", rid, is_.summary_id)
            return None
        return govde

    async def isle(self, is_: Is) -> None:
        rid = istek_kimligi_temizle(is_.request_id)
        govde = is_.govde
        if is_.durum == BEKLIYOR or govde is None:
            govde = await self._uret(is_, rid)
            if govde is None:
                return
        sonuc, durum, kod = await self.backend.ozet_gonder(is_.summary_id, govde, is_.request_id)
        toplam_ms = (self._saat() - is_.olusturma) * 1000
        tur = "hata:" + govde["errorCode"] if "errorCode" in govde else "ozet"
        if sonuc is GeriCagriSonucu.BASARILI:
            self.kuyruk.sil(is_.summary_id, is_.surum)
            self.sayaclar["gonderilen"] += 1
            log.info("callback gönderildi requestId=%s summaryId=%s tur=%s durum=%d deneme=%d toplam_ms=%.0f",
                     rid, is_.summary_id, tur, durum, is_.deneme + 1, toplam_ms)
            return
        if sonuc is GeriCagriSonucu.GECERSIZ and "errorCode" not in govde:
            log.warning("callback gövdesi reddedildi, SIMPLIFY_FAILED gönderilecek requestId=%s summaryId=%s "
                        "durum=%d kod=%s", rid, is_.summary_id, durum, kod)
            self.kuyruk.govde_kaydet(is_.summary_id, is_.surum, hata_govdesi(HATA_GENEL))
            return
        if sonuc in (GeriCagriSonucu.GECERSIZ, GeriCagriSonucu.VAZGEC):
            self.kuyruk.sil(is_.summary_id, is_.surum)
            self.sayaclar["vazgecilen"] += 1
            log.warning("callback kalıcı olarak reddedildi, iş silindi requestId=%s summaryId=%s durum=%d kod=%s",
                        rid, is_.summary_id, durum, kod)
            return
        bekleme = self.bekleme_suresi(is_.deneme)
        deneme = self.kuyruk.ertele(is_.summary_id, is_.surum, bekleme)
        if deneme >= self.azami_deneme:
            self.kuyruk.sil(is_.summary_id, is_.surum)
            self.sayaclar["vazgecilen"] += 1
            log.error("callback %d denemede gönderilemedi, iş silindi requestId=%s summaryId=%s durum=%d kod=%s",
                      deneme, rid, is_.summary_id, durum, kod)
        else:
            log.warning("callback başarısız, yeniden denenecek requestId=%s summaryId=%s durum=%d kod=%s "
                        "deneme=%d bekleme_sn=%.1f", rid, is_.summary_id, durum, kod, deneme, bekleme)
