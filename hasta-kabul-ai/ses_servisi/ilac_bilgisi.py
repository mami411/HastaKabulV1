"""İlaç sorusunun KONUSU ve konuya göre şablon yanıt (LLM'siz).

- ``konu_bul``: normalize cümleden konu kodu (kural; ``ILAC_KONU_KALIPLARI``).
- ``dagarcik_kaydi``: hekim onaylı kayıttaki ilaç adını bilgi dağarcığındaki kayda bağlar (kesin ad).
- ``metinde_dagarcik_ilaci_var`` / ``ilac_adlarini_maskele``: cümlede dağarcıktaki (ya da verilen) bir ilacın
  adı geçiyor mu; LLM'e gidecek kopyada adlar ``[ilaç]`` olur.
- ``konu_metni``: kullanıcıya okunacak cümle = konu modülü şablonları + hekim onaylı özet (doz/sıklık) +
  dağarcıktaki genel bilgi. Dağarcıkta yoksa "elimde onaylı bilgi yok" — uydurulmaz.
"""

from __future__ import annotations

import re
from types import ModuleType

from ortak.turkce import ascii_katla, ifade_gecer_mi, normalize

from .alanlar.ilac_dagarcigi import ILACLAR, IlacKaydi
from .gecmis import Ilac, Ziyaret, _ad_eslesir_mi, gecmis_metni

ILAC_MASKESI = "[ilaç]"
SAGLIK_MASKESI = "[sağlık bilgisi]"

# Konuya göre genel bilgi YOK, yanıt hekim onaylı özettir (ne işe yarar / nasıl kullanılır).
OZET_KONULARI = ("amac", "kullanim")

_SORU_EKLERI = frozenset({"mi", "mu", "miyim", "muyum", "misin", "musun", "midir", "mudur", "miydi", "muydu",
                          "miyiz", "muyuz", "misiniz", "musunuz", "mi?", "mu?"})


def soru_mu(n: str, ham: str = "") -> bool:
    """Normalize cümle soru mu? ("içebilir miyim", "olur mu", "?")."""
    return "?" in ham or any(k in _SORU_EKLERI for k in n.split())


def _gecer(n: str, ifadeler: tuple[str, ...]) -> bool:
    return any(ifade_gecer_mi(n, i) for i in ifadeler)


def konu_bul(n: str, alan: ModuleType) -> tuple[str | None, bool]:
    """(konu, güçlü mü). Güçlü: ifade tek başına ilaç sorusudur ("yan etkisi var mı").

    Birleşik konu ("dikkat": nelere dikkat etmeliyim) en genel konudur: cümlede güçlü ifadeli daha özel bir konu
    da varsa ("yan etkileri ve nelere dikkat") o konu seçilir."""
    kaliplar = getattr(alan, "ILAC_KONU_KALIPLARI", ())
    for sira, (konu, ifadeler, gucluler) in enumerate(kaliplar):
        if not _gecer(n, ifadeler):
            continue
        if konu == "unutma" and _gecer(n, getattr(alan, "ILAC_KONU_DISI_KELIMELER", ())):
            continue
        if konu == "yan_etki" and _yalniz_birinci_sahis_fiil(n, ifadeler, alan):
            continue  # "spor yapabilir miyim": ilacın yaptığı değil, kişinin yapacağı şey soruluyor
        if konu in getattr(alan, "ILAC_BIRLESIK_KONULAR", {}):
            ozel = next((k for k, _, g in kaliplar[sira + 1:] if g and _gecer(n, g)), None)
            if ozel is not None:
                return ozel, True
        return konu, _gecer(n, gucluler)
    return None, False


def konu_alt_kodlari(konu: str | None, alan: ModuleType) -> tuple[str, ...]:
    """Birleşik konunun alt konuları ("dikkat" → yemek, alkol); diğer konular kendisi."""
    if konu is None:
        return ()
    return tuple(getattr(alan, "ILAC_BIRLESIK_KONULAR", {}).get(konu, (konu,)))


def veri_konusu(konu: str | None, alan: ModuleType) -> str | None:
    """Yanıt verisindeki ``history.topic``: birleşik konuda ilk alt konu (kapalı kod listesi korunur)."""
    alt = konu_alt_kodlari(konu, alan)
    return alt[0] if alt else konu


