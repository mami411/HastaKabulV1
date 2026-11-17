"""Serbest metni anlama: ÖNCE kural tabanlı çözümleyici, LLM yalnız kural çözemediğinde.

LLM'in tek işi anlamaktır: (maskelenmiş) kullanıcı cümlesinden niyet + yapılandırılmış alanlar
(poliklinik, gün, saat, sıra, göreli istek, sorulan ilaç adı). Kullanıcıya söylenen hiçbir cümle
LLM'den gelmez; şemada serbest metin alanı yoktur ve şema dışı alanlar yok sayılır.

KVKK: LLM'e giden yük yalnız ``llm_siniri.llm_yuku_kur`` ile kurulur (tek nokta); kullanıcı
metni loglanmaz.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, replace
from datetime import date, time, timedelta
from types import ModuleType

from ortak.llm import LLMHatasi, LLMSaglayici
from ortak.turkce import ifade_gecer_mi, normalize

from .ilac_bilgisi import OZET_KONULARI, konu_bul, metinde_dagarcik_ilaci_var, soru_mu
from .ilac_tarifi import ilac_kullanim_baglami
from .llm_siniri import _bilinen_kelime_mi, _kelime_kok, kisisel_maskele, llm_yuku_kur, sistem_istemi
from .zaman import hhmm_coz

log = logging.getLogger("ses_servisi.anlama")


@dataclass(frozen=True)
class Anlam:
    niyet: str
    poliklinik: str | None = None
    gun: date | None = None
    saat_baslangic: time | None = None
    saat_bitis: time | None = None
    kaynak: str = "kural"          # "kural" | "llm" | "kural_yedek"
    llm_hatasi: str | None = None  # LLM hata kodu (yedeğe düşüldüyse)
    # Yalnız kapalı listedeki açık onay cümlesi birebir söylendiyse True. LLM bunu asla
    # True yapamaz; randevu yalnız bu bayrakla (ya da düğme olayıyla) oluşturulur.
    acik_onay: bool = False
    sira: int | None = None        # sunulan seçeneklerden kaçıncısı (1…); -1 = sonuncusu
    goreli: str | None = None      # "daha_gec" | "daha_erken"
    fark_etmez: bool = False       # eksik gün/saat tercihi önemsiz ("fark etmez", "en erken")
    ilac_adi: str | None = None    # yalnız LLM doldurur; yalnız kodda eşleştirmeye girer, gösterilmez
    gecmis_konu: str | None = None  # "liste" | "uyari" | "tani"
    # Şikâyetten çıkarılan poliklinik ÖNERİSİ (yalnız kural doldurur; seçim değildir, kullanıcı teyit eder).
    oneri: str | None = None
    alt: str | None = None         # niyetin alt türü: "yonlendirme" (sikayet), "degisiklik" | "liste" (mevcut_randevu)
    # İlaç sorusunun konusu (ILAC_KONULARI: yan_etki, alerji, yemek, alkol…). Kural ya da yalnız maskeli
    # cümleden LLM doldurur; yanıt dağarcık + hekim onaylı özetten şablonla kurulur.
    ilac_konu: str | None = None

    @property
    def zaman_var(self) -> bool:
        return self.gun is not None or self.saat_baslangic is not None


# Seçenek sunulan adımlar: sıra sayıları ve çıplak saatler ("dokuz buçuk") burada çözülür.
SECENEKLI_ADIMLAR = frozenset({"SECIM_BEKLENIYOR", "ONAY_BEKLENIYOR", "SAAT_BEKLENIYOR", "GUN_BEKLENIYOR",
                               "SLOT_SECIMI"})
CIPLAK_SAAT_ADIMLARI = frozenset({"SECIM_BEKLENIYOR", "ONAY_BEKLENIYOR", "SAAT_BEKLENIYOR"})

# --------------------------------------------------------------------------
# Kural tabanlı çözümleyici
# --------------------------------------------------------------------------

_HAFTA_GUNLERI = (  # uzun olan önce: "cumartesi" "cuma"dan, "pazartesi" "pazar"dan önce
    ("pazartesi", 0), ("cumartesi", 5), ("sali", 1), ("carsamba", 2), ("persembe", 3),
    ("cuma", 4), ("pazar", 6),
)
_AYLAR = ("ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz", "agustos", "eylul",
          "ekim", "kasim", "aralik")
_SAYI_KELIMELERI = {
    "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5, "alti": 6, "yedi": 7, "sekiz": 8,
    "dokuz": 9, "on": 10, "yirmi": 20,
}
_ONLUK_DAKIKA = {"on": 10, "yirmi": 20, "otuz": 30, "kirk": 40, "elli": 50}
_BIRLIK_DAKIKA = {k: v for k, v in _SAYI_KELIMELERI.items() if v < 10}


def _kelime_sayisi(kelimeler: list[str], i: int) -> tuple[int | None, int]:
    """kelimeler[i:] başındaki Türkçe sayıyı (1–23) okur: ("on","bir") → 11. Dönüş: (sayı, tüketilen)."""
    if i >= len(kelimeler):
        return None, 0
    ilk = _kok_sayi(kelimeler[i])
    if ilk is None:
        return None, 0
    if ilk in (10, 20) and i + 1 < len(kelimeler):
        ikinci = _kok_sayi(kelimeler[i + 1])
        if ikinci is not None and ikinci < 10:
            return ilk + ikinci, 2
    return ilk, 1


_EKLER = ("", "da", "de", "ta", "te", "a", "e", "ya", "ye", "i", "u", "dan", "den", "tan", "ten", "yi", "yu")
_ZAMAN_EKLERI = ("da", "de", "ta", "te", "a", "e", "ya", "ye", "i", "u", "yi", "yu")


def _kok_sayi(kelime: str) -> int | None:
    """"dokuz", "dokuzda", "ikide", "onda" → sayı; "once" gibi rastlantılar → None."""
    for kok, deger in _SAYI_KELIMELERI.items():
        if kelime.startswith(kok) and kelime[len(kok):] in _EKLER:
            return deger
    return None


def _rakam_sayi(kelime: str) -> tuple[int | None, str]:
    """"9", "9da", "10dan" → (sayı, ek)."""
    m = re.fullmatch(r"(\d{1,2})([a-z]*)", kelime)
    if not m:
        return None, ""
    return int(m.group(1)), m.group(2)


def _ogleden_sonraya(saat: int) -> int:
    """Poliklinik saatlerinde "saat iki" 14:00 demektir."""
    return saat + 12 if 1 <= saat <= 7 else saat


def _dakika_oku(kelimeler: list[str], j: int) -> tuple[int | None, int]:
    """Saatten sonraki dakika: "buçuk" 30, "çeyrek" 15, "on beş", "kırk beş", "30"."""
    if j >= len(kelimeler):
        return None, 0
    k = kelimeler[j]
    if k.startswith("bucuk"):
        return 30, 1
    if k.startswith("ceyrek"):
        return 15, 1
    m = re.fullmatch(r"(\d{2})[a-z]*", k)
    if m and int(m.group(1)) < 60:
        return int(m.group(1)), 1
    for kok, deger in _ONLUK_DAKIKA.items():
        if k.startswith(kok) and k[len(kok):] in _EKLER:
            if j + 1 < len(kelimeler):
                for bkok, bdeger in _BIRLIK_DAKIKA.items():
                    if kelimeler[j + 1].startswith(bkok) and kelimeler[j + 1][len(bkok):] in _EKLER:
                        return deger + bdeger, 2
            return deger, 1
    return None, 0


def _saat_oku(kelimeler: list[str], i: int) -> tuple[time | None, int, str]:
    """kelimeler[i]'den başlayan saat ifadesi. Dönüş: (saat, tüketilen, ilk kelimenin eki)."""
    sayi, ek = _rakam_sayi(kelimeler[i]) if i < len(kelimeler) else (None, "")
    tuketilen = 1
    if sayi is None:
        sayi, tuketilen = _kelime_sayisi(kelimeler, i)
        if sayi is None:
            return None, 0, ""
        son = kelimeler[i + tuketilen - 1]
        ek = next((son[len(k):] for k in _SAYI_KELIMELERI if son.startswith(k) and son[len(k):] in _EKLER), "")
    if not 0 < sayi <= 23:
        return None, 0, ""
    dakika, dt = _dakika_oku(kelimeler, i + tuketilen)
    if dakika == 15 and i + tuketilen + 1 < len(kelimeler) and kelimeler[i + tuketilen + 1] == "var":
        # "ona çeyrek var" → 09:45
        saat = _ogleden_sonraya(sayi)
        return time((saat - 1) % 24, 45), tuketilen + dt + 1, ek
    return time(_ogleden_sonraya(sayi), dakika or 0), tuketilen + dt, ek


