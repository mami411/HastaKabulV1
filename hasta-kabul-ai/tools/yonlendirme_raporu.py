"""Yönlendirme değerlendirme setini (tests/yonlendirme_seti.py) orkestratör üzerinden koşturup tablo basar.

Ağ yok: sahte API + sahte LLM (tests/conftest.py düzeneği). Beklenen yoldan sapan cümleler yanıtlarıyla listelenir.

    .venv\\Scripts\\python.exe tools\\yonlendirme_raporu.py [--hepsi] [--json DOSYA]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))
sys.path.insert(0, str(KOK / "tests"))

from conftest import Kurulum  # noqa: E402
from test_yonlendirme_seti import sahte_llm, yol_bul  # noqa: E402
from yonlendirme_seti import VAKALAR  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hepsi", action="store_true", help="doğru gidenleri de yaz")
    ap.add_argument("--json", help="sonuçları bu dosyaya yaz")
    arg = ap.parse_args()
    sonuclar = []
    for v in VAKALAR:
        k = Kurulum(sahte_llm())
        k.client.__enter__()
        try:
            oid = k.api.oturum_ac("VERIFIED")
            k.orkestre(oid, event="TC_VERIFIED")
            for onceki in v.once:
                k.orkestre(oid, userText=onceki)
            govde = k.orkestre(oid, userText=v.metin).json()
        finally:
            k.client.__exit__(None, None, None)
        yol = yol_bul(govde)
        sonuclar.append({"metin": v.metin, "once": list(v.once), "beklenen": v.yol, "gercek": yol,
                         "dogru": yol == v.yol, "kullanici": v.kullanici, "yanit": govde["replyText"]})
    yanlis = [s for s in sonuclar if not s["dogru"]]
    print(f"Toplam {len(sonuclar)} cümle, doğru {len(sonuclar) - len(yanlis)}, yanlış {len(yanlis)}")
    toplam = Counter(s["beklenen"] for s in sonuclar)
    hata = Counter(s["beklenen"] for s in yanlis)
    for yol in sorted(toplam):
        print(f"  {yol:11s} {toplam[yol] - hata[yol]:3d}/{toplam[yol]:3d}")
    for s in (sonuclar if arg.hepsi else yanlis):
        isaret = "OK " if s["dogru"] else "YANLIŞ"
        once = f"  [önce: {' / '.join(s['once'])}]" if s["once"] else ""
        print(f"\n{isaret} beklenen={s['beklenen']} gerçek={s['gercek']}{once}\n  » {s['metin']}\n  « {s['yanit']}")
    if arg.json:
        Path(arg.json).write_text(json.dumps(sonuclar, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