def dagarcik_bilgisi(kayit: IlacKaydi | None, konu: str | None, alan: ModuleType) -> str | None:
    """Dağarcık kaydında konunun hazır metni; birleşik konuda bulunan alt konuların kısa birleşimi."""
    if kayit is None or konu is None:
        return None
    parcalar = [kayit.konular[k] for k in konu_alt_kodlari(konu, alan) if kayit.konular.get(k)]
    return " ".join(parcalar) or None


def _yalniz_birinci_sahis_fiil(n: str, ifadeler: tuple[str, ...], alan: ModuleType) -> bool:
    """Yan etki kalıbı yalnız "yapar mı / yapabilir mi" gibi genel fiilden geldi ve fiil birinci şahıs soru
    ("yapabilir miyim") ise True. "Uyku yapar mı" yan etki sorusudur, "spor yapabilir miyim" değildir."""
    genel = tuple(normalize(i) for i in getattr(alan, "ILAC_YAN_ETKI_GENEL_FIILLER", ()))
    eslesen = [i for i in ifadeler if ifade_gecer_mi(n, i)]
    if not eslesen or any(normalize(i) not in genel for i in eslesen):
        return False
    return bool(re.search(r"(?<!\w)(yapar|yapabilir|yapiyor|yapti) (miyim|miyiz|muyum|muyuz)(?!\w)", n))


# ---------------------------------------------------------------- dağarcık eşleşmesi

def dagarcik_kaydi(ilac_adi: str) -> IlacKaydi | None:
    """Kayıttaki ilaç adı ("Amlodipin", "Amlodipin 5 mg tablet", "Demir sülfat") dağarcıkta hangi kayıt?
    Eşleşme kesin addır (önek + kelime sınırı); bulanık değildir: benzer adlı başka ilacın bilgisi okunmaz."""
    ad = normalize(ilac_adi)
    if not ad:
        return None
    for kayit in ILACLAR:
        for aday in kayit.adlar:
            if ad == aday or ad.startswith(aday + " "):
                return kayit
    return None


def _metin_kokleri(yalniz_tam_ad: bool = False) -> tuple[str, ...]:
    return tuple({a.split()[0] for k in ILACLAR for a in (*k.adlar, *(() if yalniz_tam_ad else k.metin_adlari))
                  if len(a.split()) == 1 or not yalniz_tam_ad})


def _kelime_ilac_mi(kelime: str, adlar: tuple[str, ...]) -> bool:
    return any(_ad_eslesir_mi(kelime, ad) for ad in adlar)


def metinde_dagarcik_ilaci_var(n: str, yalniz_tam_ad: bool = False) -> bool:
    """Normalize cümlede dağarcıktaki bir ilacın adı (ekli/STT hatalı biçimi dahil) geçiyor mu?
    ``yalniz_tam_ad``: "demir" gibi genel kelimelerle başlayan adlar sayılmaz (soyadı "Demir" ilaç değildir)."""
    kokler = _metin_kokleri(yalniz_tam_ad)
    kelimeler = n.split()
    adaylar = [*kelimeler, *(a + b for a, b in zip(kelimeler, kelimeler[1:], strict=False))]
    return any(_kelime_ilac_mi(k, kokler) for k in adaylar)


def ilac_adlarini_maskele(metin: str, ek_adlar: tuple[str, ...] = ()) -> str:
    """Dağarcıktaki ve ``ek_adlar``daki (ör. hastanın kayıtlı ilaçları) ilaç adlarını ``[ilaç]`` yapar."""
    adlar = (*_metin_kokleri(), *(normalize(a).split()[0] for a in ek_adlar if normalize(a)))
    parcalar: list[str] = []
    for parca in re.split(r"(\s+)", metin):
        if not parca or parca.isspace():
            parcalar.append(parca)
            continue
        kok = normalize(re.sub(r"['’].*$", "", parca))
        tam = normalize(parca)
        if kok and (_kelime_ilac_mi(kok.replace(" ", ""), adlar) or _kelime_ilac_mi(tam.replace(" ", ""), adlar)):
            son = parca[-1] if parca[-1] in ".,;:!?" else ""
            parcalar.append(ILAC_MASKESI + son)
        else:
            parcalar.append(parca)
    return "".join(parcalar)


