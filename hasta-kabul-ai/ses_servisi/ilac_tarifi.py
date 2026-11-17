"""Adı söylenmeyen kayıtlı ilacı TARİFİNDEN çözme ("tansiyon ilacım", "tansiyonumu düşüren ilaç", "şeker hapım",
"kalp doktorunun verdiği ilaç", "kardiyolojide yazılan ilaç", "en son yazılan ilaç", "sabah içtiğim ilaç").

Kullanıcı ilacını adıyla değil ne için / kimden / ne zaman kullandığıyla anlatır. Bu modül yalnız KURAL ve kayıtlı
ilacın kendi verisiyle çalışır (LLM'e hiçbir şey gitmez):

- ``tarif_bul``: normalize cümlede ilaç bağlamı (ilaç/hap/tablet…, "kullandığım/içtiğim", "düşüren") VE en az bir
  tarif işareti (amaç anahtarı, poliklinik, sıra, kullanım zamanı) var mı?
- ``tarif_coz``: tarif işaretlerini hekim onaylı kayıttaki ilaçlara uygular. Amaç: kaydın "ne işe yarar" metni +
  dağarcıktaki amaç metni + dağarcıktaki ``amaclar`` anahtarları; poliklinik: ilacın yazıldığı muayene; sıra: en
  yeni / önceki muayene; zaman: hekimin kullanım talimatı. Her işaret aday kümesini daraltır; bir işaret hiçbir
  ilaca uymuyorsa sonuç boştur (kayıtta yok — tahmin edilmez).
- ``ilac_kullanim_baglami``: cümle hastanın KENDİ ilacından söz ediyor mu ("tansiyon ilacım", "şeker için
  kullandığım")? Öyleyse hastalık/organ kelimesi ("tansiyon", "şeker") şikâyet sayılmaz (poliklinik önerisi yok).

Eşleme sonucu orkestratörde (``_tarif_yolu``) mevcut ilaç akışına verilir: tek ilaç → o ilacın konu yanıtı (ad
yanıtta söylenir), birden çok / hiç → "hangisi?" sorusu (kayıtlı ilaçlar sayılarak).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import ModuleType

from ortak.turkce import ifade_gecer_mi, normalize

from .alanlar.ilac_dagarcigi import IlacKaydi
from .gecmis import Ilac, Ziyaret
from .ilac_bilgisi import dagarcik_kaydi


@dataclass(frozen=True)
class Tarif:
    amaclar: tuple[str, ...] = ()     # ILAC_AMAC_ANAHTARLARI anahtarları
    poliklinik: str | None = None     # poliklinik kodu
    sira: str | None = None           # "son" | "onceki"
    zaman: str | None = None          # ILAC_TARIF_ZAMAN_KOKLERI anahtarı
    # Tarif işaretlerinin kelimeleri (konu/ek soru ayrıştırmasında ilaç adı adayı sayılmasın diye).
    kelimeler: frozenset[str] = frozenset()


def _gecer(n: str, ifadeler: tuple[str, ...]) -> bool:
    return any(ifade_gecer_mi(n, i) for i in ifadeler)


def _ilac_kelimesi(n: str, alan: ModuleType, amac_var: bool) -> bool:
    if re.search(alan.ILAC_TARIF_ILAC_KELIMESI, n):
        return True
    # "şeker apı": konuşma tanıma "hapı"nın h'sini düşürür; yalnız amaç kelimesiyle birlikte.
    return amac_var and re.search(alan.ILAC_TARIF_STT_HAP, n) is not None


# Amaç kelimesiyle ilaç işareti arasına girebilen kelimeler ("demir eksikliği için aldığım", "kalp ve tansiyon
# ilacım", "tansiyonum için içtiğim").
_ARA_KELIMELER = ("icin", "eksikl", "hastalig", "rahatsizlig", "yuksekl", "dusukl", "sorun", "ve", "ile", "olan",
                  "bozuklug", "duzey")


def _isaret_mi(kelime: str, alan: ModuleType) -> bool:
    return any(re.match(d, kelime) for d in (
        alan.ILAC_TARIF_ILAC_KELIMESI, alan.ILAC_TARIF_STT_HAP, alan.ILAC_TARIF_KULLANIM, alan.ILAC_TARIF_AMAC_FIILI))


def _amaclar(n: str, alan: ModuleType) -> tuple[str, ...]:
    """Cümlede İLAÇ TARİFİ olarak geçen amaç anahtarları: amaç kelimesinden hemen sonra (araya en çok iki bağlantı
    kelimesi ya da başka amaç kelimesi girebilir) ilaç kelimesi, kullanım fiili ya da amaç fiili gelmeli
    ("tansiyon ilacım", "tansiyonumu düşüren", "şeker için kullandığım"). "Midem ağrıyor, ilaç içsem mi" ya da
    "tansiyonum yükseldi" tarif değildir (şikâyet)."""
    kelimeler = n.split()
    tum_ifadeler = [(anahtar, normalize(i)) for anahtar, _, ifadeler, _ in alan.ILAC_AMAC_ANAHTARLARI
                    for i in ifadeler]
    bulunan: list[str] = []
    for anahtar, ifade in tum_ifadeler:
        if anahtar in bulunan:
            continue
        uzunluk = len(ifade.split())
        for idx in range(len(kelimeler) - uzunluk + 1):
            if not ifade_gecer_mi(" ".join(kelimeler[idx:idx + uzunluk]), ifade) \
                    or not kelimeler[idx].startswith(ifade.split()[0]):
                continue
            j, atlanan = idx + uzunluk, 0
            while j < len(kelimeler) and atlanan < 3 and (
                    kelimeler[j].startswith(_ARA_KELIMELER)
                    or any(" " not in i and kelimeler[j].startswith(i) for _, i in tum_ifadeler)):
                j, atlanan = j + 1, atlanan + 1
            if j < len(kelimeler) and _isaret_mi(kelimeler[j], alan):
                bulunan.append(anahtar)
                break
    return tuple(bulunan)


def _poliklinik(n: str, alan: ModuleType) -> str | None:
    """Poliklinik işareti: açık ad/eş anlamlı ("kardiyolojide", "kalp doktorunun", "dahiliyeden") ya da kök
    ("kardiyo…", "dahil…"). Tek başına organ adları ("kalp", "göz") burada sayılmaz: amaç işaretidir."""
    organ_kokleri = {"kalp", "kalb", "kemik", "kulak", "cilt", "çocuk", "kadın", "sinir", "göz", "idrar yol"}
    for p in alan.POLIKLINIKLER:
        adlar = [i for i in (p.ad, *p.es_anlamlilar) if i not in getattr(alan, "ORGAN_ES_ANLAMLILARI", ())]
        kokler = [k for k in alan.POLIKLINIK_KOKLERI.get(p.kod, ()) if k not in organ_kokleri and len(k) >= 4]
        if _gecer(n, tuple(adlar)) or _gecer(n, tuple(kokler)):
            return p.kod
    return None


def _randevu_istegi(n: str, alan: ModuleType) -> bool:
    return _gecer(n, getattr(alan, "RANDEVU_ISTEGI_IFADELERI", ())) \
            and _gecer(n, getattr(alan, "RANDEVU_ISTEK_FIILLERI", ()))


def tarif_bul(n: str, alan: ModuleType) -> Tarif | None:
    """Normalize cümle kayıtlı bir ilacı tarifle mi anıyor? (İlaç LİSTESİ isteği, ilaç önerisi isteği ve
    randevu isteği tarif sayılmaz: mevcut akış.)"""
    if not n:
        return None
    amaclar = _amaclar(n, alan)
    ilac = _ilac_kelimesi(n, alan, bool(amaclar))
    kullanim = re.search(alan.ILAC_TARIF_KULLANIM, n) is not None
    amac_fiili = re.search(alan.ILAC_TARIF_AMAC_FIILI, n) is not None
    if not (ilac or kullanim or (amac_fiili and amaclar)):
        return None
    if _gecer(n, getattr(alan, "ILAC_LISTESI_IFADELERI", ())) or _gecer(n, getattr(alan, "SAGLIK_SPESIFIK_ONERI", ())):
        return None  # "ilaçlarım neler" (liste) / "hangi ilacı almalıyım" (öneri): mevcut akış
    if ifade_gecer_mi(n, "yerine"):
        return None  # "tansiyon ilacımın yerine başka ilaç alabilir miyim": ilaç değiştirme tavsiyesi, sabit ret
    if _randevu_istegi(n, alan):
        return None  # "tansiyon ilacım için randevu almak istiyorum": randevu akışı
    poliklinik = _poliklinik(n, alan) if (ilac or kullanim) else None
    sira = None
    if ilac or kullanim:
        if _gecer(n, alan.ILAC_TARIF_SON):
            sira = "son"
        elif _gecer(n, alan.ILAC_TARIF_ONCEKI):
            sira = "onceki"
    m = re.search(alan.ILAC_TARIF_ZAMAN, n)
    zaman = m.group(1) if m else None
    if not (amaclar or poliklinik or sira or zaman):
        return None
    kelimeler = {k for k in n.split()
                 if any(k.startswith(normalize(i).split()[0][:5])
                        for anahtar, _, ifadeler, _ in alan.ILAC_AMAC_ANAHTARLARI if anahtar in amaclar
                        for i in ifadeler)}
    return Tarif(amaclar, poliklinik, sira, zaman, frozenset(kelimeler))


def ilac_kullanim_baglami(n: str, alan: ModuleType) -> bool:
    """Cümle hastanın kendi ilacından söz ediyor mu? (Tarif ya da "ilacım/hapım", "kullandığım ilaç".)
    "Başım ağrıyor ilaç içsem mi" (ilaç önerisi/izni) burada sayılmaz."""
    if tarif_bul(n, alan) is not None:
        return True
    if _randevu_istegi(n, alan):
        return False  # "şeker ilacım için randevu almak istiyorum": randevu akışı
    return re.search(r"(?<!\w)(ilac|hap)(im|imi|imin|ima|imla|imdan|larim|larimi|larimin|larimla)(?!\w)", n) is not None


def _kayit_metni(z: Ziyaret, i: Ilac, kayit: IlacKaydi | None) -> str:
    return normalize(" ".join([i.amac, (kayit.konular.get("amac", "") if kayit else "")]))


def _amaca_uyar(anahtar: str, z: Ziyaret, i: Ilac, alan: ModuleType) -> bool:
    kayit = dagarcik_kaydi(i.ad)
    if kayit is not None and anahtar in kayit.amaclar:
        return True
    kokler = next((k for a, _, _, k in alan.ILAC_AMAC_ANAHTARLARI if a == anahtar), ())
    metin = _kayit_metni(z, i, kayit)
    return any(ifade_gecer_mi(metin, k) for k in kokler)


def _poliklinik_uyar(kod: str, z: Ziyaret, alan: ModuleType) -> bool:
    p = alan.poliklinik_bul(kod)
    if p is None:
        return False
    return normalize(z.poliklinik) in {normalize(x) for x in (p.ad, p.kod, *p.es_anlamlilar)}


def tarif_coz(tarif: Tarif, ziyaretler: list[Ziyaret], alan: ModuleType) -> list[tuple[Ziyaret, Ilac]]:
    """Tarife uyan kayıtlı ilaçlar (ad başına bir kez; en yeni muayene önce). Boş liste: hiçbirine uymuyor."""
    adaylar: list[tuple[Ziyaret, Ilac]] = []
    gorulen: set[str] = set()
    for zi, z in enumerate(ziyaretler):
        if tarif.sira == "son" and zi > 0:
            continue
        if tarif.sira == "onceki" and zi == 0:
            continue
        if tarif.poliklinik and not _poliklinik_uyar(tarif.poliklinik, z, alan):
            continue
        for i in z.ilaclar:
            anahtar = normalize(i.ad)
            if anahtar in gorulen:
                continue
            if tarif.amaclar and not all(_amaca_uyar(a, z, i, alan) for a in _amac_grubu(tarif.amaclar)):
                continue
            if tarif.zaman:
                kokler = alan.ILAC_TARIF_ZAMAN_KOKLERI.get(tarif.zaman, (tarif.zaman,))
                if not any(ifade_gecer_mi(normalize(i.kullanim), k) for k in kokler):
                    continue
            gorulen.add(anahtar)
            adaylar.append((z, i))
    return adaylar


def _amac_grubu(amaclar: tuple[str, ...]) -> tuple[str, ...]:
    """Birden çok amaç anahtarı aynı ilacı anlatabilir ("kalp ve tansiyon ilacım"); "kalp" daha geneldir ve
    başka bir anahtarla birlikteyse ayrıca aranmaz."""
    ozel = tuple(a for a in amaclar if a != "kalp")
    return ozel or amaclar


def tarif_metni(tarif: Tarif, alan: ModuleType) -> str:
    """"Hangisi?/yok" yanıtındaki tarif parçası ("tansiyon için", "Kardiyoloji muayenenizde yazılmış")."""
    if tarif.amaclar:
        adlar = [ad for a, ad, _, _ in alan.ILAC_AMAC_ANAHTARLARI if a in _amac_grubu(tarif.amaclar)]
        return alan.ILAC_TARIF_AMAC.format(ad=" ve ".join(adlar))
    if tarif.poliklinik:
        p = alan.poliklinik_bul(tarif.poliklinik)
        return alan.ILAC_TARIF_POLIKLINIK.format(poliklinik=p.ad if p else tarif.poliklinik)
    if tarif.zaman:
        return alan.ILAC_TARIF_ZAMAN_METNI.format(zaman=alan.ILAC_TARIF_ZAMAN_ADLARI.get(tarif.zaman, tarif.zaman))
    return alan.ILAC_TARIF_SON_METNI if tarif.sira == "son" else alan.ILAC_TARIF_ONCEKI_METNI
