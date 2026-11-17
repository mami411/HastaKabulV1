"""Konu modülü: mobil cep sağlık asistanında sesli randevu ve geçmiş muayene soruları.

(İlk sürüm hastane lobisindeki kiosk içindi; şablonlar kişisel cihazdaki uygulama bağlamına çevrildi:
"danışma bankosu", "görevli", "kiosk ekranı" gibi ifadeler yerine "uygulama" ve "hastaneyi arayın".)
Özel hastane sürümü (MedTech): kurum adı, asistan adı, çağrı merkezi, adres ve çalışma saatleri
``ortak.kurum`` (``hasta-kabul-ai/kurum.json``) yapılandırmasından gelir; hata şablonları çağrı merkezine
yönlendirir. Başka bir kurum için bkz. kökteki KURUM.md.

Konuya özgü HER ŞEY burada durur (halisaha raporundaki "konu modülü" önerisi):
- kimlik/üslup ve kesin kurallar (LLM istemi)
- LLM çıktı şeması ve araç şemaları
- poliklinik listesi ve eş anlamlılar
- yasak ifade listeleri ve yedek cümleler (güvenlik filtresi)
- sabit cümleler (selamlama, hata mesajları)
- backend iş-mantığı hata kodları
- STT anahtar terimleri ve hayalet transkript kalıpları

Çekirdek (``orkestrator.py``) bu modülü parametre olarak alır; yeni bir konu için
yeni bir modül yazılır, çekirdek değişmez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ortak.guvenlik import Kosul, YasakKurali
from ortak.kurum import KURUM, telefon_okunusu
from ortak.turkce import rakamlari_tek_tek_oku, saatleri_oku, sayi_oku

KONU_ADI = "hasta_kabul"


# --------------------------------------------------------------------------
# Poliklinikler (backend demo kataloğu: hasta-kabul-backend/.../Seed/DemoKatalog.cs ile AYNI kodlar)
# Kod, backend'in /slots?clinic= parametresine aynen gider. Sıra: ekrandaki seçenek sırası ve ilk eşleşme.
# Eş anlamlılara halk arasındaki adlar ("cildiye", "kadın doğum", "çocuk doktoru") ve konuşma tanımanın
# bilinen yazımları ("kabebe", "ka be be", "dermatalog", "neroloji") eklidir; hepsi normalize edilmiş
# metinde kelime başından önek eşleşmesiyle aranır ("kbbye", "cildiyeye" de tutar).
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Poliklinik:
    kod: str
    ad: str
    es_anlamlilar: tuple[str, ...]


POLIKLINIKLER: tuple[Poliklinik, ...] = (
    Poliklinik("DAHILIYE", "Dahiliye",
               ("dahiliye", "iç hastalıkları", "iç hastalık", "dahiliyeci", "iç hastalıklar")),
    Poliklinik("KARDIYOLOJI", "Kardiyoloji",
               ("kardiyoloji", "kardiyolog", "kalp polikliniği", "kalp doktoru", "kalp damar",
                "kalp bölümü")),
    Poliklinik("ORTOPEDI", "Ortopedi",
               ("ortopedi", "ortoped", "kemik polikliniği", "kemik doktoru", "travmatoloji")),
    Poliklinik("GOZ", "Göz Hastalıkları",
               ("göz hastalıkları", "göz polikliniği", "göz doktoru", "göz bölümü", "göz")),
    Poliklinik("KBB", "Kulak Burun Boğaz",
               ("kulak burun boğaz", "kbb", "k b b", "kabebe", "ka be be", "kulak burun", "kulak doktoru",
                "kulakçı", "kulak polikliniği", "kbb polikliniği")),
    Poliklinik("DERMATOLOJI", "Dermatoloji",
               ("dermatoloji", "dermatolog", "dermatoloğ", "dermotoloji", "dermatalog", "cildiye", "cildiyeci",
                "cilt doktoru", "cilt hastalıkları", "cilt polikliniği", "cilt bölümü")),
    Poliklinik("KADIN_DOGUM", "Kadın Hastalıkları ve Doğum",
               ("kadın hastalıkları ve doğum", "kadın doğum", "kadındoğum", "kadın hastalıkları", "jinekoloji",
                "jinekolog", "jinekoloğ", "kadın doktoru", "kadın doğumcu", "doğumcu")),
    Poliklinik("COCUK_SAGLIGI", "Çocuk Sağlığı ve Hastalıkları",
               ("çocuk sağlığı", "çocuk hastalıkları", "çocuk doktoru", "çocuk doktor", "çocuk polikliniği",
                "çocuk bölümü", "pediatri", "pediatrist", "pedyatri")),
    Poliklinik("NOROLOJI", "Nöroloji",
               ("nöroloji", "nörolog", "nöroloğ", "neroloji", "nöroloji polikliniği", "sinir hastalıkları",
                "sinir doktoru")),
    Poliklinik("UROLOJI", "Üroloji",
               ("üroloji", "ürolog", "üroloğ", "üroloji polikliniği", "idrar yolları")),
    Poliklinik("GENEL_CERRAHI", "Genel Cerrahi",
               ("genel cerrahi", "genel cerrah", "cerrahi polikliniği", "cerrahiye", "cerraha", "cerrahi")),
)

POLIKLINIK_KODLARI = tuple(p.kod for p in POLIKLINIKLER)

# Tek başına organ adı olan eş anlamlılar. Cümlede bir şikâyet kalıbı varsa ("göz kızarıklığı", "gözüm
# bulanık") bunlar poliklinik SEÇİMİ sayılmaz; şikâyetten öneri yapılır ve teyit istenir. Şikâyet yoksa
# ("göze yarın") eskisi gibi poliklinik adıdır.
ORGAN_ES_ANLAMLILARI = frozenset({"göz"})

# Cümlenin TAMAMI bu kelimeyse (en çok "olsun/lütfen" ekiyle) poliklinik sayılır; "kalbim ağrıyor" sayılmaz.
# Anahtarlar normalize (ASCII katlı) biçimdedir; eski Türkçe karakterli anahtarlar zararsız kalır.
TEK_KELIME_POLIKLINIK: dict[str, str] = {
    "kalp": "KARDIYOLOJI", "kalbe": "KARDIYOLOJI", "kalbiye": "KARDIYOLOJI",
    "kemik": "ORTOPEDI", "kemige": "ORTOPEDI", "kemiğe": "ORTOPEDI",
    "kulak": "KBB", "kulaga": "KBB",
    "cilt": "DERMATOLOJI", "cilde": "DERMATOLOJI",
    "cocuk": "COCUK_SAGLIGI", "cocuga": "COCUK_SAGLIGI",
}

# LLM'in seçtiği poliklinik ancak metinde bu köklerden biri geçiyorsa kabul edilir
# (küçük modeller hiç söylenmemiş polikliniği uydurabiliyor). STT yazım hatalarını
# ("kardiolojiye", "dahliye") yakalayacak kadar kısa tutuldu.
POLIKLINIK_KOKLERI: dict[str, tuple[str, ...]] = {
    "DAHILIYE": ("dahil", "dahl", "dahiliy", "iç hast", "ic hast", "dahiliye"),
    "KARDIYOLOJI": ("kardi", "kardy", "kalp", "kalb"),
    "ORTOPEDI": ("ortop", "orto", "kemik", "travma"),
    "GOZ": ("göz",),
    "KBB": ("kbb", "k b b", "kabebe", "ka be be", "kulak"),
    "DERMATOLOJI": ("dermat", "dermot", "cildiye", "cilt"),
    "KADIN_DOGUM": ("kadın", "jinek", "doğumcu"),
    "COCUK_SAGLIGI": ("çocuk", "pediat", "pedyat"),
    "NOROLOJI": ("nörol", "nerol", "sinir"),
    "UROLOJI": ("ürol", "urol", "idrar yol"),
    "GENEL_CERRAHI": ("cerrah",),
}


def poliklinik_bul(kod: str | None) -> Poliklinik | None:
    if not kod:
        return None
    return next((p for p in POLIKLINIKLER if p.kod == kod.upper()), None)


# Sesli listede adı okunan poliklinikler (liste uzunsa). Ekranda seçenek olarak hepsi gönderilir.
SESLI_ORNEK_POLIKLINIKLER = ("DAHILIYE", "KARDIYOLOJI", "GOZ", "KBB")
# Bu sayıya kadar poliklinik tek tek okunur; daha fazlasında örnekler + toplam sayı okunur.
SESLI_POLIKLINIK_AZAMI = 6


def _ve_ile(adlar: list[str]) -> str:
    return adlar[0] if len(adlar) == 1 else ", ".join(adlar[:-1]) + " ve " + adlar[-1]


def poliklinik_listesi_metni() -> str:
    """Şablonlardaki ``{liste}``: tam yan cümle. Az poliklinikte hepsi ("Dahiliye, … ve Göz Hastalıkları
    polikliniklerimiz var"), çokta örnekler + sayı ("Dahiliye, Kardiyoloji, Göz Hastalıkları ve Kulak Burun
    Boğaz dahil 11 polikliniğimiz var"): 11 adı sesli okumak ~15 sn sürüyordu."""
    adlar = [p.ad for p in POLIKLINIKLER]
    if len(adlar) <= SESLI_POLIKLINIK_AZAMI:
        return f"{_ve_ile(adlar)} polikliniklerimiz var"
    ornek = [p.ad for k in SESLI_ORNEK_POLIKLINIKLER if (p := poliklinik_bul(k)) is not None] or adlar[:4]
    return f"{_ve_ile(ornek)} dahil {len(adlar)} polikliniğimiz var"


# --------------------------------------------------------------------------
# Kural tabanlı anlama için kelime listeleri (LLM çalışmazsa da akış sürsün)
# --------------------------------------------------------------------------

# Onay kelimesi yalnız kısa ve başka bilgi (gün/saat/poliklinik) taşımayan bir cümlede
# kural tabanlı onay sayılır; "yarın sabah istiyorum" onay DEĞİLDİR.
ONAY_IFADELERI = ("onaylıyorum", "onayla", "onay veriyorum", "evet", "tamam", "olur",
                  "kabul ediyorum", "kabul", "aynen", "doğru")
# Randevu YALNIZ bu kapalı listedeki cümlelerden biri, normalize edilmiş haliyle (küçük harf,
# Türkçe karakter katlama, noktalama/boşluk sadeleştirme) birebir söylendiğinde oluşturulur.
# Önek/alt dize eşleşmesi yoktur: "onaylamayın", "kabul etmiyorum", "evet mi?" onay değildir.
ACIK_ONAY_IFADELERI = (
    "evet", "onaylıyorum", "evet onaylıyorum", "onaylıyorum evet", "onayla", "evet onayla",
    "tamam onaylıyorum", "onay veriyorum", "evet onay veriyorum", "kabul ediyorum",
    "evet kabul ediyorum", "evet lütfen", "evet onaylıyorum lütfen",
)
RET_IFADELERI = ("hayır", "vazgeç", "iptal", "istemiyorum", "onaylamıyorum", "olmaz",
                 "başka saat", "başka bir saat", "değiştir", "uygun değil", "onaylama", "kabul etmiyorum",
                 "etmiyorum", "istemem", "değil")
KIMLIK_IFADELERI = ("tc", "t.c", "kimlik", "tc no", "kimlik numara", "doğum tarih", "doğum günüm",
                    "adresim", "ev adres")
TIBBI_IFADELER = ("ilaç", "ağrı", "ağrıyor", "ağrım", "hastalığım", "tedavi", "doz", "tahlil",
                  "tansiyon", "ateş", "öksürük", "ne yapmalıyım", "hangi bölüm", "hangi poliklinik",
                  "teşhis", "tanı", "reçete", "şikayet", "şikâyet")
# Açık randevu isteği (poliklinik/gün söylenmeden): "Ayşe Yılmaz için randevu istiyorum". Kural niyeti
# çıkarır, eksik bilgi sorulur; cümle LLM'e gitmez.
RANDEVU_ISTEGI_IFADELERI = ("randevu", "muayene olmak", "muayene olacağım", "doktora görün", "doktora çıkmak",
                            "hekime görün")
# "randevum oldu mu" bir istek değildir: randevu kelimesiyle birlikte istek fiili aranır.
RANDEVU_ISTEK_FIILLERI = ("istiyor", "isterim", "isteriz", "almak", "alabilir", "alayım", "alalım", "almam lazım",
                          "lazım", "gerek", "verir mi", "verebilir", "oluştur", "rica", "ayarla", "olmak ist")
# Gün parçaları: (ifadeler, başlangıç, bitiş) — yerel saat.
GUN_PARCALARI: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("öğleden sonra", "öğlenden sonra", "ikindi"), "12:00", "18:00"),
    (("öğle", "öğlen"), "11:30", "14:00"),
    (("sabah", "erken saat", "erkenden"), "08:00", "12:00"),
    (("akşam",), "17:00", "21:00"),
)

# "Fark etmez / en erken": eksik tercihin önemsiz olduğunu söyler.
FARK_ETMEZ_IFADELERI = ("fark etmez", "farketmez", "farkı yok", "farki yok", "hangisi olursa", "herhangi",
                        "hepsi olur", "en erken", "ilk boş", "en yakın", "müsait olan", "uygun olan",
                        "ne zaman olursa", "siz seçin", "sen seç", "önemli değil")
# Diyalogda düzeltme: "başka poliklinik", "başka bir gün".
POLIKLINIK_DEGISTIR_IFADELERI = ("başka poliklinik", "başka bir poliklinik", "başka bölüm", "başka bir bölüm",
                                 "poliklinik değiştir", "polikliniği değiştir", "bölüm değiştir",
                                 "farklı poliklinik", "farklı bir poliklinik", "başka doktor", "başka bir doktor")
GUN_DEGISTIR_IFADELERI = ("başka gün", "başka bir gün", "farklı gün", "farklı bir gün", "günü değiştir",
                          "gün değiştir", "başka tarih", "başka bir tarih")
# Sunulan saatlere göreli istek.
DAHA_GEC_IFADELERI = ("daha geç", "biraz geç", "daha sonra", "daha ileri", "biraz daha geç", "geç bir saat",
                      "daha geç bir saat", "sonraki saat", "daha geçe")
DAHA_ERKEN_IFADELERI = ("daha erken", "biraz erken", "daha önce", "biraz daha erken", "erken bir saat")
# Sıra sayıları (yalnız saat seçimi/onay adımında): (ifadeler, sıra; -1 = sonuncusu)
SIRA_IFADELERI: tuple[tuple[tuple[str, ...], int], ...] = (
    (("sonuncu", "sondaki", "en sondaki", "son saat", "son seçenek", "sonuncusu"), -1),
    (("birinci", "ilki", "ilkini", "ilk saat", "ilk seçenek", "ilkinde", "ilkine", "ilk olan", "ilk"), 1),
    (("ikinci", "ortadaki"), 2),
    (("üçüncü",), 3),
    (("dördüncü",), 4),
    (("beşinci",), 5),
    (("altıncı",), 6),
)

# ---- Geçmiş muayene / ilaç soruları (yanıt tamamen şablon + backend verisi)
# Geçmişe dönük işaretler: "hangi ilacı almalıyım" (tavsiye) geçmiş sorusu DEĞİLDİR.
GECMIS_IFADELERI = ("muayenem", "muayenelerim", "son muayene", "geçmiş muayene", "eski muayene",
                    "önceki muayene", "geçen muayene", "ilaçlarım", "ilaçlarımı", "ilaçlarımın", "ilacım",
                    "ilacımı", "ilacımın", "reçetem", "reçeteme", "reçetemde", "yazılan", "yazıldı", "yazılmış",
                    "yazdı", "yazmıştı", "verilen ilaç", "verdiği ilaç", "kullandığım ilaç", "kullanmam gereken",
                    "kullanacağım", "kullanmalıyım", "içeceğim", "içmem gereken", "alacağım ilaç",
                    "muayene özet", "muayene sonuc", "muayene sonuç", "tanım neydi", "teşhisim", "tanım ne",
                    # "parol kullanmam söylenmiş miydi": hekimin geçmişte söylediği/önerdiği soruluyor.
                    "söylenmiş", "söylendi", "söylemişti", "söyledi mi", "önerilmiş", "önerildi", "önermişti",
                    "verilmişti", "kullanmam söylen", "kullanmamı söyle",
                    # Gündelik / STT biçimleri (2026-11-16: "Ya en son randevu, bu doktor bana ne yazmıştı?"):
                    # hekimin geçmişte yazdığı/verdiği/söylediği, geçmiş ziyaret.
                    "yazmış", "yazdılar", "yazdıkları", "yazdığı", "yazılanlar", "yazılmıştı",
                    "ilaç verdi", "ilacı verdi", "ilaçları verdi", "ilaç vermiş", "ilacı vermiş", "ilaçları vermiş",
                    "vermişti", "vermişlerdi", "verdikleri",
                    "muayene oldum", "muayene olmuştum", "doktora gittim", "doktora gitmiştim", "hekime gittim",
                    "hekime gitmiştim", "bölüme gittim", "polikliniğe gittim", "gitmiştim",
                    "en son gittiğim", "son gittiğim", "geçen gittiğim", "önceki gittiğim", "geçen gittim",
                    # "randevu" geçmiş zamanla: geçmiş muayene ("en son randevumda", "geçen randevuda ne yazıldı").
                    # "randevum ne zaman", "yaklaşan randevum" burada YOK (mevcut randevu şablonu).
                    "son randevumda", "son randevuda", "son randevusunda", "geçen randevu", "önceki randevu",
                    "eski randevu", "geçmiş randevu",
                    # "hangi ilaçları kullanıyordum", "ne ilaç kullanıyordum"
                    "ilaç kullanıyordum", "ilaçları kullanıyordum", "ilacı kullanıyordum", "ilaçlar kullanıyordum")
# Hekimin geçmişte verdiği/söylediği ("doktor ne verdi", "ne demişti"): yalnız tam kelime biçimleri sayılır
# (anlama.py'de düzenli ifadeyle). Önek eşleşmesi "ne dediniz"i (asistana "ne dediniz?") geçmiş sanırdı.
GECMIS_HEKIM_FIILLERI = ("verdi", "verdiler", "vermiş", "vermişti", "vermişler", "vermişlerdi", "verildi",
                         "verilmişti", "dedi", "dediler", "demiş", "demişti", "demişler", "demişlerdi", "söyledi",
                         "söylediler", "söylemiş", "söylemişti", "söylemişlerdi", "önerdi", "önermiş", "önermişti",
                         "yazdı", "yazdılar", "yazmış", "yazmıştı", "yazmışlardı")
# Geçmiş sorusunun alt konusu (kural; yanıt yine hekim onaylı özetten şablonla):
# - ziyaret: "en son ne zaman muayene oldum", "en son hangi doktora gittim" → son muayenenin tarihi, polikliniği,
#   hekimi ve yazılan ilaçların adları;
# - ozet: "doktor ne demişti" → son muayenenin hekim onaylı özeti ve uyarıları (yorum eklenmez).
GECMIS_ZIYARET_IFADELERI = ("ne zaman muayene", "ne zaman doktora", "ne zaman hekime", "ne zaman gittim",
                            "ne zaman gitmiştim", "hangi doktora", "hangi doktor", "hangi hekim", "hangi bölüme git",
                            "hangi polikliniğe git", "kime gittim", "nereye gittim", "ne zamandı", "hangi tarihte")
GECMIS_OZET_FIILLERI = ("dedi", "dediler", "demiş", "demişti", "demişler", "demişlerdi", "söyledi", "söylediler",
                        "söylemiş", "söylemişti", "söylemişlerdi", "önerdi", "önermiş", "önermişti")
