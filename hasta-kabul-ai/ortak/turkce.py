"""Türkçe metin yardımcıları (konudan bağımsız).

- ``turkce_kucuk``  : Türkçe kurallı küçük harf (I→ı, İ→i). ``str.lower()`` bunu
  yanlış yapar: "ONAYLANDI".lower() == "onaylandi" olur ve "onaylandı" yasağı
  atlanır (halisaha raporundaki büyük harf açığı).
- ``ascii_katla``   : ç→c, ğ→g, ı→i, ö→o, ş→s, ü→u (Türkçe karaktersiz yazımı
  yakalamak için; "onaylandi" de "onaylandı" ile eşleşsin).
- ``sayi_oku``      : 0–999 999 arası tam sayının okunuşu.
- ``saatleri_oku``  : yalnız saatleri ("14:30", "saat 9.15") doğal okunuşa çevirir;
  fiyat, tarih ve ondalık sayılara dokunmaz (raporun ``saati_yaz`` hatası).
- ``tc_maskele``    : 11 haneli kimlik numarası benzeri dizileri gizler.
"""

from __future__ import annotations

import re

_TR_KATLAMA = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def turkce_kucuk(metin: str) -> str:
    """Türkçe kurallarla küçük harfe çevirir: ``I→ı``, ``İ→i``, gerisi ``lower()``."""
    return metin.replace("İ", "i").replace("I", "ı").lower()


def ascii_katla(metin: str) -> str:
    """Türkçe küçük harfe çevirip Türkçe karakterleri ASCII karşılığına indirir."""
    return turkce_kucuk(metin).translate(_TR_KATLAMA)


def normalize(metin: str) -> str:
    """Eşleştirme için: ASCII'ye katlanmış, noktalaması boşluğa çevrilmiş tek boşluklu metin.

    Kesme işareti silinir (``kardiyoloji'ye`` → ``kardiyolojiye``), böylece ek alan
    kelimeler kök kelimeyle başlangıçtan eşleşir.
    """
    katli = ascii_katla(metin).replace("'", "").replace("’", "")
    katli = re.sub(r"[^\w:]+", " ", katli)
    return re.sub(r"\s+", " ", katli).strip()


def ifade_gecer_mi(normal_metin: str, ifade: str) -> bool:
    """``ifade`` normalize metinde bir kelimenin başından başlayarak geçiyor mu?

    Alt dize eşleşmesinin aksine "polikliniğe" içindeki "lini" gibi kelime ortası
    rastlantıları yakalamaz; ama ekli halleri ("onaylandınız") yakalar.
    """
    hedef = normalize(ifade)
    if not hedef:
        return False
    return re.search(r"(?<!\w)" + re.escape(hedef), normal_metin) is not None


def herhangi_gecer_mi(metin: str, ifadeler: tuple[str, ...] | list[str]) -> str | None:
    """İlk eşleşen ifadeyi döndürür (yoksa None). ``metin`` ham metin olabilir."""
    normal = normalize(metin)
    for ifade in ifadeler:
        if ifade_gecer_mi(normal, ifade):
            return ifade
    return None


# --------------------------------------------------------------------------
# Sayı ve saat okuma
# --------------------------------------------------------------------------

_BIRLER = ["sıfır", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_ONLAR = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]


def _yuzden_kucuk(n: int) -> str:
    on, bir = divmod(n, 10)
    parcalar = []
    if on:
        parcalar.append(_ONLAR[on])
    if bir:
        parcalar.append(_BIRLER[bir])
    return " ".join(parcalar)


def _binden_kucuk(n: int) -> str:
    yuz, kalan = divmod(n, 100)
    parcalar = []
    if yuz:
        parcalar.append("yüz" if yuz == 1 else f"{_BIRLER[yuz]} yüz")
    if kalan:
        parcalar.append(_yuzden_kucuk(kalan))
    return " ".join(parcalar)


def sayi_oku(n: int) -> str:
    """0–999 999 arası tam sayıyı Türkçe okur. Aralık dışıysa rakamla döndürür."""
    if n < 0 or n > 999_999:
        return str(n)
    if n == 0:
        return "sıfır"
    bin_, kalan = divmod(n, 1000)
    parcalar = []
    if bin_:
        parcalar.append("bin" if bin_ == 1 else f"{_binden_kucuk(bin_)} bin")
    if kalan:
        parcalar.append(_binden_kucuk(kalan))
    return " ".join(parcalar)


def rakamlari_tek_tek_oku(dizi: str) -> str:
    """"246802" → "iki dört altı sekiz sıfır iki" (erişim kodu gibi kodlar için)."""
    return " ".join(_BIRLER[int(r)] for r in dizi if r.isdigit())


def saat_oku(saat: int, dakika: int, ekli: bool = False) -> str:
    """14:30 → "on dört buçuk", 09:00 → "dokuz", 09:15 → "dokuz on beş".

    ``ekli`` True ise (ardından "'de" gibi ek gelecekse) buçuk yerine "otuz" denir;
    yazarın ekini rakamın okunuşuna göre seçtiği varsayılır ("14:30'da" → "on dört otuzda").
    """
    saat_yazi = sayi_oku(saat)
    if dakika == 0:
        return saat_yazi
    if dakika == 30 and not ekli:
        return f"{saat_yazi} buçuk"
    return f"{saat_yazi} {sayi_oku(dakika)}"


