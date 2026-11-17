"""Sadeleştirme servisinin ayarları — hepsi ortam değişkeninden (bkz. ``.env.example``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ortak.ayarlar import ortam_bool, ortam_float, ortam_int, ortam_str

PROJE_KOKU = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SadeAyarlari:
    # Sunucu
    host: str
    port: int
    # HastaKabul API
    api_url: str
    servis_anahtari: str | None
    api_zaman_asimi_sn: float
    # LLM (sağlayıcı ayarları SADE_LLM_* / LLM_* — ortak/llm/fabrika.py)
    llm_etkin: bool
    llm_onek: str
    uretim_zaman_asimi_sn: float
    azami_token: int
    # Bilgi tabanı
    kb_kaynagi: str            # auto (backend → gömülü) | gomulu
    kb_yenileme_sn: float
    # Kuyruk ve callback
    kuyruk_dosyasi: str
    azami_kuyruk: int
    geri_cagri_azami_deneme: int
    geri_cagri_taban_bekleme_sn: float
    geri_cagri_azami_bekleme_sn: float
    # Girdi sınırları
    azami_tani: int
    azami_ilac: int
    azami_ilac_uzunlugu: int

    @classmethod
    def ortamdan(cls, ortam: Mapping[str, str] | None = None) -> SadeAyarlari:
        def s(ad: str, v: str) -> str:
            return ortam_str(ad, v, ortam) or v

        return cls(
            host=s("SADE_HOST", "127.0.0.1"),
            port=ortam_int("SADE_PORT", 8001, ortam),
            api_url=s("HASTAKABUL_API_URL", "http://localhost:3000"),
            servis_anahtari=ortam_str("SERVICE_KEY", None, ortam),
            api_zaman_asimi_sn=ortam_float("SADE_API_ZAMAN_ASIMI_SN", 10.0, ortam),
            llm_etkin=ortam_bool("SADE_LLM_ETKIN", True, ortam),
            llm_onek=s("SADE_LLM_ONEK", "SADE_LLM"),
            uretim_zaman_asimi_sn=ortam_float("SADE_URETIM_ZAMAN_ASIMI_SN", 60.0, ortam),
            azami_token=ortam_int("SADE_AZAMI_TOKEN", 700, ortam),
            kb_kaynagi=s("SADE_KB_KAYNAGI", "auto").lower(),
            kb_yenileme_sn=ortam_float("SADE_KB_YENILEME_SN", 600.0, ortam),
            kuyruk_dosyasi=s("SADE_KUYRUK_DOSYASI", str(PROJE_KOKU / "veri" / "sade_kuyruk.sqlite3")),
            azami_kuyruk=ortam_int("SADE_AZAMI_KUYRUK", 200, ortam),
            geri_cagri_azami_deneme=ortam_int("SADE_GERI_CAGRI_AZAMI_DENEME", 12, ortam),
            geri_cagri_taban_bekleme_sn=ortam_float("SADE_GERI_CAGRI_TABAN_BEKLEME_SN", 2.0, ortam),
            geri_cagri_azami_bekleme_sn=ortam_float("SADE_GERI_CAGRI_AZAMI_BEKLEME_SN", 300.0, ortam),
            azami_tani=ortam_int("SADE_AZAMI_TANI", 1000, ortam),
            azami_ilac=ortam_int("SADE_AZAMI_ILAC", 30, ortam),
            azami_ilac_uzunlugu=ortam_int("SADE_AZAMI_ILAC_UZUNLUGU", 500, ortam),
        )