# İlaç kullanımına dair soru sözcükleri: kayıttaki bir ilaç adıyla birlikte geçerse ilaç sorusu sayılır.
ILAC_SORU_IFADELERI = ("nasıl kullan", "nasıl al", "nasıl iç", "ne zaman kullan", "ne zaman al", "ne zaman iç",
                       "kaç kez", "kaç defa", "kaç kere", "günde kaç", "ne için", "ne işe yarar", "neye yarar",
                       "ne içindi", "kullanımı", "kullanılır", "ne zaman", "aç karnına", "tok karnına", "uyarı")
# Tavsiye isteği (doz değiştirme, bırakma, etkileşim): sabit ret şablonu; geçmiş verisi okunmaz.
ILAC_TAVSIYE_IFADELERI = ("dozunu", "dozu art", "dozu azalt", "doz art", "doz azalt", "dozu değiştir",
                          "bırakabilir", "bırakayım", "bırakmalı", "bıraksam", "kesebilir", "keseyim", "kessem",
                          "birlikte alabilir", "birlikte kullanabilir", "beraber alabilir", "beraber kullanabilir",
                          "ile alabilir", "ile kullanabilir", "yerine", "atlasam", "atlayabilir", "unuttum",
                          "fazla alsam", "fazla içsem", "iki tane", "çift doz", "alkol", "hamile", "gebe",
                          "emzir", "yan etki", "zararı var", "zararlı mı", "içmesem", "almasam", "kullanmasam",
                          "birlikte", "beraber")
# Tek başına ilaç tavsiyesi isteği sayılan kalıplar (bağlam aranmaz).
ILAC_TAVSIYE_KESIN = ("dozunu", "dozu art", "dozu azalt", "doz art", "doz azalt", "çift doz",
                      "birlikte alabilir", "birlikte kullanabilir", "beraber alabilir", "beraber kullanabilir",
                      "yan etki")
# Genel ilaç LİSTESİ isteği ("hangi ilaçlar yazıldı"): tek bir ilaç adı sorulmuyor; cümledeki
# tanınmayan kelime (STT hatası) "bu ilaç görünmüyor" yanıtına yol açmaz.
ILAC_LISTESI_IFADELERI = ("ilaçlar", "ilaçlarım", "hangi ilaç", "reçetem", "reçetemde", "neler yazıldı")
ILAC_BAGLAM_IFADELERI = ("ilaç", "ilac", "hap", "tablet", "şurup", "kapsül", "reçete")
# Bağlamdan bağımsız ilaç tavsiyesi isteği (bırakma/kesme/atlama/fazla alma): kural çözemediği
# cümlede bile LLM'e gönderilmez, sabit ret şablonu verilir.
ILAC_TAVSIYE_GUCLU = ("bırakabilir", "bırakayım", "bırakmalı", "bıraksam", "kesebilir miyim", "keseyim",
                      "kessem", "atlasam", "atlayabilir", "fazla alsam", "fazla içsem", "içmesem", "almasam",
                      "kullanmasam", "zararı var", "zararlı mı")

# ---- İlaç SORU KONULARI (kural; yanıt ilac_dagarcigi + hekim onaylı özetten şablonla kurulur, LLM'siz)
# (konu, ifadeler, güçlü ifadeler). Sıra önemlidir: ilk eşleşen konu seçilir ("hamileyken alkol" → alkol,
# "çayla birlikte" → yemek). Güçlü ifade tek başına ilaç sorusudur ("yan etkisi var mı"); diğerleri yalnız
# ilaç bağlamında (ilaç adı, "ilaç/hap", "bunu" + son konuşulan ilaç) ya da soru biçiminde sayılır.
# Kelime başından önek eşleşmesi (normalize); STT/gündelik yazımlar ("aleji", "yanetkisi", "ac karnina") dahil.
ILAC_KONU_KALIPLARI: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # "bira" önek olarak "biraz"ı yakalardı: yalnız ekli/öbek biçimleri.
    ("alkol", ("alkol", "içki", "birayla", "bira ile", "bira iç", "bira al", "şarap", "rakı", "votka", "viski"),
     ()),
    ("hamilelik", ("hamile", "gebe", "emzir", "anne sütü", "bebek bekli", "lohusa", "hamilelik", "gebelik"), ()),
    ("alerji", ("alerji", "allerji", "alerjik", "aleji", "alarji", "kimler kullan", "kimler iç", "kimler al",
                "kimlere", "kim kullanmamalı", "kullanmaması gereken", "kullanmamam gereken", "kullanmamalı",
                "kullanılmamalı", "içmemesi gereken", "kullanamaz", "kimler içemez", "kimler alamaz"), ()),
    ("yemek", ("aç karnına", "aç karına", "tok karnına", "tok karına", "aç mı", "tok mu", "aç mıyken", "yemekle",
               "yemekten", "yemek öncesi", "yemek sonrası", "yemeklerden", "yemek yerken", "kahvaltı",
               # "çay"/"süt" önek olarak "caydım", "sütun"u da yakalardı: öbek biçimleri.
               "greyfurt", "sütle", "süt ile", "süt iç", "süt ürün", "sütün", "süt mü", "çayla", "çay ile",
               "çay iç", "çayın", "çay mı", "kahve", "meyve suyu", "portakal suyu", "yiyecek", "yiyebilir",
               "yesem", "yemek yiy",
               # Gündelik biçimler (2026-11-16: "…dikkat etmem, yememem gereken şeyler var mı?"): yenmemesi gereken,
               # neyle alınmaz. "içecek" önek olarak "içeceğim"i yakalamaz (normalize: icecek ≠ icecegim).
               "yememem", "yememe", "yemem gereken", "yenmemesi", "yenmez", "yiyemem", "yiyemez", "yemeyeyim",
               "içecek", "neyle al", "neyle iç", "neyle kullan", "ne ile al", "ne ile iç", "nelerle", "besin",
               "gıda", "meyve"), ()),
    ("unutma", ("unut", "atlarsam", "atlasam", "atladım", "atlayabilir", "kaçırırsam", "kaçırdım", "doz kaçır"),
     ("unutursam", "unutulursa", "unuttum içmeyi", "içmeyi unut", "almayı unut", "atlarsam", "atlasam",
      "atlayabilir", "dozu kaçır", "dozu unut")),
    ("doz", ("doz", "çift doz", "iki tane", "fazla alsam", "fazla içsem", "fazla aldım", "fazla içtim",
             "fazla al", "fazla iç", "yarım tablet", "yarıya böl", "bölebilir", "bölünür", "kaç tane",
             "kaç mg", "kaç miligram", "artırabilir", "azaltabilir", "artırayım", "azaltayım", "artırsam",
             "azaltsam"),
     ("doz", "çift doz", "fazla alsam", "fazla içsem", "fazla aldım", "fazla içtim")),
    ("etkilesim", ("birlikte", "beraber", "ile alabilir", "ile kullanabilir", "ile içebilir", "başka ilaç",
                   "başka bir ilaç", "diğer ilaç", "aynı anda", "etkileş", "ağrı kesici", "antibiyotik",
                   "bitkisel", "vitamin", "karıştır"),
     ("birlikte al", "birlikte kullan", "birlikte iç", "beraber al", "beraber kullan", "beraber iç", "etkileş",
      "başka ilaç", "başka bir ilaç", "diğer ilaçlar", "aynı anda al", "aynı anda iç", "aynı anda kullan")),
    # "Nelere dikkat etmeliyim / nelerden uzak durmalıyım": ilaca özgü genel dikkat (yiyecek-içecek + alkol;
    # ``ILAC_BIRLESIK_KONULAR``). Bu ifadeyle birlikte daha özel bir konu da söylendiyse ("dikkat etmem, yememem
    # gereken") o konu kazanır (yukarıda); güçlü yan etki ifadesi de kazanır (``ilac_bilgisi.konu_bul``).
    ("dikkat", ("dikkat", "uzak dur", "uzak kal", "kaçınmam", "kaçınmalı", "kaçınılmalı", "nelerden kaçın",
                "yapmamam gereken", "yapılmaması gereken", "yapmamalıyım"), ()),
    ("yan_etki", ("yan etk", "yanetk", "yan tesir", "zararı", "zararlı", "zarar ver", "yapar mı", "yapıyor mu",
                  "yapabilir mi", "yaptı mı", "dokunur mu", "dokunuyor", "ağır gel", "rahatsız eder",
                  "sorun yapar", "sorun çıkar", "uyku yapar", "kilo yapar", "kilo aldır", "içince", "içtikten",
                  "aldıktan", "kullanınca", "kullandıktan", "başladığımdan", "başlayalı", "sakınca", "dokunur",
                  "uyutur", "uyku verir", "sersemlet", "zarar"),
     ("yan etk", "yanetk", "yan tesir", "zararı var", "zararlı mı", "zarar verir mi")),
    ("sure", ("bırak", "kesebilir", "keseyim", "kessem", "kesersem", "kesmeli", "kesmem", "içmesem", "almasam",
              "kullanmasam", "ne kadar süre", "ne zamana kadar", "kaç gün", "kaç ay", "kaç hafta", "ömür boyu",
              "hayat boyu", "sürekli mi", "hep mi", "devam etmeli", "devam edecek", "ne kadar kullan",
              "ne kadar iç", "bitince", "iyileşince", "düzelince", "geçince"),
     ("bırak", "kesebilir", "keseyim", "kessem", "kesersem", "içmesem", "almasam", "kullanmasam")),
    ("amac", ("ne işe yar", "neye yar", "ne için", "ne içindi", "işe yarıyor", "faydası", "faydalı", "ne ilacı",
              "neyin ilacı", "nedir", "niye yazıl", "neden yazıl", "niçin"), ()),
    ("kullanim", ("nasıl kullan", "nasıl al", "nasıl iç", "ne zaman kullan", "ne zaman al", "ne zaman iç",
                  "kaç kez", "kaç defa", "kaç kere", "günde kaç", "saat kaçta", "sabah mı", "akşam mı",
                  "kullanımı", "kullanılır", "ne zaman"), ()),
)
ILAC_KONULARI = tuple(k for k, _, _ in ILAC_KONU_KALIPLARI)
# Birleşik konu: dağarcıktaki birden çok konunun kısa birleşimi (sesli okunur; ILAC_KONU_AZAMI_SESLI aşılmaz).
# Yanıt verisindeki ``history.topic`` ilk alt konudur (sözleşmedeki kapalı kod listesi değişmez).
ILAC_BIRLESIK_KONULAR: dict[str, tuple[str, ...]] = {"dikkat": ("yemek", "alkol")}

# ---- Adı söylenmeyen kayıtlı ilacı TARİFLE sorma ("tansiyon ilacım", "kalp doktorunun verdiği ilaç", "en son
# yazılan ilaç", "sabah içtiğim ilaç"). Eşleme ``ses_servisi/ilac_tarifi.py``'de, kayıtlı ilacın kendi verisiyle
# (yazıldığı poliklinik, hekim onaylı "ne işe yarar" metni, kullanım talimatı, dağarcıktaki amaç anahtarları)
# yalnız kodda yapılır; LLM'e gitmez. Tek ilaca uyarsa o ilaçla yanıtlanır (ad yanıtta söylenir), birden çoğuna
# ya da hiçbirine uymuyorsa sorulur (tahmin yok).
# (anahtar, yanıtta okunan ad, kullanıcı ifadeleri (STT yazımları dahil), kayıt metninde aranan kökler)
ILAC_AMAC_ANAHTARLARI: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("tansiyon", "tansiyon", ("tansiyon", "tansiyom", "tansyon", "tansiyo", "hipertansiyon", "kan basınc",
                              "tansiyonu düşür"), ("tansiyon", "kan basınc", "hipertansiyon")),
    ("seker", "şeker", ("şeker", "diyabet", "kan şeker"), ("şeker", "diyabet")),
    ("demir", "kansızlık", ("demir", "kansızlık", "kansızlığ", "kansız", "anemi", "kan yapıcı"),
     ("demir", "kansızl", "anemi")),
    ("kalp", "kalp", ("kalp", "kalb"), ("kalp", "anjina")),
    ("kolesterol", "kolesterol", ("kolesterol", "kolestrol", "kolestol"), ("kolesterol",)),
    ("mide", "mide", ("mide", "reflü", "ülser", "gastrit"), ("mide", "reflü", "ülser")),
    ("agri", "ağrı", ("ağrı kesici", "ağrıkesici", "ağrı için", "ağrım için", "ağrısı için", "ağrı ilac",
                      "ağrı hap"), ("ağrı",)),
    ("ates", "ateş", ("ateş düşür", "ateş için", "ateşim için", "ateş ilac"), ("ateş",)),
    ("tiroid", "tiroid", ("tiroid", "tiroit", "guatr"), ("tiroid", "tiroit")),
    ("antibiyotik", "enfeksiyon", ("antibiyotik", "enfeksiyon", "mikrop", "iltihap"),
     ("antibiyotik", "bakteri", "enfeksiyon")),
    ("uyku", "uyku", ("uyku ilac", "uyku hap", "uyumak için", "uyku için"), ("uyku",)),
    ("alerji", "alerji", ("alerji ilac", "alerji hap", "alerjim için", "alerji için"), ("alerji", "kaşıntı")),
    ("vitamin", "vitamin", ("vitamin",), ("vitamin",)),
)
# İlaç bağlamı işaretleri (tarif için). Normalize metinde düzenli ifade. "apı/apım": konuşma tanımanın "hapı/hapım"
# yazımı ("şeker apı"); yalnız amaç kelimesiyle birlikte sayılır.
ILAC_TARIF_ILAC_KELIMESI = r"(?<!\w)(ilac|hap|tablet|surup|kapsul|igne|damla)\w*"
ILAC_TARIF_STT_HAP = r"(?<!\w)ap(i|im|imi|imin|imla|imi|in|ini|lar|larim)?(?!\w)"
# Kendi ilacının kullanımı ("kullandığım", "içtiğim", "aldığım", "kullanıyorum") ve amaç fiili ("düşüren",
# STT: "düşünen"). Yalnız bir amaç/poliklinik/zaman işaretiyle birlikte tarif sayılır.
ILAC_TARIF_KULLANIM = (r"(?<!\w)(kullandig|ictig|aldig|kullanmakta|kullaniyor|iciyor|kullanacag|icecegim|"
                       r"kullanmam gereken|icmem gereken)\w*")
ILAC_TARIF_AMAC_FIILI = r"(?<!\w)(dusuren|dusurucu|dusunen|dusurdugum|dusuruyor|dengeleyen|duzenleyen|ayarlayan)\w*"
# Sıra: en son yazılan / önceki muayenede yazılan.
ILAC_TARIF_SON = ("en son", "son yazılan", "son verilen", "son yazdığı", "son verdiği", "son yazdıkları",
                  "en yeni", "yeni yazılan", "yeni verilen", "son ilacım", "son reçete", "son muayenede yazılan",
                  "geçen sefer yazılan", "geçen sefer verilen")
ILAC_TARIF_ONCEKI = ("önceki muayenede", "önceki muayenemde", "eski ilacım", "ilk yazılan", "ilk muayenemde",
                     "ilk muayenede", "daha önce yazılan", "daha önceki")
# Zaman: "sabah içtiğim ilaç" → hekim talimatında o zaman geçen ilaç.
ILAC_TARIF_ZAMAN = r"(?<!\w)(sabah|aksam|gece|ogle|yatmadan|yatarken)\w* (ictig|aldig|kullandig|ilac|hap|tablet)"
ILAC_TARIF_ZAMAN_KOKLERI = {"sabah": ("sabah",), "aksam": ("akşam",), "gece": ("gece", "yatmadan", "yatarken"),
                            "ogle": ("öğle",), "yatmadan": ("gece", "yatmadan", "yatarken"),
                            "yatarken": ("gece", "yatmadan", "yatarken")}
ILAC_TARIF_ZAMAN_ADLARI = {"sabah": "sabah", "aksam": "akşam", "gece": "gece", "ogle": "öğlen",
                           "yatmadan": "gece", "yatarken": "gece"}
# Yan etki kalıplarından yalnız genel "yapmak" fiilleri: birinci şahıs soruda ("spor yapabilir miyim")
# kişinin yapacağı şeyi sorar, yan etki sorusu sayılmaz (bkz. ilac_bilgisi.konu_bul).
ILAC_YAN_ETKI_GENEL_FIILLER = ("yapar mı", "yapıyor mu", "yapabilir mi", "yaptı mı")
# Bu konular hekimin kullanım talimatıyla birlikte okunur (talimat önce; çelişkide talimat esastır).
ILAC_TALIMATLI_KONULAR = ("doz", "yemek", "unutma", "sure")
# "Unuttum" bu kelimelerle birlikteyse ilaç sorusu değildir ("erişim kodumu unuttum").
ILAC_KONU_DISI_KELIMELER = ("kod", "şifre", "parola", "numara", "randevu", "poliklinik", "saat kaç")
# İlaç bağlamında ciddi belirti (soru biçiminde değilse): sabit 112 şablonu. Yalnız ilaç bağlamında aranır
# ("nefes darlığı" tek başına şikâyettir → Kardiyoloji önerisi); bağlamsız yüz/dil şişmesi ACIL_IFADELERI'nde.
ILAC_ACIL_IFADELERI = ("nefes darlığı", "nefesim daral", "nefes almakta zorlan", "nefes almakta güçlük",
                       "boğazım şiş", "boğazım kapan", "dilim şiş", "dudaklarım şiş",
                       "dudağım şiş", "yüzüm şiş", "gözlerim şiş", "göz kapaklarım şiş", "yutkunamıyorum",
                       "kurdeşen", "bayıl", "göğsüm sıkış", "kanlı kus", "kan kus")
# İlaç bağlamı işaretleri (ilaç adı dışında): içtikten sonra belirti anlatımı.
ILAC_ALIM_IFADELERI = ("içtim", "içtikten", "içince", "aldım", "aldıktan", "alınca", "kullandım", "kullandıktan",
                       "kullanınca", "başladım", "başladığımdan", "başlayalı")

# KVKK: kullanıcının KENDİ sağlık verisini (hastalık, durum, kullandığı ilaç, şikâyet) içeren ve kuralın
# çözemediği cümleler LLM'e GÖNDERİLMEZ; sabit "tıbbi konularda yorum yapamam" şablonu verilir. Anlama için
# gerekmezler: randevu poliklinik adıyla, geçmiş soruları kural + kodda eşleştirmeyle çözülür.
# (Kelime başından önek eşleşmesi: "hasta" → hastayım/hastasıyım/hastalığım.)
SAGLIK_VERISI_IFADELERI = (
    "hastay", "hastas", "hastalı", "hastalığ", "rahatsızlı", "rahatsızlığ", "şeker", "diyabet", "insülin", "kanser", "tümör", "kemoterapi", "hiv", "aids",
    "hepatit", "hamile", "gebe", "doğum yap", "düşük yap", "depresyon", "anksiyete", "panik atak", "psikiyatr",
    "psikolog", "şizofren", "bipolar", "intihar", "bağımlı", "uyuşturucu", "alkolik", "epilepsi", "sara nöbet",
    "nöbet geçir", "astım", "koah", "tansiyon", "kolesterol", "tiroit", "tiroid", "guatr", "böbrek", "karaciğer",
    "diyaliz", "ameliyat", "kalp krizi", "inme geçir", "felç", "alzheimer", "demans", "parkinson", "migren", "alerji",
    "enfeksiyon", "mikrop", "virüs", "covid", "korona", "grip", "nezle", "zatürre", "verem", "kırık", "çıkık",
    "yaram", "yaralan", "kanama", "bulantı", "kusma", "ishal", "kabız", "baş dönme", "bayıl", "çarpıntı", "nefes darlığı",
    "kilo ver", "kilo al", "engelli", "regl", "adet gör", "adetim", "menopoz", "kısırlık", "tüp bebek", "cinsel",
    "kullanıyorum", "içiyorum", "kullanıyordum", "içiyordum", "ilaç", "ilac", "hap", "tablet", "şurup",
    "iğne", "aşı ol", "aşısı", "kalbim", "midem", "başım", "dizim", "belim", "sırtım", "karnım", "göğsüm", "bacağım",
    "kolum", "boğazım", "kulağım", "dişim", "kanım", "idrar", "ağrı", "sızlı", "sızı var", "şişlik", "şişti", "şişme", "kaşıntı", "döküntü",
    "ateş", "öksür", "halsiz", "yorgun", "uykusuz", "stres", "cildim", "cilt", "sivilce", "egzama", "sedef",
    "kulak", "burnum", "dişler", "baş ağrı", "başımda", "başım dön",
)
# Yukarıdaki sağlık cümlelerinden ilaç kullanımı beyan edenler ("Metformin kullanıyorum") bölüm önerisine
# değil "tıbbi yorum yapamam" şablonuna gider.
ILAC_BEYAN_IFADELERI = ("kullanıyorum", "içiyorum", "kullanıyordum", "içiyordum", "ilaç", "ilac", "hap", "tablet",
                        "şurup", "iğne", "aşı ol", "aşısı")