# İki nokta üst üste ile yazılan saat: önünde/arkasında rakam ya da ayırıcı+rakam olmamalı
# (12:30:45, 1:2:3 gibi diziler ve 3.14:30 gibi garipler dokunulmadan kalır).
_SAAT_IKI_NOKTA = re.compile(
    r"(?<![\d.,:])(?P<s>[01]?\d|2[0-3]):(?P<d>[0-5]\d)(?![\d]|[.,:]\d)(?P<ek>['’]\w+)?"
)
# Noktalı yazım ("20.00") ancak hemen önünde "saat" kelimesi varsa saat sayılır;
# yoksa "3.50 TL", "15.09" (tarih) ve "45.00" (fiyat) gibi sayılar bozulurdu.
_SAAT_NOKTALI = re.compile(
    r"(?P<on>\b[Ss]aat\s+)(?P<s>[01]?\d|2[0-3])\.(?P<d>[0-5]\d)(?![\d]|[.,:]\d)(?P<ek>['’]\w+)?"
)


def _saat_degistir(eslesme: re.Match[str]) -> str:
    ek = eslesme.group("ek")
    okunus = saat_oku(int(eslesme.group("s")), int(eslesme.group("d")), ekli=bool(ek))
    on = eslesme.groupdict().get("on") or ""
    return f"{on}{okunus}{ek[1:] if ek else ''}"


def saatleri_oku(metin: str) -> str:
    """Metindeki saatleri doğal Türkçe okunuşa çevirir; saat olmayan sayılara dokunmaz."""
    metin = _SAAT_NOKTALI.sub(_saat_degistir, metin)
    return _SAAT_IKI_NOKTA.sub(_saat_degistir, metin)


# --------------------------------------------------------------------------
# Kimlik numarası maskeleme
# --------------------------------------------------------------------------

# 11 rakam; aralarında boşluk, tire ya da nokta olabilir (STT "1 0 0 0 ..." diye yazabilir).
_TC_BENZERI = re.compile(r"(?<!\d)\d(?:[ \-.]?\d){10}(?!\d)")
TC_MASKESI = "[gizli numara]"


def tc_maskele(metin: str, maske: str = TC_MASKESI) -> tuple[str, bool]:
    """11 haneli kimlik numarası benzeri dizileri ``maske`` ile değiştirir.

    Dönüş: (maskelenmiş metin, maskeleme yapıldı mı). Numara doğrulanmaz;
    11 haneli her dizi gizlenir (yanlış pozitif, sızıntıdan iyidir).

    Yalnız harf içermeyen ayırıcılarla (boşluk, virgül, nokta, tire, eğik çizgi…)
    bölünmüş sayı dizileri ve yazıyla söylenen rakamlar da birlikte sayılır:
    "100,000,001,46", "100000001466" (11'den uzun), "bir sıfır sıfır … altı".
    """
    yeni, adet = _TC_BENZERI.subn(maske, metin)
    yeni, ek = _sayi_dizilerini_maskele(yeni, maske)
    return yeni, (adet + ek) > 0


# Yazıyla söylenen sayılar (ASCII'ye katlanmış). Tek basamaklılar 1 hane sayılır.
_RAKAM_KELIMELERI = frozenset({"sifir", "bir", "iki", "uc", "dort", "bes", "alti", "yedi", "sekiz", "dokuz"})
_ONLUK_KELIMELER = frozenset({"on", "yirmi", "otuz", "kirk", "elli", "altmis", "yetmis", "seksen", "doksan",
                              "yuz", "bin"})
_BUYUK_SAYI_KELIMELERI = frozenset({"milyon", "milyar"})
_SAYI_PARCASI = re.compile(r"\d+|[^\W\d_]+")
_AYIRICI = re.compile(r"[\s,.;:/\\\-_'’()]*")
_AZAMI_HANE = 11          # rakam + rakam kelimesi toplamı bu sayıya ulaşırsa gizlenir
_AZAMI_SAYI_KELIMESI = 6  # art arda bu kadar sayı kelimesi (saat/tarih böyle uzun olmaz)


def _sayi_dizilerini_maskele(metin: str, maske: str) -> tuple[str, int]:
    parcalar = list(_SAYI_PARCASI.finditer(metin))
    diziler: list[tuple[int, int]] = []
    i = 0
    while i < len(parcalar):
        bas = i
        hane = kelime = 0
        buyuk = False
        j = i
        while j < len(parcalar):
            p = parcalar[j]
            if j > bas and not _AYIRICI.fullmatch(metin, parcalar[j - 1].end(), p.start()):
                break
            parca = p.group(0)
            if parca.isdigit():
                hane += len(parca)
            else:
                k = ascii_katla(parca)
                if k in _RAKAM_KELIMELERI:
                    hane += 1
                    kelime += 1
                elif k in _ONLUK_KELIMELER:
                    hane += 1
                    kelime += 1
                elif k in _BUYUK_SAYI_KELIMELERI:
                    buyuk = True
                    kelime += 1
                else:
                    break
            j += 1
        if j > bas and (hane >= _AZAMI_HANE or kelime >= _AZAMI_SAYI_KELIMESI or (buyuk and kelime >= 2)):
            diziler.append((parcalar[bas].start(), parcalar[j - 1].end()))
        i = max(j, i + 1)
    for bas_k, bit_k in reversed(diziler):
        metin = metin[:bas_k] + maske + metin[bit_k:]
    return metin, len(diziler)


# --------------------------------------------------------------------------
# Takvim adları
# --------------------------------------------------------------------------

GUN_ADLARI = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
GUN_KISA = ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")
AY_ADLARI = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)
