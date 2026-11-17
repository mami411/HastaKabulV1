"""Ses servisinin ayarları — hepsi ortam değişkeninden (bkz. ``.env.example``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ortak.ayarlar import ortam_bool, ortam_float, ortam_int, ortam_liste, ortam_str

PROJE_KOKU = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class SesAyarlari:
    # Sunucu
    host: str
    port: int
    cors_kaynaklari: list[str]
    # HastaKabul API
    api_url: str
    servis_anahtari: str | None
    api_zaman_asimi_sn: float
    # Akış
    oturum_omru_dk: int
    azami_oturum: int
    arama_gun_sayisi: int
    sunulan_slot_sayisi: int
    slot_min_once_dk: int
    azami_metin_uzunlugu: int
    bosta_kalma_sn: float
    azami_json_govde_bayt: int
    alternatif_gun_sayisi: int
    randevu_ufuk_gun: int
    # Geçmiş muayene / ilaç soruları
    gecmis_sesli_okuma: bool
    gecmis_ikinci_faktor: bool
    gecmis_limit: int
    # Kuralın çözemediği ilaç sorusunun konusunu LLM'e sor (varsayılan kapalı: yerel 4B modelle ölçümde
    # 10 cümlenin 4'ünde yanlış konu verdi, ör. "ile şarap" → alerji). Kapalıyken yanıt "onaylı bilgim yok".
    ilac_konu_llm: bool
    # Genel sağlık/ilaç bilgi sorusu yerel LLM'le kısa genel bilgi olarak yanıtlanır (yalnız kimliği maskeli soru
    # gider; çıktı kodda denetlenir; kapanış koddan). Kapalıyken eski davranış ("tıbbi yorum yapamıyorum").
    saglik_bilgi_llm: bool
    saglik_bilgi_zaman_asimi_sn: float
    # Bilgi yolu için ayrı model (boşsa anlama LLM'i kullanılır). Ayarlar SAGLIK_LLM_* (yoksa LLM_*) okunur.
    saglik_llm_model: str | None
    # LLM
    llm_etkin: bool
    llm_onek: str
    anlama_zaman_asimi_sn: float
    # STT
    stt_saglayici: str
    stt_model: str
    stt_cihaz: str
    stt_hesaplama: str
    stt_dil: str
    stt_isin: int
    stt_model_dizini: str
    stt_cuda_dll_dizinleri: tuple[str, ...]
    stt_azami_bayt: int
    stt_azami_saniye: float
    stt_zaman_asimi_sn: float
    stt_onceden_yukle: bool
    stt_bulut_url: str | None
    stt_bulut_anahtar: str | None
    # TTS
    tts_model_yolu: str
    tts_konusma_hizi: float
    tts_azami_karakter: int
    tts_zaman_asimi_sn: float
    tts_onceden_yukle: bool
    tts_cuda: bool
    # Gözlem
    teshis_logu: bool

    @classmethod
    def ortamdan(cls, ortam: Mapping[str, str] | None = None) -> SesAyarlari:
        def s(ad: str, v: str | None) -> str | None:
            return ortam_str(ad, v, ortam)

        model_dizini = s("MODEL_DIZINI", str(PROJE_KOKU / "modeller")) or str(PROJE_KOKU / "modeller")
        return cls(
            host=s("SES_HOST", "127.0.0.1") or "127.0.0.1",
            port=ortam_int("SES_PORT", 8002, ortam),
            cors_kaynaklari=ortam_liste("CORS_IZINLI_KAYNAKLAR", [
                "http://localhost:5173", "http://localhost:5174", "http://localhost:5175",
                "http://127.0.0.1:5173", "http://127.0.0.1:5174", "http://127.0.0.1:5175",
            ], ortam),
            api_url=s("HASTAKABUL_API_URL", "http://localhost:3000") or "http://localhost:3000",
            servis_anahtari=s("SERVICE_KEY", None),
            api_zaman_asimi_sn=ortam_float("API_ZAMAN_ASIMI_SN", 8.0, ortam),
            oturum_omru_dk=ortam_int("OTURUM_OMRU_DK", 20, ortam),
            azami_oturum=ortam_int("AZAMI_OTURUM", 500, ortam),
            arama_gun_sayisi=ortam_int("ARAMA_GUN_SAYISI", 3, ortam),
            sunulan_slot_sayisi=ortam_int("SUNULAN_SLOT_SAYISI", 6, ortam),
            slot_min_once_dk=ortam_int("SLOT_MIN_ONCE_DK", 5, ortam),
            azami_metin_uzunlugu=ortam_int("AZAMI_METIN_UZUNLUGU", 500, ortam),
            bosta_kalma_sn=ortam_float("OTURUM_BOSTA_SN", 300.0, ortam),
            azami_json_govde_bayt=ortam_int("AZAMI_JSON_GOVDE_BAYT", 16 * 1024, ortam),
            alternatif_gun_sayisi=ortam_int("ALTERNATIF_GUN_SAYISI", 7, ortam),
            # Backend slotları bugün dahil bu kadar gün için açar; ötesi istenirse dürüst şablon.
            randevu_ufuk_gun=ortam_int("RANDEVU_UFUK_GUN", 8, ortam),
            gecmis_sesli_okuma=ortam_bool("GECMIS_SESLI_OKUMA", True, ortam),
            gecmis_ikinci_faktor=ortam_bool("GECMIS_IKINCI_FAKTOR", False, ortam),
            gecmis_limit=ortam_int("GECMIS_LIMIT", 3, ortam),
            ilac_konu_llm=ortam_bool("ILAC_KONU_LLM", False, ortam),
            saglik_bilgi_llm=ortam_bool("SAGLIK_BILGI_LLM", True, ortam),
            saglik_bilgi_zaman_asimi_sn=ortam_float("SAGLIK_BILGI_ZAMAN_ASIMI_SN", 10.0, ortam),
            saglik_llm_model=s("SAGLIK_LLM_MODEL", None),
            llm_etkin=ortam_bool("SES_LLM_ETKIN", True, ortam),
            llm_onek=s("SES_LLM_ONEK", "SES_LLM") or "SES_LLM",
            anlama_zaman_asimi_sn=ortam_float("ANLAMA_ZAMAN_ASIMI_SN", 12.0, ortam),
            stt_saglayici=(s("STT_SAGLAYICI", "yerel") or "yerel").lower(),
            stt_model=s("STT_MODEL", "large-v3-turbo") or "large-v3-turbo",
            stt_cihaz=(s("STT_CIHAZ", "auto") or "auto").lower(),
            stt_hesaplama=s("STT_HESAPLAMA", "auto") or "auto",
            stt_dil=s("STT_DIL", "tr") or "tr",
            stt_isin=ortam_int("STT_ISIN_GENISLIGI", 1, ortam),
            stt_model_dizini=s("STT_MODEL_DIZINI", str(Path(model_dizini) / "whisper")) or "",
            stt_cuda_dll_dizinleri=tuple(
                d.strip() for d in (s("STT_CUDA_DLL_DIZINLERI", "") or "").split(";") if d.strip()),
            stt_azami_bayt=ortam_int("STT_AZAMI_BAYT", 5 * 1024 * 1024, ortam),
            stt_azami_saniye=ortam_float("STT_AZAMI_SANIYE", 30.0, ortam),
            stt_zaman_asimi_sn=ortam_float("STT_ZAMAN_ASIMI_SN", 30.0, ortam),
            stt_onceden_yukle=ortam_bool("STT_ONCEDEN_YUKLE", True, ortam),
            stt_bulut_url=s("STT_BULUT_URL", None),
            stt_bulut_anahtar=s("STT_BULUT_ANAHTAR", None),
            tts_model_yolu=s("TTS_MODEL_YOLU", str(Path(model_dizini) / "piper" / "tr_TR-dfki-medium.onnx")) or "",
            tts_konusma_hizi=ortam_float("TTS_KONUSMA_HIZI", 1.0, ortam),
            tts_azami_karakter=ortam_int("TTS_AZAMI_KARAKTER", 600, ortam),
            tts_zaman_asimi_sn=ortam_float("TTS_ZAMAN_ASIMI_SN", 20.0, ortam),
            tts_onceden_yukle=ortam_bool("TTS_ONCEDEN_YUKLE", True, ortam),
            tts_cuda=ortam_bool("TTS_CUDA", False, ortam),
            teshis_logu=ortam_bool("TESHIS_LOGU", False, ortam),
        )