# --------------------------------------------------------------------------
# Genel sağlık / ilaç BİLGİ sorusu (ses_servisi/saglik_bilgisi.py). Dağarcıkta hazır bilgi yoksa yerel LLM'e
# kısa, genel bir yanıt yazdırılır ve sonuna KOD sabit kapanış ekler. Kişiye özel karar ("çok spesifik")
# soruları sabit metinle doktora yönlendirilir. Acil ifadeler bu yoldan ÖNCE kuralla yakalanır.
# Hepsi normalize (ASCII katlı) metinde kelime başından önek eşleşmesidir.
# --------------------------------------------------------------------------
# Sorunun sağlıkla ilgili olduğunu gösteren genel terimler (ilaç adları ayrıca koddan tanınır).
SAGLIK_BILGI_TERIMLERI = (
    "sağlık", "sağlıklı", "hastalık", "hastalığ", "belirti", "tedavi", "ilaç", "ilac", "hap", "tablet", "şurup",
    "kapsül", "iğne", "aşı", "antibiyotik", "ağrı kesici", "ağrıkesici", "vitamin", "mineral", "takviye",
    "probiyotik", "bitkisel", "doz", "yan etki", "yanetki", "prospektüs", "eczane", "reçete",
    "tansiyon", "hipertansiyon", "kolesterol", "trigliserit", "şeker hastalığ", "şeker hastası", "kan şeker",
    "diyabet", "şekerim", "şeker hastal", "insülin", "grip", "nezle", "soğuk algınl", "üşütme", "korona", "covid", "virüs", "bakteri",
    "mikrop", "enfeksiyon", "iltihap", "ateş", "öksürük", "balgam", "boğaz ağrı", "baş ağrı", "başağrı",
    "migren", "baş dönme", "mide", "reflü", "gastrit", "ülser", "hazımsızl", "ishal", "kabızl", "bulantı",
    "kusma", "alerji", "alerjik", "astım", "bronşit", "zatürre", "kalp", "damar", "çarpıntı", "ritim",
    "böbrek", "karaciğer", "safra", "tiroid", "tiroit", "guatr", "kansızl", "anemi", "demir eksik", "b12",
    "d vitamin", "kalsiyum", "magnezyum", "uykusuzl", "uyku", "stres", "kaygı", "depresyon", "anksiyete",
    "obezite", "kilo ver", "kilo al", "diyet", "beslenme", "kalori", "egzersiz", "yürüyüş", "sigara",
    "alkol", "kafein", "tuz", "kanser", "tümör", "diş", "diş eti", "göz", "katarakt", "glokom", "cilt",
    "sivilce", "akne", "egzama", "sedef", "saç dökül", "kaşıntı", "döküntü", "güneş çarp", "güneş yanığ",
    "hamilelik", "gebelik", "emzirme", "adet", "menopoz", "kemik erime", "osteoporoz", "eklem", "romatizma",
    "gut", "fıtık", "bel ağrı", "boyun ağrı", "kas ağrı", "kramp", "varis", "hemoroid", "idrar yolu",
    "prostat", "bağışıklık", "nefes", "kronik", "sendrom", "kalp krizi", "inme", "felç", "epilepsi",
    "parkinson", "alzheimer", "demans", "hepatit", "verem", "tetanoz", "kuduz", "ishal", "dehidrat",
    "susuz kal", "su iç", "nabız", "kan basınc", "kan değer", "hemoglobin", "lif", "protein", "karbonhidrat",
)
# Bilgi sorusu biçimi (soru eki/işareti dışında): "nedir", "ne işe yarar", "ne iyi gelir"…
SAGLIK_BILGI_SORU_KALIPLARI = (
    "nedir", "ne demek", "ne işe yar", "neye yar", "ne iyi gelir", "iyi gelir", "ne yapılır", "ne yapılmalı",
    "neden", "niçin", "niye", "nasıl", "nelere dikkat", "neye dikkat", "dikkat etmeli", "dikkat edilmeli",
    "farkı ne", "farkı nedir", "arasındaki fark", "hakkında bilgi", "bilgi ver", "bilgi alabilir", "anlat",
    "açıkla", "belirtiler", "nelerdir", "neler", "faydaları", "faydası", "zararları", "zararı", "ne zaman",
    "hangi besin", "hangi yiyecek", "ne yemeli", "ne yenir", "ne içilir", "sabah mı", "akşam mı", "aç mı",
    "tok mu", "bulaşır", "geçer", "önlenir", "korunur", "korunmak", "düşürülür", "düşer", "yükselir",
    "ne için", "kullanılır", "alınır", "içilir", "ne kadar", "zararlı", "faydalı", "işe yarar",
)
# Kişisel şikâyet/durum anlatımı. İlaç adı yoksa mevcut akış sürer ("başım ağrıyor" → poliklinik önerisi,
# "tansiyonum 160 normal mi" → tıbbi yorum yok + öneri); yalnız yaşam tarzı bilgi sorusu yanıtlanır.
SAGLIK_KISISEL_SIKAYET = (
    "başım", "midem", "karnım", "kalbim", "göğsüm", "belim", "sırtım", "dizim", "boğazım", "kulağım", "dişim",
    "gözüm", "burnum", "cildim", "bacağım", "kolum", "boynum", "omzum", "ağrıyor", "ağrım var", "sızlıyor",
    "çarpıyor", "yanıyor", "kaşınıyor", "öksürüyorum", "kusuyorum", "ateşim", "tansiyonum", "şekerim",
    "kolesterolüm", "nabzım", "değerlerim", "tahlillerim", "bende", "yorgunum", "halsizim", "uyuyamıyorum",
    "hastayım", "hastasıyım", "hasta oldum", "rahatsızım", "şikayetim", "şikâyetim",
    "nefesim", "nefes alamı", "nefes darlığım", "çarpıntım", "uyuşuyor", "kanıyor", "bayılıyor", "dönüyor",
    "bulanıyor", "şişti", "şişiyor",
)
# Kişisel durum (ilaç sorusunda kişiye özel karar sayılır: "hamileyim, parasetamol kullanabilir miyim").
SAGLIK_KISISEL_DURUM = (
    "hamileyim", "gebeyim", "emziriyorum", "hastasıyım", "hastayım", "yetmezliğim", "alerjim var", "alerjim",
    "yaşındayım", "ameliyat oldum", "ameliyat olacağım", "tansiyonum", "şekerim", "böbreklerim",
    "karaciğerim", "kalbim", "midem", "ülserim", "astımım", "kolesterolüm",
)
# Kişisel anlatımlı soruda yine yanıtlanan genel yaşam tarzı soruları ("kolesterolüm yüksek nelere dikkat
# etmeliyim", "başım ağrıyor ne iyi gelir").
SAGLIK_YASAM_TARZI_SORULARI = (
    "nelere dikkat", "neye dikkat", "dikkat etmeli", "ne iyi gelir", "iyi gelir", "ne yemeli", "ne yiyebilirim",
    "ne yenir", "nasıl beslen", "hangi besin", "hangi yiyecek", "nasıl düşer", "nasıl düşür", "nasıl korun",
    "egzersiz", "spor yap",
    # Şikâyet + yaşam tarzı izin sorusu ("tansiyonum çıktı, tuz yemem zararlı mı"): ilaç sorusu değil, genel bilgi.
    "zararlı mı", "zararı var mı", "zararı olur mu", "zarar verir mi", "sakıncalı mı", "yiyebilir miyim",
    "içebilir miyim", "yemem doğru", "içmem doğru", "yemeli miyim", "ne yemeliyim", "yemem gerek",
)
# Sağlık sorusu olsa da bu yolun dışı: randevu/poliklinik/yönlendirme ve hastanın KENDİ kayıtları (mevcut akış).
SAGLIK_YOLU_DISI = (
    "randevu", "poliklinik", "bölüm", "hangi doktor", "hangi hekim", "doktora git", "hekime git",
    "nereye gitmeli", "nereye gideyim", "nereye başvur", "muayenem", "muayene özet", "ilaçlarım", "ilacım",
    "reçetem", "yazılan", "yazıldı", "yazılmış", "yazdı", "yazmıştı", "verilen ilaç", "verdiği ilaç",
    "kullandığım ilaç", "kullanmam gereken", "kullanacağım", "kullanmalıyım", "içeceğim", "içmem gereken",
    "alacağım ilaç", "söylenmiş", "söylendi", "önerilmiş", "erişim kod", "müsait", "boş saat",
    "sorabilir miyim", "bir şey sor", "bir soru sor", "baktır", "kontrol ettir", "muayene ol", "doktora göster",
)
# "Çok spesifik" (kişiye özel karar): genel bilgi verilmez, sabit metinle doktora yönlendirilir.
SAGLIK_SPESIFIK_DOZ = (
    "dozu art", "dozunu art", "dozu azalt", "dozunu azalt", "doz art", "doz azalt", "dozu değiştir",
    "dozunu değiştir", "dozu ne", "dozu kaç", "kaç mg", "kaç miligram", "kaç tane", "kaç tablet", "kaç doz",
    "çift doz", "fazla alsam", "fazla içsem", "fazla aldım", "fazla içtim", "iki tane", "yarım tablet",
    "günde kaç", "kaç kez", "kaç defa", "kaç kere", "kaç saatte", "ne sıklıkla", "ne kadar içmeli",
    "ne kadar almalı", "ne kadar kullanmalı", "artırayım", "azaltayım", "artırsam", "azaltsam",
    "artırabilir miyim", "azaltabilir miyim",
)
SAGLIK_SPESIFIK_BIRAKMA = (
    "bırakabilir", "bırakayım", "bırakmalı", "bıraksam", "keseyim", "kessem", "kesebilir miyim",
    "kesmeli miyim", "içmesem", "almasam", "kullanmasam", "ara verebilir", "ara versem", "başlayayım",
    "başlamalı mıyım", "başlasam", "kullanmalı mıyım", "içmeli miyim", "almalı mıyım", "kullanayım mı",
    "içeyim mi", "alayım mı",
)
SAGLIK_SPESIFIK_TAHLIL = (
    "tahlil sonuc", "tahlil sonuç", "tahlilim", "tahlillerim", "kan değerlerim", "değerlerim", "sonuçlarım",
    "sonucum", "raporum", "emar sonuc", "röntgen sonuc", "ultrason sonuc", "çıktı normal", "normal mi",
    "yüksek mi", "düşük mü", "değerim",
)
SAGLIK_SPESIFIK_TESHIS = (
    "bende ne var", "neyim var", "hastalığım ne", "ne hastalığım", "ne hastalığı", "hasta mıyım",
    "kanser miyim", "kanser mi", "teşhis koy", "tanı koy", "teşhisim", "ne olabilir", "neden olabilir",
    "hastalık mı", "ciddi mi", "tehlikeli mi",
)
SAGLIK_SPESIFIK_ONERI = (
    "hangi ilaç", "hangi ilacı", "ne ilaç", "ilaç öner", "ilaç yaz", "hangi antibiyotik", "ne içmeliyim",
    "ne almalıyım", "ne kullanmalıyım", "hangisini kullan", "hangisini iç", "hangisini al",
    # Kişiye ilaç kullanma kararı ("antibiyotik kullanmam gerekir mi"): hekimin kararıdır.
    "kullanmam gerekir mi", "kullanmam gerekiyor mu", "kullanmalı mıyım", "almam gerekir mi", "almalı mıyım",
    "içmem gerekir mi", "içmeli miyim",
)
# Hastanın kendi şikâyetinde ilaç biçimi + izin ("gözlerim kuruyor damla kullanabilir miyim"): kişiye özel ilaç
# kararı, sabit yönlendirme (genel bilgi verilmez).
SAGLIK_ILAC_BICIMLERI = ("damla", "krem", "merhem", "sprey", "fitil", "pomad", "jel", "antibiyotik", "ağrı kesici",
                         "ağrıkesici", "vitamin", "takviye", "şurup")
# Kayıt/terim aranmadan teşhis isteği ("bende ne var sizce"): sabit metin.
SAGLIK_SPESIFIK_TESHIS_KESIN = ("bende ne var", "neyim var", "hastalığım ne", "ne hastalığım", "hasta mıyım",
                                "kanser miyim", "teşhis koy", "tanı koy")
# (Ayrıca tek başına "ile" kelimesi: koddan, kelime sınırıyla.)
SAGLIK_ILAC_BIRLIKTE = ("birlikte", "beraber", "aynı anda", "etkileş", "karıştır")
# Kişisel anlatımda bu ciddi belirtilerden biri varsa genel bilgi verilmez (mevcut akış: acil şablonu ya da
# poliklinik önerisi + "ani ve şiddetliyse 112" notu). "Nefesim daralıyor ne iyi gelir" LLM'e gitmez.
SAGLIK_CIDDI_BELIRTILER = (
    "nefesim", "nefes alam", "nefes darlığ", "göğsüm", "göğüs ağrı", "çarpıntım", "kalbim", "bayıl", "bilinc",
    "felç", "inme", "uyuş", "konuşam", "kan kus", "kan tükür", "kanama", "kanıyor", "zehir", "intihar",
    "ölmek", "kendime zarar", "şişti", "şişiyor", "nöbet", "havale", "kriz", "çok yüksek ateş", "ense sert",
)
SAGLIK_ILAC_IZIN = ("kullanabilir", "içebilir", "alabilir", "uygun mu", "sakınca", "zararı", "olur mu",
                    "kullansam", "içsem", "alsam")
# Birinci şahıs ilaç kullanım beyanı ("Lustral içiyorum"): izin sorusuyla kişiye özeldir (sabit metin); bilgi
# sorusu kalıbı yoksa beyandır, bu yol yanıtlamaz (mevcut akış).
SAGLIK_ILAC_BEYAN = ("kullanıyorum", "içiyorum", "alıyorum", "kullanmaktayım")

# Sabit metinler (kapanış KODDAN eklenir; LLM'e bırakılmaz).
SAGLIK_BILGI_KAPANIS = ("Bu genel bir bilgidir, size özel bir tavsiye değildir; kendi durumunuz için "
                        "doktorunuza danışın.")
SAGLIK_BILGI_VEREMIYORUM = ("Bu konuda size şu an güvenilir bir bilgi veremiyorum. Lütfen doktorunuza ya da "
                            "eczacınıza danışın.")
SAGLIK_SPESIFIK_GIRIS = {
    "doz": "İlaç dozu ve kullanım sıklığı kişiye göre belirlenir; bu konuda size özel bilgi veremem.",
    "birakma": ("İlaca başlama, ilacı bırakma ya da ara verme kararını kendiniz vermeyin; bu konuda size özel "
                "yanıt veremem."),
    "tahlil": "Tahlil ve ölçüm sonuçlarını yorumlayamam.",
    "teshis": "Belirtilere bakarak teşhis koyamam.",
    "iki_ilac": "İki ilacın sizin durumunuzda birlikte kullanılıp kullanılamayacağını söyleyemem.",
    "kisisel_ilac": "Bir ilacın sizin durumunuza uygun olup olmadığını söyleyemem.",
    "ilac_onerisi": "Size ilaç öneremem.",
}
SAGLIK_SPESIFIK_KAPANIS = ("Lütfen doktorunuza ya da eczacınıza danışın; kendinizi kötü hissediyorsanız 112'yi "
                           "arayın.")
# Onay beklenirken verilen bilgi yanıtından sonra (seçili randevu korunur).
SAGLIK_ONAY_HATIRLAT = "Seçtiğiniz randevu da onayınızı bekliyor."
# LLM metninin azami uzunluğu (karakter) ve en çok cümle sayısı; sesli okunur (TTS_AZAMI_KARAKTER 600).
SAGLIK_BILGI_AZAMI_KARAKTER = 450
SAGLIK_BILGI_AZAMI_CUMLE = 4
# Yaygın marka → etken madde (LLM "Parol"ü "parola" sanıyordu). Yalnız soruda geçen markanın notu yüke eklenir;
# kişisel veri değildir. Yanıtta etken maddenin adı geçebilir (denetim "başka ilaç" saymaz).
ILAC_MARKA_ETKEN = {
    "parol": "parasetamol", "minoset": "parasetamol", "tylol": "parasetamol", "calpol": "parasetamol",
    "aspirin": "asetilsalisilik asit", "coraspin": "asetilsalisilik asit", "ecopirin": "asetilsalisilik asit",
    "majezik": "flurbiprofen", "arveles": "deksketoprofen", "nurofen": "ibuprofen", "apranax": "naproksen",
    "voltaren": "diklofenak", "dolorex": "diklofenak", "augmentin": "amoksisilin ve klavulanik asit",
    "klamoks": "amoksisilin ve klavulanik asit", "cipro": "siprofloksasin", "glifor": "metformin",
    "glucophage": "metformin", "diamicron": "gliklazid", "eliquis": "apiksaban", "xarelto": "rivaroksaban",
    "coumadin": "varfarin", "plavix": "klopidogrel", "beloc": "metoprolol", "concor": "bisoprolol",
    "norvasc": "amlodipin", "euthyrox": "levotiroksin", "levotiron": "levotiroksin", "lasix": "furosemid",
    "xanax": "alprazolam", "cipralex": "essitalopram", "lustral": "sertralin", "prozac": "fluoksetin",
    "nexium": "esomeprazol", "lansor": "lansoprazol", "ventolin": "salbutamol", "zyrtec": "setirizin",
    "aerius": "desloratadin", "lyrica": "pregabalin",
}
SAGLIK_BILGI_AZAMI_SESLI = 590