def saglik_ifadelerini_maskele(metin: str, alan: ModuleType) -> str:
    """Sağlık verisi ifadelerini (``SAGLIK_VERISI_IFADELERI``, kelime başından önek) gizler."""
    ifadeler = [normalize(i).split() for i in getattr(alan, "SAGLIK_VERISI_IFADELERI", ()) if normalize(i)]
    kelimeler = list(re.finditer(r"\S+", metin))
    # Önceki maskeler ("[ilaç]", "[ad]") yeniden maskelenmez.
    katli = ["" if k.group(0).startswith("[") else ascii_katla(re.sub(r"[^\w']", "", k.group(0))).replace("'", "")
             for k in kelimeler]
    gizle: set[int] = set()
    for i in range(len(kelimeler)):
        for ifade in ifadeler:
            son = i + len(ifade) - 1
            if son >= len(kelimeler):
                continue
            if all(katli[i + j] == ifade[j] for j in range(len(ifade) - 1)) and katli[son].startswith(ifade[-1]):
                gizle.update(range(i, son + 1))
    if not gizle:
        return metin
    cikti: list[str] = []
    konum = 0
    for idx, k in enumerate(kelimeler):
        if idx not in gizle:
            continue
        cikti.append(metin[konum:k.start()])
        onceki_gizli = idx - 1 in gizle
        if not onceki_gizli:
            cikti.append(SAGLIK_MASKESI)
        konum = k.end()
    cikti.append(metin[konum:])
    return "".join(cikti)


# ---------------------------------------------------------------- yanıt

def _cumle(metin: str) -> str:
    metin = " ".join(metin.split())
    return metin if not metin or metin[-1] in ".!?" else metin + "."


def _ilac_parcasi(alan: ModuleType, z: Ziyaret, i: Ilac, konu: str, kisa: bool) -> list[str]:
    kayit = dagarcik_kaydi(i.ad)
    bilgi = dagarcik_bilgisi(kayit, konu, alan)
    giris = (alan.ILAC_KONU_GIRIS_KISA.format(ad=i.ad) if kisa else
             alan.ILAC_KONU_GIRIS.format(tarih=z.tarih_metni, poliklinik=z.poliklinik, ad=i.ad))
    talimatli = any(k in alan.ILAC_TALIMATLI_KONULAR for k in konu_alt_kodlari(konu, alan))
    talimat = (_cumle(alan.ILAC_KONU_TALIMAT.format(kullanim=i.kullanim))
               if talimatli and i.kullanim else None)
    if konu == "doz":
        # Doz: hekim talimatı + genel doz uyarısı (dağarcıkta ilaca özel ek not varsa o da).
        return [p for p in (giris, talimat, alan.ILAC_DOZ_GENEL, bilgi) if p]
    if not bilgi:
        return [p for p in (talimat, alan.ILAC_KONU_BILGI_YOK.format(ad=i.ad)) if p]
    return [p for p in (giris, talimat, bilgi) if p]


def konu_metni(alan: ModuleType, secilenler: list[tuple[Ziyaret, Ilac]], konu: str) -> str:
    """Seçilen kayıtlı ilaç(lar) için konu yanıtı. ``amac``/``kullanim`` hekim onaylı özettir."""
    if konu in OZET_KONULARI:
        return gecmis_metni(alan, [z for z, _ in secilenler], "ilac", secilenler)
    if konu == "etkilesim" and len(secilenler) >= 2:
        adlar = " ve ".join(i.ad for _, i in secilenler[:2])
        return alan.ILAC_ETKILESIM_CIFT.format(adlar=adlar)
    kapanis = alan.ILAC_KONU_KAPANIS
    secilen: list[str] = []
    for sira, (z, i) in enumerate(secilenler[:2]):
        aday = [*secilen, *_ilac_parcasi(alan, z, i, konu, kisa=sira > 0)]
        if secilen and len(_birlestir([*aday, kapanis])) > alan.ILAC_KONU_AZAMI_SESLI:
            break  # ikinci ilaç sesli sınıra sığmıyor: yalnız ilki okunur
        secilen = aday
    # Kapanış yalnız genel bilgi okunduysa eklenir ("bilgi yok" şablonu zaten danışmaya yönlendirir).
    if any(p.endswith(":") for p in secilen):
        secilen.append(kapanis)
    return _birlestir(secilen)


def _birlestir(parcalar: list[str]) -> str:
    return " ".join(p if p.endswith(":") else _cumle(p) for p in parcalar)
