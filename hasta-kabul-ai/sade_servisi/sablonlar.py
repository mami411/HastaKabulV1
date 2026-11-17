"""Onaylı sabit metinler ve LLM'siz deterministik şablon özet.

Buradaki cümleler koddadır, LLM üretmez; yine de hekim onayından geçmeden hastaya gitmez.
Uyarılar YALNIZ bu dosyadan gelir (LLM uyarı üretmez).
"""

from __future__ import annotations

import re
from typing import Any

from ortak.turkce import normalize

from .eslesme import EslemeSonucu, IlacEslesmesi, gorunur_metin

AZAMI_TANI_METNI = 2000

# ---- Uyarılar (kod seçer) -------------------------------------------------------
UYARI_ILAC = "İlaçlarınızı hekiminize danışmadan bırakmayın ve dozlarını değiştirmeyin."
UYARI_BILINMEYEN = "Açıklaması verilmeyen tanı ya da ilaçlar için hekiminize veya eczacınıza danışın."
UYARI_GENEL = "Şikayetleriniz artarsa ya da yeni bir şikayetiniz olursa hekiminize başvurun."

# ---- Bilinmeyen terimler -------------------------------------------------------
TANI_BILINMEYEN_TAMAMI = ("Hekiminizin koyduğu tanı için bilgi kaynağımızda sade bir açıklama bulunmuyor. "
                          "Tanınızın ne anlama geldiğini hekiminize sorun.")
TANI_BILINMEYEN_EK = ("Hekiminizin yazdığı bazı tanılar için bilgi kaynağımızda açıklama yok. "
                      "Bunların ne anlama geldiğini hekiminize sorun.")
ILAC_BILINMEYEN_AMAC = ("Bu ilaç için bilgi kaynağımızda açıklama yok. Ne için kullandığınızı hekiminize "
                        "ya da eczacınıza sorun.")
ILAC_KULLANIM_SORUN = "Nasıl kullanacağınızı hekiminize ya da eczacınıza sorun."
ILAC_KULLANIM_RECETEDEKI = "Hekiminizin reçetede yazdığı şekilde kullanın."


# ---- Hekimin kullanım ifadesi ------------------------------------------------------
# Hekim satırındaki kullanım kısmı ("30 mg 1x1 akşam tok karnına", "500 mg gerektiğinde günde en fazla 3 kez")
# yalnız bu sözlükteki kelimelerden oluşuyorsa hastaya AYNEN gösterilir: hekimin kendi talimatıdır,
# LLM'den geçmez, kaybolmaz. Sözlük dışı kelime varsa (serbest cümle, talimat) aynen yazılmaz.
KULLANIM_SOZLUGU = frozenset("""
x po oral agizdan dil alti mg g gr mcg ug ml cc iu unite tablet tb tab tbl kapsul kaps kap film kapli efervesan
surup damla sase ampul flakon puf olcek kasik adet tane yarim ceyrek bir iki uc dort bes alti bucuk gunde
gunluk haftada ayda kez defa kere sefer her saatte saat saatlik gun gunluk hafta ay boyunca sureyle toplam
sabah sabahlari aksam aksamlari ogle oglen ogleyin gece geceleri yatmadan yatarken once sonra ac tok karnina
yemek yemekle yemekten yemeklerden yemekte yemeklerle kahvalti kahvaltidan kahvaltida kahvaltiyla birlikte ile
ve veya ya da gerektiginde gerekirse ihtiyac halinde durumunda agri ates olursa oldugunda en fazla az kadar
""".split())
# Zamanlama kelimeleri: hekim bunlardan birini yazdıysa KB'deki genel zamanlama ("kahvaltıdan önce aç karnına")
# eklenmez — hekimin zamanlamasıyla çelişebilir.
ZAMANLAMA_KELIMELERI = frozenset("""
sabah sabahlari aksam aksamlari ogle oglen ogleyin gece geceleri yatmadan yatarken once sonra ac tok karnina
yemek yemekle yemekten yemeklerden yemekte yemeklerle yemeginden yemeklerinden kahvalti kahvaltidan
kahvaltida kahvaltiyla gerektiginde gerekirse ihtiyac saatte saatte
""".split())
AZAMI_KULLANIM_YANSIMA = 120
# Amaç ("ne işe yarar") metninde bulunmaması gereken kullanım izleri: amaç alanına KB'nin genel
# kullanım cümlesi ("Günde bir kez … alınır") taşınırsa hekimin sıklık/zamanlamasıyla çelişebilir.
KULLANIM_IZLERI = ZAMANLAMA_KELIMELERI | frozenset("""
gunde haftada ayda doz dozda dozu kez defa alin alinir aliniz alinmali alinmalidir alinmaz kullanin kullaniniz
icin bardak
""".split()) - frozenset({"icin"})