# Genel bilgi istemi. KVKK: yüke yalnız (kimliği maskelenmiş) kullanıcı sorusu girer; hasta kaydı, ad, TC,
# hekim talimatı girmez (bkz. llm_siniri.saglik_bilgi_yuku_kur). Çıktı koddan ayrıca denetlenir.
SAGLIK_BILGI_ISTEMI = """Sen bir hastanenin mobil sağlık uygulamasında genel sağlık bilgisi veren yardımcısın. Kullanıcının sorusuna Türkçe, sade ve kısa bir GENEL BİLGİ yanıtı yaz. Yanıt sesli okunacak.

Kurallar:
1. 2 ile 3 kısa cümle yaz, en fazla 50 kelime. Yalnız düz metin: madde işareti, numara, başlık, kalın yazı, emoji ve parantez kullanma.
2. Soru her zaman bir sağlık sorusudur; sorudaki kelimeleri sağlık dışı bir anlamda yorumlama. Yalnız genel ve yaygın kabul gören bilgiyi ver. Emin değilsen ya da soruyu anlamadıysan yalnızca "Bu konuda kesin bilgi veremiyorum." yaz.
3. Teşhis koyma; kişinin hangi hastalığı olabileceğini tahmin etme.
4. Hiçbir sayı ve rakam kullanma: doz, miligram, tablet sayısı, günde kaç kez, kaç gün, litre gibi miktarlar verme.
5. Kişisel karar verme ve izin verme: "sizin için uygundur", "kullanabilirsiniz", "bırakabilirsiniz", "devam edin", "kullanın", "alın" deme. İlaca başlamayı, ilacı bırakmayı ya da dozu değiştirmeyi önerme. Genel ifadeler kullan: "genellikle", "bazı kişilerde", "önerilir".
6. Soruda adı geçmeyen hiçbir ilacın ya da markanın adını yazma; ilaç önerme. Gerekirse "ilaç kullanmadan önce eczacınıza danışın" de.
7. Ani, şiddetli ya da tehlikeli belirtilerden söz ediliyorsa 112'yi aramayı söyle.
8. Yanıtın sonuna "doktorunuza danışın" gibi bir kapanış cümlesi ekleme; uygulama bunu kendisi ekler.
9. [ad], [gizli numara] gibi köşeli parantezli ifadeler gizlenmiş kişisel bilgidir; onları tekrarlama.
10. Yalnız soruyu yanıtla; not, açıklama, soru hakkında yorum ekleme. Bir ilacı ya da hastalığı tanımıyorsan uydurma, "Bu konuda kesin bilgi veremiyorum." yaz.
11. Mesajda "Konu:" satırı varsa kullanıcı o şikâyet için doktora gitmeden önce evde ne yapabileceğini soruyor. Yalnız genel öz bakım öner: yeterli sıvı almak, dinlenmek, hafif beslenmek gibi. İlaç, ilaç adı ya da ilaç türü önerme. Bitki çayı, zencefil gibi bir yöntemden YALNIZ soruda geçiyorsa söz et; o zaman kesin tedavi iddiası kurma, bazı kişilerin rahatlatıcı bulabileceğini ama bunun bir tedavi olmadığını temkinli bir dille söyle. Her cümle tam ve anlamlı olsun; örnek yanıtı kopyalama, sorulan konuya göre yaz. "Doktora başvurun" cümlesini sen yazma; uygulama ekler.

Örnek soru: Konu: mide bulantısı / Mide bulantısı için evde neler yapılabilir? Bitki çayı iyi gelir mi?
Örnek yanıt: Mide bulantısında suyu az az ve sık yudumlamak, yağlı ve ağır yiyeceklerden uzak durup kuru ve hafif yiyecekler seçmek genellikle rahatlatır. Dinlenmek ve temiz hava almak da iyi gelebilir. Ilık bitki çaylarını bazı kişiler rahatlatıcı bulur ama bunlar bir tedavi yerine geçmez.

Örnek soru: Konu: boğaz ağrısı / Boğaz ağrısı için evde neler yapılabilir?
Örnek yanıt: Boğaz ağrısında ılık sıvılar içmek ve yeterince su tüketmek boğazı rahatlatabilir. Sesinizi dinlendirmek, sigara dumanından ve çok kuru havadan uzak durmak da genellikle iyi gelir.

Örnek soru: Aspirin ne işe yarar?
Örnek yanıt: Aspirin ağrıyı, ateşi ve iltihabı azaltan bir ilaçtır. Bazı kişilerde hekim önerisiyle kanın pıhtılaşmasını azaltmak için de kullanılır. Mide kanaması geçirmiş ya da kanamaya yatkın kişilerde sorun yaratabilir.

Örnek soru: Grip ile soğuk algınlığının farkı ne?
Örnek yanıt: İkisi de virüslerle bulaşır ama grip genellikle daha ağır geçer. Grip yüksek ateş, kas ağrısı ve belirgin halsizlikle ani başlar; soğuk algınlığında daha çok burun akıntısı, hapşırma ve hafif boğaz ağrısı olur. Nefes darlığı ya da çok yüksek ateş olursa gecikmeden 112 aranmalıdır."""
SAGLIK_BILGI_MARKA_NOTU = "(Bilgi: {marka}, {etken} etken maddeli bir ilacın ticari adıdır.)"

# --------------------------------------------------------------------------
# Şikâyet için "evde ne yapabilirim / ne iyi gelir / randevu değil, fikir ver" (TAVSİYE) yolu
# (``ses_servisi/tavsiye.py``, orkestratörde ``_tavsiye_yolu``). LLM'e kullanıcının cümlesi GİTMEZ: soru koddan,
# aşağıdaki kapalı listelerden kurulur ("Konu: mide bulantısı / Mide bulantısı için evde neler yapılabilir? Bitki
# çayı iyi gelir mi?"). Acil ifadeler bu yoldan ÖNCE yakalanır; ciddi belirti, ilaç/doz, teşhis, çocuk ve gebelik
# bağlamında bu yol devreye girmez (mevcut akış). Kalıplar normalize metinde kelime başından önek eşleşmesidir.
# --------------------------------------------------------------------------
# (kod, konu adı — LLM'e ve yanıta giden sabit ad, randevu teklif edilecek poliklinik ya da None, kalıplar).
# Sıra önemlidir: ilk eşleşen konu alınır (özelden genele; "midem" en sonda genel mide konusudur).
TAVSIYE_KONULARI: tuple[tuple[str, str, str | None, tuple[str, ...]], ...] = (
    ("bulanti", "mide bulantısı", "DAHILIYE",
     ("bulantı", "bulanıyor", "midem bulan", "mide bulan", "midem kalkıyor", "kusacak gibi", "kusma hissi",
      "midem ağzıma geliyor")),
    ("kusma", "kusma", "DAHILIYE", ("kusuyorum", "kustum", "kusma", "kusmak", "kusuyor")),
    ("ishal", "ishal", "DAHILIYE", ("ishal",)),
    ("kabizlik", "kabızlık", "DAHILIYE", ("kabız",)),
    ("mide_yanmasi", "mide yanması ve reflü", "DAHILIYE",
     ("midem yan", "mide yanma", "reflü", "ekşime", "midem ekşi")),
    ("hazimsizlik", "hazımsızlık ve şişkinlik", "DAHILIYE",
     ("hazımsızl", "midem şiş", "şişkinl", "gaz sancı", "midem dol", "midem ağır")),
    ("mide_agrisi", "mide ağrısı", "DAHILIYE", ("midem ağrı", "mide ağrı", "midem kramp", "midem sancı", "gastrit")),
    ("karin_agrisi", "karın ağrısı", "DAHILIYE",
     ("karnım ağrı", "karın ağrı", "karnım sancı", "karın sancı", "karnım kramp", "karnım guruld")),
    ("bas_agrisi", "baş ağrısı", "NOROLOJI", ("başım ağrı", "baş ağrı", "başağrı", "migren", "başım zonkl")),
    ("bas_donmesi", "baş dönmesi", None, ("başım dönüyor", "baş dönme", "başım dönme")),
    ("bogaz_agrisi", "boğaz ağrısı", "KBB",
     ("boğazım ağrı", "boğaz ağrı", "boğazım yan", "boğazım kuru", "boğazım tahriş", "boğazım gıcık",
      "boğazım kaşın", "yutkunurken")),
    ("oksuruk", "öksürük", "DAHILIYE", ("öksürü",)),
    ("burun", "burun tıkanıklığı ve akıntısı", "KBB",
     ("burnum tıkal", "burnum tıkan", "burnum akıyor", "burun tıkan", "burun akıntı", "burnum akıntı")),
    ("soguk_alginligi", "soğuk algınlığı ve grip", "DAHILIYE",
     ("grip", "nezle", "soğuk algın", "üşüttüm", "üşütmüş", "üşütme")),
    ("ates", "ateş", "DAHILIYE", ("ateşim", "ateş")),
    ("halsizlik", "halsizlik ve yorgunluk", "DAHILIYE", ("halsiz", "yorgunum", "yorgunluk", "çok yorgun", "bitkin")),
    ("uykusuzluk", "uykusuzluk", None, ("uyuyamıyorum", "uykusuzl", "uyku sorun", "uykum kaç", "uyuyamıyor")),
    ("stres", "stres ve gerginlik", None, ("stres", "gerginim", "gerginlik", "kaygı")),
    ("bel_agrisi", "bel ağrısı", "ORTOPEDI", ("belim ağrı", "bel ağrı", "belim tutul")),
    ("boyun_agrisi", "boyun ağrısı", "ORTOPEDI", ("boynum ağrı", "boyun ağrı", "boynum tutul")),
    ("sirt_agrisi", "sırt ağrısı", "ORTOPEDI", ("sırtım ağrı", "sırt ağrı")),
    ("diz_agrisi", "diz ağrısı", "ORTOPEDI", ("dizim ağrı", "diz ağrı", "dizlerim ağrı")),
    ("kas_agrisi", "kas ağrısı", "ORTOPEDI", ("kas ağrı", "kaslarım ağrı", "kas tutul", "kramp")),
    ("dis_agrisi", "diş ağrısı", None, ("dişim ağrı", "diş ağrı", "dişim sızl")),
    ("kulak_agrisi", "kulak ağrısı", "KBB", ("kulağım ağrı", "kulak ağrı")),
    ("goz_kizarikligi", "göz kuruluğu ve kızarıklığı", "GOZ",
     ("gözüm kızar", "gözlerim kızar", "göz kurul", "gözlerim kuru", "gözüm kuru", "gözlerim yan", "gözüm yan",
      "gözüm sulan", "gözlerim sulan", "gözüm kaşın", "gözlerim kaşın")),
    ("kasinti", "cilt kaşıntısı ve kuruluğu", "DERMATOLOJI",
     ("kaşıntı", "kaşınıyor", "cildim kuru", "cilt kuruluğ", "cildim kaşın")),
    ("gunes_yanigi", "güneş yanığı", "DERMATOLOJI", ("güneş yanığ", "güneşte yandım")),
    ("sivilce", "sivilce", "DERMATOLOJI", ("sivilce", "akne")),
    ("idrar_yanmasi", "idrar yaparken yanma", "UROLOJI",
     ("idrarda yanma", "idrar yan", "işerken yan", "idrarım yan", "idrar yaparken yan")),
    ("adet_sancisi", "adet sancısı", "KADIN_DOGUM", ("adet sancı", "adet ağrı", "regl ağrı", "regl sancı")),
    ("mide", "mide rahatsızlığı", "DAHILIYE", ("midem", "mide")),
)
# Konuşma tanıma toleransı (normalize metinde düzenli ifade → konu). Yalnız örnekte ve provada görülen, organ
# kelimesine bağlı bozulmalar: "midem çok kullanıyor" (bulanıyor), "midem bulunuyor/bulanık". Araya yalnız
# pekiştirme kelimesi girebilir; "midem için ilaç kullanıyorum" eşleşmez. Rastgele kelimeler şikâyete çevrilmez:
# şikâyet kelimesi tamamen bozulduysa ("bu lambasına") konu oturumdaki son şikâyetten alınır.
TAVSIYE_STT_KALIPLARI: tuple[tuple[str, str], ...] = (
    (r"(?<!\w)mide\w*( (cok|biraz|hep|surekli|fena|bayagi|epey|sabahlari|aksamlari|yine|gene|da|de))* "
     r"(kullan|bulun|bulan|bulam|bulandi|bulaniy|bulanik|bulaniyo)\w*", "bulanti"),
    (r"(?<!\w)(bulanti|bulantis)\w*", "bulanti"),
)
# Tavsiye isteği işaretleri. GÜÇLÜ: şikâyet kelimesi olmasa da tavsiye isteğidir (konu oturumdan ya da "Hangi
# şikâyetiniz için?" sorusuyla bulunur). ZAYIF: yalnız cümlede ya da oturumda bir şikâyet konusu varken.
TAVSIYE_GUCLU_IFADELER = (
    "doktora gitmeden", "doktora gitmek istemiyorum", "doktora gitmeyeceğim", "doktora gitmesem",
    "doktordan önce", "hekime gitmeden", "hastaneye gitmeden", "hastaneye gitmek istemiyorum", "gitmeden önce",
    "evde ne", "evde yapabilece", "evde yapılabilece", "evde neler", "bir şey önerir", "bir şey öner",
    "ne önerirsin", "ne önerirsiniz", "öneriniz var", "önerin var", "önerin ne", "öneriniz ne", "tavsiye ver",
    "tavsiye eder", "tavsiyen", "tavsiyeniz", "fikir ver", "fikrin var", "fikriniz var", "bir fikir",
    "yapabileceğim bir şey", "yapabileceğin bir şey", "yapabileceğiniz bir şey", "yapılabilecek bir şey",
    "yapabileceğim başka", "yapılacak bir şey", "ne iyi gelir", "iyi gelir mi", "iyi gelebilir", "iyi gelen",
    "nasıl geçer", "geçmesi için", "geçirmek için", "rahatlamak için", "nasıl rahatla", "hafifletmek için",
)
TAVSIYE_ZAYIF_IFADELER = (
    "ne yemeli", "zararlı mı", "doğru mu", "ne yapabilirim", "ne yapabiliriz", "ne yapılabilir", "ne yapmalıyım", "ne yapayım", "ne yapmam lazım",
    "ne yapmam gerek", "ne yapsam", "ne yapılır", "ne yapmalı", "evde", "öneri", "tavsiye", "fikir",
    "öğrenmek istiyorum", "iyi gelir", "çay", "bitki",
)
# Kullanıcı randevu istemediğini söylüyor (öneriye "istemiyorum" dahil). "Hayır" / "başka" burada YOK: ekrandaki
# "Başka bölüm" düğmesi "hayır" gönderir; o başka poliklinik sorusudur (mevcut davranış).
RANDEVU_RET_IFADELERI = (
    "randevu değil", "randevu bakmak değil", "randevu bakma", "randevu istemiyorum", "randevu almak istemiyorum",
    "randevu almayacağım", "randevuya gerek yok", "randevu gerekmez", "randevu lazım değil", "randevu sonra",
    "doktora gitmek istemiyorum", "doktora gitmeyeceğim", "doktora gitmeden", "hastaneye gitmek istemiyorum",
    "hastaneye gitmeden", "gitmek istemiyorum",
)
# Öneriden ("randevu bakmamı ister misiniz?") sonra tek başına söylenen ret: randevu istenmiyor.
ONERI_RANDEVU_RET_TAM = ("istemiyorum", "istemem", "gerek yok", "vazgeç", "vazgeçtim", "hayır istemiyorum",
                         "hayır gerek yok", "yok istemiyorum", "şimdilik istemiyorum", "şimdilik gerek yok",
                         "hayır istemem", "yok gerek yok")
# Bu bağlamlarda tavsiye yolu devreye girmez (mevcut akış): çocuk/gebelik (özel değerlendirme gerekir), doktor ya
# da poliklinik tavsiyesi ("hangi doktoru tavsiye edersin").
TAVSIYE_ENGEL_IFADELERI = (
    "çocuğum", "çocuğumun", "çocuğuma", "oğlum", "kızım", "bebeğim", "bebeğimin", "bebek", "hamile", "gebe",
    "emziriyorum", "lohusa", "hangi doktor", "hangi hekim", "hangi bölüm", "hangi poliklinik", "doktor tavsiye",
    "hekim tavsiye", "hastane tavsiye",
)
# Alt soru: kullanıcının sorduğu ev yöntemi (normalize metinde düzenli ifade → koddan sabit soru). En çok iki tane.
TAVSIYE_ALT_SORULARI: tuple[tuple[str, str], ...] = (
    (r"(?<!\w)(cay|bitki|ihlamur|papatya|adacay|rezene)", "Bitki çayı iyi gelir mi?"),
    (r"(?<!\w)zencefil", "Zencefil iyi gelir mi?"),
    (r"(?<!\w)nane", "Nane iyi gelir mi?"),
    (r"(?<!\w)limon", "Limon iyi gelir mi?"),
    (r"(?<!\w)(bal|balli|bala|bali)(?!\w)", "Bal iyi gelir mi?"),
    (r"(?<!\w)(soda|maden suy)", "Maden suyu iyi gelir mi?"),
    (r"(?<!\w)(yogurt|ayran|kefir)", "Yoğurt ya da ayran iyi gelir mi?"),
    (r"(?<!\w)(su ic|sicak su|ilik su|bol su|sivi)", "Su ve sıvı almak iyi gelir mi?"),
    (r"(?<!\w)(ne yeme|ne yiye|ne yiyebil|yemek|beslen|ne yesem)", "Nasıl beslenmek iyi gelir?"),
    (r"(?<!\w)(kompres|buz|termofor|sicak torba|sicak uygula|soguk uygula)", "Sıcak ya da soğuk uygulama iyi gelir mi?"),
    (r"(?<!\w)(gargara|tuzlu su)", "Tuzlu suyla gargara iyi gelir mi?"),
    (r"(?<!\w)buhar", "Buhar solumak iyi gelir mi?"),
    (r"(?<!\w)(egzersiz|yuruyus|spor|esneme)", "Hafif egzersiz iyi gelir mi?"),
    (r"(?<!\w)masaj", "Masaj iyi gelir mi?"),
    (r"(?<!\w)(dinlen|uyu)", "Dinlenmek iyi gelir mi?"),
)
TAVSIYE_ALT_AZAMI = 2
# Tavsiye yanıtında LLM metni en çok bu kadar cümle (küçük model uzadıkça konudan sapıyordu; canlı deneme).
TAVSIYE_AZAMI_CUMLE = 3
# Birinci şahıs şikâyet anlatımı (SAGLIK_KISISEL_SIKAYET'e ek): "grip oldum", "kaşıntım var", "öksürüğüm geçmiyor".
TAVSIYE_KISISEL_IFADELER = (
    "oldum", "çekiyorum", "yaşıyorum", "geçiriyorum", "tuttu", "geçmiyor", "geçmedi", "başladı", "kaşıntım",
    "ağrım", "öksürüğüm", "ishalim", "bulantım", "üşüttüm", "uyuyamıyorum", "kustum", "kusuyorum", "yandım",
    "sancım", "kramp gir", "gerginim", "stresliyim",
)
# Konuya özgü sabit güvenlik notu (koddan, LLM metninden sonra). Hangi durumda beklenmemesi gerektiği.
TAVSIYE_UYARI_NOTLARI = {
    "karin_agrisi": "Ağrı çok şiddetliyse ya da ateş ve kusmayla birlikteyse beklemeden acil servise başvurun.",
    "mide_agrisi": "Ağrı çok şiddetliyse ya da kusmada kan görürseniz beklemeden acil servise başvurun.",
    "bas_agrisi": ("Ani başlayan çok şiddetli baş ağrısı, konuşma bozukluğu ya da yüzde kayma varsa beklemeyin, "
                   "112'yi arayın."),
    "bas_donmesi": "Bayılma, konuşma bozukluğu ya da kolda güçsüzlük varsa beklemeyin, 112'yi arayın.",
    "ates": "Ateş çok yüksekse ya da nefes darlığı, bilinç bulanıklığı eşlik ediyorsa 112'yi arayın.",
    "kusma": "Sıvıları tutamıyorsanız ya da kusmukta kan varsa beklemeden acil servise başvurun.",
    "ishal": "Sıvıları tutamıyorsanız ya da dışkıda kan varsa beklemeden bir sağlık kuruluşuna başvurun.",
}
TAVSIYE_SORU = "{konu} için evde neler yapılabilir?"
# Sesli yanıt: LLM metni + bu sabit satır + SAGLIK_BILGI_KAPANIS (+ ısrarsız randevu teklifi). Hepsi koddan.
TAVSIYE_SURERSE = "Şikâyetiniz sürer ya da artarsa bir doktora başvurun."
TAVSIYE_RANDEVU_TEKLIFI = "İsterseniz {poliklinik} polikliniğinden randevu da bakabilirim."
TAVSIYE_KONU_SOR = ("Hangi şikâyetiniz için soruyorsunuz? Örneğin 'mide bulantısı' ya da 'baş ağrısı' "
                    "diyebilirsiniz.")