_ZAMAN_DISI_SONRAKI = ("saat", "gun", "hafta", "tane", "kez", "kere", "defa", "ay", "yil", "dakika", "kisi",
                       "ilac", "hap", "tablet", "poliklinik", "bolum", "doktor", "hekim", "tarih", "sey",
                       "randevu", "secenek", "sira")


def _saat_bul(n: str, ciplak: bool = False) -> time | None:
    m = re.search(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)", n)
    if m:
        return time(int(m.group(1)), int(m.group(2)))
    m = re.search(r"(?<!\w)saat ([01]?\d|2[0-3]) ([0-5]\d)(?!\d)", n)  # "10.30'da" -> "10 30da"
    if m:
        return time(int(m.group(1)), int(m.group(2)))
    kelimeler = n.split()
    for i, k in enumerate(kelimeler):
        if k.startswith("saat"):
            t, _, _ = _saat_oku(kelimeler, i + 1)
            if t is not None:
                return t
    for i, _k in enumerate(kelimeler):
        t, tuketilen, ek = _saat_oku(kelimeler, i)
        if t is None:
            continue
        sonraki = kelimeler[i + tuketilen] if i + tuketilen < len(kelimeler) else ""
        if sonraki.startswith(_ZAMAN_DISI_SONRAKI) or sonraki.startswith(_AYLAR):
            continue
        dakikali = tuketilen > 1 and (t.minute != 0 or kelimeler[i + tuketilen - 1].startswith("bucuk"))
        if dakikali:
            return t  # "dokuz buçukta", "on çeyrek" — "saat" olmadan da saattir
        if ciplak and (ek in _ZAMAN_EKLERI or len(kelimeler) <= 4):
            return t  # seçim adımında "dokuz", "onu", "9 olsun"
    return None


def _aralik_bul(n: str) -> tuple[time, time] | None:
    """"saat on ile on bir arası", "10 ila 12", "ondan sonra", "11den önce"."""
    kelimeler = n.split()
    for i, k in enumerate(kelimeler):
        if k in ("ile", "ila") and 0 < i < len(kelimeler) - 1:
            sol = None
            for geri in (1, 2, 3):
                if i - geri >= 0:
                    t, tuketilen, _ = _saat_oku(kelimeler, i - geri)
                    if t is not None and i - geri + tuketilen == i:
                        sol = t
                        break
            sag, tuketilen, _ = _saat_oku(kelimeler, i + 1)
            if sol and sag:
                sonra = kelimeler[i + 1 + tuketilen: i + 4 + tuketilen]
                if k == "ila" or any(s.startswith("arasi") for s in sonra):
                    return (sol, sag) if sol <= sag else (sag, sol)
    for i, k in enumerate(kelimeler[:-1]):
        sayi, ek = _rakam_sayi(k)
        if sayi is None:
            for kok, deger in _SAYI_KELIMELERI.items():
                if k.startswith(kok) and k[len(kok):] in ("dan", "den", "tan", "ten"):
                    sayi, ek = deger, k[len(kok):]
                    break
        if sayi is None or ek not in ("dan", "den", "tan", "ten") or not 0 < sayi <= 23:
            continue
        t = time(_ogleden_sonraya(sayi), 0)
        if kelimeler[i + 1].startswith("sonra"):
            return t, time(18, 0)
        if kelimeler[i + 1].startswith("once"):
            return time(8, 0), t
    return None


# Ayın günü sözle: "ayın yirmisi", "ayın on beşinde" (iyelik/hal ekli son kelime).
_AY_GUNU_SON = {"biri": 1, "ikisi": 2, "ucu": 3, "dordu": 4, "besi": 5, "altisi": 6, "yedisi": 7, "sekizi": 8,
                "dokuzu": 9, "onu": 10, "yirmisi": 20, "otuzu": 30}
_AY_GUNU_ONLUK = {"on": 10, "yirmi": 20, "otuz": 30}
_AY_GUNU = re.compile(r"(?<!\w)ayin (?:(?P<rakam>\d{1,2})(?!\d)\w*|(?P<s1>\w+)(?: (?P<s2>\w+))?)")
# Bugünden önceki tarih bu kadar gün içindeyse "geçmişte kaldı" sayılır (gelecek yıla atanmaz).
GECMIS_TARIH_PENCERESI_GUN = 180
GECMIS = "gecmis"  # _gun_bul: söylenen gün geçmişte


def _ay_gunu_sozle(s1: str, s2: str | None) -> tuple[int | None, int]:
    """("yirmi", "besi") → (25, 2); ("onu", None) → (10, 1). Dönüş: (gün, tüketilen kelime)."""
    if s2 and s1 in _AY_GUNU_ONLUK:
        birlik = next((v for k, v in _AY_GUNU_SON.items() if v < 10 and s2.startswith(k)), None)
        if birlik is not None:
            return _AY_GUNU_ONLUK[s1] + birlik, 2
    for k, v in sorted(_AY_GUNU_SON.items(), key=lambda kv: -len(kv[0])):
        if s1.startswith(k):
            return v, 1
    return None, 0


def _ay_gunu_bul(n: str) -> list[tuple[int, int, int]]:
    """"ayın 20'si", "ayın yirmisinde" → [(baş, son, gün)] (yalnız rakamsal gün; ay bu ay)."""
    sonuc: list[tuple[int, int, int]] = []
    for m in _AY_GUNU.finditer(n):
        if m.group("rakam"):
            sonuc.append((m.start(), m.end(), int(m.group("rakam"))))
            continue
        gun, tuketilen = _ay_gunu_sozle(m.group("s1"), m.group("s2"))
        if gun is None:
            continue
        son = m.end("s2") if tuketilen == 2 else m.end("s1")
        sonuc.append((m.start(), son, gun))
    return sonuc


