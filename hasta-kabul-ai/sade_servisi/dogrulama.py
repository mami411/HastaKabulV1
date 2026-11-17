"""LLM çıktısının güvenlik denetimleri — istemle değil kodla zorlanır.

1. **Doz/sayı denetimi** (``doz_ihlalleri``): çıktıdaki her sayı, sayı+birim ve sıklık ifadesi
   izinli kaynaklarda (hekim girdisi + eşleşen KB kaydı) geçmek zorunda.
   - "30 mg", "500mg", "0,5 ml", "yarım saat", "iki hafta" → (değer, birim) çifti
   - "1x1", "2 x 1" → (2, kez) + (1, adet);  "günde iki" → (2, kez)
   - Yazıyla sayılar (iki, üç, yarım…) yalnız ardından birim/sıklık kelimesi gelince sayılır
     ("bir bardak su" sayı değildir).
   Kurallar: mg/g/mcg/ml/ünite ve süre birimleri (saat/gün/hafta/ay/dakika) aynı birimle
   kaynakta geçmeli; "kez" kaynakta "kez" olarak geçmeli; adet/tablet/kapsül/damla… kendi
   aralarında eşdeğerdir; birimsiz sayı kaynaktaki herhangi bir sayıya eşit olmalı.
2. **Yasak kalıplar** (``yasak_ihlali``): ilacı bırak/kes, dozu değiştir, fazla/az al, ilaca
   gerek yok, hekime gitmeye gerek yok, ağır teşhis/ölüm/kanser dili, bitkisel/takviye önerisi,
   hastadan üçüncü şahıs olarak söz etme. Karşılaştırma ``ortak.turkce.normalize`` ile yapılır
   (I/İ tuzağı yok: "BIRAKIN" = "bırakın" = "birakin"). Olumsuz biçimler ("bırakmayın",
   "değiştirmeyin") yasak değildir.
3. **Biçim** (``temizle``): Markdown işaretleri silinir, HTML/bağlantı reddedilir, uzun metinde
   hiç Türkçe karakter yoksa reddedilir (model ASCII KB metnini kopyalamış demektir).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ortak.guvenlik import YasakKurali, denetle
from ortak.turkce import ascii_katla, normalize

# --------------------------------------------------------------------------
# Sayı ifadeleri
# --------------------------------------------------------------------------

SERT_BIRIMLER = frozenset({"mg", "g", "mcg", "ml", "unite", "saat", "dakika", "gun", "hafta", "ay", "yil", "yuzde"})
ADET_BIRIMLERI = frozenset({"adet", "tablet", "kapsul", "damla", "olcek", "kasik", "puf", "sase", "doz"})

_BIRIM_TAM = {
    "mg": "mg", "miligram": "mg", "g": "g", "gr": "g", "gram": "g", "mcg": "mcg", "mikrogram": "mcg",
    "ug": "mcg", "µg": "mcg", "ml": "ml", "mililitre": "ml", "cc": "ml", "iu": "unite", "unite": "unite",
    "kez": "kez", "kere": "kez", "defa": "kez", "sefer": "kez",
    "adet": "adet", "tane": "adet", "tb": "tablet", "tab": "tablet", "tbl": "tablet", "hap": "adet",
    "puf": "puf", "doz": "doz", "dozu": "doz", "dozda": "doz",
    "ay": "ay", "ayda": "ay", "aylik": "ay", "aydir": "ay", "ayin": "ay",
    "dk": "dakika", "sa": "saat",
}
_BIRIM_KOK = (
    ("tablet", "tablet"), ("kapsul", "kapsul"), ("damla", "damla"), ("olcek", "olcek"), ("kasik", "kasik"),
    ("sase", "sase"), ("saat", "saat"), ("dakika", "dakika"), ("gun", "gun"), ("hafta", "hafta"),
    ("yil", "yil"), ("defa", "kez"), ("kere", "kez"), ("kez", "kez"), ("adet", "adet"), ("tane", "adet"),
    ("unite", "unite"), ("miligram", "mg"), ("mikrogram", "mcg"), ("mililitre", "ml"), ("gram", "g"),
)
_SAYI_KELIME = {
    "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5, "alti": 6, "yedi": 7, "sekiz": 8, "dokuz": 9, "on": 10,
    "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50, "yuz": 100, "bin": 1000, "yarim": 0.5,
}

_AXB = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*[x*×]\s*(\d+(?:[.,]\d+)?)(?![\d])")
_SIKLIK = re.compile(r"\b(gunde|haftada|ayda)\s+(\d+(?:[.,]\d+)?|" + "|".join(_SAYI_KELIME) +
                     r")(?:\s+(bucuk))?(?:\s+([a-zµ]+))?")
_RAKAM = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(?:\s*(%|[a-zµ]+))?")
_YAZI = re.compile(r"\b(" + "|".join(_SAYI_KELIME) + r")(?:\s+(bucuk))?\s+([a-zµ]+)")
_YUZDE = re.compile(r"%\s*(\d+(?:[.,]\d+)?)")

Ifade = tuple[float, str | None]


def birim_coz(kelime: str | None) -> str | None:
    if not kelime:
        return None
    if kelime == "%":
        return "yuzde"
    if kelime in _BIRIM_TAM:
        return _BIRIM_TAM[kelime]
    for kok, birim in _BIRIM_KOK:
        if kelime.startswith(kok):
            return birim
    return None


def _deger(ham: str) -> float:
    return round(float(ham.replace(",", ".")), 3)


def sayi_ifadeleri(metin: str) -> list[Ifade]:
    """Metindeki sayı/doz/sıklık ifadelerini (değer, birim) listesi olarak çıkarır."""
    katli = ascii_katla(metin)
    sonuc: list[Ifade] = []

    def sil(m: re.Match[str]) -> str:
        return " " * (m.end() - m.start())

    for m in _AXB.finditer(katli):
        sonuc += [(_deger(m.group(1)), "kez"), (_deger(m.group(2)), "adet")]
    katli = _AXB.sub(sil, katli)

    for m in _YUZDE.finditer(katli):
        sonuc.append((_deger(m.group(1)), "yuzde"))
    katli = _YUZDE.sub(sil, katli)

    def siklik(m: re.Match[str]) -> str:
        # "günde 2", "günde iki kez", "günde 2 tablet": sıklık (N, kez) olarak sayılır; sayı ve
        # ardındaki kelime silinir (birim tek başına kalınca başka kurala takılmaz).
        ham = m.group(2)
        deger = _deger(ham) if ham[0].isdigit() else float(_SAYI_KELIME[ham])
        if m.group(3):
            deger += 0.5
        # "haftada 1" günlük sıklık değildir: ayrı birimle sayılır ("günde 1" ile karışmaz).
        birim = {"gunde": "kez", "haftada": "kez/hafta", "ayda": "kez/ay"}[m.group(1)]
        sonuc.append((round(deger, 3), birim))
        return m.group(1) + " " * (m.end() - m.end(1))

    katli = _SIKLIK.sub(siklik, katli)

    for m in _RAKAM.finditer(katli):
        sonuc.append((_deger(m.group(1)), birim_coz(m.group(2))))
    katli = _RAKAM.sub(sil, katli)

    for m in _YAZI.finditer(katli):
        birim = birim_coz(m.group(3))
        if birim is None:
            continue
        deger = float(_SAYI_KELIME[m.group(1)]) + (0.5 if m.group(2) else 0.0)
        sonuc.append((round(deger, 3), birim))
    return sonuc


def doz_ihlalleri(cikti: str, kaynaklar: Iterable[str]) -> list[Ifade]:
    """Çıktıda olup kaynaklarda karşılığı olmayan sayı ifadelerini döndürür (boşsa geçti)."""
    kaynak_ifadeleri: list[Ifade] = []
    for k in kaynaklar:
        kaynak_ifadeleri += sayi_ifadeleri(k)
    ciftler = set(kaynak_ifadeleri)
    degerler = {d for d, _ in kaynak_ifadeleri}
    adet_degerleri = {d for d, b in kaynak_ifadeleri if b is None or b in ADET_BIRIMLERI}
    ihlaller: list[Ifade] = []
    for deger, birim in sayi_ifadeleri(cikti):
        if birim is None:
            gecerli = deger in degerler
        elif birim in ADET_BIRIMLERI:
            gecerli = deger in adet_degerleri
        else:  # sert birimler ve "kez": birebir aynı çift kaynakta olmalı
            gecerli = (deger, birim) in ciftler
        if not gecerli:
            ihlaller.append((deger, birim))
    return ihlaller


_SINIR = re.compile(r"\b(en fazla|en az|en cok|maksimum|minimum|azami|asgari)\s+((?:gunde|haftada)\s+)?"
                    r"(\d+(?:[.,]\d+)?|" + "|".join(_SAYI_KELIME) + r")(?:\s+([a-zµ]+))?")


def _sinir_ifadeleri(metin: str) -> set[tuple[str, float, str | None]]:
    sonuc = set()
    for m in _SINIR.finditer(ascii_katla(metin)):
        ham = m.group(3)
        deger = _deger(ham) if ham[0].isdigit() else float(_SAYI_KELIME[ham])
        tur = "en_cok" if m.group(1) in ("en fazla", "en cok", "maksimum", "azami") else "en_az"
        sonuc.add((tur, round(deger, 3), birim_coz(m.group(4))))
    return sonuc


def sinir_ihlalleri(cikti: str, kaynaklar: Iterable[str]) -> list[tuple[str, float, str | None]]:
    """"En fazla 500 mg" gibi üst/alt sınır ifadeleri kaynakta aynı sınır olarak geçmeli.

    Sayı denetimi "500 mg"ı (tek doz olarak kaynakta var) geçirir; ama "en fazla 500 mg" günlük
    üst sınır anlamına gelir ve uydurmadır. Bu denetim o anlam kaymasını yakalar.
    """
    kaynak = set()
    for k in kaynaklar:
        kaynak |= _sinir_ifadeleri(k)
    ihlaller = []
    for tur, deger, birim in _sinir_ifadeleri(cikti):
        if not any(t == tur and d == deger and (b == birim or b is None or birim is None) for t, d, b in kaynak):
            ihlaller.append((tur, deger, birim))
    return ihlaller


def siklik_degerleri(metin: str) -> set[float]:
    return {d for d, b in sayi_ifadeleri(metin) if b == "kez"}


# --------------------------------------------------------------------------
# Yasak kalıplar
# --------------------------------------------------------------------------

YEDEK = ""  # yedek cümle kullanılmaz: ihlalde çıktı reddedilir, yeniden denenir ya da şablona düşülür

# Sabit ifadeler: ortak.guvenlik.denetle ile (kelime başından, Türkçe katlamalı) aranır.
YASAK_KURALLARI: tuple[YasakKurali, ...] = (
    YasakKurali("agir_dil", YEDEK, ifadeler=(
        "kanser", "tümör", "ölümcül", "ölüm", "öldür", "hayati tehlike", "kalıcı hasar", "ameliyat", "felç",
        "kalp krizi", "inme", "sakat")),
    YasakKurali("guvence", YEDEK, ifadeler=(
        "endişe etmeyin", "endişelenmeyin", "korkmayın", "merak etmeyin", "ciddi bir hastalık değil",
        "tamamen geçer", "kesinlikle iyileş", "garanti")),
    YasakKurali("baska_tedavi", YEDEK, ifadeler=(
        "bitkisel", "takviye", "kür yapın", "eczaneden alabilirsiniz", "reçetesiz", "doğal yöntem")),
    YasakKurali("teshis", YEDEK, ifadeler=(
        "sizde ayrıca", "başka bir hastalığınız", "teşhis ediyorum", "tanı koyuyorum", "muhtemelen sizde")),
    # İstem/kural sızıntısı ve KB'de olmayan genel öğütler (küçük modeller kuralları çıktıya kopyalıyor).
    YasakKurali("meta", YEDEK, ifadeler=(
        "başka ilaç almayın", "başka ilaç almanız", "başka ilaç kullanmayın", "hekim onayı olmadan",
        "hekim onayını almadan", "hekimin onayıyla", "tarafından belirlen", "hastalık koymam", "tanı koymam",
        "ciddiyetini", "yorum yapma", "talimat", "bilgi metni", "onaylı sade", "onayli_sade", "hekim_kullanim",
        "veride", "verideki", "yeni bir doz", "kurallara", "yapay zeka", "dil modeli")),
)

# Normalize (ASCII katlanmış, küçük harf, noktalamasız) metne uygulanan düzenli ifadeler.
# _OLUMSUZ olumsuz eki dışarıda bırakır: "bırakın" yasak; "bırakmayın", edilgen "bırakılmaz",
# "kesilmemeli", "durdurulmamalı" serbest.
_OLUMSUZ = r"(?!(?:[iu]?[ln])?m[ae])"
_NESNE = r"(?:ilac\w*|tedavi\w*|hap\w*|tablet\w*|kapsul\w*|antibiyotig\w*|antibiyotik\w*|kullanim\w*|kur\w*)"
YASAK_DESENLER: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ilaci_birakma", re.compile(_NESNE + r"\s+(?:\w+\s+){0,2}?(?:birak|kes|durdur)" + _OLUMSUZ + r"\w*")),
    ("ilaci_birakma", re.compile(r"\b(?:kullanmayi|almayi|icmeyi|kullanmaya|almaya)\s+(?:birak|kes|son ver)" + _OLUMSUZ + r"\w*")),
    ("ilaci_birakma", re.compile(r"\b(?:birakabilir|kesebilir|durdurabilir|ara verebilir)\w*")),
    ("ilaci_birakma", re.compile(r"\b(?:ilac\w*|tedavi\w*|kullanmaniz\w*|almaniz\w*|icmeniz\w*)\s+(?:\w+\s+){0,2}?gerek(?:\w*)\s+(?:yok|kalma)")),
    ("doz_degistirme", re.compile(r"\b(?:doz\w*|miktar\w*)\s+(?:\w+\s+){0,2}?(?:artir|azalt|yukselt|dusur|degistir|ikiye|iki kat|ayarla)" + _OLUMSUZ + r"\w*")),
    ("doz_degistirme", re.compile(r"\b(?:daha fazla|daha az|fazladan|ekstra|ilave|cift doz|iki kati|iki misli)\s+(?:\w+\s+){0,2}?(?:al|kullan|ic|yut)" + _OLUMSUZ + r"\w*")),
    ("doz_degistirme", re.compile(r"\b(?:atladiginiz|unuttugunuz)\s+(?:\w+\s+){0,2}?(?:iki|cift)")),
    ("ilaci_birakma", re.compile(r"\b(?:yarida|erken(?:den)?)\s+(?:\w+\s+){0,1}?(?:birak|kes|durdur|son ver)" + _OLUMSUZ + r"\w*")),
    ("ilaci_birakma", re.compile(r"\b(?:gecince|gectiginde|gecerse|iyilesince|iyilestiginde|duzelince|duzeldiginde|"
                                 r"azalinca|azaldiginda)\s+(?:\w+\s+){0,3}?(?:birak|kes|durdur|son ver|ara ver)"
                                 + _OLUMSUZ + r"\w*")),
    ("ilaci_birakma", re.compile(r"\b(?:gecene|iyilesene|duzelene|azalana|bitene)\s+kadar")),
    # İzin veren dil ("alabilirsiniz", "kullanabilirsiniz"): hekim talimatı izin değil talimattır.
    ("izin_dili", re.compile(r"\b\w+(?:abilirsiniz|ebilirsiniz|abilirsin|ebilirsin)\b")),
    ("hekime_gitmeme", re.compile(r"\b(?:hekim\w*|doktor\w*|kontrol\w*|muayene\w*)\s+(?:\w+\s+){0,3}?gerek\w*\s+(?:yok|kalma)")),
    ("ucuncu_sahis", re.compile(r"\bhasta(?:nin|ya|da|yi|lar|larin)?\b")),
)


def yasak_ihlali(metin: str) -> str | None:
    """İlk ihlalin kural adını döndürür; yoksa None."""
    ihlal = denetle(metin, YASAK_KURALLARI)
    if ihlal is not None:
        return ihlal.kural
    normal = normalize(metin)
    for ad, desen in YASAK_DESENLER:
        if desen.search(normal):
            return ad
    return None


# --------------------------------------------------------------------------
# Biçim
# --------------------------------------------------------------------------

_TURKCE_HARFLER = set("çğıöşüÇĞİÖŞÜ")
# İzinli karakterler: Türkçe/Latin harfler, rakam, temel noktalama. Başka yazı sistemi (ör. model
# çıktısına karışan 評) ya da emoji görülürse metin reddedilir.
_YABANCI_KARAKTER = re.compile(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşüÂÎÛâîû\s.,;:!?()'’\"%/\-–]")
_CUMLE_SONU = ".!?"
_HTML = re.compile(r"<\s*/?\s*[a-zA-Z!][^>]*>")
_BAGLANTI = re.compile(r"https?://|www\.", re.I)


class BicimHatasi(ValueError):
    pass


def temizle(metin: object, *, azami: int, alan: str, azami_cumle: int = 6) -> str:
    """LLM metnini düz cümleye indirir; kullanılamazsa ``BicimHatasi``."""
    if not isinstance(metin, str):
        raise BicimHatasi(f"{alan}:tip")
    if _HTML.search(metin) or _BAGLANTI.search(metin):
        raise BicimHatasi(f"{alan}:html")
    metin = re.sub(r"(?m)^\s*(?:[-•*]|\d+[.)])\s+", "", metin)   # madde işaretleri
    metin = re.sub(r"[*#`_~]+", "", metin)                       # Markdown vurguları/başlıklar
    metin = re.sub(r"\s+", " ", metin).strip()
    if not metin:
        raise BicimHatasi(f"{alan}:bos")
    if len(metin) > azami:
        raise BicimHatasi(f"{alan}:uzun")
    if len(metin) > 80 and not (_TURKCE_HARFLER & set(metin)):
        raise BicimHatasi(f"{alan}:turkce_karakter")
    if _YABANCI_KARAKTER.search(metin):
        raise BicimHatasi(f"{alan}:karakter")
    if metin[-1] not in _CUMLE_SONU:
        # Sınıra dayanmış ve noktalamasız biten metin kesilmiştir (şema uzunluk sınırı / token sınırı).
        if len(metin) >= azami * 0.85:
            raise BicimHatasi(f"{alan}:kesik")
        metin += "."
    if len([c for c in re.split(r"[.!?]+", metin) if c.strip()]) > azami_cumle:
        raise BicimHatasi(f"{alan}:cok_cumle")
    cumleler = [normalize(c) for c in re.split(r"[.!?]+", metin) if len(c.strip()) >= 15]
    if len(cumleler) != len(set(cumleler)):
        raise BicimHatasi(f"{alan}:tekrar")
    return metin


# --------------------------------------------------------------------------
# Kaynak dağarcığı (benzerlik) denetimi
# --------------------------------------------------------------------------
# LLM serbest yazmaz: çıktıdaki her içerik kelimesi izinli kaynaklarda (eşleşen KB kaydı, onaylı sade
# metin, hekimin eşleşen terimi) ya da aşağıdaki küçük genel sözlükte geçmek zorunda. Böylece KB'de
# olmayan endikasyon, yan etki, öneri ("insülin", "ülser", "ishal", "reflü") ya da başka ilacın
# açıklaması geçemez. Türkçe ekler için kök eşleşmesi: ortak önek ≥5 harf ya da kısa kökün tamamı.
# Olumsuzluk taşıyan kelimeler (almayın, bırakılmaz, değil, yetersiz…) yalnız BİREBİR eşleşir:
# "bırakılmaz" kaynağından "bırakılır", "yetersiz"den "yeterli" türetilemez.
GENEL_SOZLUK = frozenset(normalize(k) for k in """
bu buna bunu bunun bunlar şu o bir ve ile ya da de veya yani gibi için olarak olan olur olabilir olması
denir denilir adı adını verilir siz size sizi sizin sizde sizinle var vardır oluyor olmuş olmuştur
çok daha en her zaman sık genellikle çoğu bazen ayrıca hem ise ki kadar sonucu nedeniyle neden yol
açar açabilir yapar yapabilir eder edebilir sağlar sağlayan durumdur durum hâl hal haldedir kalır
""".split())

_OLUMSUZ_KELIME = re.compile(
    r"^(?:degil|yok)\w*$"
    r"|^\w{2,}m[ae](?:z|yin|yiniz|di|mis|m[ae]l|dan|den|yacak|yecek|yan|yen|ksizin)\w*$"
    r"|^\w{2,}s[iu]z\w{0,6}$")


def olumsuz_mu(kelime: str) -> bool:
    return bool(_OLUMSUZ_KELIME.search(kelime))


def _ortak_onek(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


_YUMUSAMA = {"p": "b", "c": "c", "t": "d", "k": "g"}


def _kelime_uyar(cikti: str, kaynak: str) -> bool:
    # Ünsüz yumuşaması: "asit" ~ "asidi", "ilac" ~ "ilaci", "bebek" ~ "bebegi".
    if cikti and cikti[-1] in _YUMUSAMA and len(cikti) >= 3:
        if _kelime_uyar_yalin(cikti[:-1] + _YUMUSAMA[cikti[-1]], kaynak) and not olumsuz_mu(kaynak):
            return True
    if kaynak and kaynak[-1] in _YUMUSAMA and len(kaynak) >= 3:
        if _kelime_uyar_yalin(cikti, kaynak[:-1] + _YUMUSAMA[kaynak[-1]]) and not olumsuz_mu(cikti):
            return True
    return _kelime_uyar_yalin(cikti, kaynak)


def _kelime_uyar_yalin(cikti: str, kaynak: str) -> bool:
    if cikti == kaynak:
        return True
    if olumsuz_mu(cikti) or olumsuz_mu(kaynak):
        return False
    n = _ortak_onek(cikti, kaynak)
    if n >= 5:
        return True
    # Kısa kök ("mide" → "midenizin", "kan" → "kanınız") ya da kısalmış ek ("alınır" → "alın").
    kisa = min(len(cikti), len(kaynak))
    return n == kisa and n >= 3 and abs(len(cikti) - len(kaynak)) <= 7


def _kucuk(metin: str) -> list[str]:
    from ortak.turkce import turkce_kucuk
    return [k for k in re.sub(r"[^\w]+", " ", turkce_kucuk(metin).replace("'", "").replace("’", "")).split() if k]


_TURKCE_OZEL = set("çğıöşü")


def kaynak_disi_kelimeler(cikti: str, kaynaklar: Iterable[str]) -> list[str]:
    """Çıktıda olup izinli kaynaklarda ve genel sözlükte karşılığı olmayan kelimeler (boşsa geçti).

    Yazım denetimi de yapar: kaynak Türkçe karakterli yazılmışsa çıktıdaki kelime aynı harflerle
    yazılmalı ("tansiyon" kaynağına karşı "tansıyon" reddedilir).
    """
    # normalize → {(özgün küçük harfli yazım, kaynak Türkçe karakterli mi)}
    kaynak_kelimeleri: dict[str, set[tuple[str, bool]]] = {}
    for k in kaynaklar:
        turkce = bool(_TURKCE_OZEL & set(k.lower()))
        for ozgun in _kucuk(k):
            kaynak_kelimeleri.setdefault(ascii_katla(ozgun), set()).add((ozgun, turkce))
    ihlaller: list[str] = []
    for ozgun in _kucuk(cikti):
        if any(c.isdigit() for c in ozgun):
            continue                      # sayılar doz denetiminde
        katli = ascii_katla(ozgun)
        if katli in GENEL_SOZLUK:
            continue
        uyan = [k for k in kaynak_kelimeleri if _kelime_uyar(katli, k)]
        if not uyan:
            ihlaller.append(ozgun)
            continue
        # Yazım: Türkçe karakterli yazılmış bir kaynakta ortak kısım harf harf aynı olmalı
        # (ASCII yazılmış backend metni bu denetimden muaftır).
        def yazim_uyar(k: str) -> bool:
            n = _ortak_onek(katli, k)
            return any(not turkce or ozgun[:n] == kaynak_ozgun[:n] for kaynak_ozgun, turkce in kaynak_kelimeleri[k])
        if not any(yazim_uyar(k) for k in uyan):
            ihlaller.append(ozgun)
    return ihlaller
