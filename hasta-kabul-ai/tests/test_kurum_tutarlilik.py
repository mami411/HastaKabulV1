"""Kurum bilgisinin üç kopyası (ön yüz brand.ts, backend appsettings.json, asistan kurum.json) ve poliklinik
kataloğu tutarlı mı? Denetim kökteki ``tools/kurum-denetle.py``'dedir; depo kökü yoksa (yalnız AI kopyalandıysa)
test atlanır."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parents[2]
BETIK = KOK / "tools" / "kurum-denetle.py"

pytestmark = pytest.mark.skipif(not BETIK.exists(), reason="depo kökü (tools/kurum-denetle.py) yok")


def _modul():
    spec = importlib.util.spec_from_file_location("kurum_denetle", BETIK)
    modul = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(modul)
    return modul


def test_kurum_bilgisi_uc_yerde_tutarli():
    assert _modul().denetle() == []


def test_denetim_tutarsizligi_yakalar(tmp_path, monkeypatch):
    m = _modul()
    bozuk = json.loads(m.KURUM_JSON.read_text(encoding="utf-8"))
    bozuk["cagri_merkezi"] = "0850 111 11 11"
    bozuk["calisma_saatleri"] = "Hafta içi 08:00 ile 18:00 arası."
    dosya = tmp_path / "kurum.json"
    dosya.write_text(json.dumps(bozuk, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(m, "KURUM_JSON", dosya)
    monkeypatch.setattr(m, "asistan_oku", lambda yol=dosya: json.loads(dosya.read_text(encoding="utf-8")))
    hatalar = m.denetle()
    assert any("çağrı merkezi" in h for h in hatalar)
    assert any("09:00" in h for h in hatalar)