def _kelimeler(metin: str) -> list[str]:
    return normalize(metin).split()


def _sozluk_ici(kelime: str) -> bool:
    return kelime in KULLANIM_SOZLUGU or re.fullmatch(
        r"\d+(?:mg|g|gr|mcg|ml|cc|x\d+|tb|tab|tablet)?|\d+x|x\d+", kelime) is not None


def hekim_kullanimi_temiz(kullanim: str) -> str | None:
    """Hekimin kullanım ifadesi yalnız doz/sıklık/zamanlama kelimelerinden oluşuyorsa görünür metni."""
    temiz = gorunur_metin(kullanim).strip(" .,;:-–")
    kelimeler = _kelimeler(temiz)
    if not kelimeler or len(temiz) > AZAMI_KULLANIM_YANSIMA or not all(_sozluk_ici(k) for k in kelimeler):
        return None
    return temiz


def zamanlama_var(metin: str) -> bool:
    return any(k in ZAMANLAMA_KELIMELERI for k in _kelimeler(metin))


_KULLANIM_KOKLERI = ("alin", "alma", "kullanin", "kullaniniz", "kullanilir", "kullanilmali", "yutul", "icil",
                     "icin", "icilir")


def kullanim_izi_var(metin: str) -> bool:
    """Doz/sıklık/zamanlama ya da alma/kullanma fiili (alınır, alınıyor, almayın, kullanın…) var mı?"""
    return any(k in KULLANIM_IZLERI or k.isdigit() or (k.startswith(_KULLANIM_KOKLERI) and k != "icin")
               for k in _kelimeler(metin))


def kb_amac_metni(govde: str) -> str | None:
    """KB gövdesinden yalnız kullanım izi taşımayan cümleler (onaylı sade metni olmayan kayıt için)."""
    cumleler = [c.strip() for c in re.split(r"(?<=[.!?])\s+", govde.strip()) if c.strip()]
    temiz = [c if c[-1] in ".!?" else c + "." for c in cumleler if not kullanim_izi_var(c)]
    return " ".join(temiz) or None


def kanonik_cumle(ilac: IlacEslesmesi) -> str | None:
    acik = ilac.satir.kullanim_acik
    return f"Hekiminizin yazdığı kullanım: {acik[0].lower()}{acik[1:]}." if acik else None


def hekim_kullanim_cumlesi(ilac: IlacEslesmesi) -> str | None:
    """Hekimin yazdığından türeyen kullanım cümlesi (LLM yok): aynen yansıma + kanonik açıklama."""
    kullanim = ilac.satir.kullanim
    if not kullanim:
        return None
    temiz = hekim_kullanimi_temiz(kullanim)
    acik = ilac.satir.kullanim_acik
    if temiz and acik:
        return f"Reçetede yazan: {temiz}. Yani {acik[0].lower()}{acik[1:]}."
    if temiz:
        return f"Reçetede yazan: {temiz}."
    if acik:
        return f"{kanonik_cumle(ilac)} Ayrıntılar için reçetenize bakın."
    return ILAC_KULLANIM_RECETEDEKI


def bilinmeyen_ilac(ilac: IlacEslesmesi) -> dict[str, str]:
    hekim = hekim_kullanim_cumlesi(ilac)
    kullanim = f"{hekim} Sorunuz olursa hekiminize ya da eczacınıza sorun." if hekim else ILAC_KULLANIM_SORUN
    return {"name": ilac.gorunen_ad, "purpose": ILAC_BILINMEYEN_AMAC, "usage": kullanim}


