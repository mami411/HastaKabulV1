"""Bilgi tabanı (RAG kaynağı): kayıt tipi, gömülü kopya ve backend'den yükleme.

Kaynak sırası:
1. Backend ``GET /api/v1/kb`` (X-Service-Key). Kayıt kimlikleri backend'in ``kb_entries.id``
   Guid'leridir; callback'teki ``sourceIds`` böylece backend'de başlığa çözülür.
2. Uç yoksa (404), erişilemezse ya da yanıt bozuksa: bu dosyadaki gömülü kopya
   (``DemoDataSeeder.SeedKnowledgeBase`` ile aynı 16 kayıt, doğru Türkçe karakterlerle).
   Gömülü kimlikler ``gomulu:<terim>`` biçimindedir; backend bunları başlığa çeviremez,
   ekranda kimliğin kendisi görünür.

Backend seed metinleri Türkçe karaktersizdir ("Gunde bir kez, kahvaltidan..."). Backend'den
gelen bir kaydın gövdesi gömülü kopyayla ASCII'ye katlanınca birebir aynıysa gövde, başlık ve
eş anlamlılar gömülü (doğru karakterli) metinle değiştirilir; içerik farklıysa backend metni
olduğu gibi kullanılır ve sade şablon metinleri devre dışı kalır (yalnız LLM ya da genel şablon).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any

from ortak.turkce import normalize

from .backend import BackendHatasi

log = logging.getLogger("sade_servisi.kb")

TANI = "DIAGNOSIS"
ILAC = "MEDICATION"

# Bir ilaç ya da hastalık sınıfını anlatan, tek bir kaydı belirlemeyen eş anlamlılar.
# Bunlarla eşleşme YAPILMAZ: "antibiyotik" yazan hekime amoksisilin açıklaması, "anemi"
# yazana demir eksikliği açıklaması, "tiroid" yazana hipotiroidi açıklaması uydurulmaz.
# Terim bilinmeyenlere (unknownTerms) düşer ve hekim görür.
ZAYIF_ES_ANLAMLILAR = frozenset(normalize(s) for s in (
    "tansiyon", "diyabet", "anemi", "tiroid", "antibiyotik", "ağrı kesici", "demir ilacı",
    "proton pompa inhibitörü", "mide şikayeti",
))


@dataclass(frozen=True)
class SadeMetin:
    """Gömülü kayıtlar için önceden yazılmış, KB gövdesine dayanan sabit hasta metinleri.

    LLM yokken ya da LLM çıktısı denetimden geçmezken şablon özette kullanılır.
    Yalnız KB gövdesindeki bilgiyi ikinci çoğul şahısla yeniden söyler; yeni bilgi eklemez.
    """
    aciklama: str = ""                 # tanı için: 2-3 kısa cümle
    amac: str = ""                     # ilaç için: ne işe yarar
    kullanim: str = ""                 # ilaç için: hekim kullanım yazmadıysa KB'deki genel kullanım
    zamanlama: str = ""                # ilaç için: sıklık içermeyen zamanlama ("Aç karnına alın.")
    kb_siklik: int | None = None       # KB gövdesindeki günlük sıklık (yoksa None)
    uyari: str = ""                    # ilaca özgü onaylı uyarı (varsa)


@dataclass(frozen=True)
class KbKaydi:
    id: str
    terim: str
    baslik: str
    govde: str
    tur: str                                  # DIAGNOSIS | MEDICATION
    es_anlamlilar: tuple[str, ...] = ()
    sade: SadeMetin | None = None             # yalnız içeriği gömülü kopyayla aynı kayıtlarda
    ek_es_anlamlilar: tuple[str, ...] = ()    # servis tarafı: uluslararası yazım / kesin marka adları

    @property
    def tani_mi(self) -> bool:
        return self.tur == TANI

    def eslesme_ifadeleri(self) -> tuple[str, ...]:
        """Eşleşmede kullanılacak normalize ifadeler (zayıf eş anlamlılar hariç)."""
        ifadeler = [self.terim, *self.es_anlamlilar, *self.ek_es_anlamlilar]
        sonuc: list[str] = []
        for ifade in ifadeler:
            n = normalize(ifade)
            if n and n not in ZAYIF_ES_ANLAMLILAR and n not in sonuc:
                sonuc.append(n)
        return tuple(sonuc)

    def kaynak_metni(self) -> str:
        """Doz/sayı denetiminde 'izinli kaynak' olarak kullanılan metin."""
        return " . ".join([self.terim, self.baslik, self.govde, *self.es_anlamlilar])


def _k(terim: str, baslik: str, govde: str, tur: str, es: tuple[str, ...] = (), *,
       sade: SadeMetin, ek: tuple[str, ...] = ()) -> KbKaydi:
    return KbKaydi(id=f"gomulu:{normalize(terim).replace(' ', '-')}", terim=terim, baslik=baslik, govde=govde,
                   tur=tur, es_anlamlilar=es, sade=sade, ek_es_anlamlilar=ek)


# --------------------------------------------------------------------------
# Gömülü kopya: DemoDataSeeder.SeedKnowledgeBase (16 kayıt), Türkçe karakterleriyle.
# Gövdeler ASCII'ye katlanınca seed metniyle birebir aynıdır (testle güvence altında).
# ``ek`` alanı seed'de yoktur: yalnız aynı etken maddenin uluslararası yazımı ve
# etken maddesi kesin bilinen tek etkenli marka adları.
# --------------------------------------------------------------------------
GOMULU_KB: tuple[KbKaydi, ...] = (
    _k("Dispepsi", "Dispepsi (hazımsızlık)",
       "Midede yanma, şişme ve erken doyma ile giden hazımsızlık tablosu. Genellikle mide asidinin fazla "
       "üretilmesine bağlıdır.", TANI, ("hazımsızlık", "mide şikayeti"),
       sade=SadeMetin(aciklama="Midenizde yanma, şişkinlik ve çabuk doyma gibi şikayetler var. Buna hazımsızlık "
                               "denir. Çoğu zaman midenin fazla asit üretmesinden kaynaklanır.")),
    _k("Lansoprazol", "Lansoprazol",
       "Mide asidi üretimini azaltan ilaç. Günde bir kez, kahvaltıdan yarım saat önce aç karnına alınır.",
       ILAC, ("lansor", "proton pompa inhibitörü"), ek=("lansoprazole",),
       sade=SadeMetin(amac="Midenizin ürettiği asidi azaltır.",
                      kullanim="Günde bir kez, kahvaltıdan yarım saat önce aç karnına alın.",
                      zamanlama="Kahvaltıdan yarım saat önce, aç karnına alın.", kb_siklik=1)),
    _k("Gastrit", "Gastrit (mide iltihabı)",
       "Mide iç yüzeyinin tahriş olması. Acılı veya yanma hissi veren şikayetlere yol açar.", TANI,
       ("mide iltihabı",),
       sade=SadeMetin(aciklama="Midenizin iç yüzeyi tahriş olmuş. Buna gastrit, yani mide iltihabı denir. "
                               "Midenizde ağrı ya da yanma hissine yol açabilir.")),
    _k("Famotidin", "Famotidin",
       "Mide asidini azaltan ilaç. Genellikle akşam yemeğinden sonra alınır.", ILAC, (), ek=("famotidine",),
       sade=SadeMetin(amac="Midenizin ürettiği asidi azaltır.",
                      kullanim="Genellikle akşam yemeğinden sonra alın.",
                      zamanlama="Genellikle akşam yemeğinden sonra alın.", kb_siklik=1)),
    _k("Hipertansiyon", "Hipertansiyon (yüksek tansiyon)",
       "Kan basıncının sürekli yüksek seyretmesi. Tuz kısıtlaması ve düzenli ilaç kullanımı gerektirir.", TANI,
       ("yüksek tansiyon", "tansiyon"), ek=("hipertansif",),
       sade=SadeMetin(aciklama="Kan basıncınız, yani tansiyonunuz sürekli yüksek seyrediyor. Buna yüksek tansiyon "
                               "denir. Yemeklerde tuzu azaltmanız ve ilacınızı düzenli kullanmanız gerekir.")),
    _k("Amlodipin", "Amlodipin",
       "Damarları gevşeterek tansiyonu düşüren ilaç. Günde bir kez, her gün aynı saatte alınır.", ILAC, (),
       ek=("amlodipine", "norvasc"),
       sade=SadeMetin(amac="Damarlarınızı gevşeterek tansiyonunuzu düşürür.",
                      kullanim="Günde bir kez, her gün aynı saatte alın.",
                      zamanlama="Her gün aynı saatte alın.", kb_siklik=1)),
    _k("Tip 2 Diyabet", "Tip 2 diyabet (şeker hastalığı)",
       "Vücudun insülini yeterince kullanamaması. Beslenme düzeni ve ilaç ile kontrol altına alınır.", TANI,
       ("şeker hastalığı", "diyabet"), ek=("tip 2 diabetes", "tip ii diyabet", "diabetes mellitus tip 2"),
       sade=SadeMetin(aciklama="Vücudunuz insülin hormonunu yeterince kullanamıyor. Buna tip 2 diyabet, yani "
                               "şeker hastalığı denir. Beslenme düzeniniz ve ilacınızla kontrol altına alınır.")),
    _k("Metformin", "Metformin",
       "Kan şekerini dengeleyen ilaç. Mide şikayetini azaltmak için yemekle birlikte alınır.", ILAC, (),
       ek=("glucophage",),
       sade=SadeMetin(amac="Kan şekerinizi dengeler.",
                      kullanim="Midenizi rahatsız etmemesi için yemekle birlikte alın.",
                      zamanlama="Midenizi rahatsız etmemesi için yemekle birlikte alın.")),
    _k("Demir Eksikliği Anemisi", "Demir eksikliği anemisi (kansızlık)",
       "Kandaki demir deposunun azalması. Halsizlik ve solukluk yapar.", TANI, ("kansızlık", "anemi"),
       ek=("demir eksikliği", "demir eksikliğine bağlı anemi"),
       sade=SadeMetin(aciklama="Kanınızdaki demir deposu azalmış. Buna demir eksikliğine bağlı kansızlık denir. "
                               "Halsizlik ve solukluk yapabilir.")),
    _k("Demir Sülfat", "Demir sülfat",
       "Demir deposunu dolduran ilaç. C vitamini içeren meyve suyuyla, süt ve çay olmadan alınır.", ILAC,
       ("demir ilacı",), ek=("ferröz sülfat", "ferrous sulfate"),
       sade=SadeMetin(amac="Vücudunuzun azalan demir deposunu doldurur.",
                      kullanim="C vitamini içeren bir meyve suyuyla alın. Süt ve çayla birlikte almayın.",
                      zamanlama="C vitamini içeren bir meyve suyuyla alın. Süt ve çayla birlikte almayın.")),
    _k("Migren", "Migren",
       "Zonklayıcı, genellikle tek taraflı baş ağrısı. Işık ve sese hassasiyet eşlik edebilir.", TANI, (),
       sade=SadeMetin(aciklama="Başınızın genellikle bir tarafında zonklayan bir ağrı oluyor. Buna migren denir. "
                               "Ağrı sırasında ışık ve ses sizi rahatsız edebilir.")),
    _k("Parasetamol", "Parasetamol",
       "Ağrı ve ateş düşürücü ilaç. Günde en fazla belirtilen dozda ve tok karnına alınır.", ILAC,
       ("ağrı kesici",), ek=("paracetamol", "asetaminofen", "acetaminophen", "parol"),
       sade=SadeMetin(amac="Ağrınızı azaltır ve ateşinizi düşürür.",
                      kullanim="Tok karnına alın. Günde, belirtilen dozdan fazla almayın.",
                      zamanlama="Tok karnına alın.",
                      uyari="Ağrı kesicinizi günde, hekiminizin belirttiği dozdan fazla almayın.")),
    _k("Akut Sinüzit", "Akut sinüzit",
       "Burun çevresindeki sinüslerin iltihaplanması. Yüzde basınç hissi ve burun tıkanıklığı yapar.", TANI,
       ("sinüzit",),
       sade=SadeMetin(aciklama="Burnunuzun çevresindeki boşluklar, yani sinüsler iltihaplanmış. Buna akut sinüzit "
                               "denir. Yüzünüzde basınç hissi ve burun tıkanıklığı yapabilir.")),
    _k("Amoksisilin", "Amoksisilin",
       "Bakteri kaynaklı enfeksiyonlarda kullanılan antibiyotik. Kur yarıda bırakılmaz.", ILAC,
       ("antibiyotik",), ek=("amoxicillin", "amoksisilin trihidrat"),
       sade=SadeMetin(amac="Bakterilerin yol açtığı enfeksiyonu tedavi eden bir antibiyotiktir.",
                      kullanim="Hekiminizin söylediği süre boyunca kullanın. Şikayetleriniz geçse bile yarıda "
                               "bırakmayın.",
                      zamanlama="Şikayetleriniz geçse bile yarıda bırakmayın.",
                      uyari="Antibiyotiğinizi şikayetleriniz geçse bile yarıda bırakmayın.")),
    _k("Hipotiroidi", "Hipotiroidi (tiroid bezinin az çalışması)",
       "Tiroid hormonunun yetersiz üretilmesi. Halsizlik, üşüme ve kilo artışı yapabilir.", TANI, ("tiroid",),
       ek=("hipotiroidizm",),
       sade=SadeMetin(aciklama="Tiroid bezinizin ürettiği hormon yetersiz. Buna hipotiroidi, yani tiroid bezinin "
                               "az çalışması denir. Halsizlik, üşüme ve kilo artışı yapabilir.")),
    _k("Levotiroksin", "Levotiroksin",
       "Eksik tiroid hormonunu yerine koyan ilaç. Sabah aç karnına, bir bardak su ile alınır.", ILAC, (),
       ek=("levothyroxine", "levotiroksin sodyum", "euthyrox", "levotiron"),
       sade=SadeMetin(amac="Vücudunuzda eksik olan tiroid hormonunu yerine koyar.",
                      kullanim="Sabah aç karnına, bir bardak su ile alın.",
                      zamanlama="Sabah aç karnına, bir bardak su ile alın.", kb_siklik=1)),
)


def _gomulu_eslesen(terim: str) -> KbKaydi | None:
    n = normalize(terim)
    return next((k for k in GOMULU_KB if normalize(k.terim) == n), None)


def backend_kaydini_hazirla(ham: dict[str, Any]) -> KbKaydi | None:
    """Backend'in ``{id, term, title, body, kind, synonyms}`` kaydını içe alır; bozuksa None."""
    try:
        kid = str(ham["id"]).strip()
        terim = str(ham["term"]).strip()
        baslik = str(ham.get("title") or terim).strip()
        govde = str(ham["body"]).strip()
        tur_ham = ham.get("kind")
        es = ham.get("synonyms") or []
    except (KeyError, TypeError):
        return None
    if isinstance(tur_ham, int):
        tur = {0: TANI, 1: ILAC}.get(tur_ham, "")
    else:
        tur = str(tur_ham or "").strip().upper()
        tur = {"DIAGNOSIS": TANI, "MEDICATION": ILAC}.get(tur, "")
    if not (kid and terim and govde and tur) or not isinstance(es, list) or len(kid) > 64:
        return None
    es_anlamlilar = tuple(str(s).strip() for s in es if isinstance(s, str) and s.strip())
    kayit = KbKaydi(id=kid, terim=terim, baslik=baslik, govde=govde, tur=tur, es_anlamlilar=es_anlamlilar)
    gomulu = _gomulu_eslesen(terim)
    if gomulu is not None and gomulu.tur == tur:
        # Ek eş anlamlılar (marka/uluslararası yazım) yalnız etken madde aynıysa taşınır.
        kayit = replace(kayit, ek_es_anlamlilar=gomulu.ek_es_anlamlilar)
        if normalize(govde) == normalize(gomulu.govde):
            # Aynı içerik, yalnız Türkçe karakter farkı: doğru yazımlı metni ve sade şablonu kullan.
            es_onarilmis = tuple(
                next((g for g in gomulu.es_anlamlilar if normalize(g) == normalize(s)), s) for s in es_anlamlilar)
            kayit = replace(kayit, terim=gomulu.terim,
                            baslik=gomulu.baslik if normalize(baslik) == normalize(gomulu.baslik) else baslik,
                            govde=gomulu.govde, es_anlamlilar=es_onarilmis, sade=gomulu.sade)
    return kayit