RANDEVU_RET_YANIT = "Peki, randevu bakmıyorum."
RANDEVU_RET_TAVSIYE_TEKLIFI = "İsterseniz {konu} için evde dikkat edilebilecek genel önerileri anlatabilirim."
RANDEVU_RET_GENEL = "Başka bir konuda yardımcı olabilirim."
# Tavsiye teklifinden ("…anlatabilirim") sonra kabul (cümlenin tamamı, normalize).
TAVSIYE_KABUL_IFADELERI = ("evet", "olur", "tamam", "evet lütfen", "olur lütfen", "lütfen", "isterim",
                           "evet isterim", "tabii", "tabi", "elbette", "anlat", "anlatın", "anlatır mısın",
                           "anlatır mısınız", "evet anlat", "evet anlatın", "olur anlat", "lütfen anlat",
                           "evet lütfen anlat", "tamam anlat", "dinliyorum")
# Randevu akışı dışında anlaşılmayan cümle: randevuya zorlamayan nötr yanıt (akış ortasında eski davranış).
ANLASILAMADI_NOTR = ("Sizi tam anlayamadım. Randevu alabilir, bir şikâyetiniz için genel bilgi isteyebilir ya da "
                     "ilaçlarınızı sorabilirsiniz; ne istediğinizi kısaca söyler misiniz?")
ANLASILAMADI_NOTR_EKRAN = ("Sizi anlayamadım. Örneğin 'yarın Kardiyoloji', 'midem bulanıyor ne yapabilirim' ya da "
                           "'ilaçlarım neler' diyebilir, ekrandaki düğmeleri de kullanabilirsiniz.")
# Randevu akışı dışında tek başına "hayır / istemiyorum / değil": poliklinik sorulmaz.
RET_NOTR = "Peki. Başka nasıl yardımcı olabilirim?"

# --------------------------------------------------------------------------
# Şikâyet → poliklinik ÖNERİSİ (teşhis değil; kullanıcı teyit eder). LLM'siz, kural.
# Kelime başından önek eşleşmesi (normalize: küçük harf + Türkçe karakter katlama), böylece ekli hâller
# ("kalbimde", "dizlerim") ve STT'nin Türkçe karaktersiz yazımı ("gozum", "kalbim agriyor") tutar.
# Önekler bilerek dar: "bel" (belki), "diz" (dizi), "kas" (kasım), "boyun" (boyunca), "şaşı" (şaşırdım),
# "kırık" (halsizlik/kırıklık) gibi rastlantılar yerine ekli biçimler yazıldı. Bir kalıp başka bir
# polikliniğin daha uzun kalıbının içinde kalıyorsa sayılmaz ("göz tansiyonu" → Göz, Kardiyoloji değil).
# --------------------------------------------------------------------------
SIKAYET_KALIPLARI: dict[str, tuple[str, ...]] = {
    "GOZ": (
        "gözüm", "gözler", "göz ağrı", "göz kızar", "göz kaşın", "göz sulan", "göz yaşar", "göz kurul",
        "göz tansiyon", "göz çapak", "göz seğir", "göz kapağ", "göz kontrol", "göz muayene", "gözlük", "gözlüğ",
        "bulanık gör", "görme bulanık", "bulanıklaş", "net gör", "göremiyorum", "çift gör", "görmem", "görme kayb",
        "görme sorun", "görme bozuk", "görüşüm bulanık", "uzağı gör", "yakını gör", "katarakt", "glokom",
        "şaşılık", "şaşılığ", "arpacık", "lens", "gözüm kaşın", "gözlerim kaşın", "gözüm kızar", "gözlerim kızar",
    ),
    "KARDIYOLOJI": (
        "kalp", "kalb", "çarpıntı", "göğüs ağrı", "göğsüm", "göğüs sıkış", "tansiyon", "hipertansiyon",
        "nefes darlığ", "nefesim daral", "nefes nefese", "ritim bozuk", "ritm bozuk", "aritmi", "efor", "ekg",
        "anjiyo", "stent", "bypass", "baypas",
    ),
    "ORTOPEDI": (
        "belim", "bel ağrı", "bel fıtı", "boynum", "boyun ağrı", "boyun fıtı", "boyun düzleş", "dizim", "dizler",
        "diz ağrı", "diz kapağ", "omuz", "omzum", "eklem", "kemik", "kas ağrı", "kaslarım", "kas tutul", "kas yırt",
        "kırıldı", "kırılmış", "kırdım", "kırığı", "kırık var", "kırık mı", "burkul", "burktum", "burkma", "çıkık", "çıkığ",
        "fıtık", "fıtığ", "menisk", "kireçlen", "sırtım", "sırt ağrı", "bileğim", "bilek", "topuğum", "topuk",
        "dirsek", "kalçam", "kalça ağrı", "skolyoz", "düz taban", "tendon", "kemiğ",
    ),
    "DAHILIYE": (
        "mide", "hazımsızlık", "hazımsızlığ", "reflü", "gastrit", "ülser", "bulantı", "kusma", "kusuyorum", "ishal", "kabız",
        "karnım", "karın ağrı", "şeker", "diyabet", "insülin", "tiroid", "tiroit", "guatr", "halsiz", "yorgunluk", "yorgunluğ",
        "yorgunum", "çok yorgun", "ateş", "grip", "nezle", "soğuk algın", "öksürük", "öksürü", "kansızlık", "kansızlığ",
        "anemi", "demir eksik", "kolesterol", "genel kontrol", "check up", "checkup", "çekap", "kan tahlil",
        "tahlil", "kan değer", "vitamin eksik", "b12", "d vitamin",
    ),
    # ---- Özel hastane branşları (MedTech). Organ yeri belirten uzun kalıplar ("gözüm kaşınıyor",
    # "kulağım kaşınıyor") kısa genel kalıbı ("kaşın" → Dermatoloji) örtüşmede bastırır.
    "KBB": (
        "kulağım", "kulaklarım", "kulak ağrı", "kulak çınla", "kulağımda", "kulak akıntı", "kulak tıkan",
        "kulağım kaşın", "kulağım akıyor", "işitme", "duyamıyorum", "iyi duymuyorum", "az duyuyorum", "burnum",
        "burnumda", "burun tıkan", "burun kanama", "burun eğri", "geniz", "sinüzit", "boğazım", "boğaz ağrı",
        "boğazımda", "bademcik", "ses kısıklığ", "sesim kısıl", "sesim çıkmıyor", "horla", "horluyorum",
    ),
    "DERMATOLOJI": (
        "cildim", "cildimde", "ciltte", "cilt kuruluğ", "cilt lekes", "döküntü", "kaşıntı", "kaşınıyor",
        "kaşıntım", "sivilce", "akne", "egzama", "sedef", "kurdeşen", "ürtiker", "siğil", "benlerim",
        "ben kontrol", "vücudumda ben", "saç dökül", "saçım dökül", "saçlarım dökül", "mantar", "yüzümde leke",
        "lekelerim", "güneş yanığ", "uçuk",
    ),
    "KADIN_DOGUM": (
        "hamile", "hamilelik", "gebeyim", "gebelik", "gebe kal", "bebek bekliyorum", "adetim", "adet gör",
        "adet düzensiz", "adet sancı", "adetlerim", "adet kanama", "regl", "menopoz", "jinekolojik", "smear",
        "rahim", "yumurtalık", "vajinal", "akıntım", "doğum kontrol", "kısırlık", "tüp bebek",
        "çocuğum olmuyor", "lohusa",
    ),
    "COCUK_SAGLIGI": (
        "çocuğum", "çocuğumun", "çocuğuma", "oğlum", "oğlumun", "kızım", "kızımın", "bebeğim", "bebeğimin",
        "bebeğime", "çocuk aşı", "bebek aşı", "aşı takvim",
    ),
    "NOROLOJI": (
        "başım ağrı", "baş ağrı", "başağrı", "migren", "uyuşma", "uyuşuyor", "karıncalan", "titreme",
        "ellerim titri", "elim titri", "unutkan", "hafızam", "denge kayb", "dengemi kayb", "sara hastal",
        "sara nöbet", "epilepsi", "sinir sıkış", "yüz felci", "parkinson", "alzheimer",
    ),
    "UROLOJI": (
        "idrar yap", "idrarım", "idrarda", "idrar yolu", "idrar kaçır", "idrar yan", "sık idrar", "idrara çık",
        "idrar zorl", "çişim", "işerken", "prostat", "böbrek taş", "böbreğimde taş", "mesane", "taş düşür",
    ),
    "GENEL_CERRAHI": (
        "kasık fıtığ", "kasık fıtık", "göbek fıtığ", "göbek fıtık", "safra kese", "safrakese", "safra taş",
        "apandis", "hemoroid", "basur", "makat", "anal fiss", "kıl dönme", "yağ beze", "memede", "memem",
        "meme kontrol", "göğsümde kitle", "tiroid nodül", "guatr ameliyat",
    ),
}
# Birden çok polikliniğe işaret eden şikâyette bu poliklinikler (sırayla) öncelikli: "hamileyim, karnım
# ağrıyor" → Kadın Doğum; "çocuğumun ateşi var" → Çocuk Sağlığı (Dahiliye değil).
SIKAYET_ONCELIKLI = ("KADIN_DOGUM", "COCUK_SAGLIGI")
# Şikâyetle birlikte tavsiye/teşhis sorusu ("midem ağrıyor hangi ilacı almalıyım"): yorum yapılmaz
# (tıbbi yanıt şablonu), yalnız bölüm önerisi eklenir.
TIBBI_TAVSIYE_SORULARI = ("ne yapmalıyım", "ne yapayım", "ne yapmam lazım", "ne yapmam gerek", "hangi ilaç",
                          "ne içmeliyim", "ne almalıyım", "ne kullanmalıyım", "tedavi", "teşhis", "tanı koy",
                          "ciddi mi", "tehlikeli mi", "normal mi", "neden olur", "nedeni ne", "ne olabilir",
                          "hastalık mı", "geçer mi", "kanser mi")
# "Hangi bölüme gitmeliyim": şikâyet varsa öneri, yoksa poliklinik listesi sorulur.
YONLENDIRME_SORULARI = ("hangi bölüm", "hangi poliklinik", "hangi doktor", "hangi hekim", "nereye gitmeli",
                        "nereye gideyim", "nereye başvur")
# Öneriden sonraki kısa yanıt (cümlenin TAMAMI, normalize edilmiş). Bu bir RANDEVU ONAYI DEĞİLDİR: yalnız
# önerilen polikliniği seçer; randevu yine ayrı açık onayla oluşur (ACIK_ONAY_IFADELERI + onay sorusu).
ONERI_KABUL_IFADELERI = ("evet", "olur", "tamam", "bakalım", "evet bakalım", "olur bakalım", "tamam bakalım",
                         "evet lütfen", "olur lütfen", "lütfen", "evet bak", "bak", "bakın", "bakar mısın",
                         "bakar mısınız", "evet isterim", "isterim", "aynen", "uygun", "evet olur", "tamam olur",
                         "evet tamam", "kabul", "olsun", "evet olsun", "olur olsun", "evet onaylıyorum",
                         "onaylıyorum", "tabii", "tabi", "elbette", "evet istiyorum", "istiyorum")
ONERI_RET_IFADELERI = ("hayır", "istemiyorum", "istemem", "başka", "olmaz", "gerek yok", "yok", "vazgeç",
                       "farklı")

# --------------------------------------------------------------------------
# Acil durum: randevu akışına sokulmaz, her adımda önceliklidir, LLM'e gitmez. Liste bilerek dar ve
# somut: "acil randevu", "acele", "acil servis nerede" gibi günlük ifadeler bu listede YOKTUR.
# --------------------------------------------------------------------------
ACIL_IFADELERI = (
    "nefes alamıyor", "nefes alamıyoruz", "nefesim kesil", "nefesim durdu", "boğuluyor",
    # STT toleransı: demo provasında Whisper "Nefes alamıyorum"u bir kez "mefes alamıyorum" yazdı ve acil
    # kaçtı. Acilde yanlış pozitif (112 hatırlatması) yanlış negatiften çok daha az zararlıdır.
    "mefes alamıyor",
    "göğsümde şiddetli", "şiddetli göğüs ağrı", "göğsüm sıkışıyor", "göğsümde sıkışma", "göğsümde baskı",
    "kalp krizi geçiriyor", "kalp krizi mi geçir", "kriz geçiriyor",
    "bayılacak gibi", "bayılıyorum", "bayıldı", "bilincini kaybet", "bilinci kapan", "kendinden geçti",
    "uyanmıyor", "ayılmıyor",
    "felç geçiriyor", "felç oluyor", "felç geldi", "felç inmiş", "inme geçiriyor", "yüzüm kaydı", "yüzü kaydı",
    "ağzım kaydı", "ağzı kaydı", "dilim tutuldu",
    "kanama durmuyor", "kanamam durmuyor", "kanaması durmuyor", "kan durmuyor", "çok kan kaybed", "kan kus",
    "intihar", "kendimi öldür", "kendime zarar", "canıma kıy", "yaşamak istemiyorum", "ölmek istiyorum",
    "hayatıma son",
    "zehir içti", "zehir içtim", "çamaşır suyu içti", "çamaşır suyu yuttu", "deterjan içti", "deterjan yuttu",
    "kimyasal içti", "tuz ruhu içti", "pil yuttu", "ilaç yuttu", "nöbet geçiriyor", "havale geçiriyor", "ambulans", "imdat",
    # Genel bilgi yolu için gözden geçirildi (2026-11-16): bilgi sorusuna eklenmiş birinci şahıs ciddi belirti
    # ("göğsüm sıkışıyor, ne iyi gelir") LLM'e gitmeden burada yakalanır. Hepsi anlatım biçimidir; "zehirlenme
    # belirtileri nelerdir" gibi bilgi soruları yakalanmaz.
    "zehirlendim", "zehirlendi", "kutu ilaç içti", "bütün ilaçları içti", "çok fazla ilaç içti",
    "kendimi kes", "kendimi as", "kendimi öldürmek", "nöbet geçirdi", "kolumu kaldıramıyor", "konuşamıyorum",
    "yüzüm sarktı", "yüzümün bir tarafı", "en şiddetli baş ağrı", "kan tükürüyor", "göğsüm sıkıştı",
    "göğsüm çok ağrıyor", "nefes almakta çok zorlan",
    # Yönlendirme seti taraması (2026-11-16): aşırı doz anlatımı ("yanlışlıkla çok fazla hap içtim").
    "fazla hap içti", "fazla hap yuttu", "kutu hap içti", "bütün hapları içti", "çok fazla ilaç aldı",
    "çok fazla hap aldı", "ilaç kutusunu içti", "yanlışlıkla fazla ilaç", "yanlışlıkla fazla hap",
    # Ağır alerjik tepki belirtisi (yüz/dil/dudak şişmesi): her bağlamda 112. Soru biçimi ("şişer mi") yakalanmaz.
    "dilim şişti", "dilim şişiyor", "dudaklarım şişti", "dudaklarım şişiyor", "dudağım şişti", "yüzüm şişti",
    "yüzüm şişiyor", "boğazım kapanıyor", "boğazım şişti nefes",
    # Tavsiye yolu için gözden geçirildi (2026-11-16): kanlı kusma ve şiddetli karın ağrısı anlatımı.
    "kanlı kus", "kusmukta kan", "kusmuğumda kan", "kahve telvesi gibi kus", "kahve telvesi gibi bir şey kus",
    "şiddetli karın ağrı", "karnımda şiddetli ağrı", "karnım çok şiddetli ağrı", "dayanılmaz karın ağrı",
    "karnımda dayanılmaz", "karnım dayanılmaz",
)
# İkisi birlikte geçerse acil (her biri tek başına değil): göğüs ağrısına eşlik eden bulantı, terleme, nefes
# darlığı, kola/çeneye yayılma, bayılma hissi kalp krizi belirtisi olabilir ("midem bulanıyor ve göğsüm ağrıyor");
# ense sertliğiyle baş ağrısı / ateş; şiddetli karın ağrısı. Soru biçiminde de yakalanır ("göğsüm ağrıyor midem
# bulanıyor ne yapabilirim"): acilde yanlış pozitif yanlış negatiften daha az zararlıdır.
ACIL_BIRLIKTE: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("göğsüm ağrı", "göğsümde ağrı", "göğüs ağrı", "göğsüm acı", "göğsümde acı", "göğsümde bask", "göğsüm sıkış",
      "göğsüm sızl", "göğsümde sızı", "kalbim sıkış", "kalbim ağrı", "kalbimde ağrı",
      "kalbimde bask", "kalbim acı"),
     ("bulan", "bulantı", "kusu", "kustum", "terl", "soğuk ter", "nefes", "sol kol", "koluma", "kolum", "çeneme",
      "çenem", "bayıl", "başım dön", "baş dön", "sırtıma vur")),
    (("ense sert", "ensem sert", "ensem tutul", "boynumu eğemi", "boynumu bükemi"),
     ("ateş", "başım ağrı", "baş ağrı", "kusu", "bulan")),
    (("karnım", "karın", "midem"), ("şiddetli ağrı", "şiddetli sancı", "çok şiddetli", "dayanılmaz",
                                     "katlanılmaz")),
)
# Kalıp eşleşse de bu bağlamda acil sayılmaz ("burnumdan nefes alamıyorum" nezledir).
ACIL_ISTISNALARI: dict[str, tuple[str, ...]] = {
    "nefes alamıyor": ("burnum", "burnu", "burundan"),
    "mefes alamıyor": ("burnum", "burnu", "burundan"),
}

# --------------------------------------------------------------------------
# Sohbet ifadeleri (kural + şablon; LLM'e gitmez)
# --------------------------------------------------------------------------
# Cümlenin tamamı (dolgu kelimeleri atıldıktan sonra) bunlardan biriyse selamlaşmadır.
SELAM_IFADELERI = ("merhaba", "merhabalar", "selam", "selamlar", "selamün aleyküm", "selamun aleykum",
                   "günaydın", "iyi günler", "iyi akşamlar", "hey", "alo", "nasılsın", "nasılsınız",
                   "merhaba nasılsın", "merhaba nasılsınız")
SOHBET_DOLGU = ("asistan", "efendim", "hocam", "canım", "ya", "şey")
YARDIM_IFADELERI = ("ne yapabilirsin", "neler yapabilirsin", "ne yapabiliyorsun", "neler yapabiliyorsun",
                    "ne yapabilirsiniz", "neler yapabilirsiniz", "ne işe yarıyorsun", "ne sorabilirim",
                    "neler sorabilirim", "yardım", "nasıl çalışıyorsun", "nasıl yardımcı olabilirsin")
TESEKKUR_IFADELERI = ("teşekkür", "sağ ol", "sağol", "eyvallah", "eline sağlık", "çok sağ ol")
VEDA_IFADELERI = ("görüşürüz", "hoşça kal", "hoşçakal", "güle güle", "çıkış", "çıkmak istiyorum", "kapat",
                  "iyi geceler", "başka sorum yok", "başka bir şey yok", "bitirelim", "bitir")
# Sohbet ifadesi sayılması için en çok bu kadar kelime (uzun cümlede asıl istek aranır).
SOHBET_AZAMI_KELIME = 6

# Mevcut randevular: backend'de hastanın gelecek randevularını döndüren uç YOK; uydurulmaz, sabit şablon.
MEVCUT_RANDEVU_IFADELERI = ("randevularım", "mevcut randevu", "randevumu iptal", "randevumu sil",
                            "randevumu ertele", "randevumu değiştir", "randevumu göster", "randevumu öğren",
                            "randevum ne zaman", "randevum var mı", "randevum hangi", "aldığım randevu",
                            "yaklaşan randevu", "gelecek randevu", "randevu iptal", "randevuyu iptal",
                            "randevuyu ertele", "randevuyu sil")
MEVCUT_RANDEVU_DEGISIKLIK = ("iptal", "sil", "ertele", "değiştir")


# --------------------------------------------------------------------------
# LLM istemi ve şemalar
# --------------------------------------------------------------------------

NIYETLER = ("poliklinik_ve_zaman", "onay", "ret", "baska_saat", "secim", "fark_etmez", "poliklinik_degistir",
            "gun_degistir", "gecmis_muayene", "ilac_sorusu", "ilac_tavsiye", "tibbi_soru", "kimlik",
            "konu_disi", "belirsiz")