def ilac_kullanimi(ilac: IlacEslesmesi) -> str:
    """Bilinen ilacın kullanım metni. Her zaman koddan gelir (LLM kullanım yazmaz).

    Sıklık ve doz yalnız hekim satırından; KB'deki genel zamanlama yalnız hekimin sıklığıyla
    çelişmiyorsa VE (KB zamanlaması bir zaman ifadesiyse) hekim kendi zamanlamasını yazmadıysa eklenir.
    """
    kayit = ilac.kayit
    assert kayit is not None
    sade = kayit.sade
    hekim = hekim_kullanim_cumlesi(ilac)
    if hekim is None:
        return sade.kullanim if sade and sade.kullanim else ILAC_KULLANIM_RECETEDEKI
    zamanlama = sade.zamanlama if sade else ""
    if zamanlama:
        siklik_uyumlu = sade.kb_siklik is None or sade.kb_siklik == ilac.satir.siklik
        hekim_zamani = hekim_kullanimi_temiz(ilac.satir.kullanim) is None or zamanlama_var(ilac.satir.kullanim)
        if not siklik_uyumlu or (zamanlama_var(zamanlama) and hekim_zamani):
            zamanlama = ""
    return f"{hekim} {zamanlama}".strip()


def bilinen_ilac_sablonu(ilac: IlacEslesmesi, amac: str | None = None) -> dict[str, str]:
    kayit = ilac.kayit
    assert kayit is not None
    sade = kayit.sade
    if not amac:
        if sade and sade.amac:
            amac = sade.amac
        else:
            kb_amac = kb_amac_metni(kayit.govde)
            amac = f"Bilgi notu: {kb_amac}" if kb_amac else ILAC_BILINMEYEN_AMAC
    return {"name": ilac.gorunen_ad, "purpose": amac, "usage": ilac_kullanimi(ilac)}


def tani_metni(esleme: EslemeSonucu, eslesen_aciklama: str | None) -> str:
    parcalar: list[str] = []
    if esleme.tanilar and eslesen_aciklama:
        parcalar.append(eslesen_aciklama)
    if esleme.bilinmeyen_tanilar:
        parcalar.append(TANI_BILINMEYEN_EK if parcalar else TANI_BILINMEYEN_TAMAMI)
    if not parcalar:
        parcalar.append(TANI_BILINMEYEN_TAMAMI)
    metin = " ".join(parcalar)
    return metin if len(metin) <= AZAMI_TANI_METNI else metin[: AZAMI_TANI_METNI - 1].rstrip() + "…"


def sablon_tani_aciklamasi(esleme: EslemeSonucu) -> str:
    cumleler = []
    for t in esleme.tanilar:
        sade = t.kayit.sade
        cumleler.append(sade.aciklama if sade and sade.aciklama else f"Tanınız: {t.kayit.baslik}. {t.kayit.govde}")
    return " ".join(cumleler)


def uyarilar(esleme: EslemeSonucu) -> list[str]:
    liste: list[str] = []
    if esleme.ilaclar:
        liste.append(UYARI_ILAC)
    for ilac in esleme.bilinen_ilaclar:
        sade = ilac.kayit.sade if ilac.kayit else None
        if sade and sade.uyari and sade.uyari not in liste:
            liste.append(sade.uyari)
    if esleme.bilinmeyenler:
        liste.append(UYARI_BILINMEYEN)
    liste.append(UYARI_GENEL)
    return liste


def govde_olustur(esleme: EslemeSonucu, tani_aciklamasi: str | None,
                  ilac_amaclari: dict[str, str] | None) -> dict[str, Any]:
    """Callback gövdesi. ``ilac_amaclari``: no → LLM'in (denetlenmiş) amaç cümlesi; yoksa şablon.

    Kullanım (doz, sıklık, zamanlama) HER ZAMAN koddan gelir.

    İlaç listesi girdiyle birebir aynı sıradadır; ad, sourceIds, unknownTerms ve uyarılar koddan gelir.
    """
    ilaclar: list[dict[str, str]] = []
    for ilac in esleme.ilaclar:
        if ilac.kayit is None:
            ilaclar.append(bilinmeyen_ilac(ilac))
        else:
            ilaclar.append(bilinen_ilac_sablonu(ilac, (ilac_amaclari or {}).get(ilac.no)))
    return {
        "diagnosisPlain": tani_metni(esleme, tani_aciklamasi),
        "medications": ilaclar,
        "warnings": uyarilar(esleme),
        "unknownTerms": esleme.bilinmeyenler,
        "sourceIds": esleme.kaynak_idleri,
    }


def sablon_govdesi(esleme: EslemeSonucu) -> dict[str, Any]:
    return govde_olustur(esleme, sablon_tani_aciklamasi(esleme), None)
