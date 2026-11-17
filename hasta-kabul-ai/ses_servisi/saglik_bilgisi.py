"""Genel sağlık / ilaç BİLGİ sorusu yolu.

Akış (orkestratör ``_Baglam._saglik_bilgi_yaniti``):
1. Acil ifade her zaman ÖNCE kuralla yakalanır (``anlama.acil_mi`` → 112 şablonu); bu modüle hiç gelinmez.
2. ``saglik_karari``: cümle sağlıkla ilgili bir bilgi sorusu mu (``bilgi``), kişiye özel karar mı (``spesifik``:
   doz, bırakma/başlama, tahlil yorumu, teşhis, iki ilacın birlikte kullanımı, kişisel durumda ilaç, ilaç
   önerisi), yoksa bu yolun dışı mı (``None``: randevu, kendi kayıtları, şikâyet anlatımı, sağlık dışı).
3. Dağarcıkta hazır bilgi varsa oradan (değişmedi); yoksa ``genel_bilgi_uret`` yerel LLM'e YALNIZ kimliği
   maskelenmiş soruyu gönderir (``llm_siniri.saglik_bilgi_yuku_kur``), çıktıyı ``yanit_denetle`` ile denetler.
   Denetimden geçmeyen, zaman aşımına uğrayan ya da hata veren yanıt yerine sabit "güvenilir bilgi
   veremiyorum" metni verilir. Kapanış ("genel bilgidir… doktorunuza danışın") her zaman KODDAN eklenir.

Loglara soru ve yanıt metni yazılmaz; yalnız ret nedeni kodu ve süre.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from types import ModuleType

from ortak.llm import LLMHatasi, LLMSaglayici
from ortak.turkce import ascii_katla, ifade_gecer_mi, normalize

from .alanlar.ilac_dagarcigi import ILACLAR, IlacKaydi
from .anlama import sikayet_poliklinikleri
from .gecmis import _ad_eslesir_mi
from .ilac_bilgisi import konu_bul, metinde_dagarcik_ilaci_var, soru_mu
from .llm_siniri import _ILAC_MARKALARI, LLMYuku, _ilac_bicimli_mi, saglik_bilgi_yuku_kur

log = logging.getLogger("ses_servisi.saglik_bilgisi")


@dataclass(frozen=True)
class SaglikKarari:
    tur: str            # "bilgi" | "spesifik"
    ilac_var: bool      # cümlede ilaç adı (dağarcık, ilaç biçimli, bilinen marka ya da verilen ek ad) geçiyor
    neden: str = ""     # spesifik nedeni: doz | birakma | tahlil | teshis | iki_ilac | kisisel_ilac | ilac_onerisi


def _gecer(n: str, ifadeler: tuple[str, ...]) -> bool:
    return any(ifade_gecer_mi(n, i) for i in ifadeler)


def ilac_adlari(n: str, ek_adlar: tuple[str, ...] = ()) -> list[str]:
    """Normalize cümlede geçen farklı ilaç adları (kök). Dağarcık adları, ilaç biçimli kelimeler (-pril,
    -statin…), bilinen markalar ve ``ek_adlar`` (hastanın kayıtlı ilaçları) sayılır."""
    bulunan: list[str] = []
    ekler = [normalize(a).split()[0] for a in ek_adlar if normalize(a)]
    kayit_adlari = [a for k in ILACLAR for a in k.adlar if len(a.split()) == 1]
    for kelime in n.split():
        if len(kelime) < 4:
            continue
        ad = next((a for a in (*kayit_adlari, *ekler) if _ad_eslesir_mi(kelime, a)), None)
        if ad is None:
            # Bulanık marka eşleşmesi yalnız uzun adlarda ("asprin" → aspirin); "lyrica" ↔ "ayrıca" gibi kısa
            # adlar yalnız önekle.
            marka = next((m for m in _ILAC_MARKALARI
                          if (kelime.startswith(m) and len(kelime) - len(m) <= 4)
                          or (len(m) >= 7 and _ad_eslesir_mi(kelime, m))), None)
            if marka:
                ad = marka
            elif _ilac_bicimli_mi(kelime):
                ad = kelime
        if ad and not any(_ad_eslesir_mi(ad, b) or _ad_eslesir_mi(b, ad) for b in bulunan):
            bulunan.append(ad)
    if not bulunan and metinde_dagarcik_ilaci_var(n, yalniz_tam_ad=True):
        bulunan.append("dagarcik")
    return bulunan


def dagarcik_kaydi_bul(n: str) -> IlacKaydi | None:
    """Cümlede adı geçen dağarcık kaydı (kesin ad ya da ekli/STT hatalı biçim; ``metin_adlari`` sayılmaz)."""
    for kelime in n.split():
        for kayit in ILACLAR:
            if any(len(a.split()) == 1 and _ad_eslesir_mi(kelime, a) for a in kayit.adlar):
                return kayit
    return None


_GENEL_ILAC_KELIMELERI = ("ilaç", "ilac", "hap", "tablet", "şurup", "kapsül", "iğne")
# STT'nin bitişik yazdığı soru eki: "kullanabilirmiyim", "olurmu" değil (kısa ek rastlantısı çok).
_BITISIK_SORU = re.compile(r"\w{3,}(miyim|muyum|misin|musun|miyiz|muyuz|misiniz|musunuz|midir|mudur)(?!\w)")


# Terim listesinde olmayan tahlil/görüntüleme sözcükleri: tahlil yorumu isteği yalnız bunlarla sabit metne gider.
_TAHLIL_KELIMELERI = ("tahlil", "kan değer", "değerlerim", "emar", "röntgen", "ultrason", "kan sonuc", "kan sonuç")
_TAKIP_ISARETLERI = ("peki", "bunu", "bunun", "bununla", "onu", "onun", "onunla", "bunları", "bunlar")


def _konu_disi_terim_var(n: str, alan: ModuleType) -> bool:
    """İlaç konusu kelimeleri ("yan etkisi", "alkol", "aç karnına") atıldıktan sonra cümlede hâlâ bir sağlık
    terimi var mı? "Alkol karaciğere zararlı mı" → evet (karaciğer); "peki alkol?" → hayır (takip sorusu)."""
    konu, _ = konu_bul(n, alan)
    if konu is None or konu == "etkilesim":
        return True
    if sikayet_poliklinikleri(n, alan):
        return True  # "şekerim var, tatlı yiyebilir miyim": hastalık kelimesi konu kökü sayılmaz  # etkileşim kalıbı geniş ("antibiyotik kullanırken"); adsız ilaç ise genel kelimeyle yakalanır
    ifadeler = next((i for k, i, _ in getattr(alan, "ILAC_KONU_KALIPLARI", ()) if k == konu), ())
    kokler = tuple({k for i in ifadeler for k in normalize(i).split() if len(k) >= 3})
    kalan = " ".join(k for k in n.split() if not k.startswith(kokler))
    return _gecer(kalan, alan.SAGLIK_BILGI_TERIMLERI)


def _ile_var(n: str) -> bool:
    return re.search(r"(?<!\w)(ile|ıle|yla|yle)(?!\w)", n) is not None or re.search(r"\w(yla|yle|la|le) ", n) is not None


def saglik_karari(maskeli: str, alan: ModuleType, ek_ilac_adlari: tuple[str, ...] = ()) -> SaglikKarari | None:
    """Kişisel maskeden geçmiş cümle için yol kararı (bkz. modül açıklaması). Acil denetimi ÇAĞIRANDADIR."""
    n = normalize(maskeli)
    if not n:
        return None
    ilaclar = ilac_adlari(n, ek_ilac_adlari)
    ilac_baglami = bool(ilaclar) or _gecer(n, (*_GENEL_ILAC_KELIMELERI, "doz", "antibiyotik", "ağrı kesici"))
    # Sağlık terimi: genel ilaç kelimeleri ("ilaç", "hap") tek başına sayılmaz — adı söylenmeyen ilaç sorusu
    # ("ilacı ne işe yarar") hastanın kayıtlarına dönüktür, mevcut akış yanıtlar.
    terim_metni = " ".join(k for k in n.split() if not k.startswith(tuple(normalize(g) for g in _GENEL_ILAC_KELIMELERI)))
    if not ilaclar and not _gecer(terim_metni, alan.SAGLIK_BILGI_TERIMLERI):
        if _gecer(n, getattr(alan, "SAGLIK_SPESIFIK_TESHIS_KESIN", ())):
            return SaglikKarari("spesifik", False, "teshis")  # "bende ne var sizce": terimsiz teşhis isteği
        if _gecer(n, _TAHLIL_KELIMELERI) and _spesifik_neden(n, [], False, alan) == "tahlil":
            return SaglikKarari("spesifik", False, "tahlil")  # "tahlil sonucum yüksek çıktı ne demek"
        return None  # sağlıkla ilgisiz: mevcut davranış
    if not ilaclar and (_gecer(n, _GENEL_ILAC_KELIMELERI) or _gecer(n, _TAKIP_ISARETLERI)
                        or not _konu_disi_terim_var(n, alan)):
        # Adı söylenmeyen ilaca dönük soru ("ilacın yan etkisi var mı", "peki alkol?", "bunu aç karnına mı
        # içeyim"): hastanın kayıtlı ilacı kastediliyor; mevcut ilaç akışı (hangi ilaç? / son ilaç) yanıtlar.
        return None
    if _gecer(n, alan.SAGLIK_YOLU_DISI) or _poliklinik_adi_var(n, alan):
        return None  # randevu / yönlendirme / hastanın kendi kayıtları: mevcut akış
    if not ilaclar and _gecer(n, ("bugün", "yarın", "hafta", "saat", "sabah", "öğle", "akşam", "pazartesi",
                                   "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar")):
        return None  # ilaç adı yokken gün/saat kelimesi: randevu tercihi olabilir
    soru = soru_mu(n, maskeli) or _BITISIK_SORU.search(n) is not None \
        or _gecer(n, alan.SAGLIK_BILGI_SORU_KALIPLARI)
    kisisel_sikayet = _gecer(n, alan.SAGLIK_KISISEL_SIKAYET)
    if not ilaclar and _gecer(n, getattr(alan, "SAGLIK_ILAC_BICIMLERI", ())) and _gecer(n, alan.SAGLIK_ILAC_IZIN) \
            and (kisisel_sikayet or sikayet_poliklinikleri(n, alan)) and not _gecer(n, alan.SAGLIK_CIDDI_BELIRTILER):
        # "Gözlerim kuruyor damla kullanabilir miyim": kendi şikâyeti için ilaç kararı → sabit yönlendirme.
        return SaglikKarari("spesifik", False, "kisisel_ilac")
    if kisisel_sikayet and not ilaclar:
        # Şikâyet anlatımı ("başım ağrıyor", "tansiyonum 160 normal mi"): mevcut akış (poliklinik önerisi,
        # "tıbbi yorum yapamam"). Yalnız ciddi belirti içermeyen yaşam tarzı bilgi sorusu yanıtlanır.
        neden = _spesifik_neden(n, ilaclar, ilac_baglami, alan)
        ciddi = _gecer(n, alan.SAGLIK_CIDDI_BELIRTILER)
        if neden == "tahlil" and not ciddi:
            return SaglikKarari("spesifik", False, neden)  # "kan değerlerim çıktı bakabilir misin"
        if soru and not neden and not ciddi and _gecer(n, alan.SAGLIK_YASAM_TARZI_SORULARI):
            return SaglikKarari("bilgi", False)
        if neden == "teshis" and not ciddi and not sikayet_poliklinikleri(n, alan):
            return SaglikKarari("spesifik", False, "teshis")  # "bende ne var": öneri yapılamıyor, sabit metin
        return None
    neden = _spesifik_neden(n, ilaclar, ilac_baglami, alan)
    if not neden and ilaclar and kisisel_sikayet and (_gecer(n, alan.SAGLIK_ILAC_IZIN)
                                                      or _gecer(n, ("iyi gelir", "iyi gelebilir", "işe yarar mı"))):
        # "Başım ağrıyor parol içsem iyi gelir mi": kendi şikâyeti için ilaç kararı → sabit yönlendirme.
        neden = "kisisel_ilac"
    if not neden and ilaclar and _gecer(n, alan.SAGLIK_ILAC_BEYAN):
        # "Metformin kullanıyorum alkol içebilir miyim": kişisel durumda izin sorusu → sabit metin.
        # "Lustral içiyorum" (beyan): bilgi sorusu kalıbı yoksa bu yol yanıtlamaz.
        if _gecer(n, alan.SAGLIK_ILAC_IZIN):
            neden = "kisisel_ilac"
        elif not _gecer(n, alan.SAGLIK_BILGI_SORU_KALIPLARI):
            return None
    if neden:
        return SaglikKarari("spesifik", bool(ilaclar), neden)
    if not soru:
        return None  # soru değil (beyan/şikâyet): mevcut akış
    return SaglikKarari("bilgi", bool(ilaclar))


def _poliklinik_adi_var(n: str, alan: ModuleType) -> bool:
    for p in alan.POLIKLINIKLER:
        ifadeler = [i for i in (p.ad, *p.es_anlamlilar) if i not in getattr(alan, "ORGAN_ES_ANLAMLILARI", ())]
        if any(ifade_gecer_mi(n, i) for i in ifadeler if len(normalize(i)) > 4 or " " in i):
            return True
    return False


def _spesifik_neden(n: str, ilaclar: list[str], ilac_baglami: bool, alan: ModuleType) -> str:
    if ilac_baglami and _gecer(n, alan.SAGLIK_SPESIFIK_DOZ):
        return "doz"
    if ilac_baglami and _gecer(n, alan.SAGLIK_SPESIFIK_BIRAKMA):
        return "birakma"
    if len(ilaclar) >= 2 and (_gecer(n, alan.SAGLIK_ILAC_BIRLIKTE) or _ile_var(n)):
        return "iki_ilac"
    if ilac_baglami and ifade_gecer_mi(n, "yerine"):
        return "ilac_onerisi"
    if _gecer(n, alan.SAGLIK_SPESIFIK_ONERI):
        return "ilac_onerisi"
    if re.search(r"\d", n) and (_gecer(n, alan.SAGLIK_SPESIFIK_TAHLIL)
                               or _gecer(n, ("tahlil", "değer", "sonuç", "sonuc", "çıktı", "ölçtüm", "seviye"))):
        return "tahlil"
    if _gecer(n, alan.SAGLIK_SPESIFIK_TAHLIL) and _gecer(n, ("tahlil", "değer", "sonuç", "sonuc", "rapor",
                                                             "emar", "röntgen", "ultrason", "seviye")):
        return "tahlil"
    if _gecer(n, alan.SAGLIK_SPESIFIK_TESHIS):
        return "teshis"
    if ilaclar and _gecer(n, alan.SAGLIK_KISISEL_DURUM) and _gecer(n, alan.SAGLIK_ILAC_IZIN):
        return "kisisel_ilac"
    return ""


def spesifik_metni(alan: ModuleType, neden: str) -> str:
    giris = alan.SAGLIK_SPESIFIK_GIRIS.get(neden, alan.SAGLIK_SPESIFIK_GIRIS["kisisel_ilac"])
    return f"{giris} {alan.SAGLIK_SPESIFIK_KAPANIS}"


# ---------------------------------------------------------------- çıktı denetimi

_BIRIM = (r"(mg|miligram|mililitre|ml|mcg|mikrogram|µg|gr|gram|g|ünite|unite|iu|tablet|kapsül|kapsul|draje|"
          r"damla|ölçek|olcek|kaşık|kasik|doz|puf|fitil|ampul|şase|sase|mmhg|mg/dl|mmol)")
_SAYI_KELIMESI = r"(bir|iki|üç|uc|dört|dort|beş|bes|altı|alti|yedi|sekiz|dokuz|on|yirmi|otuz|elli|yüz|yarım|yarim|çeyrek|ceyrek)"
_DOZ_DESENLERI = (
    re.compile(r"\d+([.,]\d+)?\s*-?\s*" + _BIRIM + r"(?!\w)", re.I),
    re.compile(r"(?<!\w)" + _SAYI_KELIMESI + r"\s+" + _BIRIM + r"(?!\w)", re.I),
    re.compile(r"(?<!\w)günde\s+(\d+|" + _SAYI_KELIMESI + r"|birkaç|kaç)\s+(kez|kere|defa|doz|tablet|kapsül)", re.I),
    re.compile(r"(?<!\w)(\d+|" + _SAYI_KELIMESI + r")\s+saatte\s+bir", re.I),
    re.compile(r"(?<!\w)\d+\s*[xX×]\s*\d+(?!\w)"),
    re.compile(r"\d+\s*/\s*\d+"),  # 140/90 gibi eşik değerleri
    # İstem rakam yasaklar: harfe bitişik olmayan her rakam ("2-2,5 litre", "3 ay") reddedilir. "B12", "D3"
    # gibi harfe bitişik adlar ve "112" (acil hattı) serbesttir.
    re.compile(r"(?<![\w])(?!112(?!\d))\d+(?![a-zA-ZçğıöşüÇĞİÖŞÜ])"),
)
# İlaç bağlamında emir kipi ("ilaçlarınızı kullanmaya devam edin", "antibiyotiği … kullanın").
_ILAC_EMRI = re.compile(r"(?<!\w)(devam edin|kullanin|alin|icin\.|icin$|baslayin|kesin\.|birakin|artirin|azaltin)(?!\w)")
# Teşhis / kişisel karar / ilaç başlatma-bırakma (ASCII katlı, kelime başından).
_TESHIS_DESENLERI = (
    r"(?<!\w)sizde \w+ (var|olabilir|olmus|olabilir mi)", r"(?<!\w)sizde (bir )?\w+ (hastaligi|rahatsizligi)",
    r"(?<!\w)muhtemelen", r"(?<!\w)buyuk ihtimalle", r"(?<!\w)buyuk olasilikla", r"(?<!\w)teshis(iniz| koy)",
    r"(?<!\w)taniniz", r"(?<!\w)hastaliginiz", r"(?<!\w)\w+ hastasisiniz", r"(?<!\w)hastasiniz",
)
_KARAR_DESENLERI = (
    r"(?<!\w)sizin icin (uygun|guvenli|sakincasiz|zararsiz|iyi)", r"(?<!\w)size uygun",
    r"(?<!\w)birakabilirsiniz", r"(?<!\w)birakin(?!\w)", r"(?<!\w)kesebilirsiniz", r"(?<!\w)birakmaniz",
    r"(?<!\w)(kullanmaya|almaya|icmeye) baslay", r"(?<!\w)baslayabilirsiniz", r"(?<!\w)dozu(nu)? (artir|azalt|degistir)",
    r"(?<!\w)(dozunuzu|dozu) (iki|yari)", r"(?<!\w)endise etmeyin", r"(?<!\w)endiselenmeyin",
    r"(?<!\w)tehlikeli degil", r"(?<!\w)hicbir (sakincasi|zarari|riski)",
    r"(?<!\w)guvenli(dir|\s+olur|\s+sayilir)", r"(?<!\w)sorun (olmaz|yoktur|yaratmaz|cikarmaz)",
    r"(?<!\w)zararsizdir", r"(?<!\w)(kullanmaniz|almaniz|icmeniz) (gerekir|gerekmez|onerilir|uygundur)",
)
# Değerlendirmede görülen, yanlışsa zarar verebilecek kesin iddialar ("zatürre bulaşıcı değildir", reflüde
# "yemeklerden sonra yatın"): reddedilir, sabit metin verilir.
_RISKLI_IDDIA_DESENLERI = (
    r"(?<!\w)bulasici degil", r"(?<!\w)bulasmaz(?!\w)", r"(?<!\w)bir kisiden digerine gecmez",
    r"(?<!\w)yemek\w* sonra (hemen )?yat(mayi(?!n)|in(?!\w)|abilir)",
)
# Modelin soru hakkında yorum ya da not eklemesi (konuyu karıştırdığının işareti).
_META_DESENLERI = (
    r"(?<!\w)not:", r"(?<!\w)bu soru", r"(?<!\w)bu yanit", r"(?<!\w)kelimesi(?!\w)", r"(?<!\w)ifadesi(?!\w)",
    r"(?<!\w)yapay zeka", r"(?<!\w)dil modeli",
)
# İlaç bağlamında izin veren ikinci şahıs fiil ("Metforminle araba kullanabilirsiniz", "ilacı içebilirsiniz").
_IZIN_FIILI = re.compile(r"(?<!\w)\w*(abilirsiniz|ebilirsiniz)(?!\w)")
_ILAC_KELIMESI = re.compile(r"(?<!\w)(ilac|hap|tablet|surup|kapsul|antibiyotik|agri kesici)")
_YABANCI_HARF = re.compile(r"[^\w\s.,;:!?'’‘“”\"%\-–—/()…]|[Ͱ-ϿЀ-ӿ֐-ۿ฀-๿"
                           r"぀-ヿ㐀-鿿가-힯]")
_INGILIZCE = re.compile(r"(?<![\w'])(the|and|is|are|of|you|your|with|for|this|that|should|may|can|please|"
                        r"doctor|consult)(?![\w'])", re.I)
_TURKCE_IPUCU = re.compile(r"(?<!\w)(ve|bir|bu|ile|için|olarak|da|de|gibi|daha|veya|ya|ancak|çok|olan|en|ise)"
                           r"(?!\w)|[çğışöüÇĞİŞÖÜ]", re.I)
_MADDE = re.compile(r"(^|\n)\s*([-*•●▪]|\d+[.)]|#+)\s+")
_KAPANIS_CUMLESI = re.compile(r"(doktor|hekim|eczac|saglik (uzman|profesyonel|kurulus))\w*\s.*danis", re.I)
# Tavsiye yanıtında "sürerse doktora başvurun" satırı koddan eklenir: modelin kendi başvuru/danışma cümlesi atılır.
_TAVSIYE_KAPANIS_CUMLESI = re.compile(
    r"(doktor|hekim|eczac|saglik (uzman|profesyonel|kurulus))\w*\s.*(danis|basvur|gorun|gidin|gitmek|gitmeniz)", re.I)
# Tavsiye (ev yöntemi) yanıtında kesin tedavi iddiası: "zencefil bulantıyı geçirir", "kesinlikle iyi gelir".
_TAVSIYE_RISKLI_DESENLER = (
    r"(?<!\w)tedavi eder", r"(?<!\w)tedavi edici", r"(?<!\w)iyilestirir(?!\w)", r"(?<!\w)sifa",
    r"(?<!\w)kesin(likle)? (iyi gelir|ise yarar|gecer|gecirir|cozum)", r"(?<!\w)mucize",
    r"(?<!\w)\w+ (bulantiyi|agriyi|oksurugu|ishali|atesi) gecirir(?!\w)", r"(?<!\w)ilac yerine (gecer|kullan)",
    r"(?<!\w)doktora gitmenize gerek (yok|kalmaz)", r"(?<!\w)doktora gerek (yok|kalmaz)",
)


def _cumlelere_bol(metin: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<=[.!?])\s+", metin) if c.strip()]


def yanit_denetle(ham: str, soru: str, alan: ModuleType, *, azami_karakter: int | None = None,
                  tavsiye: bool = False, azami_cumle: int | None = None) -> tuple[str | None, str]:
    """LLM metnini kullanıcıya okunabilir hale getirir ya da reddeder. Dönüş: (metin ya da None, neden kodu).

    Ret: boş/bozuk, Türkçe dışı karakter ya da dil, madde işareti/markdown liste, doz/miligram/sayı+birim ve
    sıklık kalıpları, teşhis ve kişisel karar kalıpları, ilaç bağlamında izin veren "-ebilirsiniz", soruda
    geçmeyen ilaç adı, maske sızıntısı, uzunluk sınırı. Temizlik: kod bloğu/kalın yazı işaretleri, satır sonları,
    tırnaklar; modelin kendi "doktorunuza danışın" cümlesi (kapanış koddan eklendiği için) atılır."""
    if not isinstance(ham, str):
        return None, "bos"
    metin = ham.replace("\r", "\n").strip().strip("\"'“”‘’`")
    if not metin:
        return None, "bos"
    if _MADDE.search(metin):
        return None, "liste"
    metin = re.sub(r"[*_`#]+", "", metin)
    metin = " ".join(metin.split())
    if "[" in metin or "]" in metin or "{" in metin or "<" in metin:
        return None, "maske_ya_da_bicim"
    if _YABANCI_HARF.search(metin):
        return None, "yabanci_karakter"
    if len(_INGILIZCE.findall(metin)) >= 2 or not _TURKCE_IPUCU.search(metin):
        return None, "dil"
    if metin.count("(") > 2:
        return None, "parantez"
    # Tek parantez: içeriği virgülle cümleye katılır (sesli okumada parantez yok).
    metin = re.sub(r"\s*\(([^()]*)\)", r", \1,", metin).replace(",,", ",").replace(", .", ".").replace(",.", ".")
    metin = metin.replace("(", "").replace(")", "")
    if any(d.search(metin) for d in _DOZ_DESENLERI):
        return None, "doz"
    katli = ascii_katla(metin)
    if any(re.search(d, katli) for d in _TESHIS_DESENLERI):
        return None, "teshis"
    if any(re.search(d, katli) for d in _KARAR_DESENLERI):
        return None, "kisisel_karar"
    if any(re.search(d, katli) for d in _META_DESENLERI):
        return None, "meta"
    if any(re.search(d, katli) for d in _RISKLI_IDDIA_DESENLERI):
        return None, "riskli_iddia"
    if tavsiye and any(re.search(d, katli) for d in _TAVSIYE_RISKLI_DESENLER):
        return None, "riskli_iddia"
    soru_n = normalize(soru)
    soru_ilaclari = ilac_adlari(soru_n)
    for cumle in _cumlelere_bol(katli):
        ilacli = _ILAC_KELIMESI.search(cumle) or ilac_adlari(normalize(cumle))
        if ilacli and _IZIN_FIILI.search(cumle):
            return None, "izin"
        if ilacli and _ILAC_EMRI.search(cumle) and "eczaci" not in cumle and "doktor" not in cumle \
                and "hekim" not in cumle:
            return None, "ilac_emri"
    yanit_ilaclari = [a for a in ilac_adlari(normalize(metin)) if a != "dagarcik"]
    yabanci_ilac = [a for a in yanit_ilaclari
                    if not any(_ad_eslesir_mi(a, b) or _ad_eslesir_mi(b, a) or a.startswith(b[:5]) or b.startswith(a[:5])
                               for b in soru_ilaclari)]
    if yabanci_ilac:
        return None, "baska_ilac"
    cumleler = _cumlelere_bol(metin)
    # Modelin kendi danışma kapanışı atılır (kod sabit kapanışı ekler); başka cümle kalmıyorsa ret.
    kapanis = _TAVSIYE_KAPANIS_CUMLESI if tavsiye else _KAPANIS_CUMLESI
    cumleler = [c for c in cumleler if not (kapanis.search(ascii_katla(c)) and len(c) < 120
                                            and "112" not in c and "acil" not in ascii_katla(c))]
    if tavsiye and "iyi gelir mi" not in ascii_katla(soru):
        # Ev yöntemi sorulmadı: modelin istemden kopyaladığı "…tedavi yerine geçmez" cümlesi konusuzdur, atılır.
        cumleler = [c for c in cumleler if not re.search(r"tedavi (yerine gecmez|degildir)|rahatlatici bul",
                                                         ascii_katla(c))]
    # Kesik çıktı (azami token): yalnız tam cümleler.
    cumleler = [c for c in cumleler if c[-1] in ".!?"]
    if not cumleler:
        return None, "kesik"
    # Sesli okunacak: en çok N cümle ve karakter sınırı; fazlası baştan sona sırayla kırpılır. 112/acil
    # uyarısı taşıyan cümle kırpılamaz (sığmıyorsa yanıt reddedilir).
    azami = min(azami_karakter or alan.SAGLIK_BILGI_AZAMI_KARAKTER, alan.SAGLIK_BILGI_AZAMI_KARAKTER)
    if azami_cumle is None and tavsiye:
        azami_cumle = getattr(alan, "TAVSIYE_AZAMI_CUMLE", None)
    azami_cumle = min(azami_cumle or alan.SAGLIK_BILGI_AZAMI_CUMLE, alan.SAGLIK_BILGI_AZAMI_CUMLE)
    secilen: list[str] = []
    for c in cumleler:
        aday = " ".join([*secilen, c])
        if len(secilen) < azami_cumle and len(aday) <= azami:
            secilen.append(c)
        elif _acil_cumlesi(c):
            # Uyarı cümlesi için sondaki sıradan cümle(ler) çıkarılır.
            while secilen and (len(" ".join([*secilen, c])) > azami
                               or len(secilen) >= azami_cumle):
                if _acil_cumlesi(secilen[-1]):
                    return None, "uzun"
                secilen.pop()
            secilen.append(c)
    if not secilen or len(" ".join(secilen)) > azami:
        return None, "uzun"
    metin = " ".join(secilen)
    if len(metin) < 25:
        return None, "kisa"
    return metin, ""


def _acil_cumlesi(cumle: str) -> bool:
    return "112" in cumle or "acil" in ascii_katla(cumle)


# ---------------------------------------------------------------- üretim

@dataclass(frozen=True)
class BilgiSonucu:
    metin: str | None        # denetimden geçmiş LLM metni (kapanışsız); None → sabit "veremiyorum" metni
    neden: str               # "" | llm_yok | yuk_yok | zaman_asimi | llm_hatasi | <denetim nedeni>
    sure_ms: float = 0.0


async def genel_bilgi_uret(soru: str, *, alan: ModuleType, llm: LLMSaglayici | None,
                           zaman_asimi_sn: float, azami_token: int = 220, yuk: LLMYuku | None = None,
                           azami_karakter: int | None = None, tavsiye: bool = False) -> BilgiSonucu:
    """Yerel LLM'den kısa genel bilgi metni. Asla istisna fırlatmaz (asistan sessiz kalmamalı).
    ``yuk`` verilirse (tavsiye yolu: koddan kurulmuş konu + soru) ``soru`` yerine o gönderilir."""
    if llm is None:
        return BilgiSonucu(None, "llm_yok")
    if yuk is None:
        yuk = saglik_bilgi_yuku_kur(soru, alan=alan)
    if yuk is None:
        return BilgiSonucu(None, "yuk_yok")
    baslangic = time.perf_counter()
    try:
        cagri = llm.metin_uret(list(yuk.mesajlar), sistem=yuk.sistem, sicaklik=0.2, azami_token=azami_token,
                               zaman_asimi_sn=zaman_asimi_sn)
        ham = await asyncio.wait_for(cagri, zaman_asimi_sn)
    except TimeoutError:
        log.warning("genel sağlık bilgisi: zaman aşımı (%.1f sn)", zaman_asimi_sn)
        return BilgiSonucu(None, "zaman_asimi", (time.perf_counter() - baslangic) * 1000)
    except LLMHatasi as hata:
        kod = "zaman_asimi" if hata.kod == "LLM_ZAMAN_ASIMI" else "llm_hatasi"
        log.warning("genel sağlık bilgisi: LLM hatası %s", hata.kod)
        return BilgiSonucu(None, kod, (time.perf_counter() - baslangic) * 1000)
    except Exception as hata:  # noqa: BLE001 — sağlayıcı ne fırlatırsa fırlatsın asistan yanıt vermeli
        log.warning("genel sağlık bilgisi: beklenmeyen hata %s", type(hata).__name__)
        return BilgiSonucu(None, "llm_hatasi", (time.perf_counter() - baslangic) * 1000)
    sure = (time.perf_counter() - baslangic) * 1000
    metin, neden = yanit_denetle(ham, yuk.mesajlar[0].icerik, alan, azami_karakter=azami_karakter,
                                 tavsiye=tavsiye)
    if metin is None:
        log.info("genel sağlık bilgisi: yanıt denetimden geçmedi (%s)", neden)
    return BilgiSonucu(metin, neden, sure)


def yanit_birlestir(alan: ModuleType, llm_metni: str, ara: str = "") -> str:
    """LLM metni + (isteğe bağlı koddan ek, ör. hekim talimatı) + SABİT kapanış."""
    parcalar = [llm_metni.strip()]
    if ara:
        aday = " ".join([*parcalar, ara, alan.SAGLIK_BILGI_KAPANIS])
        if len(aday) <= alan.SAGLIK_BILGI_AZAMI_SESLI:
            parcalar.append(ara)
    parcalar.append(alan.SAGLIK_BILGI_KAPANIS)
    return " ".join(parcalar)