# KVKK: LLM'in işi YALNIZ anlamaktır; kullanıcıya gidecek hiçbir metin üretmez (şemada serbest
# yanıt alanı yoktur). Şema dışı alanlar yok sayılır (anlama._llm_ciktisini_dogrula).
ANLAMA_SEMASI: dict = {
    "type": "object",
    "properties": {
        "niyet": {"type": "string", "enum": list(NIYETLER)},
        "poliklinik": {"type": ["string", "null"], "enum": [*POLIKLINIK_KODLARI, None]},
        "gun": {"type": ["string", "null"], "description": "YYYY-MM-DD ya da null"},
        "saat_baslangic": {"type": ["string", "null"], "description": "HH:MM ya da null"},
        "saat_bitis": {"type": ["string", "null"], "description": "HH:MM ya da null"},
        "sira": {"type": ["integer", "null"], "description": "Sunulan seçeneklerden kaçıncısı (1, 2, 3…); sonuncusu -1"},
        "goreli": {"type": ["string", "null"], "enum": ["daha_gec", "daha_erken", None]},
        "ilac_adi": {"type": ["string", "null"], "description": "Kullanıcının sorduğu ilacın adı, söylediği gibi"},
        "ilac_konu": {"type": ["string", "null"], "enum": [*ILAC_KONULARI, None],
                      "description": "İlaç sorusunun konusu; ilaç sorusu değilse ya da emin değilsen null"},
    },
    "required": ["niyet", "poliklinik", "gun", "saat_baslangic", "saat_bitis"],
}

# Yalnız ilaç sorusunun KONU etiketi (ILAC_KONUSU adımı). Alan zorunludur: ANLAMA_SEMASI'nda isteğe bağlı
# olduğu için yerel model (yapılandırılmış çıktı) alanı hiç yazmıyordu ve etiket hep boş dönüyordu.
ILAC_KONU_SEMASI: dict = {
    "type": "object",
    "properties": {
        "ilac_konu": {"type": ["string", "null"], "enum": [*ILAC_KONULARI, None],
                      "description": "İlaç sorusunun konusu; emin değilsen null"},
    },
    "required": ["ilac_konu"],
}

# İstemde kişisel veri YOKTUR: yalnız tarih/takvim, poliklinik adları ve diyalog adımının sabit
# açıklaması (bkz. ses_servisi/llm_siniri.py — LLM yükünü kuran tek yer).
SISTEM_ISTEMI = """Sen bir hastanenin mobil sağlık asistanı uygulamasındaki sesli asistanın ANLAMA bileşenisin. Kullanıcıya hiçbir metin yazmazsın.
Görevin YALNIZCA kullanıcının son cümlesini anlamak ve aşağıdaki JSON'u doldurmaktır. Randevu oluşturamaz, onay veremezsin.

Bugün: {bugun_metni} (saat dilimi Europe/Istanbul, şu an {saat_metni}).
Takvim (bunu kullan, kendin hesaplama):
{takvim}

Poliklinikler (kod = ad):
{poliklinikler}

Diyalog adımı: {adim}

Niyetler:
- poliklinik_ve_zaman: kullanıcı bir poliklinik ve/veya gün/saat tercihi söylüyor.
- secim: ekranda sunulan saatlerden birini sırasıyla ("ilki", "ikincisi", "sonuncusu") ya da göreli ("daha geç", "daha erken") seçiyor.
- fark_etmez: gün/saat tercihi yok ("fark etmez", "en erken", "hangisi olursa").
- onay: seçili randevuyu onaylıyor ("evet", "onaylıyorum").
- ret: seçili randevuyu istemiyor / vazgeçiyor.
- baska_saat: başka saatleri görmek istiyor (gün ya da saat belirtmeden).
- poliklinik_degistir: başka bir poliklinik istiyor ama adını söylemiyor.
- gun_degistir: başka bir gün istiyor ama günü söylemiyor.
- gecmis_muayene: kendi geçmiş muayenesini, orada yazılan ilaçları ya da hekimin uyarılarını soruyor.
- ilac_sorusu: kendisine yazılmış bir ilaç hakkında soru soruyor (ne işe yarar, nasıl kullanılır, yan etki, alerji, yemek, alkol, unutma, hamilelik, birlikte kullanım, süre, doz).
- ilac_tavsiye: bir ilacın yerine başka ilaç gibi yukarıdaki konulara girmeyen bir ilaç tavsiyesi istiyor.
- tibbi_soru: şikâyet anlatıyor, yeni ilaç/tanı/tedavi soruyor ya da hangi bölüme gitmesi gerektiğini soruyor.
- kimlik: kimlik numarası, TC ya da kişisel bilgi söylüyor veya soruyor.
- konu_disi: randevu ve muayene bilgisiyle ilgisiz bir şey soruyor.
- belirsiz: anlaşılmıyor.

Kesin kurallar:
1. poliklinik yalnız kullanıcı bir polikliniği adıyla ya da açıkça ("kalp polikliniği", "göz doktoru") söylediyse doldurulur; yalnız şikâyet anlatıyorsa null ve niyet=tibbi_soru.
2. gun takvimdeki YYYY-MM-DD değerlerinden biri olmalı; "yarın", "cuma" gibi ifadeleri takvimden çevir. Söylenmediyse null.
3. Belirli bir saat söylendiyse saat_baslangic ve saat_bitis aynı HH:MM olur. "sabah", "öğleden sonra" gibi aralıkları HH:MM aralığına çevir. Söylenmediyse ikisi de null.
4. sira yalnız kullanıcı sıra sayısı söylediyse doldurulur; goreli yalnız "daha geç/erken" dendiyse.
5. ilac_adi yalnız kullanıcı bir ilaç adını kendisi söylediyse, söylediği biçimde yazılır; yoksa null. İlaç adı uydurma. Cümlede [ilaç] gizlenmiş bir ilaç adıdır.
6. ilac_konu yalnız niyet ilac_sorusu ise doldurulur: amac (ne işe yarar), kullanim (nasıl / ne zaman), yan_etki, alerji (alerji, kimler kullanmamalı), yemek (aç/tok, yiyecek-içecek), alkol, unutma (doz unutma/atlama), hamilelik (hamilelik/emzirme), etkilesim (başka ilaçla birlikte), sure (ne kadar süre, bırakma), doz (doz miktarı/değiştirme). Emin değilsen null.
7. Yalnız JSON döndür."""

# Diyalog adımlarının LLM'e giden SABİT açıklamaları (kapalı liste; dışındaki adım gönderilmez).
ADIM_ACIKLAMALARI = {
    "SLOT_SECIMI": "Kullanıcı poliklinik/saat seçiyor; ekranda uygun saatler listeleniyor.",
    "POLIKLINIK_BEKLENIYOR": "Asistan hangi poliklinikten randevu istendiğini sordu.",
    "GUN_BEKLENIYOR": "Asistan randevunun hangi gün olacağını sordu.",
    "SAAT_BEKLENIYOR": "Asistan sabah mı öğleden sonra mı, ya da belirli bir saat olup olmadığını sordu.",
    "SECIM_BEKLENIYOR": "Asistan birkaç uygun saat okudu ve hangisinin istendiğini sordu.",
    "ONAY_BEKLENIYOR": "Ekranda seçili bir randevu var ve kullanıcıdan onay bekleniyor.",
    "HEKIM_BEKLENIYOR": "Asistan poliklinikteki hekimlerden hangisinin tercih edildiğini sordu.",
    "ILAC_KONUSU": ("Kullanıcı kendisine yazılmış bir ilaç hakkında soru soruyor; ilaç adı [ilaç] olarak gizlendi. "
                    "niyet=ilac_sorusu ve ilac_konu alanını doldur."),
}

# Bu servis şu an anlama için JSON çıktısı kullanır. Aynı işi araç çağrısıyla yapmak
# isteyen sağlayıcı/model için eşdeğer araç şeması (model seçimi ölçümünde de kullanılır).
ARAC_SEMALARI: tuple[dict, ...] = (
    {
        "ad": "uygun_saatleri_ara",
        "aciklama": "Bir poliklinik için uygun randevu saatlerini arar. Yan etkisi yoktur, saat kilitlemez.",
        "parametreler": {
            "type": "object",
            "properties": {
                "poliklinik": {"type": "string", "enum": list(POLIKLINIK_KODLARI)},
                "gun": {"type": "string", "description": "YYYY-MM-DD (Europe/Istanbul)"},
                "saat_baslangic": {"type": "string", "description": "HH:MM"},
                "saat_bitis": {"type": "string", "description": "HH:MM"},
            },
            "required": ["poliklinik"],
        },
    },
    {
        "ad": "randevuyu_onayla",
        "aciklama": "Kullanıcı ekrandaki seçili saati açıkça onayladığında çağrılır.",
        "parametreler": {"type": "object", "properties": {}},
    },
)


# --------------------------------------------------------------------------
# Sabit cümleler
# --------------------------------------------------------------------------

# Kurum bilgisi (ortak.kurum / kurum.json). Değerler eksiz kullanılır (bkz. KURUM.md).
KURUM_TAM_AD = KURUM.tam_ad
KURUM_KISA_AD = KURUM.kisa_ad
ASISTAN_ADI = KURUM.asistan_adi
CAGRI_MERKEZI = KURUM.cagri_merkezi
# Hata ve yönlendirme şablonlarındaki ortak parça: "0850 000 00 00 numaralı çağrı merkezimizi".
CAGRI_MERKEZIMIZI = f"{CAGRI_MERKEZI} numaralı çağrı merkezimizi"

SELAMLAMA = (f"Merhaba, ben {ASISTAN_ADI}. {KURUM_TAM_AD} adına size yardımcı olacağım. Önce kimliğinizi "
             "doğrulayalım: kimlik numaranızı sesli söylemeyin; uygulamadaki tuş takımından girin.")
TC_TUS_TAKIMINDAN = "Kimlik numaranızı sesli söylemeyin; uygulamadaki tuş takımından girin."
ONCE_KIMLIK = "Devam etmek için önce kimliğinizi uygulamadaki tuş takımıyla doğrulamamız gerekiyor."
KIMLIK_DOGRULANMADI = "Kimliğiniz henüz doğrulanmadı. Lütfen kimlik numaranızı uygulamadaki tuş takımından girin."
# HANDOFF yanıtlarının metinleri (reason kodları sözleşmedir, değişmez; yalnız metinler mobil bağlama göre).
DEVIR = ("Bu oturumda işleme devam edemiyorum. Uygulamadan yeni bir işlem başlatabilir ya da yardım için "
         f"{CAGRI_MERKEZIMIZI} arayabilirsiniz.")
# reason=TC_ATTEMPTS_EXHAUSTED (backend oturumu HANDED_OFF yaptı).
DEVIR_TC = ("Kimliğiniz doğrulanamadı ve deneme hakkınız doldu. Güvenliğiniz için bu oturum kapatıldı; "
            f"yardım için {CAGRI_MERKEZIMIZI} arayabilirsiniz.")
OTURUM_GECERSIZ = ("Oturumunuzun süresi dolmuş ya da oturum geçersiz. "
                   "Lütfen uygulamadan işlemi yeniden başlatın.")
# Doğrulanmış oturumun süresi doldu (backend: VERIFIED oturum açılıştan 15 dk sonra EXPIRED; 409
# VOICE_SESSION_NOT_VERIFIED). Ön yüz data.restart=true görünce yeni oturum açıp TC adımına döner.
OTURUM_SURESI_DOLDU = ("Oturumunuzun süresi doldu. Güvenliğiniz için kimliğinizi yeniden doğrulamanız "
                       "gerekiyor; uygulama sizi kimlik adımına götürecek.")
# Girişten sonraki karşılama: ne yapılabildiği + poliklinik listesi (data.expect yine "poliklinik").
POLIKLINIK_SOR = (f"Hoş geldiniz, kimliğiniz doğrulandı. Ben {ASISTAN_ADI}. Randevu alabilir, geçmiş "
                  "muayenelerinizde yazılan ilaçları sorabilirsiniz. Randevu için şikâyetinizi ya da poliklinik "
                  "adını söyleyin; {liste}.")
POLIKLINIK_SOR_SAATSIZ = POLIKLINIK_SOR
SAATLER_HAZIR = "{poliklinik} için {aralik} uygun saatler ekranda. Size uyan saati seçin."
SAAT_BULUNAMADI = ("{poliklinik} için {aralik} boş saat görünmüyor. "
                   "Başka bir gün ya da poliklinik söyleyebilirsiniz.")
SAAT_TAM_BULUNAMADI = "İstediğiniz saat dolu ya da mevcut değil. En yakın uygun saatler ekranda."
ONAY_SOR = ("{etiket}. Bu randevuyu oluşturayım mı? Onaylıyorsanız 'evet, onaylıyorum' deyin "
            "ya da 'Seçimimi onayla' düğmesine basın.")

# ---- Konuşarak randevu bulma (yuva doldurma). Her cümle koddan; {…} yerlerine kod/API verisi girer.
POLIKLINIK_SORU = "Hangi poliklinikten randevu istiyorsunuz? {liste}."
# İlk anlaşılmamada adımın kısa sorusunun önüne eklenir.
ANLASILAMADI_ONEKI = "Sizi tam anlayamadım. "
POLIKLINIK_SORU_TEKRAR = ("Poliklinik adını kısaca söyler misiniz? Örneğin 'Kardiyoloji' ya da "
                          "'Göz' diyebilirsiniz.")
POLIKLINIK_GEREKLI = "Randevu için bir poliklinik seçmemiz gerekiyor. {liste}."
GUN_SORU = "{poliklinik} için hangi gün olsun? Örneğin 'yarın' ya da 'en erken' diyebilirsiniz."
GUN_SORU_TEKRAR = ("Hangi gün gelmek istersiniz? 'Bugün', 'yarın', bir haftanın günü ya da "
                   "'fark etmez' diyebilirsiniz.")
SAAT_SORU = "{gun} için sabah mı olsun, öğleden sonra mı, ya da belirli bir saat var mı?"
SAAT_SORU_TEKRAR = "Hangi saat size uyar? 'Sabah', 'saat on' ya da 'en erken' diyebilirsiniz."
SECENEK_SUN = "{secenekler} uygun. Hangisini istersiniz?"
# Poliklinikten önce gün/saat söylendi ("haftaya salı"): tercih saklanır ve teyit edilir.
TERCIH_NOT_ALINDI = "Tercihinizi not aldım: {tercih}."
GECMIS_TARIH = "Bu tarih geçmişte kaldı, hangi gün olsun?"
SECENEK_EKRANDA_DAHA = " Ekranda başka saatler de var."
SECIM_TEKRAR = "Hangi saati istersiniz? 'İlki', 'ikincisi' ya da saati söyleyebilirsiniz."
ISTENEN_YOK_YAKIN = "{istek} uygun saat yok. En yakın seçenekler: {secenekler}. Hangisini istersiniz?"
GUN_YOK_YAKIN = ("{poliklinik} için {gun} boş saat yok. En yakın seçenekler: {secenekler}. "
                 "Hangisini istersiniz?")
POLIKLINIK_HIC_YOK = ("{poliklinik} için önümüzdeki günlerde boş saat görünmüyor. "
                      "Başka bir poliklinik söyleyebilirsiniz.")
DAHA_GEC_YOK = "Bundan daha geç uygun saat görünmüyor. Başka bir gün söyleyebilirsiniz."
DAHA_ERKEN_YOK = "Bundan daha erken uygun saat görünmüyor. Ekrandaki saatlerden birini seçebilirsiniz."
SIRA_YOK = "Ekranda o sırada bir saat yok. {soru}"
ANLASILAMADI_EKRAN = ("Sizi anlayamadım. Ekrandaki seçeneklerden birine dokunabilir ya da örneğin "
                      "'{ornek}' diyebilirsiniz.")
ANLASILAMADI_ORNEKLERI = {
    "poliklinik": "Kardiyoloji",
    "gun": "yarın",
    "saat": "sabah",
    "secim": "ilki",
    "onay": "evet, onaylıyorum",
    "hekim": "fark etmez",
}
# Ekran düğmesi olarak da gösterilen kısa yanıtlar (dokununca aynı metin gönderilir).
GUN_SECENEKLERI = ("Bugün", "Yarın", "En erken")
SAAT_SECENEKLERI = ("Sabah", "Öğleden sonra", "En erken")

# ---- Geçmiş muayene ve ilaç soruları (yalnız hekim onaylı özet; yorum/tavsiye yok)
GECMIS_ONCE_KIMLIK = ("Geçmiş muayene bilgileriniz için önce kimliğinizi doğrulamamız gerekiyor. "
                      "Lütfen kimlik numaranızı uygulamadaki tuş takımından girin.")
GECMIS_YENIDEN_DOGRULAMA = ("Uzun süre işlem yapılmadığı için güvenliğiniz gereği muayene bilgilerinizi "
                            "göstermeden önce kimliğinizi yeniden doğrulamamız gerekiyor; uygulama sizi kimlik "
                            "adımına götürecek.")
GECMIS_YOK = ("Sistemde hekiminizin onayladığı bir muayene özetiniz bulunmuyor. Son muayenenizin özeti "
              "henüz hekim onayı bekliyor olabilir; onaydan sonra burada görünür.")
GECMIS_ALINAMADI = ("Muayene bilgilerinize şu an ulaşamıyorum. Biraz sonra tekrar deneyin; bilgi için "
                    f"{CAGRI_MERKEZIMIZI} arayabilirsiniz, acil bir durumda 112'yi arayın.")
GECMIS_EK_DOGRULAMA = ("Muayene bilgileriniz için ek doğrulama gerekiyor; bu sürümde uygulamadan yapılamıyor. "
                       f"Bilgi için {CAGRI_MERKEZIMIZI} arayabilirsiniz.")
GECMIS_GIRIS = "{tarih} tarihli {poliklinik} muayenenizde {sayi} ilaç yazılmış."
GECMIS_GIRIS_ILACSIZ = "{tarih} tarihli {poliklinik} muayenenizde ilaç yazılmamış."
GECMIS_TANI = "{tarih} tarihli {poliklinik} muayenenizin özeti: {tani}"
GECMIS_ILAC = "{ad}: {amac} {kullanim}"
GECMIS_ILAC_TEK = "{tarih} tarihli {poliklinik} muayenenizde yazılan {ad}: {amac} {kullanim}"
GECMIS_UYARI = "Hekiminizin uyarısı: {uyari}"
GECMIS_UYARI_YOK = "Bu muayene için ayrıca bir uyarı yazılmamış."
GECMIS_DIGERLERI_EKRANDA = "Diğer ilaçlar ekranda."
GECMIS_KAPANIS = "Sorunuz olursa hekiminize danışın."
# "En son ne zaman muayene oldum / hangi doktora gittim": son onaylı muayenenin tarihi, polikliniği, hekimi.
GECMIS_SON_ZIYARET = "Hekim onaylı kayıtlarınıza göre son muayeneniz {tarih} tarihinde {poliklinik} polikliniğinde{hekim}."
# Poliklinikle sorulduysa ("kardiyolojiye en son ne zaman gittim") yalnız o polikliniğin son muayenesi.
GECMIS_SON_POLIKLINIK_ZIYARET = ("Hekim onaylı kayıtlarınıza göre son {poliklinik} muayeneniz {tarih} "
                                 "tarihinde{hekim}.")
GECMIS_ZIYARET_HEKIM = ", {hekim} ile yapılmış"
GECMIS_ZIYARET_HEKIMSIZ = " yapılmış"
GECMIS_ZIYARET_ILACLAR = "Bu muayenede yazılan ilaçlar: {adlar}."
GECMIS_ZIYARET_ILACSIZ = "Bu muayenede ilaç yazılmamış."
GECMIS_ONCEKI_ZIYARETLER = "Önceki muayeneleriniz: {liste}; ayrıntısı ekranda."
# "Doktor ne demişti": yalnız hekim onaylı özet ve uyarılar okunur (GECMIS_TANI + GECMIS_UYARI); yorum eklenmez.
GECMIS_AYRINTI_EKRANDA = "Ayrıntısı ekranda."
# Sorulan hekim adı onaylı kayıtlarda yok ("Zeynep Hanım ne yazmıştı").
GECMIS_HEKIM_YOK = ("Hekim onaylı kayıtlarınızda bu adla bir hekimin muayenesi görünmüyor; kayıtlarınız ekranda. "
                    "Sorunuz olursa hekiminize danışın.")
