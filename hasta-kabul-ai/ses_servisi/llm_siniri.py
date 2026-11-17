"""KVKK sınırı: LLM'e giden yükü kuran TEK yer (anlama yükü ``llm_yuku_kur``, genel sağlık bilgisi yükü
``saglik_bilgi_yuku_kur``).

LLM'e giden tek veri:
- maskelenmiş kullanıcı cümlesi (TC/uzun rakam dizisi, telefon, e-posta, kendini tanıtırken
  söylenen ad-soyad, ilaç adları ``[ilaç]`` ve sağlık ifadeleri ``[sağlık bilgisi]`` gizlenir),
- sabit sistem istemi (konu modülündeki ``SISTEM_ISTEMI``),
- kişisel olmayan bağlam: bugünün tarihi/saati, 7 günlük takvim, poliklinik adları ve diyalog
  adımının KAPALI listeden sabit açıklaması.

LLM'e ASLA gitmeyenler: hasta adı, TC, patientRef, oturum kimliği, erişim kodu, randevu kayıtları,
tanı, ilaç listesi, özet metinleri, hekim adı, önceki turların asistan yanıtları. Bunu imza
zorlar: ``llm_yuku_kur`` oturum nesnesi almaz; yalnız kullanıcı cümlesi + tarih + adım kodu alır.
Adım kodu kapalı listede değilse gönderilmez. İsteğe bağlı ``ek_ilac_adlari`` (hastanın kayıtlı ilaç adları)
YALNIZ maskeleme içindir: cümlede geçenler ``[ilaç]`` olur, adların kendisi yüke hiç girmez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from types import ModuleType

from ortak.llm import Mesaj
from ortak.turkce import ascii_katla, tc_maskele, turkce_kucuk

from .ilac_bilgisi import ILAC_MASKESI, ilac_adlarini_maskele, saglik_ifadelerini_maskele
from .zaman import takvim_metni, tarih_metni

AD_MASKESI = "[ad]"
TELEFON_MASKESI = "[gizli numara]"
EPOSTA_MASKESI = "[gizli e-posta]"

# ---------------------------------------------------------------- maskeleme

_EPOSTA = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Ayırıcılarla bölünmüş rakam dizisi; toplam 7+ rakam telefon/kimlik benzeri sayılır.
# Saat ("10:30") ve tarih ("20 kasım") bu eşiğe ulaşmaz.
_RAKAM_DIZISI = re.compile(r"(?<![\d:])\+?\d(?:[\s\-.()/]*\d){6,}(?![\d:])")

# Kendini tanıtma tetikleyicileri (ASCII'ye katlanmış, küçük harf). Sonrasındaki 1–3 kelime ad sayılır.
_AD_TETIKLERI = ("benim adim", "benim ismim", "adim", "ismim", "adi soyadim", "ad soyadim", "ad soyad",
                 "adim soyadim", "isim soyisim", "ismim soyismim")
# Ad sanılmaması gereken kelimeler (ASCII katlı). Bunlardan birine gelince ad bitti sayılır.
_DUR_KELIMELERI = frozenset("""
ve ile icin de da ki mi mu randevu randevum poliklinik poliklinigi doktor hekim istiyorum isterim
olsun lutfen yarin bugun sabah ogle ogleden aksam saat gun hafta pazartesi sali carsamba persembe cuma
cumartesi pazar dahiliye kardiyoloji ortopedi goz kalp kemik muayene ilac ilaclarim tc kimlik numaram
numara telefon telefonum evet hayir tamam bir iki uc var yok ne nasil hangi bu su o
""".split())
_UNVANLAR = ("hanim", "bey", "hanimefendi", "beyefendi")
# Önek olarak durduran kökler (ekli biçimler: "kardiyolojiye", "randevumu", "ilaçlarımı").
_DUR_KOKLERI = ("randevu", "poliklinik", "kardiyo", "dahiliy", "ortoped", "muayene", "ilac", "doktor",
                "hekim", "istiyor", "numara", "telefon", "kimlik")


def _durdurucu_mu(kok: str) -> bool:
    return kok in _DUR_KELIMELERI or kok.startswith(_DUR_KOKLERI)


def _kelime_kok(kelime: str) -> str:
    return ascii_katla(re.sub(r"['’].*$", "", kelime)).strip(".,;:!?\"()")


def _ad_maskele(metin: str) -> tuple[str, bool]:
    """Kendini tanıtma kalıplarındaki ad-soyadı ``[ad]`` ile değiştirir.

    - "adım Ayşe Yılmaz", "benim adım ayşe", "ismim …", "ad soyad …": sonraki 1–3 kelime
      (durdurucu kelimeye kadar), büyük/küçük harf fark etmeksizin.
    - "ben Ayşe Yılmaz": "ben"den sonra BÜYÜK harfle başlayan kelimeler (STT adları büyük yazar;
      "ben kardiyolojiye …" gibi cümleler bozulmaz).
    - "Ayşe Hanım", "Mehmet Bey": unvandan önceki büyük harfli 1–2 kelime.
    """
    kelimeler = list(re.finditer(r"\S+", metin))
    if not kelimeler:
        return metin, False
    katli = [_kelime_kok(k.group(0)) for k in kelimeler]
    gizle: set[int] = set()
    i = 0
    while i < len(kelimeler):
        eslesen = 0
        for tetik in _AD_TETIKLERI:
            parca = tetik.split()
            if katli[i:i + len(parca)] == parca:
                eslesen = len(parca)
                break
        if eslesen:
            j = i + eslesen
            adet = 0
            while j < len(kelimeler) and adet < 3:
                k = katli[j]
                if not k or _durdurucu_mu(k) or any(ch.isdigit() for ch in k):
                    break
                gizle.add(j)
                adet += 1
                ham = kelimeler[j].group(0)
                if ham[-1:] in ".,;:!?":
                    break
                j += 1
            i = max(j, i + 1)
            continue
        if katli[i] == "ben":
            j = i + 1
            adet = 0
            while j < len(kelimeler) and adet < 3:
                ham = kelimeler[j].group(0)
                if not ham[:1].isupper() or _durdurucu_mu(katli[j]):
                    break
                gizle.add(j)
                adet += 1
                if ham[-1:] in ".,;:!?":
                    break
                j += 1
            i = max(j, i + 1)
            continue
        if katli[i] in _UNVANLAR and i > 0:
            for geri in (1, 2):
                j = i - geri
                if j < 0:
                    break
                ham = kelimeler[j].group(0)
                if not ham[:1].isupper() or _durdurucu_mu(katli[j]):
                    break
                gizle.add(j)
        i += 1
    if not gizle:
        return metin, False
    # Art arda gizlenen kelimeler tek "[ad]" olur; son kelimenin noktalaması korunur.
    parcalar: list[str] = []
    konum = 0
    idx = 0
    while idx < len(kelimeler):
        if idx not in gizle:
            idx += 1
            continue
        j = idx
        while j + 1 in gizle:
            j += 1
        son_ham = kelimeler[j].group(0)
        parcalar.append(metin[konum:kelimeler[idx].start()])
        parcalar.append(AD_MASKESI + (son_ham[-1] if son_ham[-1] in ".,;:!?" else ""))
        konum = kelimeler[j].end()
        idx = j + 1
    parcalar.append(metin[konum:])
    return "".join(parcalar), True


def _rakam_dizisi_maskele(metin: str) -> tuple[str, bool]:
    adet = 0

    def degistir(m: re.Match[str]) -> str:
        nonlocal adet
        if sum(ch.isdigit() for ch in m.group(0)) >= 7:
            adet += 1
            return TELEFON_MASKESI
        return m.group(0)

    yeni = _RAKAM_DIZISI.sub(degistir, metin)
    return yeni, adet > 0


@dataclass(frozen=True)
class Maskeleme:
    metin: str
    tc_var: bool       # 11 haneli kimlik benzeri dizi (metin LLM'e hiç gitmez)
    maskelendi: bool   # herhangi bir maskeleme yapıldı mı


def kisisel_maskele(metin: str) -> Maskeleme:
    """Kullanıcı cümlesindeki kişisel verileri gizler (TC, telefon/uzun rakam, e-posta, ad)."""
    yeni, tc_var = tc_maskele(metin)
    yeni, e = re.subn(_EPOSTA, EPOSTA_MASKESI, yeni)
    yeni, tel = _rakam_dizisi_maskele(yeni)
    yeni, ad = _ad_maskele(yeni)
    return Maskeleme(yeni, tc_var, bool(tc_var or e or tel or ad))


# ---------------------------------------------------------------- LLM yükü

@dataclass(frozen=True)
class LLMYuku:
    sistem: str
    mesajlar: tuple[Mesaj, ...]

    def tum_metin(self) -> str:
        """Sızıntı denetimi için yükteki tüm metin."""
        return "\n".join([self.sistem, *(m.icerik for m in self.mesajlar)])


_SAAT_METNI = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def sistem_istemi(alan: ModuleType, bugun: date, simdi_metni: str, adim: str) -> str:
    """Sabit istem + kişisel olmayan bağlam. ``adim`` kapalı listede değilse boş geçer."""
    poliklinikler = "\n".join(f"- {p.kod} = {p.ad}" for p in alan.POLIKLINIKLER)
    return alan.SISTEM_ISTEMI.format(
        bugun_metni=f"{tarih_metni(bugun)} {bugun.year}",
        saat_metni=simdi_metni if _SAAT_METNI.match(simdi_metni or "") else "",
        takvim=takvim_metni(bugun, 7),
        poliklinikler=poliklinikler,
        adim=alan.ADIM_ACIKLAMALARI.get(adim, ""),
    )


# ---------------------------------------------------------------- yalnız LLM yüküne özgü ek maskeleme
# Kural katmanı bunlara ihtiyaç duymaz (cümleyi kendisi çözer), LLM'in ise niyeti anlamak için hiçbirine
# ihtiyacı yoktur. Bu yüzden yalnız LLM'e giden kopyada uygulanır; kural ayrıştırmasını bozmaz.

# 5+ rakam (ayırıcılarla bölünmüş olabilir): erişim kodu (6 hane), kısa numara, posta kodu, yıl+gün…
# Saat ("10:30") iki nokta içerdiği için eşleşmez.
_KISA_RAKAM_DIZISI = re.compile(r"(?<![\d:])\d(?:[\s\-.()/]*\d){4,}(?![\d:])")
# "kod" kelimesinden sonra gelen 4+ rakam ("erişim kodum 4829")
_KOD_SONRASI = re.compile(r"(?i)(kod\w*\s*:?\s*)\d{4,}")
# Büyük harfle yazılmış ama bilinen alan kelimesi olmayan kelimeler (ad, soyad, adres, marka/ilaç adı).
# STT özel adları büyük harfle yazar; cümle başındaki kelime de büyük yazılır, bu yüzden cümle başında
# yaygın kelimeler beyaz listededir. Bilinmeyen kelime gizlenirse LLM yalnız niyeti biraz daha zor anlar;
# gizlenmezse kişisel/sağlık verisi dışarı gider — tercih bilerek ilkinden yana.
_BILINEN_KOKLER = (
    "evet", "hayir", "tamam", "olur", "peki", "ben", "bana", "benim", "biz", "bir", "bu", "su", "o", "acaba",
    "merhaba", "selam", "gunaydin", "iyi", "lutfen", "tesekkur", "sag", "ozur", "pardon", "kusura", "yarin",
    "bugun", "obur", "ertesi", "sabah", "ogle", "aksam", "ikindi", "gece", "en", "ilk", "son", "ikinci",
    "ucuncu", "hangi", "hangisi", "ne", "nasil", "nerede", "kac", "neden", "nicin", "randevu", "poliklinik",
    "doktor", "hekim", "bolum", "baska", "daha", "biraz", "farketmez", "fark", "uygun", "musait", "saat",
    "gun", "hafta", "haftaya", "gelecek", "onumuzdeki", "sonra", "once", "erken", "gec", "simdi", "hemen",
    "olsun", "istiyorum", "isterim", "vazgec", "iptal", "onay", "kabul", "dogru", "yanlis", "degil", "yok",
    "var", "hepsi", "herhangi", "siz", "sen", "kimlik", "tc", "muayene", "ilac", "recete", "tani", "sey",
    "hmm", "ee", "ya", "yani", "aslinda", "galiba", "sanirim", "mesela", "ornegin", "yeni", "eski", "gecen",
    "gecmis", "ayni", "diger", "sonraki", "onceki", "mumkun", "olabilir", "ama", "fakat", "ve", "veya",
    "ya da", "ile", "icin", "gibi", "kadar", "kalp", "kemik", "goz", "ic", "cocuk", "kadin", "dis",
)
_AYLAR_GUNLER = ("ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz", "agustos", "eylul", "ekim",
                 "kasim", "aralik", "pazartesi", "sali", "carsamba", "persembe", "cuma", "cumartesi", "pazar")
_SAYI_KOKLERI = ("sifir", "bir", "iki", "uc", "dort", "dord", "bes", "alti", "yedi", "sekiz", "dokuz", "on",
                 "yirmi", "otuz", "kirk", "elli", "bucuk", "ceyrek")
_SAYI_EKLERI = frozenset({"da", "de", "ta", "te", "a", "e", "ya", "ye", "i", "u", "dan", "den", "tan", "ten",
                          "yi", "yu", "si", "su", "inci", "nci", "uncu", "unci", "inde", "nde"})


def _bilinen_kelime_mi(kok: str, alan: ModuleType) -> bool:
    if not kok or not kok[0].isalpha():
        return True
    if kok in _BILINEN_KOKLER or kok in _DUR_KELIMELERI or kok in _UNVANLAR:
        return True
    alan_kokleri = [ascii_katla(k).split()[0] for p in alan.POLIKLINIKLER for k in (p.ad, *p.es_anlamlilar)]
    for kokler in getattr(alan, "POLIKLINIK_KOKLERI", {}).values():
        alan_kokleri += [ascii_katla(k).split()[0] for k in kokler]
    # Ekli biçimler ("kardiyolojiye", "yarına", "pazartesiye", "onaylıyorum"): kök önekiyle kabul edilir.
    onekler = (*_AYLAR_GUNLER, *_SAYI_KOKLERI, *(k for k in _BILINEN_KOKLER if len(k) >= 4),
               *(k for k in alan_kokleri if len(k) >= 3), *_DUR_KOKLERI, "onay", "randev", "muayen")
    if kok in _SAYI_KOKLERI or any(kok.startswith(s) and kok[len(s):] in _SAYI_EKLERI for s in _SAYI_KOKLERI):
        return True  # "Onda", "Dokuzda", "İkiye" (ama "Onur" değil)
    return kok.startswith(tuple(o for o in onekler if len(o) >= 3))


def _buyuk_harfli_maskele(metin: str, alan: ModuleType) -> str:
    """Büyük harfle başlayan ve bilinen alan kelimesi olmayan kelimeleri ``[ad]`` yapar."""
    parcalar: list[str] = []
    konum = 0
    onceki_gizli = False
    for m in re.finditer(r"\S+", metin):
        ham = m.group(0)
        govde = ham.strip(".,;:!?\"()[]")
        if govde[:1].isupper() and govde[:1].isalpha() and not _bilinen_kelime_mi(_kelime_kok(govde), alan):
            son = ham[-1] if ham[-1:] in ".,;:!?" else ""
            if onceki_gizli:
                # Art arda gizli kelimeler tek "[ad]": önceki parçadaki noktalama korunur.
                parcalar[-1] = parcalar[-1].rstrip(".,;:!?") + son
                konum = m.end()
                continue
            parcalar.append(metin[konum:m.start()])
            parcalar.append(AD_MASKESI + son)
            konum = m.end()
            onceki_gizli = True
            continue
        onceki_gizli = False
    parcalar.append(metin[konum:])
    return "".join(parcalar)


# Dağarcıkta olmayan ilaç adları için biçim ipucu (ASCII katlı, küçük harf): uluslararası etken madde ekleri
# ve yaygın marka adları. Kapsam kısmidir (bkz. README "Bilinen sınırlar"); hastanın KENDİ kayıtlı ilaçları
# ayrıca adıyla maskelenir (``ek_ilac_adlari``: orkestratör geçmişi LLM'den önce belleğe alır).
_ILAC_EKLERI = re.compile(
    r"^(?:[a-z]{3,}|[a-z]{2}(?=sartan))(statin|pril|sartan|olol|dipin|prazol|tidin|silin|sillin|cillin|mycin|misin|floksasin|floxacin|"
    r"profen|fenak|azepam|zolam|tiazid|semid|formin|gliptin|gliflozin|parin|ksaban|xaban|dogrel|grel|triptan|"
    r"setron|oksetin|oxetine|pram|lukast|siklin|cycline|zolid|azol|tadin|tidine|mab|tinib|virin|vudin|"
    r"metazon|nizolon|kortizon|kain|morfin)[a-z]{0,6}$")
_ILAC_MARKALARI = (
    "aspirin", "coraspin", "ecopirin", "parol", "minoset", "majezik", "arveles", "nurofen", "apranax", "voltaren",
    "dolorex", "augmentin", "klamoks", "cipro", "glifor", "glucophage", "diamicron", "eliquis", "xarelto",
    "coumadin", "plavix", "beloc", "concor", "norvasc", "euthyrox", "levotiron", "lasix", "xanax", "cipralex",
    "lustral", "prozac", "nexium", "lansor", "ventolin", "zyrtec", "aerius", "lyrica",
)


def _ilac_bicimli_mi(kelime: str) -> bool:
    kok = ascii_katla(kelime.lower()).strip(".,;:!?\"'()’")
    kok = re.split(r"['’]", kok)[0]
    if len(kok) < 5 or not kok.isalpha():
        return False
    return bool(_ILAC_EKLERI.match(kok)) or any(kok.startswith(m) and len(kok) - len(m) <= 4
                                                 for m in _ILAC_MARKALARI)


def _ilac_bicimlilari_maskele(metin: str) -> str:
    return re.sub(r"\S+", lambda m: (ILAC_MASKESI + (m.group(0)[-1] if m.group(0)[-1] in ".,;:!?" else ""))
                  if not m.group(0).startswith("[") and _ilac_bicimli_mi(m.group(0)) else m.group(0), metin)


def llm_icin_maskele(kullanici_metni: str, alan: ModuleType, ek_ilac_adlari: tuple[str, ...] = ()) -> str:
    """LLM'e gidecek kullanıcı cümlesi: kişisel maskeleme + yalnız LLM'e özgü ek maskeler.
    ``ek_ilac_adlari``: hastanın hekim onaylı kayıtlarındaki ilaç adları (dağarcıkta olmasalar da gizlenir)."""
    metin = kisisel_maskele(kullanici_metni).metin
    metin = _KOD_SONRASI.sub(lambda m: m.group(1) + TELEFON_MASKESI, metin)
    metin = _KISA_RAKAM_DIZISI.sub(TELEFON_MASKESI, metin)
    # İlaç adı (bilgi dağarcığındaki ve hastanın kayıtlı adları, ekli/STT hatalı biçimleri dahil; ayrıca ilaç
    # biçimli bilinmeyen kelimeler) ve sağlık ifadeleri LLM'e gitmez: niyet/konu için gerekmezler.
    metin = ilac_adlarini_maskele(metin, ek_ilac_adlari)
    metin = _ilac_bicimlilari_maskele(metin)
    metin = saglik_ifadelerini_maskele(metin, alan)
    return _buyuk_harfli_maskele(metin, alan)


# ---------------------------------------------------------------- genel sağlık bilgisi yükü
# Ayrı yol (ses_servisi/saglik_bilgisi.py): yanıt üretilebilsin diye ilaç adı ve sağlık kelimeleri AÇIK kalır;
# kimlik bilgileri (TC, telefon/uzun sayı, e-posta, kod, kendini tanıtırken söylenen ad, cümle ortasında büyük
# harfle yazılmış ve ilaç/sağlık terimi olmayan kelime) maskelenir. Yükte hasta kaydı YOKTUR: imza yalnız
# kullanıcı cümlesi alır; ad, erişim kodu, muayene tarihi, reçete/özet, hekim talimatı, randevu bilgisi
# eklenemez. "Bu ilaç bu hastaya yazıldı" bilgisi de gitmez (yük her hasta için aynıdır).

_KESME_EKI = re.compile(r"['’]\w*$")


def _saglik_terimi_mi(kelime: str, alan: ModuleType) -> bool:
    kok = normalize_kelime(kelime)
    if len(kok) < 3:
        return False
    if _ilac_bicimli_mi(kelime) or ilac_adlarini_maskele(kelime) != kelime:
        return True
    terimler = [ascii_katla(t).split()[0] for t in getattr(alan, "SAGLIK_BILGI_TERIMLERI", ()) if t.strip()]
    return any(kok.startswith(t) for t in terimler if len(t) >= 3)


def normalize_kelime(kelime: str) -> str:
    return ascii_katla(_KESME_EKI.sub("", kelime)).strip(".,;:!?\"()[]")


def saglik_sorusu_maskele(kullanici_metni: str, alan: ModuleType) -> Maskeleme:
    """Genel bilgi yoluna giden soru: kişisel maskeleme + kod/kısa numara + kimlik benzeri büyük harfli kelime.
    İlaç adları ve sağlık kelimeleri açık kalır."""
    m = kisisel_maskele(kullanici_metni)
    metin = _KOD_SONRASI.sub(lambda x: x.group(1) + TELEFON_MASKESI, m.metin)
    metin = _KISA_RAKAM_DIZISI.sub(TELEFON_MASKESI, metin)
    parcalar: list[str] = []
    konum = 0
    kelimeler = list(re.finditer(r"\S+", metin))
    for idx, k in enumerate(kelimeler):
        ham = k.group(0)
        govde = ham.strip(".,;:!?\"()[]")
        if not govde[:1].isalpha() or not govde[:1].isupper() or govde.startswith("["):
            continue
        cumle_basi = idx == 0 or kelimeler[idx - 1].group(0)[-1:] in ".!?"
        kesmeli = bool(_KESME_EKI.search(govde))
        sonraki = normalize_kelime(kelimeler[idx + 1].group(0)) if idx + 1 < len(kelimeler) else ""
        bilinen = (_bilinen_kelime_mi(_kelime_kok(govde), alan) or _saglik_terimi_mi(govde, alan))
        if bilinen:
            continue
        # Cümle başındaki tek kelime genellikle ad değildir ("Grip ile…", "Aspirin…"); yine de ardından
        # "için/adına" geliyorsa ya da kesme ekliyse ("Mehmet'in") ad sayılır.
        if cumle_basi and not kesmeli and sonraki not in ("icin", "adina", "hanim", "bey"):
            continue
        son = ham[-1] if ham[-1:] in ".,;:!?" else ""
        parcalar.append(metin[konum:k.start()])
        parcalar.append(AD_MASKESI + son)
        konum = k.end()
    parcalar.append(metin[konum:])
    yeni = "".join(parcalar)
    return Maskeleme(yeni, m.tc_var, m.maskelendi or yeni != m.metin)


def saglik_bilgi_yuku_kur(kullanici_metni: str, *, alan: ModuleType) -> LLMYuku | None:
    """Genel sağlık bilgisi yükü: sabit istem + tek kullanıcı mesajı (maskeli soru). Geçmiş tur, hasta kaydı,
    tarih ya da oturum bağlamı YOK. TC benzeri dizi taşıyan cümle için ``None``: maskeli de olsa gönderilmez."""
    m = saglik_sorusu_maskele(kullanici_metni, alan)
    if m.tc_var or not m.metin.strip():
        return None
    icerik = "\n".join([f"Soru: {turkce_harf_onar(m.metin, alan)}", *marka_notlari(m.metin, alan)])
    return LLMYuku(sistem=alan.SAGLIK_BILGI_ISTEMI, mesajlar=(Mesaj.kullanici(icerik),))


def saglik_tavsiye_yuku_kur(konu_kodu: str, alt_sorular: tuple[str, ...], *, alan: ModuleType) -> LLMYuku | None:
    """Şikâyet için "evde ne yapılabilir" yükü: sabit istem + tek kullanıcı mesajı. Mesaj YALNIZ kapalı
    listelerden kurulur (``TAVSIYE_KONULARI`` konu adı + ``TAVSIYE_ALT_SORULARI`` sabit alt sorular); kullanıcının
    cümlesi, ad, TC, hasta kaydı, oturum bağlamı girmez. Bilinmeyen konu kodu → ``None``."""
    from .tavsiye import konu_adi, tavsiye_sorusu  # döngüsel içe aktarmayı önlemek için yerel

    ad = konu_adi(konu_kodu, alan)
    soru = tavsiye_sorusu(konu_kodu, alt_sorular, alan)
    if ad is None or soru is None:
        return None
    return LLMYuku(sistem=alan.SAGLIK_BILGI_ISTEMI, mesajlar=(Mesaj.kullanici(f"Konu: {ad}\nSoru: {soru}"),))


_TR_HARFLER = re.compile(r"[çğışöüÇĞİŞÖÜ]")
_ONARILAN_IFADELER = ("ne işe yarar", "iyi gelir", "nasıl", "niçin", "düşer", "yükselir", "geçer", "içilir",
                      "alınır", "kullanılır", "zararlı", "faydalı", "belirtiler", "dikkat", "için", "farkı",
                      "arasındaki")


def turkce_harf_onar(metin: str, alan: ModuleType) -> str:
    """Türkçe karaktersiz yazılmış soruda ("bas agrisina ne iyi gelir") bilinen sağlık terimlerinin ve soru
    kalıplarının Türkçe harflerini geri koyar; model "bas"ı "bacak" sanıyordu. Türkçe harf içeren metne
    dokunulmaz. Yalnız kelime başından eşleşen, harf farkı olan terimler değişir."""
    if _TR_HARFLER.search(metin):
        return metin
    terimler = sorted({t for t in (*getattr(alan, "SAGLIK_BILGI_TERIMLERI", ()), *_ONARILAN_IFADELER)
                       if ascii_katla(t) != turkce_kucuk(t)}, key=len, reverse=True)
    for t in terimler:
        desen = r"(?<!\w)" + re.escape(ascii_katla(t)).replace(r"\ ", r"\s+")
        metin = re.sub(desen, turkce_kucuk(t), metin, flags=re.I)
    return metin


def marka_notlari(metin: str, alan: ModuleType) -> list[str]:
    """Soruda geçen yaygın ilaç markası için sabit "etken maddesi" notu (kişisel veri değil)."""
    from .gecmis import _ad_eslesir_mi  # döngüsel içe aktarmayı önlemek için yerel

    eslesme = getattr(alan, "ILAC_MARKA_ETKEN", {})
    notlar: list[str] = []
    for kelime in metin.split():
        kok = normalize_kelime(kelime).lower()
        if len(kok) < 4 or kok.startswith("["):
            continue
        for marka, etken in eslesme.items():
            if (kok.startswith(marka) and len(kok) - len(marka) <= 4) or (len(marka) >= 7 and _ad_eslesir_mi(kok, marka)):
                notu = alan.SAGLIK_BILGI_MARKA_NOTU.format(marka=marka.capitalize(), etken=etken)
                if notu not in notlar:
                    notlar.append(notu)
                break
    return notlar


def llm_yuku_kur(kullanici_metni: str, *, alan: ModuleType, bugun: date, simdi_metni: str,
                 adim: str, ek_ilac_adlari: tuple[str, ...] = ()) -> LLMYuku:
    """LLM'e gidecek yükü kurar. Tek kullanıcı mesajı (maskeli cümle); geçmiş tur yok."""
    maskeli = llm_icin_maskele(kullanici_metni, alan, ek_ilac_adlari)
    return LLMYuku(sistem=sistem_istemi(alan, bugun, simdi_metni, adim),
                   mesajlar=(Mesaj.kullanici(maskeli),))
