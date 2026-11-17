"""Küçük kalıcı iş kuyruğu (SQLite, tek dosya).

Amaç: 202 ile kabul edilen her istek, süreç yeniden başlasa da bir callback ile sonlansın.

Tabloda YALNIZ işlemek için gereken asgari veri durur: ``summaryId``, ``requestId``, tanı ve
ilaç satırları (üretim için), üretilen callback gövdesi (yeniden denemede LLM'i tekrar
çağırmamak için) ve deneme sayaçları. ``patientRef`` saklanmaz. Callback başarılı olunca ya
da kalıcı olarak vazgeçilince satır silinir.

Aynı ``summaryId`` yeniden gelirse (ör. hekim reddetti, backend yeniden istedi) satır yeni
girdiyle değiştirilir ve ``surum`` artar; o sırada işlenen eski sürümün sonucu yazılmaz.

``uretim_deneme`` üretime BAŞLAMADAN artırılır: süreç üretim sırasında çökerse (zehirli girdi,
bellek, sürücü) yeniden başlatmada sayaç korunur ve işçi sonsuz çökme döngüsüne girmeden işi
``SIMPLIFY_FAILED`` ile kapatır.

KVKK: ``secure_delete`` açıktır (silinen satırların baytları sıfırlanır); kuyruk boşalınca WAL
dosyası kesilir. Dosya izinleri: Windows'ta klasörün ACL'ini devralır (bkz. README).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BEKLIYOR = "BEKLIYOR"       # üretim bekliyor
GONDERILECEK = "GONDERILECEK"  # gövde hazır, callback bekliyor

_SEMA = """
CREATE TABLE IF NOT EXISTS isler (
    summary_id      TEXT PRIMARY KEY,
    request_id      TEXT NOT NULL,
    tani            TEXT,
    ilaclar         TEXT,
    durum           TEXT NOT NULL,
    govde           TEXT,
    surum           INTEGER NOT NULL DEFAULT 1,
    deneme          INTEGER NOT NULL DEFAULT 0,
    sonraki_zaman   REAL NOT NULL,
    olusturma       REAL NOT NULL,
    uretim_deneme   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_isler_sonraki ON isler (sonraki_zaman);
"""


@dataclass(frozen=True)
class Is:
    summary_id: str
    request_id: str
    tani: str
    ilaclar: list[str]
    durum: str
    govde: dict[str, Any] | None
    surum: int
    deneme: int
    sonraki_zaman: float
    olusturma: float
    uretim_deneme: int = 0


class Kuyruk:
    def __init__(self, yol: str | Path, *, saat=time.time):
        self.yol = str(yol)
        if self.yol != ":memory:":
            Path(self.yol).parent.mkdir(parents=True, exist_ok=True)
        self._saat = saat
        self._kilit = threading.Lock()
        self._db = sqlite3.connect(self.yol, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL" if self.yol != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.executescript(_SEMA)
        sutunlar = {r[1] for r in self._db.execute("PRAGMA table_info(isler)")}
        if "uretim_deneme" not in sutunlar:   # eski sürümün dosyası
            self._db.execute("ALTER TABLE isler ADD COLUMN uretim_deneme INTEGER NOT NULL DEFAULT 0")

    def kapat(self) -> None:
        with self._kilit:
            self._db.close()

    @staticmethod
    def _is(satir: tuple) -> Is:
        sid, rid, tani, ilaclar, durum, govde, surum, deneme, sonraki, olusturma, uretim_deneme = satir
        return Is(sid, rid, tani or "", json.loads(ilaclar) if ilaclar else [], durum,
                  json.loads(govde) if govde else None, surum, deneme, sonraki, olusturma, uretim_deneme)

    _SUTUNLAR = ("summary_id, request_id, tani, ilaclar, durum, govde, surum, deneme, sonraki_zaman, olusturma, "
                 "uretim_deneme")

    def ekle(self, summary_id: str, request_id: str, tani: str, ilaclar: list[str]) -> int:
        """İşi ekler ya da (aynı summaryId) yeni girdiyle değiştirir. Yeni sürümü döndürür."""
        simdi = self._saat()
        with self._kilit:
            self._db.execute(
                """INSERT INTO isler (summary_id, request_id, tani, ilaclar, durum, govde, surum, deneme,
                                      sonraki_zaman, olusturma)
                   VALUES (?, ?, ?, ?, ?, NULL, 1, 0, ?, ?)
                   ON CONFLICT(summary_id) DO UPDATE SET
                       request_id=excluded.request_id, tani=excluded.tani, ilaclar=excluded.ilaclar,
                       durum=excluded.durum, govde=NULL, surum=isler.surum + 1, deneme=0, uretim_deneme=0,
                       sonraki_zaman=excluded.sonraki_zaman""",
                (summary_id, request_id, tani, json.dumps(ilaclar, ensure_ascii=False), BEKLIYOR, simdi, simdi))
            return self._db.execute("SELECT surum FROM isler WHERE summary_id=?", (summary_id,)).fetchone()[0]

    def ekle_hazir(self, summary_id: str, request_id: str, govde: dict[str, Any]) -> int:
        """Üretim gerektirmeyen iş (ör. girdi sınır aşımı → hata gövdesi): yalnız callback gönderilir.

        Hekim girdisi saklanmaz.
        """
        simdi = self._saat()
        with self._kilit:
            self._db.execute(
                """INSERT INTO isler (summary_id, request_id, tani, ilaclar, durum, govde, surum, deneme,
                                      sonraki_zaman, olusturma)
                   VALUES (?, ?, NULL, NULL, ?, ?, 1, 0, ?, ?)
                   ON CONFLICT(summary_id) DO UPDATE SET
                       request_id=excluded.request_id, tani=NULL, ilaclar=NULL, durum=excluded.durum,
                       govde=excluded.govde, surum=isler.surum + 1, deneme=0, uretim_deneme=0,
                       sonraki_zaman=excluded.sonraki_zaman""",
                (summary_id, request_id, GONDERILECEK, json.dumps(govde, ensure_ascii=False), simdi, simdi))
            return self._db.execute("SELECT surum FROM isler WHERE summary_id=?", (summary_id,)).fetchone()[0]

    def uretim_basladi(self, summary_id: str, surum: int) -> int:
        """Üretim denemesini KALICI olarak sayar (çökmeden önce); yeni sayacı döndürür."""
        with self._kilit:
            self._db.execute("UPDATE isler SET uretim_deneme=uretim_deneme+1 WHERE summary_id=? AND surum=?",
                             (summary_id, surum))
            satir = self._db.execute("SELECT uretim_deneme FROM isler WHERE summary_id=? AND surum=?",
                                     (summary_id, surum)).fetchone()
        return satir[0] if satir else 0

    def sonraki(self) -> Is | None:
        """Zamanı gelmiş en eski iş."""
        with self._kilit:
            satir = self._db.execute(
                f"SELECT {self._SUTUNLAR} FROM isler WHERE sonraki_zaman <= ? "
                "ORDER BY sonraki_zaman, olusturma LIMIT 1",
                (self._saat(),)).fetchone()
        return self._is(satir) if satir else None

    def en_yakin_zaman(self) -> float | None:
        with self._kilit:
            satir = self._db.execute("SELECT MIN(sonraki_zaman) FROM isler").fetchone()
        return satir[0] if satir and satir[0] is not None else None

    def govde_kaydet(self, summary_id: str, surum: int, govde: dict[str, Any]) -> bool:
        """Üretilen gövdeyi yazar; girdiye artık gerek yok, silinir. Sürüm değiştiyse False."""
        with self._kilit:
            imlec = self._db.execute(
                "UPDATE isler SET durum=?, govde=?, tani=NULL, ilaclar=NULL WHERE summary_id=? AND surum=?",
                (GONDERILECEK, json.dumps(govde, ensure_ascii=False), summary_id, surum))
            return imlec.rowcount == 1

    def ertele(self, summary_id: str, surum: int, bekleme_sn: float) -> int:
        """Callback başarısız: deneme sayısını artırır, sonraki denemeyi ileri atar."""
        with self._kilit:
            self._db.execute(
                "UPDATE isler SET deneme=deneme+1, sonraki_zaman=? WHERE summary_id=? AND surum=?",
                (self._saat() + bekleme_sn, summary_id, surum))
            satir = self._db.execute("SELECT deneme FROM isler WHERE summary_id=?", (summary_id,)).fetchone()
        return satir[0] if satir else 0

    def sil(self, summary_id: str, surum: int) -> bool:
        with self._kilit:
            silindi = self._db.execute("DELETE FROM isler WHERE summary_id=? AND surum=?",
                                       (summary_id, surum)).rowcount == 1
            if silindi and self.yol != ":memory:" and \
                    self._db.execute("SELECT COUNT(*) FROM isler").fetchone()[0] == 0:
                # Kuyruk boş: WAL'daki eski sayfalar (sağlık verisi) ana dosyaya yazılıp WAL kesilir.
                try:
                    self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
            return silindi

    def __len__(self) -> int:
        with self._kilit:
            return self._db.execute("SELECT COUNT(*) FROM isler").fetchone()[0]

    def durumlar(self) -> dict[str, int]:
        with self._kilit:
            return dict(self._db.execute("SELECT durum, COUNT(*) FROM isler GROUP BY durum").fetchall())
