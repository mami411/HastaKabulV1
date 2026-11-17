"""ai-simplify testleri için bellek içi sahte backend (httpx.MockTransport) ve yardımcılar.

Gerçek backend sözleşmesi: PUT /api/v1/summaries/{id} (X-Service-Key), GET /api/v1/kb (X-Service-Key;
gerçek backend'e bu görevde eklendi). Ağ kullanmaz.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ortak.llm import LLMYanit, SahteSaglayici

ANAHTAR = "test-servis-anahtari"

# DemoDataSeeder.SeedKnowledgeBase — backend'in döndürdüğü biçimde (Türkçe karaktersiz).
SEED_KB = [
    ("Dispepsi", "Dispepsi (hazimsizlik)", "Midede yanma, sisme ve erken doyma ile giden hazimsizlik tablosu. Genellikle mide asidinin fazla uretilmesine baglidir.", "DIAGNOSIS", ["hazimsizlik", "mide sikayeti"]),
    ("Lansoprazol", "Lansoprazol", "Mide asidi uretimini azaltan ilac. Gunde bir kez, kahvaltidan yarim saat once ac karnina alinir.", "MEDICATION", ["lansor", "proton pompa inhibitoru"]),
    ("Gastrit", "Gastrit (mide iltihabi)", "Mide ic yuzeyinin tahris olmasi. Acili veya yanma hissi veren sikayetlere yol acar.", "DIAGNOSIS", ["mide iltihabi"]),
    ("Famotidin", "Famotidin", "Mide asidini azaltan ilac. Genellikle aksam yemeginden sonra alinir.", "MEDICATION", []),
    ("Hipertansiyon", "Hipertansiyon (yuksek tansiyon)", "Kan basincinin surekli yuksek seyretmesi. Tuz kisitlamasi ve duzenli ilac kullanimi gerektirir.", "DIAGNOSIS", ["yuksek tansiyon", "tansiyon"]),
    ("Amlodipin", "Amlodipin", "Damarlari gevseterek tansiyonu dusuren ilac. Gunde bir kez, her gun ayni saatte alinir.", "MEDICATION", []),
    ("Tip 2 Diyabet", "Tip 2 diyabet (seker hastaligi)", "Vucudun insulini yeterince kullanamamasi. Beslenme duzeni ve ilac ile kontrol altina alinir.", "DIAGNOSIS", ["seker hastaligi", "diyabet"]),
    ("Metformin", "Metformin", "Kan sekerini dengeleyen ilac. Mide sikayetini azaltmak icin yemekle birlikte alinir.", "MEDICATION", []),
    ("Demir Eksikligi Anemisi", "Demir eksikligi anemisi (kansizlik)", "Kandaki demir deposunun azalmasi. Halsizlik ve solukluk yapar.", "DIAGNOSIS", ["kansizlik", "anemi"]),
    ("Demir Sulfat", "Demir sulfat", "Demir deposunu dolduran ilac. C vitamini iceren meyve suyuyla, sut ve cay olmadan alinir.", "MEDICATION", ["demir ilaci"]),
    ("Migren", "Migren", "Zonklayici, genellikle tek tarafli bas agrisi. Isik ve sese hassasiyet eslik edebilir.", "DIAGNOSIS", []),
    ("Parasetamol", "Parasetamol", "Agri ve ates dusurucu ilac. Gunde en fazla belirtilen dozda ve tok karnina alinir.", "MEDICATION", ["agri kesici"]),
    ("Akut Sinuzit", "Akut sinuzit", "Burun cevresindeki sinuslerin iltihaplanmasi. Yuzde basinc hissi ve burun tikanikligi yapar.", "DIAGNOSIS", ["sinuzit"]),
    ("Amoksisilin", "Amoksisilin", "Bakteri kaynakli enfeksiyonlarda kullanilan antibiyotik. Kur yarida birakilmaz.", "MEDICATION", ["antibiyotik"]),
    ("Hipotiroidi", "Hipotiroidi (tiroid bezinin az calismasi)", "Tiroid hormonunun yetersiz uretilmesi. Halsizlik, usume ve kilo artisi yapabilir.", "DIAGNOSIS", ["tiroid"]),
    ("Levotiroksin", "Levotiroksin", "Eksik tiroid hormonunu yerine koyan ilac. Sabah ac karnina, bir bardak su ile alinir.", "MEDICATION", []),
]


def seed_kb_json() -> list[dict[str, Any]]:
    return [{"id": str(uuid.uuid4()), "term": t, "title": b, "body": g, "kind": k, "synonyms": list(s)}
            for t, b, g, k, s in SEED_KB]


@dataclass
class SahteBackend:
    kb_modu: str = "404"                      # "404" | "liste" | "ag"
    put_yanitlari: list[Any] = field(default_factory=list)   # sırayla: 200/400/404/500/"ag"; bitince 200
    kb: list[dict[str, Any]] = field(default_factory=seed_kb_json)
    putlar: list[dict[str, Any]] = field(default_factory=list)
    kb_cagri: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._isle)

    def _isle(self, istek: httpx.Request) -> httpx.Response:
        yol = istek.url.path
        if istek.headers.get("X-Service-Key") != ANAHTAR:
            return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED", "message": "x", "requestId": "r"}})
        if yol == "/api/v1/kb" and istek.method == "GET":
            self.kb_cagri += 1
            if self.kb_modu == "ag":
                raise httpx.ConnectError("bağlantı reddedildi", request=istek)
            if self.kb_modu == "404":
                return httpx.Response(404)
            return httpx.Response(200, json=self.kb)
        if yol.startswith("/api/v1/summaries/") and istek.method == "PUT":
            sid = yol.rsplit("/", 1)[-1]
            durum = self.put_yanitlari.pop(0) if self.put_yanitlari else 200
            self.putlar.append({"summaryId": sid, "govde": json.loads(istek.content), "durum": durum,
                                "basliklar": dict(istek.headers)})
            if durum == "ag":
                raise httpx.ConnectError("bağlantı reddedildi", request=istek)
            if durum >= 400:
                kod = {400: "VALIDATION_FAILED", 404: "NOT_FOUND", 409: "INVALID_STATUS_TRANSITION"}.get(durum, "INTERNAL_ERROR")
                return httpx.Response(durum, json={"error": {"code": kod, "message": "x", "requestId": "r"}})
            return httpx.Response(200, json={"summaryId": sid, "status": "READY"})
        return httpx.Response(404)

    @property
    def basarili_putlar(self) -> list[dict[str, Any]]:
        return [p for p in self.putlar if p["durum"] == 200]


def bekle(kosul: Callable[[], bool], sure: float = 5.0) -> bool:
    son = time.monotonic() + sure
    while time.monotonic() < son:
        if kosul():
            return True
        time.sleep(0.02)
    return kosul()


class YavasSaglayici(SahteSaglayici):
    """Her çağrıda ``gecikme_sn`` bekleyen sahte LLM (202'nin hemen döndüğünü sınamak için)."""

    def __init__(self, *a: Any, gecikme_sn: float = 1.5, **kw: Any):
        super().__init__(*a, **kw)
        self.gecikme_sn = gecikme_sn

    async def _sohbet(self, *a: Any, **kw: Any) -> LLMYanit:
        await asyncio.sleep(self.gecikme_sn)
        return await super()._sohbet(*a, **kw)


def llm_json(tani: str = "", ilaclar: list[tuple[str, ...]] | None = None) -> str:
    """Sahte LLM yanıtı. Öğe: (no, amaç) ya da (no, amaç, kullanım) — kullanım artık yok sayılır."""
    oge = []
    for n, a, *k in ilaclar or []:
        oge.append({"no": n, "amac": a, **({"kullanim": k[0]} if k and k[0] else {})})
    return json.dumps({"tani": tani, "ilaclar": oge}, ensure_ascii=False)


def sade_ortami(tmp: Path, **degisen: str) -> dict[str, str]:
    ortam = {"SERVICE_KEY": ANAHTAR, "HASTAKABUL_API_URL": "http://sahte-api",
             "SADE_KUYRUK_DOSYASI": str(tmp / "kuyruk.sqlite3"), "SADE_GERI_CAGRI_TABAN_BEKLEME_SN": "0.01",
             "SADE_GERI_CAGRI_AZAMI_BEKLEME_SN": "0.05", "SADE_GERI_CAGRI_AZAMI_DENEME": "5"}
    ortam.update(degisen)
    return ortam