# Son muayene listesinde önceki muayenelerin ilaçları da anılır: İlaçlarım sayfası hepsini gösterir, asistan
# yalnız son muayeneyi okursa hasta diğer ilaçlarını bırakılmış sanabilir.
GECMIS_ONCEKI_ILACLAR = "Önceki muayenelerinizde de yazılanlar: {adlar}; ayrıntısı ekranda."
GECMIS_EKRANDA = ("Muayene bilgileriniz ekranda gösteriliyor; gizliliğiniz için sesli okumuyorum. "
                  "Sorunuz olursa hekiminize danışın.")
# Kullanıcının söylediği ilaç hekim onaylı kayıtlarda yok: başka ilacın bilgisi okunmaz, LLM'e gidilmez.
ILAC_BULUNAMADI = ("Kayıtlarınızda bu ilaç görünmüyor. Yalnızca hekiminizin onayladığı muayene özetlerindeki "
                   "ilaçları okuyabilirim; sorunuz olursa hekiminize danışın.")
# Sorulan poliklinik için onaylı özet yok (ör. Ortopedi muayenesinin özeti henüz onay bekliyor).
GECMIS_POLIKLINIK_YOK = ("{poliklinik} için hekim onaylı bir kayıt görünmüyor; onay bekliyor olabilir. "
                         "Sorunuz olursa hekiminize danışın.")
ILAC_TAVSIYE_RET = "Bu konuda bilgi veremem; hekiminize ya da eczacınıza danışın."
# ---- İlaç soru konuları (genel bilgi: ses_servisi/alanlar/ilac_dagarcigi.py; doz/sıklık: hekim onaylı özet)
ILAC_KONU_GIRIS = "{tarih} tarihli {poliklinik} muayenenizde yazılan {ad} hakkında genel bilgi:"
ILAC_KONU_GIRIS_KISA = "{ad} hakkında genel bilgi:"
ILAC_KONU_TALIMAT = "Hekiminizin talimatı: {kullanim}"
ILAC_KONU_KAPANIS = ("Bu genel bir bilgidir, hekiminizin talimatı esastır; kendi durumunuz için hekiminize ya da "
                     "eczacınıza danışın.")
ILAC_DOZ_GENEL = ("Dozu kendiniz değiştirmeyin; değişiklik için hekiminize sorun. Fazla aldıysanız 114 zehir "
                  "hattını, kendinizi kötü hissediyorsanız 112'yi arayın.")
# Dağarcıkta ilaç ya da konu yok: uydurulmaz.
ILAC_KONU_BILGI_YOK = "{ad} için bu konuda elimde onaylı bilgi yok; hekiminize ya da eczacınıza danışın."
# Konu anlaşılamadı (kural çözemedi, LLM konu veremedi ya da cümle LLM'e gönderilemezdi).
ILAC_KONU_TANIMSIZ = ("{ad} için bu soruya dair elimde onaylı bilgi yok; hekiminize ya da eczacınıza danışın. "
                      "İsterseniz ne işe yaradığını, yan etkilerini, aç ya da tok alınmasını, alkolle kullanımını "
                      "ya da unutursanız ne yapacağınızı sorabilirsiniz.")
# İki ilacın birlikte kullanımı: ilaç çiftine özel onaylı bilgi yok.
ILAC_ETKILESIM_CIFT = ("{adlar} ilaçlarının birlikte kullanımına özel onaylı bilgim yok. Bu ilaçlar hekim onaylı "
                       "kayıtlarınızda görünüyor; birlikte kullanım için hekiminize ya da eczacınıza danışın ve "
                       "kullandığınız tüm ilaçları onlara söyleyin.")
# Konu soruldu ama hangi ilaç olduğu belli değil.
ILAC_HANGISI = "Hangi ilacınız için soruyorsunuz? Kayıtlarınızda {ilaclar} görünüyor."
ILAC_HANGISI_EKRANDA = "Hangi ilacınız için soruyorsunuz? Kayıtlarınızdaki ilaçlar ekranda; adını söyleyebilirsiniz."
# Tarifle sorulan ilaç ("tansiyon ilacım"): yanıt metninde ad geçmiyorsa başa eklenir (kullanıcı hangi ilaçtan söz
# edildiğini bilsin). Birden çok ilaca uyuyorsa / hiçbirine uymuyorsa sorulur (tahmin yok).
ILAC_TARIF_ON = "Kayıtlarınızda buna uyan ilaç {ad}."
ILAC_TARIF_HANGISI = ("Kayıtlarınızda {tarif} birden çok ilaç var: {ilaclar}. Hangisini soruyorsunuz? Adını "
                      "söyleyebilirsiniz.")
ILAC_TARIF_YOK = ("Hekim onaylı kayıtlarınızda {tarif} bir ilaç görünmüyor. Kayıtlarınızda {ilaclar} var; "
                  "bunlardan birini soruyorsanız adını söyleyebilirsiniz.")
ILAC_TARIF_EKRANDA = ("Hangi ilacınız için soruyorsunuz? Kayıtlarınızdaki ilaçlar ekranda; adını "
                      "söyleyebilirsiniz.")
# Tarif parçaları ({tarif} içinde): amaç → "tansiyon için", poliklinik → "Kardiyoloji muayenenizde yazılmış",
# zaman → "sabah kullanılan", sıra → "son muayenenizde yazılmış".
ILAC_TARIF_AMAC = "{ad} için"
ILAC_TARIF_POLIKLINIK = "{poliklinik} muayenenizde yazılmış"
ILAC_TARIF_ZAMAN_METNI = "{zaman} kullanılan"
ILAC_TARIF_SON_METNI = "son muayenenizde yazılmış"
ILAC_TARIF_ONCEKI_METNI = "önceki muayenelerinizde yazılmış"
ILAC_KAYITTA_YOK = ("Hekim onaylı kayıtlarınızda yazılmış bir ilaç görünmüyor; ilaç sorularınızı hekiminize ya da "
                    "eczacınıza danışın.")
ILAC_KONU_AZAMI_SESLI = 590
# Sesli okunan geçmiş metninin üst sınırı (TTS_AZAMI_KARAKTER'in altında kalmalı).
GECMIS_AZAMI_SESLI = 520
ONCE_SAAT_SECIN = "Onaydan önce lütfen ekrandaki saatlerden birini seçin."
ONAY_ICIN_ACIK = ("Randevuyu oluşturmam için açık onayınız gerekiyor. Onaylıyorsanız 'Seçimimi onayla' "
                  "düğmesine basın ya da yalnızca 'onaylıyorum' deyin.")
SECIM_GUNCEL_DEGIL = ("Ekrandaki seçim güncel değil; randevu oluşturulmadı. "
                      "Lütfen ekrandaki saatlerden birini yeniden seçin.")
BOSTA_KALDI = ("Uzun süre işlem yapılmadığı için seçiminiz kaldırıldı; randevu oluşturulmadı. "
               "Güncel saatler ekranda, lütfen yeniden seçin.")
SLOT_GECTI = ("Seçtiğiniz saat geçti ya da çok yaklaştı; randevu oluşturulmadı. "
              "Güncel saatler ekranda, lütfen yeni bir saat seçin.")
DOGRULANDI_SISTEM_HATASI = ("Kimliğiniz doğrulandı ancak randevu sistemine şu an ulaşamıyorum. "
                            "Biraz sonra 'Tekrar dene' düğmesine basın ya da randevu için "
                            f"{CAGRI_MERKEZIMIZI} arayın; acil bir durumda 112'yi arayın.")
SECIM_IPTAL = "Seçiminizi kaldırdım. Uygun saatler ekranda; başka bir saat seçebilirsiniz."
SLOT_LISTEDE_YOK = "Bu saat artık listede değil. Lütfen ekrandaki saatlerden birini seçin."
RANDEVU_OLUSTU = ("Randevunuz oluşturuldu: {etiket}, {poliklinik}, {hekim}. "
                  "Erişim kodunuz {kod}. Bu kodu hasta portalında kullanabilirsiniz.")
RANDEVU_ZATEN_VAR = "Bu oturumda randevunuz oluşturuldu: {etiket}, {poliklinik}. Erişim kodunuz {kod}."
SLOT_ALINDI_YENI = ("Seçtiğiniz saat az önce başka bir hastaya verildi; randevunuz oluşturulmadı. "
                    "Lütfen ekrandaki güncel saatlerden yeni bir saat seçin.")
# Backend iş kuralı reddi, ama özel şablonu olmayan bir kodla (ör. INVALID_STATUS_TRANSITION ya da backend'e
# sonradan eklenen yeni bir 409): sistem hatası DEĞİLDİR ("ulaşamıyorum" denmez), aynı onay tekrar denenmez.
RANDEVU_OLUSTURULAMADI = ("Bu saat için randevu oluşturulamadı. Güncel saatler ekranda; başka bir saat "
                          "seçebilirsiniz.")
# ---- Backend 409 APPOINTMENT_CONFLICT: randevu oluşmadı; seçim kaldırılır, gün/saat yeniden sorulur.
# Backend iki durumu aynı kodla döner; hangisi olduğu yalnız mesajdan anlaşılır. Mesaj yalnız İPUCUDUR
# (kalıp tutmazsa genel şablon): karar kodla verilir, mesaj metni kullanıcıya gösterilmez.
RANDEVU_CAKISMA_SAAT = ("Bu saatte zaten başka bir randevunuz var; randevu oluşturulmadı. "
                        "Başka bir saat seçebilirsiniz.")
RANDEVU_CAKISMA_GUN = ("{gun} {poliklinik} polikliniğinde zaten bir randevunuz var; randevu oluşturulmadı. "
                       "Başka bir gün seçebilirsiniz.")
RANDEVU_CAKISMA = ("Bu saatte ya da aynı gün bu poliklinikte zaten bir randevunuz var; randevu oluşturulmadı. "
                   "Başka bir gün ya da saat seçebilirsiniz.")
# Normalize edilmiş backend mesajında aranan ipuçları → çakışma türü.
# Çakışmadan sonra gün sorusu (örnek "yarın" verilmez: çakışan gün yarın olabilir).
CAKISMA_GUN_SORU = "Hangi gün olsun? Örneğin 'en erken' ya da bir haftanın gününü söyleyebilirsiniz."
CAKISMA_IPUCLARI: tuple[tuple[str, str], ...] = (("ayni gun", "gun"), ("bu saatte", "saat"))
# Randevu ufku (backend slotları bugün dahil N gün için açar; RANDEVU_UFUK_GUN).
UFUK_DISI = "Randevular yalnız önümüzdeki {gun_sayisi} gün için açık; en geç {son_tarih} tarihine bakabilirim."
UFUK_GUN_SORU = "Hangi gün olsun?"
SISTEM_HATASI = ("Şu an sisteme ulaşamıyorum, işleminiz tamamlanmadı. Biraz sonra tekrar deneyin ya da "
                 f"{CAGRI_MERKEZIMIZI} arayın; acil bir durumda 112'yi arayın.")
SISTEM_HATASI_SAATLER = ("Şu an randevu saatlerini alamıyorum. Biraz sonra tekrar deneyin ya da randevu için "
                         f"{CAGRI_MERKEZIMIZI} arayın; acil bir durumda 112'yi arayın.")
SONUC_BELIRSIZ = ("Randevu işleminizin sonucunu doğrulayamadım. Aynı saati tekrar almaya çalışmayın; "
                  f"randevunuzun durumunu öğrenmek için {CAGRI_MERKEZIMIZI} arayın.")
# Servisin beklenmeyen hatası (app.py, HTTP 500).
BEKLENMEYEN_HATA = f"Beklenmeyen bir sorun oluştu. Lütfen tekrar deneyin ya da {CAGRI_MERKEZIMIZI} arayın."
TIBBI_YORUM_YOK = "Tıbbi konularda yorum yapamıyorum; bunu hekiminize sorabilirsiniz."
TIBBI_YANIT = TIBBI_YORUM_YOK + " Randevu için şikâyetinizi ya da poliklinik adını söyleyebilirsiniz."
# "Hangi bölüme gitmeliyim" (şikâyet söylenmeden).
YONLENDIRME_SOR = ("Şikâyetinizi kısaca söylerseniz genellikle hangi polikliniğe başvurulduğunu söyleyebilirim; "
                   "{liste}.")
KONU_DISI_YANIT = ("Bu konuda yardımcı olamıyorum. Randevu alabilir, geçmiş muayenelerinizde yazılan ilaçları "
                   "sorabilirsiniz.")
ANLASILAMADI = ("Sizi tam anlayamadım. Bir poliklinik adını ya da bir gün söyleyebilir, ekrandaki "
                "düğmeleri de kullanabilirsiniz.")
ANLAMA_KULLANILAMIYOR = ("Şu an serbest konuşmayı anlayamıyorum. Lütfen poliklinik adını kısaca "
                         "söyleyin ya da ekrandaki düğmeleri kullanın.")
TAMAMLANDI_BILGI = "Randevunuz oluşturuldu. Yeni bir işlem için uygulamadan yeniden başlatabilirsiniz."
# Randevu alınmış oturumda yeni randevu/saat isteği (oturum başına bir randevu).
YENI_RANDEVU_ICIN = "Yeni bir randevu için Çıkış'a dokunup yeniden giriş yapın."

# ---- Şikâyet → poliklinik önerisi (teşhis yok; yalnız yönlendirme ve teyit)
ONERI_SOR = ("Bu şikâyet için genellikle {poliklinik} polikliniğine başvurulur. {poliklinik} polikliniğinden "
             "randevu bakmamı ister misiniz?")
# Öneriye eklenen güvenlik notu (yalnız bu poliklinikler için).
ONERI_ACIL_NOTU = {
    "KARDIYOLOJI": "Şikâyetiniz ani ve şiddetliyse beklemeyin, 112'yi arayın.",
    "NOROLOJI": ("Ani başlayan çok şiddetli baş ağrısı, konuşma bozukluğu ya da yüzde kayma varsa beklemeyin, "
                 "112'yi arayın."),
    "KADIN_DOGUM": "Hamilelikte kanama ya da şiddetli karın ağrısı varsa beklemeyin, 112'yi arayın.",
    "COCUK_SAGLIGI": ("Çocuğunuzda nefes darlığı, bilinç bulanıklığı ya da havale varsa beklemeyin, "
                      "112'yi arayın."),
}
# Onay beklenirken söylenen şikâyet: bilgi verilir, seçili randevu korunur (soru sorulmaz; "evet" karışmasın).
ONERI_BILGI = "Bu şikâyet için genellikle {poliklinik} polikliniğine başvurulur."
ONAY_BEKLIYOR_HATIRLAT = ("Seçtiğiniz randevu onayınızı bekliyor; başka bir poliklinik isterseniz adını "
                          "söyleyin.")
# Birden çok polikliniğe işaret eden ya da listede olmayan şikâyet.
SIKAYET_BELIRSIZ = ("Bu konuda doğru bölümü ben söyleyemem; {liste}. Hangisinden randevu "
                    "istersiniz?")
# ---- Acil durum (her adımda öncelikli; randevu akışına sokulmaz)
ACIL_YANIT = ("Bu acil bir durum olabilir. Randevu beklemeyin; hemen 112'yi arayın ya da en yakın acil "
              "servise başvurun.")
# ---- Sohbet
YARDIM = (f"Ben {ASISTAN_ADI}. Randevu alabilir, geçmiş muayenelerinizde yazılan ilaçları ve genel sağlık "
          "sorularınızı sorabilir, çağrı merkezi, adres ve çalışma saatlerini öğrenebilirsiniz. Örneğin 'yarın sabah "
          "Kardiyoloji', 'gözüm bulanık görüyor' ya da 'son muayenemde hangi ilaçlar yazıldı' diyebilirsiniz.")
YARDIM_GIRIS_ONCESI = (f"Ben {ASISTAN_ADI}. Randevu alabilir, geçmiş muayenelerinizde yazılan ilaçları "
                       "sorabilirsiniz. Önce kimliğinizi doğrulayalım: kimlik numaranızı sesli söylemeyin; "
                       "uygulamadaki tuş takımından girin.")
YARDIM_TAMAMLANDI = ("Randevunuz oluşturuldu. Geçmiş muayenelerinizde yazılan ilaçları sorabilirsiniz; yeni "
                     "bir randevu için uygulamadan yeniden başlatın.")
SELAM_YANIT = (f"Merhaba, ben {ASISTAN_ADI}; size nasıl yardımcı olabilirim? Randevu alabilir ya da geçmiş "
               "muayenelerinizde yazılan ilaçları sorabilirsiniz.")
TESEKKUR_YANIT = "Rica ederim. Başka bir isteğiniz varsa söyleyebilirsiniz."
VEDA_YANIT = f"Görüşmek üzere, sağlıklı günler dilerim. {KURUM_TAM_AD} olarak her zaman yanınızdayız."
# ---- Mevcut randevular (backend'de gelecek randevu listesi ucu yok: uydurulmaz)
MEVCUT_RANDEVU_YOK = ("Bu sürümde mevcut randevularınızı gösteremiyorum. Randevu oluştururken verilen erişim "
                      "koduyla hasta portalından bakabilirsiniz. İsterseniz şimdi yeni bir randevu alabiliriz.")
MEVCUT_RANDEVU_IPTAL = ("Bu sürümde randevu iptal edemiyor ya da değiştiremiyorum; bunun için "
                        f"{CAGRI_MERKEZIMIZI} arayabilirsiniz. İsterseniz şimdi yeni bir randevu alabiliriz.")
GECERSIZ_GIRDI = "İstek anlaşılamadı. Lütfen işlemi yeniden başlatın."

# ---- Çok hekimli poliklinikte hekim seçimi (hekim adları backend'in /slots yanıtındaki doctorDisplayName'den;
# LLM'e gitmez). Soru evet/hayır sorusu değildir ("evet" karışmasın).
HEKIM_SORU = ("{poliklinik} polikliniğimizde {hekimler} hasta kabul ediyor. Hangi hekimimizden randevu "
              "istersiniz? Fark etmezse 'fark etmez' diyebilirsiniz.")
HEKIM_SORU_TEKRAR = ("Hangi hekimimizi tercih edersiniz? Hekimin adını söyleyebilir ya da 'fark etmez' "
                     "diyebilirsiniz.")
# Hekim sorusuna adaylara uymayan bir kişi adıyla yanıt verildi.
HEKIM_BULUNAMADI = ("Bu poliklinikte bu adla bir hekimimiz görünmüyor. {hekimler} arasından seçebilir ya da "
                    "'fark etmez' diyebilirsiniz.")
HEKIM_NOT_ALINDI = "Tercihinizi not aldım: {hekim}."  # ad sonundaki nokta orkestratörde kırpılır ("T.." olmasın)
HEKIM_FARK_ETMEZ_SECENEGI = "Fark etmez"
# Seçilen hekimin ufukta hiç boş saati yok: tercih kaldırılır, hekim sorusu yeniden sorulur.
HEKIM_BOS = ("{hekim} için önümüzdeki günlerde boş saat görünmüyor. Başka bir hekimimizi seçebilir ya da "
             "'fark etmez' diyebilirsiniz.")