def _gun_bul(n: str, bugun: date) -> date | str | None:
    """Metindeki gün ifadeleri (göreli, "20 kasım", "ayın 20'si", hafta günü) arasından olumsuzlanmayan
    ("... değil") SON söyleneni seçer: "yarın değil cuma olsun" → cuma. Söylenen gün bugünden önceyse
    (``GECMIS_TARIH_PENCERESI_GUN`` içinde) ``GECMIS`` döner: gelecek yıla atanmaz, kullanıcıya sorulur."""
    adaylar: list[tuple[int, date | str]] = []

    def ekle(bas: int, son: int, g: date | str) -> None:
        if not re.match(r"\s+degil\b", n[son:]):
            adaylar.append((bas, g))

    # "yer'in sabah": Whisper bir Türkçe seste (Piper dfki) "yarın sabah"ı 5/5 "Yerin sabah" yazdı (demo
    # provası 2026-11-16). Yalnız arkasından gün parçası/saat geldiğinde ya da cümle tek başına buysa
    # "yarın" sayılır ("yerinde", "yerine" etkilenmez). Aynı provada bir kez "Yırın sabah" da geldi ve gün
    # bulunamadığı için "bugün"e düştü; "yırın" (normalize: "yirin") da aynı koşulla kabul edilir.
    yerin = r"y[ei]r ?in(?= (?:sabah|ogle\w*|aksam\w*|saat|icin|olsun|erken)\b|$)"
    for m in re.finditer(r"(?<!\w)(bugun\w*|obur gun\w*|ertesi gun\w*|yarin\w*|" + yerin + ")", n):
        k = m.group(1)
        fark = 0 if k.startswith("bugun") else 1 if k.startswith(("yarin", "yer", "yir")) else 2
        ekle(m.start(), m.end(), bugun + timedelta(days=fark))
    for m in re.finditer(r"\b([0-3]?\d) (" + "|".join(_AYLAR) + r")\w*", n):
        ay = _AYLAR.index(m.group(2)) + 1
        try:
            hedef = date(bugun.year, ay, int(m.group(1)))
        except ValueError:
            continue
        if hedef < bugun:
            if (bugun - hedef).days <= GECMIS_TARIH_PENCERESI_GUN:
                ekle(m.start(), m.end(), GECMIS)
                continue
            try:
                hedef = date(bugun.year + 1, ay, int(m.group(1)))
            except ValueError:
                continue
        ekle(m.start(), m.end(), hedef)
    for bas, son, gun in _ay_gunu_bul(n):
        try:
            hedef = date(bugun.year, bugun.month, gun)
        except ValueError:
            continue
        ekle(bas, son, GECMIS if hedef < bugun else hedef)
    gun_nolari = dict(_HAFTA_GUNLERI)
    for m in re.finditer(r"(?<!\w)(pazartesi|cumartesi|sali|carsamba|persembe|cuma|pazar)\w*", n):
        fark = (gun_nolari[m.group(1)] - bugun.weekday()) % 7
        if fark == 0 and not n[:m.start()].endswith("bu "):
            fark = 7  # cuma günü söylenen "cuma" gelecek cumadır; bugün için "bugün"/"bu cuma" denir
        elif re.search(r"(?:haftaya|gelecek hafta|onumuzdeki hafta|sonraki hafta|ertesi hafta)\w* $",
                       n[:m.start()]):
            fark += 7  # "haftaya salı": bu haftanın değil gelecek haftanın salısı
        ekle(m.start(), m.end(), bugun + timedelta(days=fark))
    if not adaylar:
        return None
    return max(adaylar, key=lambda a: a[0])[1]


_AD_SONRASI = ("icin", "adina", "namina", "isimli", "adli", "hanim", "bey")