@dataclass
class KbDurumu:
    kayitlar: tuple[KbKaydi, ...]
    kaynak: str                       # "api" | "gomulu"
    yuklenme_zamani: float = field(default_factory=time.monotonic)
    son_hata: str | None = None       # yalnız hata türü/kodu; içerik yok


class BilgiTabani:
    """KB önbelleği: backend'den yükler, olmazsa gömülü kopyaya düşer; süre dolunca tazeler."""

    def __init__(self, getirici: Any | None, *, kip: str = "auto", yenileme_sn: float = 600.0,
                 hata_sonrasi_yenileme_sn: float = 60.0):
        # getirici: ``async () -> list[dict]`` (BackendIstemcisi.kb_getir); 404/ağ hatasında istisna atar.
        self._getirici = getirici
        self.kip = kip
        self.yenileme_sn = yenileme_sn
        self.hata_sonrasi_yenileme_sn = hata_sonrasi_yenileme_sn
        self.durum = KbDurumu(GOMULU_KB, "gomulu", yuklenme_zamani=-1e18)
        self._ilk = True

    def _taze_mi(self) -> bool:
        if self._ilk:
            return False
        sure = self.yenileme_sn if self.durum.kaynak == "api" else self.hata_sonrasi_yenileme_sn
        return time.monotonic() - self.durum.yuklenme_zamani < sure

    async def kayitlar(self) -> KbDurumu:
        if self.kip == "gomulu" or self._getirici is None:
            self._ilk = False
            return self.durum
        if self._taze_mi():
            return self.durum
        self._ilk = False
        try:
            ham = await self._getirici()
            if not isinstance(ham, list):
                raise ValueError("liste değil")
            kayitlar = tuple(k for k in (backend_kaydini_hazirla(h) for h in ham if isinstance(h, dict)) if k)
            if not kayitlar:
                raise ValueError("boş KB")
            self.durum = KbDurumu(kayitlar, "api")
            log.info("KB backend'den yüklendi kayit=%d", len(kayitlar))
        except Exception as exc:  # noqa: BLE001 — her hata gömülü kopyaya düşer
            neden = str(exc) if isinstance(exc, (ValueError, BackendHatasi)) else type(exc).__name__
            if self.durum.kaynak == "api" and self.kip == "auto":
                # Daha önce backend'den yüklenmiş KB eldeyse onu korur (kimlikler tutarlı kalsın).
                self.durum.yuklenme_zamani = time.monotonic()
                self.durum.son_hata = neden
                log.warning("KB tazelenemedi, önceki backend kopyası kullanılıyor neden=%s", neden)
            else:
                self.durum = KbDurumu(GOMULU_KB, "gomulu", son_hata=neden)
                log.warning("KB backend'den alınamadı, gömülü kopya kullanılıyor neden=%s", neden)
        return self.durum