# Hekim sorusuna "tercihim yok" yanıtları (önek eşleşmesi). "En erken/en yakın" burada değil: genel akışta
# gün/saat de serbest bırakılıp en erken saatler sunulur.
HEKIM_FARK_ETMEZ_IFADELERI = ("fark etmez", "farketmez", "fark etmiyor", "farketmiyor", "farkı yok", "farki yok",
                              "hangisi olursa", "hangisi uygunsa", "herhangi", "kim olursa", "kim uygunsa",
                              "önemli değil", "hepsi olur", "tercihim yok", "tercih yok", "siz seçin", "sen seç",
                              "uygun olan", "müsait olan", "hiç fark etmez", "biri olur")
# Cümlenin TAMAMI bunlardan biriyse "tercih yok" sayılır.
HEKIM_TERCIH_YOK_TAM = ("yok", "hayır", "hayır yok", "yok hayır", "hayır fark etmez", "bilmiyorum", "farketmez")
# Unvanla seçim ("profesör olsun"): yalnız adaylardan TEK birine uyuyorsa.
HEKIM_UNVAN_IFADELERI: dict[str, tuple[str, ...]] = {
    "prof": ("profesör", "prof"),
    "doc": ("doçent", "doç", "doc"),
    "uzm": ("uzman", "uzm"),
    "op": ("operatör", "op dr"),
}

# ---- Kurum soruları (kural + sabit şablon; LLM'e gitmez). Ücret ve sigorta için bilgi UYDURULMAZ: çağrı
# merkezine yönlendirilir. Ödeme/provizyon gibi iş akışı yoktur. Sıra önemlidir (ilk eşleşen konu):
# "sigortam muayene ücretini karşılıyor mu" → sigorta.
KURUM_SORU_KALIPLARI: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sigorta", ("sigorta", "sgk", "ssk", "bağkur", "bağ kur", "emekli sandığ", "anlaşma", "anlaşmalı",
                 "tamamlayıcı sağlık", "provizyon", "poliçe", "sağlık güvence", "devlet karşıl", "devlet öd")),
    ("ucret", ("ücret", "fiyat", "kaç para", "kaç lira", "kaç tl", "ne kadar tutar", "ne kadar öde",
               "ne kadar para", "muayene ne kadar", "muayenesi ne kadar", "katkı pay", "ödeme", "para al",
               "para ver", "paralı mı", "masraf", "parası ne kadar", "parası kaç", "ne kadar tutuyor")),
    ("cagri_merkezi", ("çağrı merkez", "telefon numaranız", "telefon numarası", "telefonunuz", "numaranız ne",
                       "numaranız kaç", "numaranızı", "hastanenin numara", "hastane numara", "hastanenin telefon",
                       "hastane telefon", "sizi nasıl arayabilir", "sizi arayabilir", "nereden arayabilir",
                       "kimi arayabilir", "iletişim", "irtibat", "santral", "bilgi hattı", "danışma hattı",
                       "randevu hattı", "sizi aramak", "nasıl ararım", "nasıl arayabilirim", "nasıl ulaşırım",
                       "nasıl ulaşabilirim", "size nasıl ulaş", "telefonla nasıl")),
    ("adres", ("adresiniz", "adresi ne", "adresi nedir", "adresi nerede", "hastanenin adres", "hastane adres",
               "hastane nerede", "hastaneniz nerede", "hastanenin yeri", "hastane nerde", "neredesiniz",
               "nerdesiniz", "nerede bulunuyor", "nasıl gelebilirim", "nasıl gelirim", "nasıl giderim",
               "nasıl gidebilirim", "yol tarifi", "konumunuz", "hangi semtte", "hastaneye nasıl")),
    ("calisma_saatleri", ("çalışma saat", "mesai saat", "kaçta açıl", "kaçta kapan", "kaça kadar açık",
                          "kaça kadar çalış", "açık mısınız", "açık mı acaba", "hastane açık", "hafta sonu açık",
                          "cumartesi açık", "pazar açık", "cumartesi günü açık", "pazar günü açık",
                          "hafta sonu çalış", "çalışıyor musunuz", "hangi saatlerde", "hangi günler açık",
                          "kaçta başlıyor", "kaça kadar hasta")),
)
KURUM_YANITLARI: dict[str, str] = {
    "cagri_merkezi": (f"{KURUM_TAM_AD} çağrı merkezi numarası {CAGRI_MERKEZI}. Randevu dışındaki sorularınız için "
                      "bu numarayı arayabilirsiniz; randevuyu bana da aldırabilirsiniz."),
    "adres": (f"{KURUM_TAM_AD} adresi: {KURUM.adres}. Yol tarifi için {CAGRI_MERKEZIMIZI} arayabilirsiniz."),
    "calisma_saatleri": (f"{KURUM.calisma_saatleri} Randevu için poliklinik adını söyleyebilirsiniz; acil "
                         "durumlarda 112'yi arayın."),
    "ucret": ("Muayene ücretleri poliklinik ve hekime göre değişebildiği için size buradan ücret bilgisi "
              f"veremiyorum. Güncel ücret bilgisi için {CAGRI_MERKEZIMIZI} arayabilirsiniz."),
    "sigorta": ("Sigortanızın, SGK ya da özel sağlık sigortası anlaşmasının geçerli olup olmadığını buradan "
                "söyleyemiyorum. Anlaşmalı kurumlar ve sigorta bilgisi için "
                f"{CAGRI_MERKEZIMIZI} arayabilirsiniz."),
}
# Kurum yanıtı onay beklenirken verildiyse eklenir (seçili randevu korunur).
KURUM_ONAY_HATIRLAT = "Seçtiğiniz randevu da onayınızı bekliyor."
# Kurum yanıtı randevu akışının ortasında (hekim/gün/saat/seçim sorusu beklenirken) verildiyse adımın sorusu
# yeniden sorulur: kullanıcı nerede kaldığını kaçırmasın (seçimler korunur).
KURUM_ADIM_HATIRLAT = "Randevunuza devam edelim. "
# Akış ortasında bazı konuların kapanışı farklıdır ("poliklinik adını söyleyebilirsiniz" poliklinik seçildiyse
# çelişir).
# Randevu alındıktan sonra da (TAMAMLANDI) bu metinler kullanılır: asistan oturumda ikinci randevu almaz.
KURUM_YANITLARI_AKISTA: dict[str, str] = {
    "calisma_saatleri": f"{KURUM.calisma_saatleri} Acil durumlarda 112'yi arayın.",
    "cagri_merkezi": (f"{KURUM_TAM_AD} çağrı merkezi numarası {CAGRI_MERKEZI}. Randevu dışındaki sorularınız "
                      "için bu numarayı arayabilirsiniz."),
}


# --------------------------------------------------------------------------
# Güvenlik filtresi kuralları (LLM metni kullanıcıya gitmeden önce)
# --------------------------------------------------------------------------

GUVENLI_YEDEK = ("Randevunuz henüz oluşturulmadı. Size uygun saati seçip onayladığınızda "
                 "işlemi tamamlayabilirim.")

GUVENLIK_KURALLARI: tuple[YasakKurali, ...] = (
    # Kesin onay iddiaları: LLM metninde HİÇBİR zaman. Başarı cümlesini yalnız şablon kurar.
    # Kök tabanlı (kelime başından önek): "onaylan" → onaylandı/onaylanmıştır/onaylanmış.
    YasakKurali("kesin_onay", GUVENLI_YEDEK, ifadeler=(
        "onaylan", "onayladım", "onayladık", "onaylı", "kesinleş", "kesin randevu", "kesin onay",
        "teyit edil", "teyit ettim", "teyit ettik")),
    # Başarı iddiaları: randevu aracı gerçekten başarılı dönmediyse. Olumsuz biçimler
    # ("oluşturulmadı", "alınmadı") bu öneklerle eşleşmez.
    YasakKurali("basari_iddiasi", GUVENLI_YEDEK, kosul=Kosul.BASARI_YOKSA, ifadeler=(
        "oluşturuldu", "oluşturulmuş", "oluşturdum", "oluşturduk", "alındı", "alınmış", "aldım", "aldık",
        "kaydedildi", "kaydedilmiş", "kaydettim", "kaydettik", "rezerve", "ayrıldı", "ayrılmış",
        "ayırdım", "ayırdık", "ayarlandı", "ayarlanmış", "planlandı", "planlanmış", "tanımlandı",
        "tanımlanmış", "tamamlandı", "tamamlanmış", "erişim kod", "işleminiz tamam")),
    # Tek başına masum, randevu/kayıt/işlem bağlamında başarı iddiası olan kelimeler.
    YasakKurali("basari_iddiasi_baglamli", GUVENLI_YEDEK, kosul=Kosul.BASARI_YOKSA,
                baglam_ifadeleri=("randevu", "kayıt", "kaydınız", "işlem", "yeriniz", "saatiniz"),
                desenler=(r"\bhaz[ıi]r(d[ıi]r)?\b", r"\byap[ıi]ld[ıi]\b", r"\byap[ıi]lm[ıi][şs]",
                          r"\bverild[ıi]\b", r"\bverilmi[şs]", r"\btamam(d[ıi]r)?\b",
                          r"\bger[çc]ekle[şs]ti", r"\bba[şs]ar[ıi]yla\b")),
    # Sistem hatası olduysa "dolu/yok" denemez (D4-b): müsaitlik hakkında bilgi yok.
    YasakKurali("dolu_iddiasi", SISTEM_HATASI_SAATLER, kosul=Kosul.SISTEM_HATASI_VARSA, ifadeler=(
        "dolu", "müsait değil", "boş değil", "yer yok", "saat yok", "uygun saat bulunmuyor")),
    # Tıbbi tavsiye / teşhis.
    YasakKurali("tibbi_tavsiye", TIBBI_YANIT, ifadeler=(
        "teşhis", "tanınız", "hastalığınız", "ilacınızı", "ilaç alın", "ilaç kullan", "doz", "tedaviniz", "tedavi olmalı", "reçete", "muhtemelen", "büyük ihtimalle",
        "kalp krizi", "acil servise gitmeniz gerek"),
        desenler=(r"\b\d+\s?(mg|ml|mcg|tablet)\b",)),
    # Kimlik/kişisel bilgi istemek ya da tekrarlamak.
    YasakKurali("kimlik_isteme", TC_TUS_TAKIMINDAN, ifadeler=(
        "kimlik numaranızı söyle", "kimlik numaranızı yaz", "tc numaranızı söyle", "tc kimlik",
        "kimlik numaranız nedir", "kimlik numaranızı öğren", "telefon numaranızı",
        "adınızı ve soyadınızı", "doğum tarihiniz"),
        desenler=(r"\[gizli numara\]",)),
)

# Kullanıcıya gidecek LLM metninin azami uzunluğu (karakter); aşarsa yedek cümle kullanılır.
AZAMI_LLM_YANIT_UZUNLUGU = 300


# --------------------------------------------------------------------------
# Backend hata kodları (HastaKabul.Domain.Common.ErrorCodes)
# İş-mantığı hataları kullanıcıya yansıtılabilir; geri kalan HER kod sistem hatasıdır
# (kapalı liste: bilinmeyen kod "dolu" değil "sistem hatası" sayılır — halisaha D4-b).
# --------------------------------------------------------------------------

IS_MANTIGI_HATALARI = frozenset({
    "SLOT_TAKEN", "NOT_FOUND", "VOICE_SESSION_NOT_FOUND", "VOICE_SESSION_NOT_VERIFIED",
    "VOICE_SESSION_EXHAUSTED", "INVALID_STATUS_TRANSITION",
    # Demo sağlamlaştırması (2026-11-16): hastanın başka randevusuyla çakışma ve geçmişte kalan slot (409).
    "APPOINTMENT_CONFLICT", "SLOT_IN_PAST",
    # VALIDATION_FAILED bilerek yok: bu servis hep geçerli istek gönderir; 400 gelirse
    # sözleşme/yapılandırma hatasıdır ve "boş saat yok" diye yansıtılmamalıdır (D2).
})


# --------------------------------------------------------------------------
# Ses: TTS ön işleme, STT anahtar terimleri, hayalet transkriptler
# --------------------------------------------------------------------------

_KOD_DESENI = re.compile(r"(?P<on>[Kk]od(?:unuz|u)?\s*:?\s*)(?P<kod>\d{4,8})\b")


# ---- Doz metni (YALNIZ sesli kopya; ekrandaki metin değişmez). Yalnız anlamı değiştirmeyen, kesin
# açılımlar: sayıdan sonra gelen birim kısaltması, "2x1" ve ondalık sayı. Emin olunmayan kalıplara
# ("8 saatte bir", "günde 3 kez 1 tablet", "15.09.2026") dokunulmaz. Piper "mg"yi "mege", "2x1 7 gün"ü
# tek sözcük gibi okuyordu (STT geri dönüşü "2x17g").
_BIRIMLER = {"mg": "miligram", "ml": "mililitre", "mcg": "mikrogram", "µg": "mikrogram", "μg": "mikrogram",
             "g": "gram"}
_HARF = r"\w"
_DOZ_DESENI = re.compile(r"(?<![\w.,:])(?P<tam>\d{1,6})(?:[.,](?P<kesir>\d{1,3}))?\s?(?P<birim>mcg|mg|ml|µg|μg|g)"
                         r"(?!" + _HARF + r")", re.IGNORECASE)
_CARPI_DESENI = re.compile(r"(?<![\w.,:])(?P<a>\d)\s?[xX×]\s?(?P<b>\d)(?![\w.,:])")
_ONDALIK_VIRGUL = re.compile(r"(?<![\w.,:])(?P<tam>\d{1,6}),(?P<kesir>\d{1,3})(?![\d.,:])")
# Eğik çizgili derişim: "12,5 mg/5 ml", "250 mg/5ml", "5 mg/ml". Piper "/" işaretini okumuyordu; STT geri
# dönüşü "12,5 mg 5 ml" oluyor ve iki ayrı miktar gibi duyuluyordu. Yalnız İKİ TARAFI da bilinen birim olan
# kalıp "bölü" ile açılır (anlam değişmez); "100 IU/ml", "40 mg/gün", "mg/kg" gibi emin olunmayan kalıplara
# dokunulmaz.
_BIRIM = r"(?:mcg|mg|ml|µg|μg|g)"
_EGIK_DOZ_DESENI = re.compile(r"(?<![\w.,:])(?P<sol>\d{1,6}(?:[.,]\d{1,3})?\s?" + _BIRIM + r")\s?/\s?"
                              r"(?P<sag>(?:\d{1,6}(?:[.,]\d{1,3})?\s?)?" + _BIRIM + r")(?![\w/])", re.IGNORECASE)


def _egik_doz(m: re.Match[str]) -> str:
    sag = m.group("sag")
    if not any(ch.isdigit() for ch in sag):
        # "5 mg/ml" → "beş miligram bölü bir mililitre" (anlam aynı; çıplak "bölü mililitre" STT geri
        # dönüşünde "bölümü illeti" oluyordu, "bölü bir mililitre" doğru geri geliyor).
        sag = f"bir {_BIRIMLER[sag.lower()]}"
    return f"{m.group('sol')} bölü {sag}"


def _ondalik_oku(tam: str, kesir: str | None) -> str:
    """"12","5" → "on iki buçuk"; "0","5" → "sıfır virgül beş"; "2","25" → "iki virgül yirmi beş"."""
    okunus = sayi_oku(int(tam))
    if kesir is None:
        return okunus
    if kesir == "5" and int(tam) > 0:
        return f"{okunus} buçuk"
    sifirlar = len(kesir) - len(kesir.lstrip("0"))
    kalan = kesir.lstrip("0")
    parca = " ".join(["sıfır"] * sifirlar + ([sayi_oku(int(kalan))] if kalan else []))
    return f"{okunus} virgül {parca}"


def doz_metnini_oku(metin: str) -> str:
    """"80 mg" → "seksen miligram", "12.5 mg" → "on iki buçuk miligram", "1x1" → "bir çarpı bir",
    "2,5 tablet" → "iki buçuk tablet", "12,5 mg/5 ml" → "on iki buçuk miligram bölü beş mililitre".
    Noktalı ondalık yalnız birimden önce açılır (tarih/saat olabilir)."""
    metin = _EGIK_DOZ_DESENI.sub(_egik_doz, metin)
    metin = _DOZ_DESENI.sub(lambda m: f"{_ondalik_oku(m.group('tam'), m.group('kesir'))} "
                                      f"{_BIRIMLER[m.group('birim').lower()]}", metin)
    metin = _CARPI_DESENI.sub(lambda m: f"{sayi_oku(int(m.group('a')))} çarpı {sayi_oku(int(m.group('b')))}", metin)
    return _ONDALIK_VIRGUL.sub(lambda m: _ondalik_oku(m.group("tam"), m.group("kesir")), metin)


# ---- Hekim unvanları ve kurum adı (YALNIZ sesli kopya). Piper "Uzm. Dr."yi harf harf, "MedTech"i İngilizce
# yazımla bozuk okuyordu (bkz. KURUM.md "Okunuş denetimi").
_UNVAN_OKUNUSLARI = (
    (re.compile(r"\bProf\.\s*Dr\."), "Profesör Doktor"),
    (re.compile(r"\bDoç\.\s*Dr\."), "Doçent Doktor"),
    (re.compile(r"\bUzm\.\s*Dr\."), "Uzman Doktor"),
    (re.compile(r"\bOp\.\s*Dr\."), "Operatör Doktor"),
    (re.compile(r"\bDr\.(?=\s)"), "Doktor"),
)
# Telefon numarası: "0850 000 00 00", "0 850 000 00 00", "0212 123 45 67" (ayırıcı boşluk ya da tire).
_TELEFON_DESENI = re.compile(r"(?<![\d])0\s?\d{3}[\s-]\d{3}[\s-]\d{2}[\s-]\d{2}(?![\d])")


def unvanlari_oku(metin: str) -> str:
    """"Uzm. Dr. Mehmet D." → "Uzman Doktor Mehmet D."."""
    for desen, okunus in _UNVAN_OKUNUSLARI:
        metin = desen.sub(okunus, metin)
    return metin


def kurum_okunuslari(metin: str) -> str:
    """kurum.json ``okunuslar`` eşlemesi ("MedTech" → "Med tek"); ekli biçimler de ("MedTech'in")."""
    for yazim, okunus in KURUM.okunuslar.items():
        metin = re.sub(r"(?<!\w)" + re.escape(yazim), okunus, metin)
    return metin


def telefonlari_oku(metin: str) -> str:
    """"0850 000 00 00" → "sıfır sekiz yüz elli, sıfır sıfır sıfır, sıfır sıfır, sıfır sıfır"."""
    return _TELEFON_DESENI.sub(lambda m: telefon_okunusu(m.group(0)), metin)


def tts_on_isleme(metin: str) -> str:
    """TTS'e gitmeden önce: erişim kodunu rakam rakam, saatleri doğal okunuşla, doz kısaltmalarını
    açık yaz. Saatler doz açılımından ÖNCE okunur; doz desenleri iki nokta içeren diziye dokunmaz."""
    metin = telefonlari_oku(metin)
    metin = _KOD_DESENI.sub(lambda m: m.group("on") + rakamlari_tek_tek_oku(m.group("kod")), metin)
    metin = metin.replace(" · ", ", ")
    metin = unvanlari_oku(kurum_okunuslari(metin))
    return doz_metnini_oku(saatleri_oku(metin))


STT_ANAHTAR_TERIMLER = (f"{KURUM_KISA_AD}, randevu, poliklinik, dahiliye, kardiyoloji, ortopedi, göz hastalıkları, "
                        "kulak burun boğaz, KBB, dermatoloji, cildiye, kadın doğum, çocuk doktoru, nöroloji, "
                        "üroloji, genel cerrahi, yarın, öğleden sonra, onaylıyorum, muayene, ilaç, reçete")

# Whisper'ın sessizlik/gürültüde uydurduğu bilinen Türkçe kalıplar (hayalet transkript).
HAYALET_KALIPLAR = ("altyazı m.k", "altyazı", "abone olmayı unutmayın", "izlediğiniz için teşekkürler",
                    "videoyu beğenmeyi", "bir sonraki videoda görüşmek üzere", "abone ol")
