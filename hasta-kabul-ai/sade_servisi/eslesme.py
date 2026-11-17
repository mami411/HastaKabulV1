"""Hekim girdisini bilgi tabanı kayıtlarıyla deterministik olarak eşler (RAG'in "R" adımı).

Kurallar (LLM yok, embedding yok; aynı girdi her zaman aynı sonucu verir):

- Metin ``ortak.turkce.normalize`` ile katlanır: Türkçe kurallı küçük harf, ASCII'ye indirme
  (``Hazımsızlık`` = ``HAZIMSIZLIK`` = ``hazimsizlik``), noktalama → boşluk.
- Tanı metni bölütlere ayrılır (virgül, noktalı virgül, nokta, eğik çizgi, parantez, iki nokta,
  satır sonu, " ve "). Her bölüt ayrı eşlenir.
- **Kapsama kuralı:** bir bölüt ancak tüm anlamlı kelimeleri KB ifadeleriyle (terim, eş
  anlamlı) ya da izinli niteleyicilerle ("kontrolsüz", "hafif", ICD kodu…) karşılanıyorsa
  eşleşmiş sayılır. "Pulmoner hipertansiyon", "kronik gastrit", "tip 1 diyabet",
  "düşük tansiyon" gibi bölütler KB kaydına zorla bağlanmaz; bilinmeyen terim olur ve hekim görür.
- Zayıf (sınıf adı) eş anlamlılar hiç eşleşmez (bkz. ``bilgi_tabani.ZAYIF_ES_ANLAMLILAR``).
- Bulanık eşleşme yalnız tek kelimelik ifadelerde, ilk harf aynıysa ve Levenshtein uzaklığı
  küçükse yapılır (tanıda ≥8 harf ve ≤1; ilaçta 6–9 harf ≤1, ≥10 harf ≤2). "hipotansiyon" ile
  "hipertansiyon" (uzaklık 2) bu yüzden eşleşmez.
- İlaç satırı ad ve kullanım olarak ayrılır: ilk rakam ya da form/kullanım kelimesinden
  ("tablet", "günde", "sabah", "tok"…) önceki kelimeler addır. ``AxB`` (``1x1``, ``2 x 1``)
  ve "günde N" kalıpları kanonik kullanıma çevrilir: "Günde 2 kez, her seferinde 1 adet".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ortak.turkce import ascii_katla, normalize

from .bilgi_tabani import KbKaydi

AZAMI_BILINMEYEN = 20
AZAMI_TERIM_UZUNLUGU = 60
AZAMI_TERIM_KELIME = 5
# Terim benzeri olmayan (cümle, talimat, garip karakterli) bilinmeyen girdiler hastaya da gidebilen
# ``unknownTerms`` ve ``name`` alanlarına aynen yazılmaz; yerine bu sabitler yazılır.
TERIM_DISI_TANI = "Açıklanamayan tanı ifadesi"
TERIM_DISI_ILAC = "Açıklanamayan ilaç satırı"
_TERIM_KARAKTERLERI = re.compile(r"^[0-9A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû][0-9A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû .,'’/%+\-]*$")
# Talimat/komut kokan kelimeler (normalize, kelime başı): terim değildir.
_TALIMAT_KOKLERI = ("talimat", "soyle", "yok say", "unut", "sistem", "system", "prompt", "ignore", "hastaya",
                    "birak", "kesin", "kesme", "durdur", "katla", "artir", "azalt", "de ki", "kural", "asistan",
                    "yapay zeka", "cevap", "yanit", "gerek yok")

# Tanı bölütünde KB ifadesinin yanında bulunabilecek, anlamı değiştirmeyen kelimeler (normalize).
TANI_NITELEYICILERI = frozenset({
    "hafif", "orta", "agir", "siddetli", "evre", "derece", "kontrolsuz", "kontrollu", "kontrol", "disi",
    "altinda", "yeni", "tani", "tanili", "tanisi", "teshis", "teshisi", "esansiyel", "primer", "olasi",
    "on", "ile", "uyumlu", "bulgusu", "bulgulari", "tablosu", "nedeniyle", "takibi", "takip", "atak",
    "atagi", "ataklari", "akut", "ve", "de", "da", "icin",
})
# İlaç adında etken maddeye eşlik edebilecek tuz/salım kelimeleri.
ILAC_NITELEYICILERI = frozenset({"hcl", "sodyum", "xr", "sr", "retard", "trihidrat", "film", "kapli",
                                 "tablet", "tb", "tab", "kapsul"})
# Bu kelimelerden biri görülünce ilaç adı biter, kullanım başlar (normalize).
KULLANIM_BASLATICILARI = frozenset({
    "tablet", "tb", "tab", "tbl", "kapsul", "kaps", "kap", "film", "efervesan", "surup", "damla", "sase",
    "ampul", "flakon", "krem", "merhem", "gunde", "gunluk", "sabah", "aksam", "ogle", "gece", "aksamlari",
    "sabahlari", "tok", "ac", "yemek", "yemekle", "yemekten", "yemeklerden", "her", "gerektiginde",
    "gerekirse", "ihtiyac", "haftada", "ayda", "kahvalti", "kahvaltidan", "x", "po", "oral", "agizdan",
    "yatmadan", "once", "sonra", "dil", "kullan", "kullanin", "al", "alin", "ic", "icin",
})

_ICD = re.compile(r"^[a-z]\d{2}\d{0,2}$")
_BOLUT_AYIRICI = re.compile(r"[,;/\n\r+()\[\]:]|\.(?=\s|$)|\s+[vV][eE]\s+")

_SAYI_KELIME = {"bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5, "alti": 6}
_AXB = re.compile(r"(?<![\d.,])(\d{1,2})\s*[x*×]\s*(\d{1,2}(?:[.,]\d)?)(?![\d])")
_ACIK_MIKTAR = re.compile(r"(?:\d+(?:[.,]\d+)?|yarim|bir|iki|uc|dort)\s*(?:puf|tablet|tb|tab|kapsul|kaps|damla|olcek|"
                          r"kasik|sase|adet|tane|ampul|flakon|ml|cc)\b")
_GUNDE = re.compile(r"\bgunde\s+(\d{1,2}|bir|iki|uc|dort|bes|alti)\b")


# --------------------------------------------------------------------------
# Yardımcılar
# --------------------------------------------------------------------------

def levenshtein(a: str, b: str, sinir: int) -> int:
    """Sınırı aşınca erken biten düzenleme uzaklığı (sınır+1 döndürür)."""
    if abs(len(a) - len(b)) > sinir:
        return sinir + 1
    onceki = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        simdiki = [i]
        en_kucuk = i
        for j, cb in enumerate(b, 1):
            deger = min(onceki[j] + 1, simdiki[j - 1] + 1, onceki[j - 1] + (ca != cb))
            simdiki.append(deger)
            en_kucuk = min(en_kucuk, deger)
        if en_kucuk > sinir:
            return sinir + 1
        onceki = simdiki
    return onceki[-1]


def _kelimeler(metin: str) -> list[str]:
    return [k.strip(":") for k in normalize(metin).split() if k.strip(":")]


def _kelime_uyar(kelime: str, ifade_kelimesi: str, son: bool) -> bool:
    """Tam eşleşme; son kelimede Türkçe ek ("hipertansiyonu", "gastriti") de kabul edilir."""
    if kelime == ifade_kelimesi:
        return True
    return son and kelime.startswith(ifade_kelimesi) and len(ifade_kelimesi) >= 4 \
        and len(kelime) - len(ifade_kelimesi) <= 5 and kelime[len(ifade_kelimesi):].isalpha()


def _bulanik_sinir(uzunluk: int, ilac: bool) -> int:
    if ilac:
        return 2 if uzunluk >= 10 else (1 if uzunluk >= 6 else 0)
    return 1 if uzunluk >= 8 else 0


_BOSLUK_DENETIM = frozenset(chr(c) for c in (9, 10, 11, 12, 13))


def gorunur_metin(metin: str) -> str:
    """NFC; denetim/biçim karakterleri (sıfır genişlikli boşluk, yön değiştirme U+202E…) atılır."""
    metin = unicodedata.normalize("NFC", metin)
    metin = "".join(" " if c in _BOSLUK_DENETIM else c for c in metin
                    if c in _BOSLUK_DENETIM or unicodedata.category(c) not in ("Cc", "Cf", "Co", "Cs", "Cn"))
    return re.sub(r"\s+", " ", metin).strip()


def _kisalt(metin: str) -> str:
    metin = gorunur_metin(metin).strip(" -–.,;:")
    return metin if len(metin) <= AZAMI_TERIM_UZUNLUGU else metin[: AZAMI_TERIM_UZUNLUGU - 1].rstrip() + "…"


def terim_benzeri(metin: str) -> bool:
    """Kısa, yalnız harf/rakam/temel noktalama, talimat kokmayan bir terim mi?

    ``unknownTerms`` ve bilinmeyen ilaç adı hekim onayından sonra hasta portalına da gider; bu alanlara
    hekim girdisinden cümle ya da talimat taşınmaz.
    """
    temiz = gorunur_metin(metin)
    if not temiz or temiz != re.sub(r"\s+", " ", unicodedata.normalize("NFC", metin)).strip():
        return False          # görünmez/denetim karakteri vardı
    if len(temiz) > AZAMI_TERIM_UZUNLUGU or len(temiz.split()) > AZAMI_TERIM_KELIME:
        return False
    if not _TERIM_KARAKTERLERI.match(temiz):
        return False
    normal = " " + normalize(temiz) + " "
    return not any(f" {kok}" in normal for kok in _TALIMAT_KOKLERI)


@dataclass
class _Kapsama:
    kayitlar: list[KbKaydi] = field(default_factory=list)
    kalan: list[str] = field(default_factory=list)


def _kapsa(kelimeler: list[str], adaylar: Sequence[KbKaydi], niteleyiciler: frozenset[str],
           ilac: bool) -> _Kapsama:
    """Kelime listesini KB ifadeleriyle kapsar; kapsanmayan anlamlı kelimeleri döndürür."""
    kapsandi = [False] * len(kelimeler)
    bulunan: list[KbKaydi] = []
    # Uzun ifadeler önce: "demir eksikligi anemisi" "demir eksikligi"nden önce denenir.
    ifadeler = sorted(((k, ifade.split()) for k in adaylar for ifade in k.eslesme_ifadeleri()),
                      key=lambda x: -len(x[1]))
    for kayit, parcalar in ifadeler:
        n = len(parcalar)
        for i in range(len(kelimeler) - n + 1):
            if any(kapsandi[i:i + n]):
                continue
            uyar = all(_kelime_uyar(kelimeler[i + j], parcalar[j], j == n - 1) for j in range(n))
            if not uyar and n == 1:
                sinir = _bulanik_sinir(len(parcalar[0]), ilac)
                k = kelimeler[i]
                uyar = (sinir > 0 and len(k) >= 6 and k[0] == parcalar[0][0]
                        and levenshtein(k, parcalar[0], sinir) <= sinir)
            if uyar:
                for j in range(n):
                    kapsandi[i + j] = True
                if kayit not in bulunan:
                    bulunan.append(kayit)
    kalan: list[str] = []
    for i, kelime in enumerate(kelimeler):
        if kapsandi[i] or kelime in niteleyiciler or _ICD.match(kelime):
            continue
        onceki = kelimeler[i - 1] if i else ""
        if kelime.isdigit() and (onceki in ("evre", "derece") or _ICD.match(onceki)):
            continue
        kalan.append(kelime)
    return _Kapsama(bulunan, kalan)


# --------------------------------------------------------------------------
# İlaç satırı
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class IlacSatiri:
    ham: str
    ad: str            # hekimin yazdığı ad (özgün yazımıyla)
    kullanim: str      # addan sonraki kısım (doz, sıklık, zamanlama), özgün yazımıyla
    siklik: int | None = None          # günde kaç kez (AxB ya da "günde N"den)
    kullanim_acik: str | None = None   # "Günde 1 kez, her seferinde 1 adet"


def _sayi_yaz(deger: str) -> str:
    return deger.replace(".", ",")


def kullanim_coz(kullanim: str) -> tuple[int | None, str | None]:
    """Hekimin kullanım ifadesinden günlük sıklığı ve kanonik açıklamayı çıkarır."""
    katli = ascii_katla(kullanim)
    m = _AXB.search(katli)
    if m:
        siklik = int(m.group(1))
        miktar = _sayi_yaz(m.group(2))
        if 1 <= siklik <= 6:
            # Satırda AxB dışında ayrıca bir miktar yazılmışsa ("2 puf 4x1", "1x1 yarım tablet")
            # "her seferinde B adet" çıkarımı yapılmaz; yalnız sıklık söylenir.
            kalan = katli[:m.start()] + " " + katli[m.end():]
            if _ACIK_MIKTAR.search(kalan):
                return siklik, f"Günde {siklik} kez"
            return siklik, f"Günde {siklik} kez, her seferinde {miktar} adet"
    m = _GUNDE.search(katli)
    if m:
        ham = m.group(1)
        siklik = int(ham) if ham.isdigit() else _SAYI_KELIME[ham]
        if 1 <= siklik <= 6:
            return siklik, f"Günde {siklik} kez"
    return None, None


def ilac_satiri_ayir(satir: str) -> IlacSatiri:
    satir = re.sub(r"\s+", " ", satir).strip()
    parcalar = satir.split(" ")
    sinir = len(parcalar)
    for i, parca in enumerate(parcalar):
        n = normalize(parca)
        if i > 0 and (any(c.isdigit() for c in parca) or n in KULLANIM_BASLATICILARI
                      or re.match(r"^\d", n)):
            sinir = i
            break
        if i == 0 and any(c.isdigit() for c in parca) and not re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3}", parca):
            sinir = 0
            break
    ad = " ".join(parcalar[:sinir]).strip(" ,;:-–")
    kullanim = " ".join(parcalar[sinir:]).strip(" ,;:-–")
    if not ad:
        ad, kullanim = satir, ""
    siklik, acik = kullanim_coz(kullanim)
    return IlacSatiri(ham=satir, ad=ad, kullanim=kullanim, siklik=siklik, kullanim_acik=acik)


# --------------------------------------------------------------------------
# Eşleme sonucu
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TaniEslesmesi:
    yazilan: str
    kayit: KbKaydi


@dataclass(frozen=True)
class IlacEslesmesi:
    no: str              # "i1", "i2"… (LLM çıktısında ilacı bu kimlik taşır)
    satir: IlacSatiri
    kayit: KbKaydi | None

    @property
    def gorunen_ad(self) -> str:
        if self.kayit is None and not terim_benzeri(self.satir.ad):
            return f"{TERIM_DISI_ILAC} ({self.no[1:]}. satır)"
        ad =_kisalt(self.satir.ad) or "İlaç"
        if self.kayit is not None and normalize(self.kayit.terim) not in normalize(ad):
            return f"{ad} ({self.kayit.terim})"
        return ad


@dataclass
class EslemeSonucu:
    tanilar: list[TaniEslesmesi]
    bilinmeyen_tanilar: list[str]
    ilaclar: list[IlacEslesmesi]

    @property
    def bilinen_ilaclar(self) -> list[IlacEslesmesi]:
        return [i for i in self.ilaclar if i.kayit is not None]

    @property
    def bilinmeyenler(self) -> list[str]:
        sonuc: list[str] = []
        gorulen: set[str] = set()
        adaylar = [t if terim_benzeri(t) else TERIM_DISI_TANI for t in self.bilinmeyen_tanilar] +                   [i.gorunen_ad for i in self.ilaclar if i.kayit is None]
        for t in adaylar:
            t = _kisalt(t)
            n = normalize(t)
            if t and n not in gorulen:
                gorulen.add(n)
                sonuc.append(t)
        return sonuc[:AZAMI_BILINMEYEN]

    @property
    def kaynak_idleri(self) -> list[str]:
        idler: list[str] = []
        for k in [t.kayit for t in self.tanilar] + [i.kayit for i in self.bilinen_ilaclar]:
            if k is not None and k.id not in idler:
                idler.append(k.id)
        return idler

    @property
    def hepsi_bilinen(self) -> bool:
        return not self.bilinmeyen_tanilar and all(i.kayit is not None for i in self.ilaclar)

    @property
    def eslesen_var(self) -> bool:
        return bool(self.tanilar or self.bilinen_ilaclar)


def tani_bolutleri(tani: str) -> list[str]:
    return [b.strip() for b in _BOLUT_AYIRICI.split(tani) if b and b.strip()]


def esle(tani: str, ilaclar: Iterable[str], kayitlar: Sequence[KbKaydi]) -> EslemeSonucu:
    tani_kayitlari = [k for k in kayitlar if k.tani_mi]
    ilac_kayitlari = [k for k in kayitlar if not k.tani_mi]

    tanilar: list[TaniEslesmesi] = []
    bilinmeyen: list[str] = []
    for bolut in tani_bolutleri(tani):
        kelimeler = _kelimeler(bolut)
        if not kelimeler:
            continue
        kapsama = _kapsa(kelimeler, tani_kayitlari, TANI_NITELEYICILERI, ilac=False)
        if kapsama.kayitlar and not kapsama.kalan:
            for kayit in kapsama.kayitlar:
                if all(t.kayit.id != kayit.id for t in tanilar):
                    tanilar.append(TaniEslesmesi(_kisalt(bolut), kayit))
        elif kapsama.kalan:
            # Hiç anlamlı kelimesi olmayan bölütler ("Tanı:") yok sayılır; diğerleri bilinmeyen.
            # Ham bölüt tutulur (terim denetimi ``bilinmeyenler``de; kısaltma terimi gizlemesin).
            bilinmeyen.append(bolut)

    ilac_sonuclari: list[IlacEslesmesi] = []
    sira = 0
    for ham in ilaclar:
        if not ham or not ham.strip():
            continue
        sira += 1
        satir = ilac_satiri_ayir(ham)
        kapsama = _kapsa(_kelimeler(satir.ad), ilac_kayitlari, ILAC_NITELEYICILERI, ilac=True)
        # Tam olarak tek etken madde ve kapsanmayan kelime yok: aksi halde (kombinasyon, bilinmeyen
        # marka, yazım belirsizliği) ilaç bilinmeyen sayılır.
        kayit = kapsama.kayitlar[0] if len(kapsama.kayitlar) == 1 and not kapsama.kalan else None
        ilac_sonuclari.append(IlacEslesmesi(no=f"i{sira}", satir=satir, kayit=kayit))
    return EslemeSonucu(tanilar, bilinmeyen, ilac_sonuclari)
