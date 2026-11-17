"""Geçmiş muayene / ilaç yanıtları: tamamen şablon + backend verisi.

Kaynak: ``GET /api/v1/voice-sessions/{id}/medical-history?limit=3`` (yalnız hekim onaylı özetler,
en yeniden eskiye). Bu modül:
- API yanıtını katı biçimde ayrıştırır (tipi bozuk kayıt atlanır, metinler kırpılır),
- kullanıcının söylediği ilaç adını kayıttaki adlarla KODDA bulanık eşleştirir (LLM'e ilaç
  listesi gitmez; LLM yalnız "kullanıcının söylediği ad" alanını çıkarabilir),
- sesli/ekran yanıtını konu modülündeki şablonlarla kurar. Tıbbi yorum ya da tavsiye eklenmez;
  hekimin onayladığı metin aktarılır ve "hekiminize danışın" şablonuyla kapatılır.

Veri orkestratör belleğinde yalnız oturum süresince tutulur, loglanmaz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from types import ModuleType
from typing import Any

from ortak.turkce import AY_ADLARI, ascii_katla, normalize

from .zaman import utc_coz, yerel

_AZAMI_METIN = 600
_AZAMI_ILAC = 20
_AZAMI_UYARI = 10
_KONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _metin(deger: Any, azami: int = _AZAMI_METIN) -> str:
    if not isinstance(deger, str):
        return ""
    return _KONTROL.sub(" ", deger).strip()[:azami]


@dataclass(frozen=True)
class Ilac:
    ad: str
    amac: str
    kullanim: str


@dataclass(frozen=True)
class Ziyaret:
    zaman: datetime          # UTC
    poliklinik: str
    hekim: str
    tani: str
    ilaclar: tuple[Ilac, ...]
    uyarilar: tuple[str, ...]

    @property
    def tarih_metni(self) -> str:
        y = yerel(self.zaman)
        return f"{y.day} {AY_ADLARI[y.month - 1]}"

    def disa(self) -> dict[str, Any]:
        y = yerel(self.zaman)
        return {
            "date": f"{y.day} {AY_ADLARI[y.month - 1]} {y.year}",
            "clinicName": self.poliklinik,
            "doctorDisplayName": self.hekim,
            "diagnosisPlain": self.tani,
            "medications": [{"name": i.ad, "purpose": i.amac, "usage": i.kullanim} for i in self.ilaclar],
            "warnings": list(self.uyarilar),
        }


def ziyaretleri_ayristir(veri: Any) -> list[Ziyaret]:
    """API yanıtını doğrular; bozuk kayıtları atlar. Sıralama en yeniden eskiye zorlanır."""
    if not isinstance(veri, dict) or not isinstance(veri.get("visits"), list):
        raise ValueError("visits listesi yok")
    sonuc: list[Ziyaret] = []
    for ham in veri["visits"]:
        if not isinstance(ham, dict):
            continue
        try:
            zaman = utc_coz(str(ham.get("scheduledAt") or ""))
        except ValueError:
            continue
        ilaclar = []
        for m in (ham.get("medications") or [])[:_AZAMI_ILAC]:
            if isinstance(m, dict) and _metin(m.get("name"), 120):
                ilaclar.append(Ilac(_metin(m.get("name"), 120), _metin(m.get("purpose")), _metin(m.get("usage"))))
        uyarilar = tuple(u for u in (_metin(x, 400) for x in (ham.get("warnings") or [])[:_AZAMI_UYARI]) if u)
        sonuc.append(Ziyaret(zaman, _metin(ham.get("clinicName"), 80), _metin(ham.get("doctorDisplayName"), 80),
                             _metin(ham.get("diagnosisPlain"), 2000), tuple(ilaclar), uyarilar))
    sonuc.sort(key=lambda z: z.zaman, reverse=True)
    return sonuc


# ---------------------------------------------------------------- bulanık ilaç eşleşmesi

def _levenshtein(a: str, b: str, sinir: int) -> int:
    if abs(len(a) - len(b)) > sinir:
        return sinir + 1
    onceki = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        simdiki = [i]
        for j, cb in enumerate(b, 1):
            simdiki.append(min(onceki[j] + 1, simdiki[j - 1] + 1, onceki[j - 1] + (ca != cb)))
        onceki = simdiki
    return onceki[-1]


def _ad_eslesir_mi(aday: str, ilac_adi: str) -> bool:
    """``aday`` (kullanıcının sözünden bir kelime ya da birleşik kelime) ilaç adına benziyor mu?

    Ekli biçimler ("lansoprazolü", "lansoprazolün") ve STT yazım hataları ("lanzoprazol")
    tolere edilir; kısa ve genel kelimeler ("ilaç") eşleşmez.
    """
    ad = normalize(ilac_adi).split()[0] if normalize(ilac_adi) else ""
    if len(ad) < 4 or len(aday) < 4:
        return False
    if aday.startswith(ad) and len(aday) - len(ad) <= 4:  # Türkçe ek
        return True
    # Yanlış ilacın talimatını okumamak için tolerans dar: benzer adlı farklı ilaçlar ("Klonopin" ↔
    # "Klonidin", "Paroksetin" ↔ "Parol") eşleşmemeli. 8 harfe kadar tek, daha uzun adlarda iki harf
    # farkına izin verilir; ön ek kıyasında kalan kısım Türkçe ek uzunluğunu (≤4) aşamaz.
    sinir = 1 if len(ad) <= 8 else 2
    if len(ad) <= len(aday) <= len(ad) + 4 and _levenshtein(aday[:len(ad)], ad, sinir) <= sinir:
        return True
    return _levenshtein(aday, ad, sinir) <= sinir


def ilac_bul(metin: str, ziyaretler: list[Ziyaret]) -> list[tuple[Ziyaret, Ilac]]:
    """Metinde adı geçen (bulanık) kayıtlı ilaçlar; en yeni ziyaret önce."""
    kelimeler = normalize(metin).split()
    adaylar = set(kelimeler)
    adaylar |= {a + b for a, b in zip(kelimeler, kelimeler[1:], strict=False)}  # "lanso prazol"
    # Kesme işaretli ek: STT "Metformini"yi "Metormil'i", "Metormen'in" yazabiliyor (provada Piper fahrettin
    # sesiyle 5/5 yanlış). Ekin yapıştığı biçim tolerans dışına düşer; kesmeden önceki kök de aday olur
    # (tolerans aynı: 9+ harfli adda en çok 2 harf farkı).
    for parca in metin.split():
        for ayrac in ("'", "’"):
            if ayrac in parca:
                kok = normalize(parca.split(ayrac)[0])
                if kok:
                    adaylar.add(kok.replace(" ", ""))
    bulunan: list[tuple[Ziyaret, Ilac]] = []
    gorulen: set[str] = set()
    for z in ziyaretler:
        for i in z.ilaclar:
            anahtar = normalize(i.ad)
            if anahtar in gorulen:
                continue
            ilk = anahtar.split()[0] if anahtar else ""
            # Gündelik kelime yalnız ilacın tam adıyla (ekli) başlıyorsa eşleşir; bulanık eşleşmeye giremez.
            if any(_ad_eslesir_mi(a, i.ad) and (not gundelik_mi(a) or (ilk and a.startswith(ilk)))
                   for a in adaylar):
                bulunan.append((z, i))
                gorulen.add(anahtar)
    return bulunan


# İlaç adı sayılmayacak soru kelimelerinin kökleri (ASCII katlı). Bunlar dışında kalan uzunca bir
# kelime, kullanıcının kayıtlarda olmayan bir ilaç adı söylediğine işaret eder ("Aspirin nasıl kullanılır").
_SORU_KOKLERI = ("nasil", "zaman", "kullan", "alin", "alir", "alaca", "alaca", "alma", "icil", "icer", "icme",
                 "icece", "yarar", "yariyor", "icin", "gunde", "kere", "sabah", "aksam", "ogle", "gece", "yemek",
                 "once", "sonra", "benim", "bana", "olan", "yazil", "yazdi", "doktor", "hekim", "lutfen", "acaba",
                 "soyle", "anlat", "ogren", "bilgi", "tane", "kadar", "hangi", "neden", "nedir", "midir",
                 "musun", "misin", "miyim", "gerek", "lazim", "tablet", "hap", "ilac", "recete", "surup",
                 "soyle", "oneri", "onerm", "veril", "verdi", "miydi", "muydu", "midir", "mudur", "neydi",
                 "nelerdi", "muayene", "kontrol", "uyari", "tani", "teshis", "gecen", "gecmis", "onceki",
                 "eski", "hafta", "ayin", "tarih", "sorun", "simdi", "hala", "devam", "almam", "almami",
                 # İlacı amacıyla anan genel kelimeler ("tansiyon ilacım"): ilaç adı değil.
                 "tansiyon", "seker", "mide", "agri", "kolesterol", "ates", "alerji", "uyku", "vitamin",
                 # Genel soru/istek kelimeleri ("ilaçlarım neler", "tekrar söyler misiniz").
                 "neler", "hangileri", "seyler", "hepsi", "tekrar", "goster", "okuy", "okur", "bakar", "bakabil",
                 "verir", "ister", "istiyor", "merak", "dogru", "peki", "evet", "hayir", "tamam", "aldigim",
                 "kullandig", "aldim", "ictig",
                 # İlaç konu sorularındaki genel kelimeler ("alerjim olmaması gerekiyor mu", "sakıncası var mı").
                 "doz", "vars", "yoksa", "halde", "kimler", "kimse", "olma", "olur", "olaca", "oldu", "sakinca", "tehlike", "mumkun", "durum", "yapar", "yapab",
                 "etki", "sebep", "uygun", "icebil", "alabil", "kullanabil", "yiyebil", "bunu", "bunun", "onun",
                 "fazla", "ayni", "anda", "baska", "diger", "ilacla", "hapla", "gerekir", "gerekiy", "lazim",
                 "onemli", "normal", "ciddi", "sorabil", "soyley", "ogrenebil", "biliyor", "bilmek", "istiyor")


# Gündelik konuşma ve dolgu kelimeleri (ASCII katlı kök, önekle): ilaç adı değildir. "Ya en son randevu, bu
# doktor bana ne yazmıştı?" cümlesinde "randevu" ilaç adayı sayılıp "Kayıtlarınızda bu ilaç görünmüyor" deniyordu
# (2026-11-16). Bu kökler kayıtlı ilaç adlarıyla BULANIK eşleşmeye de giremez ("demişti" ≠ "Demir sülfat").
# Kısa dolgu kelimeleri ("ya", "bu", "şey", "hani", "bana") zaten 5 harften kısa olduğu için aday sayılmaz.
GUNDELIK_KOKLERI = (
    "randevu", "doktor", "hekim", "hoca", "sefer", "hani", "mesele", "konu", "ilgili", "seyi", "seyler", "boyle", "soyle",
    "galiba", "herhal", "sanir", "sanki", "acaba", "falan", "filan", "yani", "hatirl", "hatirla", "unuttu",
    "gitti", "gitmis", "gidis", "gidince", "gidecek", "gecen", "gecende", "gecenler", "evvel", "onceki", "onceden",
    "sonuncu", "sonra", "demis", "dedi", "dedik", "dediler", "soyled", "soylem", "yazmis", "yazdi", "yazdik",
    "yazil", "verdi", "vermis", "veril", "verdik", "bakti", "bakmis", "oldum", "olmus", "olmust", "oldug",
    "muayene", "polikli", "bolum", "hastane", "bugun", "yarin", "zaman", "nerede", "nereye", "neydi", "nelerdi",
    "kimdi", "kimin", "hangisi", "bunlar", "sunlar", "onlar", "simdi", "tamam", "lutfen", "merhaba", "selam",
    "tesekkur", "pardon", "affeders", "kusura", "anlatir", "tekrar", "vardi", "bilmiyor", "bilmem", "sordum",
    "soruyor", "soracak", "ogrenmek", "istedi", "gerekiyor", "sadece", "yalniz", "bilgi", "ozet", "durum",
    "kullanacakt", "icecekt", "alacakt", "kullaniyord", "kullandig", "aliyord", "iciyord",
)
# Fiil çekim sonları (ASCII katlı): "yazmışlardı", "gittiğimde", "kullanacaktım" ilaç adı değildir.
_FIIL_SONLARI = ("misti", "musti", "mistim", "mustum", "mislar", "muslar", "mislardi", "muslardi", "dilar",
                 "diler", "tilar", "tiler", "dular", "duler", "tular", "tuler", "digimde", "tigimde", "dugumde",
                 "tugumde", "digim", "tigim", "dugum", "tugum", "acaktim", "ecektim", "acakti", "ecekti", "iyordum",
                 "iyordu", "uyordum", "uyordu", "miydi", "muydu", "diniz", "tiniz", "dunuz", "tunuz")


def gundelik_mi(kelime: str) -> bool:
    """ASCII katlı tek kelime gündelik/dolgu kelimesi ya da çekimli fiil mi (ilaç adı adayı değil)?"""
    if kelime.startswith(GUNDELIK_KOKLERI):
        return True
    return any(kelime.endswith(s) and len(kelime) - len(s) >= 2 for s in _FIIL_SONLARI)


def _alan_kokleri(alan: ModuleType | None) -> tuple[str, ...]:
    """Konu modülündeki geçmiş/ilaç soru ifadelerinin ve poliklinik adlarının kelimeleri (ilaç adı değil)."""
    if alan is None:
        return ()
    ifadeler = [*getattr(alan, "GECMIS_IFADELERI", ()), *getattr(alan, "ILAC_SORU_IFADELERI", ()),
                *getattr(alan, "ILAC_BAGLAM_IFADELERI", ()), *getattr(alan, "ILAC_ALIM_IFADELERI", ()),
                *getattr(alan, "ILAC_ACIL_IFADELERI", ()), *getattr(alan, "SAGLIK_VERISI_IFADELERI", ())]
    for _, konu_ifadeleri, _ in getattr(alan, "ILAC_KONU_KALIPLARI", ()):
        ifadeler += konu_ifadeleri
    for p in getattr(alan, "POLIKLINIKLER", ()):
        ifadeler += [p.ad, *p.es_anlamlilar]
    return tuple({k for i in ifadeler for k in normalize(i).split() if len(k) >= 4})


def ilac_adayi_var_mi(metin: str, alan: ModuleType | None = None) -> bool:
    """Metinde soru kalıbı (ve ``alan`` verilirse poliklinik/geçmiş ifadesi) dışında, ilaç adı
    olabilecek bir kelime var mı? ("aspirin ne için yazılmıştı" → evet; "ortopedi muayenemde ne
    yazıldı" → hayır)."""
    alan_kelimeleri = _alan_kokleri(alan)
    for kelime in ascii_katla(normalize(metin)).split():
        kelime = kelime.strip(".,;:!?\"'()[]")
        if len(kelime) < 5 or kelime.startswith(_SORU_KOKLERI) or kelime.startswith("[") or gundelik_mi(kelime):
            continue
        # Alan kelimesi yalnız Türkçe ekiyle kabul edilir ("muayenemde", "ortopediye"); "Kardiyoaspirin"
        # gibi bir alan kelimesiyle başlayan uzun ad ilaç adayı olarak kalır.
        if any(kelime.startswith(k[:-1] if len(k) > 5 else k) and len(kelime) - len(k) <= 5
               for k in alan_kelimeleri):
            continue
        return True
    return False


def poliklinik_ziyaretleri(ziyaretler: list[Ziyaret], kod: str, alan: ModuleType) -> list[Ziyaret]:
    """Yalnız ``kod`` polikliniğine ait ziyaretler (API ``clinicName`` görünen addır: "Göz Hastalıkları",
    eski seed'de "Goz Hastaliklari"; karşılaştırma ASCII katlı yapılır)."""
    p = next((x for x in alan.POLIKLINIKLER if x.kod == kod), None)
    if p is None:
        return []
    adlar = {normalize(a) for a in (p.ad, p.kod, *p.es_anlamlilar)}
    return [z for z in ziyaretler if normalize(z.poliklinik) in adlar]


# ---------------------------------------------------------------- yanıt metni

def _ilac_cumlesi(alan: ModuleType, i: Ilac) -> str:
    return " ".join(alan.GECMIS_ILAC.format(ad=i.ad, amac=i.amac, kullanim=i.kullanim).split())


def _cumle(metin: str) -> str:
    metin = metin.strip()
    return metin if not metin or metin[-1] in ".!?" else metin + "."


def gecmis_metni(alan: ModuleType, ziyaretler: list[Ziyaret], konu: str = "liste",
                 ilac_eslesmesi: list[tuple[Ziyaret, Ilac]] | None = None, poliklinik_suzuldu: bool = False) -> str:
    """Sesli/ekran yanıtı. ``konu``: liste | ilac | uyari | tani | ziyaret | ozet."""
    if not ziyaretler:
        return alan.GECMIS_YOK
    kapanis = alan.GECMIS_KAPANIS
    if konu == "ziyaret" and hasattr(alan, "GECMIS_SON_ZIYARET"):
        return _ziyaret_metni(alan, ziyaretler, poliklinik_suzuldu)
    if konu == "ozet" and hasattr(alan, "GECMIS_AYRINTI_EKRANDA"):
        son = ziyaretler[0]
        cumleler = []
        if son.tani:
            cumleler.append(_cumle(alan.GECMIS_TANI.format(tarih=son.tarih_metni, poliklinik=son.poliklinik,
                                                           tani=son.tani)))
        if cumleler:
            cumleler += [_cumle(alan.GECMIS_UYARI.format(uyari=u)) for u in son.uyarilar]
            return _sigdir(alan, cumleler, kapanis, alan.GECMIS_AYRINTI_EKRANDA)
        konu = "uyari" if son.uyarilar else "liste"
    if konu == "ilac" and ilac_eslesmesi:
        parcalar = []
        for z, i in ilac_eslesmesi[:2]:
            parcalar.append(_cumle(" ".join(alan.GECMIS_ILAC_TEK.format(
                tarih=z.tarih_metni, poliklinik=z.poliklinik, ad=i.ad, amac=i.amac, kullanim=i.kullanim).split())))
        return " ".join([*parcalar, kapanis])
    son = ziyaretler[0]
    if konu == "uyari":
        if son.uyarilar:
            govde = [_cumle(alan.GECMIS_UYARI.format(uyari=u)) for u in son.uyarilar]
        else:
            govde = [alan.GECMIS_UYARI_YOK]
        return _sigdir(alan, [f"{son.tarih_metni} tarihli {son.poliklinik} muayeneniz.", *govde], kapanis)
    if konu == "tani" and son.tani:
        return _sigdir(alan, [_cumle(alan.GECMIS_TANI.format(tarih=son.tarih_metni, poliklinik=son.poliklinik,
                                                             tani=son.tani))], kapanis)
    onceki = _onceki_ilac_adlari(son, ziyaretler[1:])
    if onceki and hasattr(alan, "GECMIS_ONCEKI_ILACLAR"):
        kapanis = f"{alan.GECMIS_ONCEKI_ILACLAR.format(adlar=', '.join(onceki))} {kapanis}"
    if not son.ilaclar:
        giris = alan.GECMIS_GIRIS_ILACSIZ.format(tarih=son.tarih_metni, poliklinik=son.poliklinik)
        return _sigdir(alan, [giris], kapanis)
    giris = alan.GECMIS_GIRIS.format(tarih=son.tarih_metni, poliklinik=son.poliklinik, sayi=len(son.ilaclar))
    return _sigdir(alan, [giris, *(_cumle(_ilac_cumlesi(alan, i)) for i in son.ilaclar)], kapanis)


def _onceki_ilac_adlari(son: Ziyaret, oncekiler: list[Ziyaret]) -> list[str]:
    """Son muayenede olmayan, önceki onaylı muayenelerde yazılmış ilaç adları (sırayla, tekrarsız)."""
    gorulen = {i.ad.casefold() for i in son.ilaclar}
    adlar = []
    for z in oncekiler:
        for i in z.ilaclar:
            if i.ad.casefold() not in gorulen:
                gorulen.add(i.ad.casefold())
                adlar.append(i.ad)
    return adlar[:4]


def _ziyaret_metni(alan: ModuleType, ziyaretler: list[Ziyaret], poliklinik_suzuldu: bool) -> str:
    """"En son ne zaman muayene oldum / hangi doktora gittim": son onaylı muayenenin tarihi, polikliniği, hekimi
    ve o muayenede yazılan ilaçların adları; önceki onaylı muayeneler kısaca (ayrıntı ekranda)."""
    son = ziyaretler[0]
    hekim = (alan.GECMIS_ZIYARET_HEKIM.format(hekim=son.hekim) if son.hekim
             else alan.GECMIS_ZIYARET_HEKIMSIZ)
    sablon = alan.GECMIS_SON_POLIKLINIK_ZIYARET if poliklinik_suzuldu else alan.GECMIS_SON_ZIYARET
    cumleler = [sablon.format(tarih=son.tarih_metni, poliklinik=son.poliklinik, hekim=hekim)]
    if son.ilaclar:
        cumleler.append(alan.GECMIS_ZIYARET_ILACLAR.format(adlar=", ".join(i.ad for i in son.ilaclar[:6])))
    else:
        cumleler.append(alan.GECMIS_ZIYARET_ILACSIZ)
    if len(ziyaretler) > 1:
        liste = ", ".join(f"{z.tarih_metni} {z.poliklinik}".strip() for z in ziyaretler[1:4])
        cumleler.append(alan.GECMIS_ONCEKI_ZIYARETLER.format(liste=liste))
    return _sigdir(alan, cumleler, alan.GECMIS_KAPANIS, alan.GECMIS_AYRINTI_EKRANDA)


def _hekim_ad_kelimeleri(hekim: str) -> list[str]:
    """Hekimin görünen adındaki ad kelimeleri ("Doç. Dr. Elif K." → ["elif"]); unvan ve baş harf atılır."""
    unvanlar = {"prof", "doc", "dr", "uzm", "op", "yrd", "dt", "doktor", "hekim", "profesor", "docent", "uzman",
                "operator"}
    return [k for k in normalize(hekim).split() if len(k) >= 3 and k not in unvanlar]


def _ad_gecer(kelimeler: list[str], ad: str) -> bool:
    # "Elif'in" → "elifin": ad + en çok 4 harflik ek. Üç harfli adlar ("Can") yalnız tam kelime.
    return any(k == ad or (len(ad) >= 4 and k.startswith(ad) and len(k) - len(ad) <= 4) for k in kelimeler)


_KISI_SONRASI = re.compile(r"(hanim|bey|hoca)(in|im|a|e|ya|ye|nin|m|min|ma)?")
_KISI_DEGIL = frozenset({"son", "sen", "ben", "siz", "biz", "bir", "bana", "bize", "beni", "benim", "bey", "hanim",
                         "hoca", "ne", "bu", "o", "su"})
_KISI_ONCESI = ("doktor", "dr", "hekim", "profesor", "docent", "uzman", "operator")


def hekim_suz(ziyaretler: list[Ziyaret], metin: str) -> tuple[list[Ziyaret] | None, set[str]]:
    """Cümlede onaylı muayenelerdeki bir hekimin adı geçiyorsa yalnız o hekimin muayeneleri.

    Dönüş: (süzülmüş ziyaretler ya da ``None`` = hekim adı sorulmadı, ilaç adayı sayılmayacak ad kelimeleri).
    Boş liste: kişi adı ("Zeynep Hanım", "doktor Zeynep") sorulmuş ama onaylı kayıtlarda böyle bir hekim yok.
    Eşleme yalnız kodda yapılır; ad ne LLM'e ne loga gider."""
    kelimeler = normalize(metin).split()
    secilen: list[Ziyaret] = []
    adlar: set[str] = set()
    for z in ziyaretler:
        eslesen = [ad for ad in _hekim_ad_kelimeleri(z.hekim) if _ad_gecer(kelimeler, ad)]
        if eslesen:
            secilen.append(z)
            adlar.update(k for k in kelimeler for ad in eslesen if k == ad or k.startswith(ad))
    if secilen:
        return secilen, adlar
    # Hekim adı gibi söylenen ama kayıtta olmayan kişi: "<Ad> Hanım/Bey/Hoca", "doktor <Ad>" (ham metinde büyük harf).
    kisi: set[str] = set()
    for i, k in enumerate(kelimeler):
        onceki = kelimeler[i - 1] if i > 0 else ""
        if _KISI_SONRASI.fullmatch(k) and len(onceki) >= 3 and onceki not in _KISI_ONCESI \
                and onceki not in ("son", "sen", "ben", "siz", "biz", "bir") and not gundelik_mi(onceki):
            kisi.add(onceki)
    ham = metin.split()
    for i, parca in enumerate(ham[:-1]):
        sonraki = ham[i + 1].strip(".,;:!?\"'()")
        aday = normalize(sonraki.split("'")[0].split("’")[0])
        # Yalnız baş harfi büyük ad ("doktor Zeynep"); tamamı büyük harfli STT/yazı ("DOKTOR BANA") ad sayılmaz.
        if normalize(parca) in _KISI_ONCESI and sonraki[:1].isupper() and not sonraki.isupper()                 and len(aday) >= 3 and aday not in _KISI_DEGIL and not gundelik_mi(aday):
            kisi.add(aday)
    if kisi:
        return [], kisi
    return None, set()


def _sigdir(alan: ModuleType, cumleler: list[str], kapanis: str, tasma_notu: str | None = None) -> str:
    """Sesli okuma sınırını aşmadan cümleleri ekler; sığmayanlar için "ekranda" notu."""
    sinir = alan.GECMIS_AZAMI_SESLI
    secilen = [cumleler[0]]
    for c in cumleler[1:]:
        aday = " ".join([*secilen, c, kapanis])
        if len(aday) > sinir:
            secilen.append(tasma_notu or alan.GECMIS_DIGERLERI_EKRANDA)
            break
        secilen.append(c)
    return " ".join([*secilen, kapanis])