def _ad_benzerlerini_cikar(metin: str, alan: ModuleType) -> str:
    """Kural ayrıştırması için ad benzeri kelimeleri atar ("Ayşe Yılmaz için randevu istiyorum").

    Atılan: büyük harfle başlayan ve bilinen alan kelimesi olmayan kelime — cümle ortasındaysa,
    iki ya da daha uzun büyük harfli dizinin parçasıysa ya da ardından "için/adına/hanım…"
    geliyorsa. Ardından "için/adına" gelen ve bilinmeyen kelime içeren büyük harfli dizi bütünüyle
    atılır ("Ayşe Pazar için" bir pazar günü değildir). Poliklinik adı içeren dizi korunur.
    Cümle başındaki tek kelime ("Sonuncusu olsun") olduğu gibi kalır.
    """
    kelimeler = list(re.finditer(r"\S+", metin))
    if not kelimeler:
        return metin
    govdeler = [k.group(0).strip(".,;:!?\"()[]") for k in kelimeler]
    buyuk = [g[:1].isalpha() and g[:1].isupper() for g in govdeler]
    kokler = [_kelime_kok(g) for g in govdeler]
    poliklinik_ifadeleri = [s for p in alan.POLIKLINIKLER for s in (p.ad, *p.es_anlamlilar)]
    # Poliklinik adının kelimeleri ad sayılmaz: "İÇ HASTALIKLARI", "KULAK BURUN BOĞAZ" büyük harfle yazılsa da.
    poliklinik_kelimeleri = {w for s in poliklinik_ifadeleri for w in normalize(s).split()}
    at: set[int] = set()
    i = 0
    while i < len(kelimeler):
        if not buyuk[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(kelimeler) and buyuk[j + 1]:
            j += 1
        dizi = range(i, j + 1)
        bilinmeyen = [k for k in dizi if not _bilinen_kelime_mi(kokler[k], alan)
                      and normalize(govdeler[k]) not in poliklinik_kelimeleri]
        dizi_n = normalize(" ".join(govdeler[k] for k in dizi))
        poliklinikli = any(ifade_gecer_mi(dizi_n, s) for s in poliklinik_ifadeleri)
        sonraki = kokler[j + 1] if j + 1 < len(kelimeler) else ""
        if bilinmeyen and not poliklinikli and sonraki in _AD_SONRASI:
            at.update(dizi)
        elif bilinmeyen and (i > 0 or j > i or sonraki in _AD_SONRASI):
            at.update(bilinmeyen)
        i = j + 1
    if not at:
        return metin
    return " ".join(k.group(0) for idx, k in enumerate(kelimeler) if idx not in at)


def acik_onay_mi(metin: str, alan: ModuleType) -> bool:
    """Metin, kapalı açık-onay listesindeki bir cümleyle normalize edilmiş haliyle BİREBİR
    aynı mı? Soru işareti içeren metin hiçbir zaman onay değildir."""
    if "?" in metin or "¿" in metin:
        return False
    n = normalize(metin)
    return bool(n) and n in {normalize(i) for i in alan.ACIK_ONAY_IFADELERI}


def _en_erken_mi(n: str) -> bool:
    """"en erken (saat)" = sıralı listenin başı; sabah aralığı ya da tam saat sanılmamalı."""
    return ifade_gecer_mi(n, "en erken") or ifade_gecer_mi(n, "ilk bos")


def _sira_bul(n: str, alan: ModuleType) -> int | None:
    for ifadeler, sira in alan.SIRA_IFADELERI:
        if any(ifade_gecer_mi(n, i) for i in ifadeler):
            return sira
    return None


def ilac_baglaminda_mi(n: str, alan: ModuleType) -> bool:
    """Cümle bir ilaçtan söz ediyor mu: "ilaç/hap…", "içtikten sonra…" ya da dağarcıktaki bir ilacın adı."""
    return (any(ifade_gecer_mi(n, i) for i in (*getattr(alan, "ILAC_BAGLAM_IFADELERI", ()),
                                                *getattr(alan, "ILAC_ALIM_IFADELERI", ())))
            or metinde_dagarcik_ilaci_var(n))


def acil_mi(n: str, alan: ModuleType) -> bool:
    """Normalize metinde acil durum ifadesi var mı (bağlam istisnaları düşülerek). İlaç bağlamında anlatılan
    ciddi belirti ("ilacı içtim nefes darlığı oldu") da acildir; soru biçimi ("nefes darlığı yapar mı") değildir."""
    istisnalar = getattr(alan, "ACIL_ISTISNALARI", {})
    for ifade in getattr(alan, "ACIL_IFADELERI", ()):
        if ifade_gecer_mi(n, ifade) and not any(ifade_gecer_mi(n, i) for i in istisnalar.get(ifade, ())):
            return True
    for birinci, ikinci in getattr(alan, "ACIL_BIRLIKTE", ()):
        if any(ifade_gecer_mi(n, i) for i in birinci) and any(ifade_gecer_mi(n, i) for i in ikinci):
            return True
    ilac_acil = getattr(alan, "ILAC_ACIL_IFADELERI", ())
    return (any(ifade_gecer_mi(n, i) for i in ilac_acil) and not soru_mu(n)
            and ilac_baglaminda_mi(n, alan))


def sikayet_poliklinikleri(n: str, alan: ModuleType) -> list[str]:
    """Şikâyet kalıplarının işaret ettiği poliklinik kodları (metindeki ilk geçiş sırasıyla, tekrarsız).

    Bir eşleşme başka bir polikliniğin daha uzun eşleşmesiyle örtüşüyorsa atılır: "göz tansiyonu" yalnız Göz'e
    işaret eder ("tansiyon" Kardiyoloji'ye ayrıca sayılmaz), "gözüm kaşınıyor" Göz'e ("kaşın" Dermatoloji'ye
    sayılmaz). Kalan birden çok poliklinikten biri ``SIKAYET_ONCELIKLI``'daysa yalnız o döner ("çocuğumun
    ateşi var" → Çocuk Sağlığı)."""
    eslesmeler: list[tuple[int, int, str]] = []
    for kod, kaliplar in getattr(alan, "SIKAYET_KALIPLARI", {}).items():
        for kalip in kaliplar:
            hedef = normalize(kalip)
            if not hedef:
                continue
            for m in re.finditer(r"(?<!\w)" + re.escape(hedef), n):
                eslesmeler.append((m.start(), m.end(), kod))
    kalan = [e for e in eslesmeler
             if not any(d[2] != e[2] and d[0] < e[1] and e[0] < d[1] and (d[1] - d[0]) > (e[1] - e[0])
                        for d in eslesmeler)]
    kodlar: list[str] = []
    for _, _, kod in sorted(kalan):
        if kod not in kodlar:
            kodlar.append(kod)
    if len(kodlar) > 1:
        oncelikli = next((k for k in getattr(alan, "SIKAYET_ONCELIKLI", ()) if k in kodlar), None)
        if oncelikli is not None:
            return [oncelikli]
    return kodlar


def kurum_sorusu(n: str, alan: ModuleType) -> str | None:
    """Kurum sorusu konusu ("cagri_merkezi", "adres", "calisma_saatleri", "ucret", "sigorta") ya da None.
    Normalize metinde önek eşleşmesi; yanıt sabit şablondur (LLM'e gitmez). Uzun cümlede ("kalbim ağrıyor,
    yarın sabah kardiyoloji") aranmaz: kurum sorusu kısa ve doğrudandır."""
    if not n or len(n.split()) > 14:
        return None
    for konu, ifadeler in getattr(alan, "KURUM_SORU_KALIPLARI", ()):
        if any(ifade_gecer_mi(n, i) for i in ifadeler):
            return konu
    return None


def _sohbet_niyeti(n: str, alan: ModuleType) -> str | None:
    """Kısa sohbet cümleleri: "merhaba", "ne yapabilirsin", "teşekkürler", "görüşürüz"."""
    dolgu = {normalize(d) for d in getattr(alan, "SOHBET_DOLGU", ())}
    kelimeler = [k for k in n.split() if k not in dolgu]
    if not kelimeler or len(kelimeler) > getattr(alan, "SOHBET_AZAMI_KELIME", 6):
        return None
    cumle = " ".join(kelimeler)

    def gecer(ifadeler: tuple[str, ...]) -> bool:
        return any(ifade_gecer_mi(cumle, i) for i in ifadeler)

    if cumle in {normalize(s) for s in getattr(alan, "SELAM_IFADELERI", ())}:
        return "selam"
    if gecer(getattr(alan, "YARDIM_IFADELERI", ())):
        return "yardim"
    if gecer(getattr(alan, "VEDA_IFADELERI", ())):
        return "veda"
    if gecer(getattr(alan, "TESEKKUR_IFADELERI", ())):
        return "tesekkur"
    return None


def kural_tabanli_anla(metin: str, bugun: date, alan: ModuleType, adim: str | None = None) -> Anlam:
    maske = kisisel_maskele(metin)
    n = normalize(maske.metin)
    # Acil durum her şeyden önce (TC içeren cümlede bile): sabit 112 şablonu, randevu akışı yok.
    if n and acil_mi(n, alan):
        return Anlam("acil")
    if maske.tc_var:
        return Anlam("kimlik")
    if not n:
        return Anlam("belirsiz")

    def gecer(ifadeler: tuple[str, ...]) -> bool:
        return any(ifade_gecer_mi(n, i) for i in ifadeler)

    if gecer(alan.KIMLIK_IFADELERI):
        return Anlam("kimlik")
    # Kurum soruları (çağrı merkezi, adres, çalışma saatleri, ücret, sigorta): sabit şablon, LLM'siz.
    kurum = kurum_sorusu(n, alan)
    if kurum is not None:
        return Anlam("kurum_bilgisi", alt=kurum)
    # Ad benzeri kelimeler niyet çıkarımına karışmaz (gün/poliklinik sanılmaz, cümleyi LLM'e itmez).
    n_tam = n
    n = normalize(_ad_benzerlerini_cikar(maske.metin, alan))
    if n != n_tam and not _gecmis_mi(n, alan) and _gecmis_mi(n_tam, alan):
        # Ad ayıklaması geçmiş sorusunu bozdu: tamamı büyük harfli STT/yazı ("BU DOKTOR BANA NE YAZMIŞTI") ya da
        # hekim adıyla soru ("Mehmet Bey ne demişti"). Bu cümle geçmiş sorusu olarak tam metinle çözülür; ad
        # eşlemesi orkestratörde yalnız kodda yapılır.
        n = n_tam
    if not n:
        return Anlam("belirsiz")

    # Geçmiş muayene / ilaç (gün/saat ayrıştırmasından önce: "ilacımı sabah mı içeceğim" randevu değil)
    ilac_baglami = gecer(alan.ILAC_BAGLAM_IFADELERI)
    gecmis = _gecmis_mi(n, alan)
    if gecmis and gecer(alan.RANDEVU_ISTEGI_IFADELERI) and gecer(alan.RANDEVU_ISTEK_FIILLERI) \
            and not hekim_gecmis_sorusu_mu(n, alan):
        # "Şeker ilacım için randevu almak istiyorum": "ilacım" geçse de istek randevudur, geçmiş okunmaz.
        gecmis = False
    ilac_sorusu = gecer(alan.ILAC_SORU_IFADELERI)
    # İlaç soru konusu (yan etki, alerji, aç/tok, alkol, unutma, hamilelik, birlikte kullanım, süre, doz):
    # yanıt dağarcık + hekim onaylı özetten şablonla kurulur (tavsiye reddinden önce).
    konu, guclu = konu_bul(n, alan)
    # "X yerine başka ilaç" ilaç değiştirme tavsiyesidir: konu yanıtı değil, sabit ret.
    if konu and konu not in OZET_KONULARI and not ifade_gecer_mi(n, "yerine"):
        baglam = ilac_baglami or gecmis or metinde_dagarcik_ilaci_var(n)
        randevu_kokusu = gecer(_RANDEVU_KELIMELERI) or metindeki_poliklinik(n, alan) is not None
        soru_bypass = konu in _SORU_ILE_YETEN_KONULAR and soru_mu(n, maske.metin) and not randevu_kokusu
        # Şikâyet anlatımı + yaşam tarzı sorusu ("tansiyonum çıktı, tuz yemem zararlı mı", "şekerim var tatlı
        # yiyebilir miyim"): ilaç bağlamı yoksa konu kelimesi kişinin kendi yaptığı şeydir, ilaç sorusu değil.
        # Açık yan etki ifadesi ("başım ağrıyor yan etkisi mi") ilaç sorusu kalır.
        if not baglam and not ilac_kullanim_baglami(n, alan) and sikayet_poliklinikleri(n, alan) \
                and not gecer(_YAN_ETKI_ACIK):
            guclu = soru_bypass = False
        if guclu or baglam or soru_bypass:
            return Anlam("ilac_sorusu", poliklinik=metindeki_poliklinik(n, alan), gecmis_konu="liste",
                         ilac_konu=konu)
    if konu in OZET_KONULARI and metinde_dagarcik_ilaci_var(n, yalniz_tam_ad=True) and not gecer(_RANDEVU_KELIMELERI):
        # "Metformini sabah mı akşam mı içmeliyim": ilaç adıyla kullanım sorusu; "sabah/akşam" randevu tercihi
        # sanılmaz (gün/saat ayrıştırmasından önce). Ad kayıtla kodda eşleşir (orkestratör).
        return Anlam("ilac_sorusu", gecmis_konu="liste")
    if gecer(alan.ILAC_TAVSIYE_KESIN) or (gecer(alan.ILAC_TAVSIYE_IFADELERI)
                                          and (ilac_baglami or gecmis or ilac_sorusu)):
        return Anlam("ilac_tavsiye")
    if gecmis or (ilac_sorusu and ilac_baglami):
        konu = ("uyari" if ifade_gecer_mi(n, "uyari") else
                "tani" if (ifade_gecer_mi(n, "tani") or ifade_gecer_mi(n, "teshis")) else
                gecmis_alt_konu(n, alan, ilac_baglami))
        # "ortopedi muayenemde ne yazıldı": sorulan poliklinik taşınır, yalnız o kayıt okunur.
        return Anlam("gecmis_muayene" if gecmis and not ilac_sorusu else "ilac_sorusu",
                     poliklinik=metindeki_poliklinik(n, alan), gecmis_konu=konu)

    # Poliklinik: önce açık ad/eş anlamlı ("göz doktoru", "kardiyoloji"), sonra tek kelime kök ("kalp").
    # Tek başına organ adı ("göz") yalnız cümlede şikâyet kalıbı YOKSA poliklinik seçimidir; şikâyet varsa
    # öneri yapılır ve teyit istenir. Kullanıcı hem şikâyet hem poliklinik söylediyse poliklinik esastır.
    poliklinik = metindeki_poliklinik(n, alan, organ_haric=True)
    if poliklinik is None:
        # Tek başına söylenen kök ("kalp", "kemik"): şikâyet cümlesi değil, bölüm adı kısaltması.
        kelimeler = re.findall(r"\w+", n)
        if kelimeler and len(kelimeler) <= 2 and kelimeler[0] in alan.TEK_KELIME_POLIKLINIK and (
                len(kelimeler) == 1 or kelimeler[1] in ("olsun", "lutfen", "lütfen", "polikliniği", "poliklinigi")):
            poliklinik = alan.TEK_KELIME_POLIKLINIK[kelimeler[0]]
    sikayet = [] if poliklinik else sikayet_poliklinikleri(n, alan)
    if poliklinik is None and not sikayet:
        poliklinik = metindeki_poliklinik(n, alan)
    # Mevcut randevu sorusu ("randevularım neler", "yarınki randevumu iptal et"): backend'de bu uç yok,
    # sabit şablon. Poliklinik adı geçiyorsa ("randevumu kardiyoloji yerine ortopedi yap") akış sürer.
    if poliklinik is None and gecer(getattr(alan, "MEVCUT_RANDEVU_IFADELERI", ())):
        return Anlam("mevcut_randevu",
                     alt="degisiklik" if gecer(getattr(alan, "MEVCUT_RANDEVU_DEGISIKLIK", ())) else "liste")
    tavsiye = gecer(getattr(alan, "TIBBI_TAVSIYE_SORULARI", ()))
    gun = _gun_bul(n, bugun)
    if gun == GECMIS:
        return Anlam("gecmis_tarih", poliklinik)
    fark = gecer(alan.FARK_ETMEZ_IFADELERI)
    # "ayın 20'si" bir gündür, saat 20:00 değil: saat aranmadan önce çıkarılır.
    n_saat = n
    for bas, son, _ in reversed(_ay_gunu_bul(n)):
        n_saat = n_saat[:bas] + " " + n_saat[son:]
    aralik = _aralik_bul(n_saat)
    saat = None if aralik else _saat_bul(n_saat, ciplak=adim in CIPLAK_SAAT_ADIMLARI)
    bas, bit = aralik if aralik else (saat, saat)
    if bas is None and not _en_erken_mi(n):
        for ifadeler, b, s in alan.GUN_PARCALARI:
            if gecer(ifadeler):
                bas, bit = hhmm_coz(b), hhmm_coz(s)
                break

    if sikayet and ilac_kullanim_baglami(n, alan) and not gecer(getattr(alan, "SAGLIK_SPESIFIK_ONERI", ())):
        # Hastanın kendi ilacından söz ediliyor ("tansiyon ilacım", "şeker için kullandığım"): hastalık/organ kelimesi
        # şikâyet değildir, poliklinik önerilmez. İlaç sorusudur (kimlik öncesi "önce kimlik"; sonrası kayıtla).
        return Anlam("ilac_sorusu", gecmis_konu="liste", ilac_konu=konu)
    if sikayet:
        # Şikâyet → poliklinik ÖNERİSİ (teşhis değil). Tek polikliniğe işaret ediyorsa öneri, birden
        # çoğuna işaret ediyorsa öneri yok (liste sorulur). Gün/saat söylendiyse saklanır.
        oneri = sikayet[0] if len(sikayet) == 1 else None
        return Anlam("tibbi_soru" if tavsiye else "sikayet", None, gun, bas, bit, oneri=oneri, fark_etmez=fark)
    if poliklinik or gun or bas:
        return Anlam("poliklinik_ve_zaman", poliklinik, gun, bas, bit, fark_etmez=fark)
    sohbet = _sohbet_niyeti(n, alan)
    if sohbet:
        return Anlam(sohbet)
    if gecer(alan.POLIKLINIK_DEGISTIR_IFADELERI):
        return Anlam("poliklinik_degistir")
    if gecer(alan.GUN_DEGISTIR_IFADELERI):
        return Anlam("gun_degistir")
    if fark:
        return Anlam("fark_etmez", fark_etmez=True)
    if adim in SECENEKLI_ADIMLAR:
        if gecer(alan.DAHA_GEC_IFADELERI):
            return Anlam("secim", goreli="daha_gec")
        if gecer(alan.DAHA_ERKEN_IFADELERI):
            return Anlam("secim", goreli="daha_erken")
        sira = _sira_bul(n, alan)
        if sira is not None:
            return Anlam("secim", sira=sira)
    ret = gecer(alan.RET_IFADELERI)
    if ret and ifade_gecer_mi(n, "baska"):
        return Anlam("baska_saat")
    if ret:
        return Anlam("ret")
    if ifade_gecer_mi(n, "baska") or ifade_gecer_mi(n, "sonraki") or ifade_gecer_mi(n, "diger"):
        return Anlam("baska_saat")
    if acik_onay_mi(maske.metin, alan):
        return Anlam("onay", acik_onay=True)
    if gecer(alan.ONAY_IFADELERI) and len(n.split()) <= 6:
        # Onaya benziyor ama kapalı listede değil ("evet mi?", "tamam"): randevu oluşturmaz,
        # yalnız açık onay istenir.
        return Anlam("onay")
    if gecer(alan.TIBBI_IFADELER):
        return _saglik_anlami(n, alan, tavsiye)
    if ilac_sorusu and gecer(_ILAC_ADI_SORULARI):
        # "Lansoprazol nasıl kullanılır": ad kayıtla kodda eşleştirilir (orkestratör).
        return Anlam("ilac_sorusu", gecmis_konu="liste")
    if (konu in OZET_KONULARI or soru_mu(n, maske.metin)) and metinde_dagarcik_ilaci_var(n, yalniz_tam_ad=True) \
            and not gecer(_RANDEVU_KELIMELERI):
        # "Metformin ne işe yarıyor", "Metforminle araba kullanabilir miyim": bilinen ilaç adıyla soru. Cümle
        # LLM'e gitmez; ad kayıtla kodda eşleşir, konu gerekirse yalnız maskeli cümleden sorulur (orkestratör).
        return Anlam("ilac_sorusu", gecmis_konu="liste")
    if gecer(getattr(alan, "RANDEVU_ISTEGI_IFADELERI", ())) and gecer(getattr(alan, "RANDEVU_ISTEK_FIILLERI", ())):
        # "Randevu istiyorum" (adla ya da adsız): niyet açık, eksik bilgi sorulur; LLM'e gidilmez.
        return Anlam("randevu_istegi")
    # KVKK: kuralın çözemediği ama kullanıcının kendi sağlık verisini taşıyan cümle LLM'e gitmez;
    # sabit şablonla yanıtlanır (tavsiye isteği ise ret, değilse "tıbbi yorum yapamam").
    if gecer(getattr(alan, "ILAC_TAVSIYE_GUCLU", ())):
        return Anlam("ilac_tavsiye")
    if gecer(getattr(alan, "SAGLIK_VERISI_IFADELERI", ())):
        return _saglik_anlami(n, alan, tavsiye)
    return Anlam("belirsiz")


# İlaç bağlamı olmadan, yalnız soru biçimiyle ilaç sorusu sayılan konular ("alkol alabilir miyim"). Diğer
# konuların zayıf ifadeleri ("birlikte", "kaç gün") günlük konuşmada da geçer; ilaç bağlamı ister.
_SORU_ILE_YETEN_KONULAR = ("alkol", "yemek", "alerji", "hamilelik")
_YAN_ETKI_ACIK = ("yan etk", "yanetk", "yan tesir")
_RANDEVU_KELIMELERI = ("randevu", "saat", "gün", "bugün", "yarın", "hafta", "poliklinik", "bölüm", "sıra")

# Hekimin geçmişteki eylemi ("doktor ne verdi", "bana ne yazdılar", "kardiyolog ne demişti"): fiil tam kelime
# olarak (önek değil: "ne dediniz" asistana sorulur), yanında soru sözcüğü ve hekim/geçmiş/kişi işareti aranır.
_GECMIS_SORU_SOZCUKLERI = ("ne", "neler", "nelerdi", "neydi", "hangi", "hangisini", "hangilerini")
# Önekle aranır ("doktorum", "ilaçları"); tam kelime olarak aranır ("son" ≠ "sonra").
_GECMIS_ISARET_ONEKLERI = ("doktor", "hekim", "hoca", "ilac", "recete")
_GECMIS_ZAMAN_KELIMELERI = ("son", "sonuncu", "gecen", "gecende", "gecenlerde", "onceki", "onceden", "evvelki")
_GECMIS_KISI_KELIMELERI = ("bana", "bize", "benim", "dr", "bey", "beyin", "hanim", "hanimin")


def _kelime_var(n: str, kelimeler: tuple[str, ...]) -> bool:
    katli = {normalize(k) for k in kelimeler}
    return any(k in katli for k in n.split())


def _gecmis_zamanli(n: str) -> bool:
    return _kelime_var(n, _GECMIS_ZAMAN_KELIMELERI) or ifade_gecer_mi(n, "daha once")


def _gecmis_mi(n: str, alan: ModuleType) -> bool:
    return any(ifade_gecer_mi(n, i) for i in alan.GECMIS_IFADELERI) or hekim_gecmis_sorusu_mu(n, alan)


def hekim_gecmis_sorusu_mu(n: str, alan: ModuleType) -> bool:
    """Normalize cümle hekimin geçmişte yazdığını/verdiğini/söylediğini ya da geçmiş ziyareti mi soruyor?
    ("geçen sefer doktor ne verdi", "doktor ne demişti", "en son hangi doktora gittim").

    Fiil tam kelime olmalı; ayrıca bir soru sözcüğü ve hekim ya da geçmiş işareti gerekir: "doktor bana randevu
    verdi mi" (soru sözcüğü yok) ve "ne dediniz" (fiil listede yok) geçmiş sorusu değildir."""
    fiil = _kelime_var(n, getattr(alan, "GECMIS_HEKIM_FIILLERI", ()))
    if fiil and _kelime_var(n, _GECMIS_SORU_SOZCUKLERI) and (
            any(ifade_gecer_mi(n, i) for i in _GECMIS_ISARET_ONEKLERI) or _kelime_var(n, _GECMIS_KISI_KELIMELERI)
            or _gecmis_zamanli(n) or metindeki_poliklinik(n, alan) is not None):
        return True
    # "En son hangi doktora gittim", "kardiyolojiye en son ne zaman gittim": geçmiş zaman işareti + ziyaret sorusu.
    # "Hangi doktora gitmeliyim" (yönlendirme) geçmiş sorusu değildir.
    ziyaret = any(ifade_gecer_mi(n, i) for i in getattr(alan, "GECMIS_ZIYARET_IFADELERI", ()))
    gitti = re.search(r"(?<!\w)(gittim|gitmistim|gittik|muayene oldum|muayene olmustum)(?!\w)", n) is not None
    return bool(ziyaret and (gitti or _gecmis_zamanli(n)))


def gecmis_alt_konu(n: str, alan: ModuleType, ilac_baglami: bool) -> str:
    """Geçmiş sorusunun alt konusu: ``ziyaret`` (ne zaman / hangi hekim), ``ozet`` ("doktor ne demişti") ya da
    ``liste`` (yazılan ilaçlar; varsayılan)."""
    if ilac_baglami or ifade_gecer_mi(n, "yaz") or re.search(r"(?<!\w)(ne |neler )ver", n):
        # İlaç / reçete soruluyor; "hangi doktora gittim, ne yazdı" ziyaret yanıtı da ilaç adlarını sayar.
        if any(ifade_gecer_mi(n, i) for i in getattr(alan, "GECMIS_ZIYARET_IFADELERI", ())) \
                and not ilac_baglami:
            return "ziyaret"
        return "liste"
    if any(ifade_gecer_mi(n, i) for i in getattr(alan, "GECMIS_ZIYARET_IFADELERI", ())):
        return "ziyaret"
    if _kelime_var(n, getattr(alan, "GECMIS_OZET_FIILLERI", ())):
        return "ozet"
    return "liste"

# Tavsiye/teşhis isteği sayılan genel tıbbi sözcükler (yorum yapılmaz).
_TIBBI_YORUM_SOZCUKLERI = ("doz", "reçete", "teşhis", "tanı", "tedavi")
_GENEL_SIKAYET_SOZCUKLERI = ("ağrı", "hastalığım", "şikayet", "şikâyet", "rahatsızlığım", "sorunum")


def _saglik_anlami(n: str, alan: ModuleType, tavsiye: bool) -> Anlam:
    """Kalıplarla eşleşmeyen sağlık cümlesi (LLM'e GİTMEZ): tavsiye/teşhis isteği ya da ilaç beyanı
    "tıbbi yorum yapamam"; yalnız "hangi bölüme gitmeliyim" → şikâyet sorulur; diğerleri (listede olmayan
    şikâyet: "başım dönüyor", "cildimde döküntü") → poliklinik listesi sorulur."""
    def gecer(ifadeler: tuple[str, ...]) -> bool:
        return any(ifade_gecer_mi(n, i) for i in ifadeler)

    if tavsiye or gecer(_TIBBI_YORUM_SOZCUKLERI) or gecer(getattr(alan, "ILAC_BEYAN_IFADELERI", ())):
        return Anlam("tibbi_soru")
    yonlendirme = gecer(getattr(alan, "YONLENDIRME_SORULARI", ()))
    saglik = gecer(getattr(alan, "SAGLIK_VERISI_IFADELERI", ())) or gecer(_GENEL_SIKAYET_SOZCUKLERI)
    if yonlendirme and not saglik:
        return Anlam("sikayet", alt="yonlendirme")
    return Anlam("sikayet")


def metindeki_poliklinik(n: str, alan: ModuleType, organ_haric: bool = False) -> str | None:
    """Normalize metinde adı/eş anlamlısı geçen poliklinik kodu (ilk eşleşen). ``organ_haric``: tek başına
    organ adı olan eş anlamlılar ("göz") sayılmaz (şikâyet cümlesinde seçim değildir)."""
    organlar = getattr(alan, "ORGAN_ES_ANLAMLILARI", frozenset()) if organ_haric else frozenset()
    for p in alan.POLIKLINIKLER:
        if any(ifade_gecer_mi(n, i) for i in p.es_anlamlilar if i not in organlar) or ifade_gecer_mi(n, p.ad):
            return p.kod
    return None


_ILAC_ADI_SORULARI = ("nasıl kullan", "nasıl al", "nasıl iç", "ne işe yarar", "neye yarar", "ne için",
                      "kaç kez", "günde kaç", "kullanımı", "kullanılır", "ne zaman al", "ne zaman kullan",
                      "ne zaman iç")


# --------------------------------------------------------------------------
# LLM ile anlama (yalnız kural çözemediğinde)
# --------------------------------------------------------------------------

def _istem(alan: ModuleType, bugun: date, simdi_metni: str, adim: str, baglam: str = "",
           ufuk_gun: int = 7) -> str:
    """Geriye uyumluluk (tools/llm_olcum.py). ``baglam`` KULLANILMAZ: LLM'e oturum bağlamı gitmez."""
    del baglam, ufuk_gun
    return sistem_istemi(alan, bugun, simdi_metni, adim)


def _llm_ciktisini_dogrula(veri: dict, bugun: date, ufuk_gun: int, alan: ModuleType) -> Anlam:
    """Şema dışı alanlar yok sayılır; her alan tip/aralık denetiminden geçer."""
    if not isinstance(veri, dict):
        veri = {}
    niyet = veri.get("niyet")
    if niyet not in alan.NIYETLER:
        niyet = "belirsiz"
    poliklinik = veri.get("poliklinik")
    if poliklinik not in alan.POLIKLINIK_KODLARI:
        poliklinik = None
    gun = None
    if isinstance(veri.get("gun"), str):
        try:
            aday = date.fromisoformat(veri["gun"].strip())
            if bugun <= aday <= bugun + timedelta(days=ufuk_gun):
                gun = aday
        except ValueError:
            gun = None
    bas = hhmm_coz(veri.get("saat_baslangic")) if isinstance(veri.get("saat_baslangic"), str) else None
    bit = hhmm_coz(veri.get("saat_bitis")) if isinstance(veri.get("saat_bitis"), str) else None
    if bas and bit and bit < bas:
        bas, bit = bit, bas
    if bas and not bit:
        bit = bas
    sira = veri.get("sira")
    if isinstance(sira, bool) or not isinstance(sira, int) or not (sira == -1 or 1 <= sira <= 6):
        sira = None
    goreli = veri.get("goreli") if veri.get("goreli") in ("daha_gec", "daha_erken") else None
    ilac = veri.get("ilac_adi")
    if not isinstance(ilac, str) or not (2 <= len(ilac.strip()) <= 60) or not re.search(r"[^\W\d_]", ilac):
        ilac = None
    konu = veri.get("ilac_konu")
    if niyet != "ilac_sorusu" or konu not in getattr(alan, "ILAC_KONULARI", ()):
        konu = None
    return Anlam(niyet, poliklinik, gun, bas, bit, kaynak="llm", sira=sira, goreli=goreli,
                 fark_etmez=niyet == "fark_etmez", ilac_adi=ilac.strip() if ilac else None,
                 gecmis_konu="liste" if niyet in ("gecmis_muayene", "ilac_sorusu") else None, ilac_konu=konu)


_GUN_KANITI = re.compile(
    r"(?<!\w)(bugun|yarin|obur|ertesi|hafta|gun|pazartesi|sali|carsamba|persembe|cuma|cumartesi|pazar|"
    + "|".join(_AYLAR) + r")|\d")
_SAAT_KANITI = re.compile(r"(?<!\w)(saat|sabah|ogle|aksam|ikindi|erken|gec|bucuk|ceyrek)|\d")
_SIRA_KANITI = re.compile(r"(?<!\w)(ilk|son|orta|\w+(inci|nci|uncu|unci|ıncı))|\d")
_GORELI_KANITI = re.compile(r"(?<!\w)(gec|erken|sonra|once|ileri)")


def _temellendir(llm: Anlam, metin: str, alan: ModuleType) -> Anlam:
    """LLM'in metinde dayanağı olmayan alanlarını atar (küçük modeller "hmm şey" için bile
    poliklinik ve gün uydurabiliyor — ölçümde görüldü)."""
    n = normalize(metin)
    sonuc = llm
    if llm.poliklinik and not any(ifade_gecer_mi(n, k) for k in alan.POLIKLINIK_KOKLERI.get(llm.poliklinik, ())):
        sonuc = replace(sonuc, poliklinik=None)
    if llm.gun and not _GUN_KANITI.search(n):
        sonuc = replace(sonuc, gun=None)
    if llm.saat_baslangic and not _SAAT_KANITI.search(n):
        sonuc = replace(sonuc, saat_baslangic=None, saat_bitis=None)
    elif llm.saat_baslangic and llm.saat_baslangic == llm.saat_bitis and _saat_bul(n, ciplak=True) is None:
        # Tam saat ancak metinde sayıyla/sayı kelimesiyle söylenmişse kabul edilir
        # ("en erken saat" için model 08:00 uydurmuştu; uçtan uca denemede görüldü).
        sonuc = replace(sonuc, saat_baslangic=None, saat_bitis=None)
    if sonuc.saat_baslangic and _en_erken_mi(n) and _saat_bul(n) is None:
        sonuc = replace(sonuc, saat_baslangic=None, saat_bitis=None)
    if sonuc.sira is not None and not _SIRA_KANITI.search(n):
        sonuc = replace(sonuc, sira=None)
    if sonuc.goreli and not _GORELI_KANITI.search(n):
        sonuc = replace(sonuc, goreli=None)
    if sonuc.ilac_adi:
        hedef = normalize(sonuc.ilac_adi).replace(" ", "")
        kelimeler = n.split()
        birlesik = {a + b for a, b in zip(kelimeler, kelimeler[1:], strict=False)}
        if not hedef or not any(k.startswith(hedef[:5]) for k in [*kelimeler, *birlesik]):
            sonuc = replace(sonuc, ilac_adi=None)  # metinde geçmeyen ilaç adı uydurmadır
    if sonuc.niyet == "secim" and sonuc.sira is None and sonuc.goreli is None:
        sonuc = replace(sonuc, niyet="belirsiz")
    return sonuc


def _birlestir(llm: Anlam, kural: Anlam) -> Anlam:
    """LLM sonucu birincil; kuralın kesin bulduğu (açık kelimeyle söylenmiş) alanlar
    LLM'in boş bıraktığı ya da çeliştiği yerde kazanır."""
    sonuc = llm
    if kural.poliklinik and llm.poliklinik != kural.poliklinik:
        sonuc = replace(sonuc, poliklinik=kural.poliklinik)
    if kural.gun and not llm.gun:
        sonuc = replace(sonuc, gun=kural.gun)
    if (kural.saat_baslangic and kural.saat_baslangic == kural.saat_bitis
            and llm.saat_baslangic and llm.saat_baslangic == llm.saat_bitis
            and llm.saat_baslangic != kural.saat_baslangic):
        sonuc = replace(sonuc, saat_baslangic=kural.saat_baslangic, saat_bitis=kural.saat_bitis)
    elif kural.saat_baslangic and not llm.saat_baslangic:
        sonuc = replace(sonuc, saat_baslangic=kural.saat_baslangic, saat_bitis=kural.saat_bitis)
    if sonuc.niyet in ("belirsiz", "konu_disi", "tibbi_soru") and kural.poliklinik:
        sonuc = replace(sonuc, niyet="poliklinik_ve_zaman")
    if sonuc.niyet in ("ret", "baska_saat", "belirsiz") and sonuc.zaman_var:
        sonuc = replace(sonuc, niyet="poliklinik_ve_zaman")
    if sonuc.niyet == "poliklinik_ve_zaman" and not (sonuc.poliklinik or sonuc.zaman_var):
        sonuc = replace(sonuc, niyet=kural.niyet if kural.niyet != "poliklinik_ve_zaman" else "belirsiz")
    return sonuc


async def anla(metin: str, *, alan: ModuleType, llm: LLMSaglayici | None, bugun: date,
               simdi_metni: str, adim: str, ufuk_gun: int = 7, zaman_asimi_sn: float | None = None,
               toplam_sure_sn: float | None = None, llm_zorla: bool = False,
               ek_ilac_adlari: tuple[str, ...] = ()) -> Anlam:
    """Önce kural; LLM yalnız kural ``belirsiz`` dediğinde (ya da ``llm_zorla`` ile, kuralın
    çözemediği bir alan — ör. sorulan ilacın adı — gerektiğinde) çağrılır.

    ``zaman_asimi_sn`` tek LLM denemesinin, ``toplam_sure_sn`` (verilmezse aynı değer)
    yeniden denemeler dahil tüm anlama çağrısının üst sınırıdır."""
    maske = kisisel_maskele(metin)
    kural = kural_tabanli_anla(maske.metin, bugun, alan, adim=adim)
    if kural.niyet == "acil":
        return kural  # acil durum: sabit 112 şablonu, LLM'e gidilmez
    if maske.tc_var or kural.niyet == "kimlik":
        return replace(kural, niyet="kimlik")  # TC içeren metin LLM'e hiç gitmez
    if kural.acik_onay:
        return kural  # kapalı listedeki açık onay: LLM'e sorulmaz
    if adim == "ONAY_BEKLENIYOR" and kural.niyet in ("onay", "ret"):
        return kural
    if kural.niyet != "belirsiz" and not llm_zorla:
        return kural  # kural çözdü: cümle LLM'e gitmez
    if llm is None:
        return kural
    yuk = llm_yuku_kur(metin, alan=alan, bugun=bugun, simdi_metni=simdi_metni, adim=adim,
                       ek_ilac_adlari=ek_ilac_adlari)
    butce = toplam_sure_sn if toplam_sure_sn is not None else zaman_asimi_sn
    try:
        cagri = llm.json_uret(list(yuk.mesajlar), sistem=yuk.sistem, sema=alan.ANLAMA_SEMASI,
                              sema_adi="anlam", zaman_asimi_sn=zaman_asimi_sn)
        veri = await (asyncio.wait_for(cagri, butce) if butce else cagri)
    except LLMHatasi as hata:
        log.warning("LLM anlama başarısız, kural tabanlı yedeğe geçildi: %s (%s)", hata.kod,
                    getattr(hata, "durum_kodu", None))
        return replace(kural, kaynak="kural_yedek", llm_hatasi=hata.kod)
    except TimeoutError:
        log.warning("LLM anlama toplam süre bütçesini (%.1f sn) aştı, kural tabanlı yedeğe geçildi", butce)
        return replace(kural, kaynak="kural_yedek", llm_hatasi="LLM_TIMEOUT")
    llm_anlam = _temellendir(_llm_ciktisini_dogrula(veri, bugun, ufuk_gun, alan), maske.metin, alan)
    return _birlestir(llm_anlam, kural)


async def ilac_konusu_sor(metin: str, *, alan: ModuleType, llm: LLMSaglayici | None, bugun: date,
                          simdi_metni: str, zaman_asimi_sn: float | None = None) -> str | None:
    """Kural konuyu çıkaramadığında LLM'e YALNIZ konu etiketi sorulur.

    ``metin`` çağıranda ilaç adları ``[ilaç]`` yapılmış olmalıdır; yük yine ``llm_yuku_kur`` ile kurulur
    (dağarcıktaki ilaç adları ve sağlık ifadeleri orada da maskelenir). LLM yoksa, hata verirse ya da
    konu kapalı listede değilse ``None``: yanıt "elimde onaylı bilgi yok" şablonudur."""
    if llm is None:
        return None
    yuk = llm_yuku_kur(metin, alan=alan, bugun=bugun, simdi_metni=simdi_metni, adim="ILAC_KONUSU")
    # Konu alanı zorunlu olan ayrı şema: genel şemada alan isteğe bağlıdır ve model onu hiç yazmıyordu.
    sema = getattr(alan, "ILAC_KONU_SEMASI", alan.ANLAMA_SEMASI)
    try:
        cagri = llm.json_uret(list(yuk.mesajlar), sistem=yuk.sistem, sema=sema,
                              sema_adi="ilac_konu", zaman_asimi_sn=zaman_asimi_sn)
        veri = await (asyncio.wait_for(cagri, zaman_asimi_sn) if zaman_asimi_sn else cagri)
    except LLMHatasi as hata:
        log.warning("LLM ilaç konusu çıkarılamadı: %s", hata.kod)
        return None
    except TimeoutError:
        log.warning("LLM ilaç konusu zaman aşımı")
        return None
    konu = veri.get("ilac_konu") if isinstance(veri, dict) else None
    return konu if konu in getattr(alan, "ILAC_KONULARI", ()) else None
