"""Kurum bilgisinin üç kopyası tutarlı mı? (ön yüz brand.ts, backend appsettings.json, asistan kurum.json)

Kullanım:  py -3 C:\\Dev\\HastaKabul\\tools\\kurum-denetle.py     (çıkış kodu 0 = tutarlı, 1 = fark var)
Yalnız standart kitaplık; sır okumaz (appsettings.Local.json, .env okunmaz). AI testleri de bu modülü çağırır
(tests/test_kurum_tutarlilik.py). Ayrıntı: kökteki KURUM.md.

Denetlenenler:
- tam ad, kısa ad, uygulama adı, asistan adı, çağrı merkezi numarası üç dosyada aynı;
- brand.ts `callCenter.tel` görünen numarayla aynı rakamlar (+90 + baştaki 0'sız numara);
- asistanın çalışma saatleri cümlesi, backend'in slot ürettiği blokların başlangıç/bitiş saatlerini içeriyor;
- poliklinik kodları ve adları backend kataloğu (DemoKatalog.cs) ile asistan (POLIKLINIKLER) arasında aynı.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
BRAND_TS = KOK / "hasta-kabul-frontend" / "src" / "brand.ts"
APPSETTINGS = KOK / "hasta-kabul-backend" / "src" / "HastaKabul.Api" / "appsettings.json"
KURUM_JSON = KOK / "hasta-kabul-ai" / "kurum.json"
DEMO_KATALOG = (KOK / "hasta-kabul-backend" / "src" / "HastaKabul.Infrastructure" / "Persistence" / "Seed"
                / "DemoKatalog.cs")
ALAN_PY = KOK / "hasta-kabul-ai" / "ses_servisi" / "alanlar" / "hasta_kabul.py"


def _ts_alan(metin: str, ad: str) -> str | None:
    m = re.search(rf"\b{ad}:\s*\"([^\"]*)\"", metin)
    return m.group(1) if m else None


def brand_oku(yol: Path = BRAND_TS) -> dict[str, str | None]:
    metin = yol.read_text(encoding="utf-8")
    cagri = re.search(r"callCenter:\s*\{(.*?)\}", metin, re.S)
    cagri_metni = cagri.group(1) if cagri else ""
    return {
        "tam_ad": _ts_alan(metin, "fullName"),
        "kisa_ad": _ts_alan(metin, "shortName"),
        "uygulama_adi": _ts_alan(metin, "appName"),
        "asistan_adi": _ts_alan(metin, "assistantName"),
        "cagri_merkezi": _ts_alan(cagri_metni, "display"),
        "cagri_tel": _ts_alan(cagri_metni, "tel"),
    }


def backend_oku(yol: Path = APPSETTINGS) -> dict:
    kurum = json.loads(yol.read_text(encoding="utf-8-sig")).get("Kurum") or {}
    return {
        "tam_ad": kurum.get("TamAd"),
        "kisa_ad": kurum.get("KisaAd"),
        "uygulama_adi": kurum.get("UygulamaAdi"),
        "asistan_adi": kurum.get("AsistanAdi"),
        "cagri_merkezi": kurum.get("CagriMerkezi"),
        "saatler": kurum.get("CalismaSaatleri") or {},
    }


def asistan_oku(yol: Path = KURUM_JSON) -> dict:
    return json.loads(yol.read_text(encoding="utf-8"))


def _katalog_backend(yol: Path = DEMO_KATALOG) -> dict[str, str]:
    metin = yol.read_text(encoding="utf-8")
    # Poliklinik satırları: new("KOD", "Ad", [...]) — hekim satırlarında üçüncü argüman dizi değildir.
    return dict(re.findall(r"new\(\"([A-Z_]+)\",\s*\"([^\"]+)\",\s*\[", metin))


def _katalog_asistan(yol: Path = ALAN_PY) -> dict[str, str]:
    metin = yol.read_text(encoding="utf-8")
    return dict(re.findall(r"Poliklinik\(\"([A-Z_]+)\",\s*\"([^\"]+)\"", metin))


def denetle() -> list[str]:
    """Bulunan tutarsızlıklar (boş liste = tutarlı)."""
    hatalar: list[str] = []
    on, arka, ai = brand_oku(), backend_oku(), asistan_oku()
    for alan, etiket in (("tam_ad", "tam ad"), ("kisa_ad", "kısa ad"), ("uygulama_adi", "uygulama adı"),
                         ("asistan_adi", "asistan adı"), ("cagri_merkezi", "çağrı merkezi")):
        degerler = {"brand.ts": on.get(alan), "appsettings.json": arka.get(alan), "kurum.json": ai.get(alan)}
        if None in degerler.values() or len(set(degerler.values())) != 1:
            hatalar.append(f"{etiket} farklı ya da eksik: {degerler}")

    rakam = re.sub(r"\D", "", on.get("cagri_merkezi") or "")
    tel = on.get("cagri_tel") or ""
    beklenen_tel = "+90" + rakam[1:] if rakam.startswith("0") else "+90" + rakam
    if tel != beklenen_tel:
        hatalar.append(f"brand.ts callCenter.tel ({tel}) görünen numarayla ({on.get('cagri_merkezi')}) uyuşmuyor; "
                       f"beklenen {beklenen_tel}")

    saat_cumlesi = ai.get("calisma_saatleri") or ""
    for gun, bloklar in (arka.get("saatler") or {}).items():
        for blok in bloklar or []:
            for saat in str(blok).split("-"):
                if saat.strip() and saat.strip() not in saat_cumlesi:
                    hatalar.append(f"kurum.json calisma_saatleri '{saat.strip()}' saatini içermiyor "
                                   f"(appsettings {gun}: {blok})")

    kb, ka = _katalog_backend(), _katalog_asistan()
    if not kb or not ka:
        hatalar.append("poliklinik kataloğu okunamadı (DemoKatalog.cs ya da POLIKLINIKLER biçimi değişmiş)")
    elif kb != ka:
        hatalar.append(f"poliklinik kodu/adı farklı: yalnız backend {sorted(set(kb.items()) - set(ka.items()))}, "
                       f"yalnız asistan {sorted(set(ka.items()) - set(kb.items()))}")
    return hatalar


def main() -> int:
    hatalar = denetle()
    if hatalar:
        print("Kurum bilgisi TUTARSIZ:")
        for h in hatalar:
            print(" -", h)
        return 1
    print("Kurum bilgisi tutarlı (brand.ts, appsettings.json, kurum.json, poliklinik kataloğu).")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
