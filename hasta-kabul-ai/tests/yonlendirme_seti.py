"""Yönlendirme değerlendirme seti: gerçek bir hastanın sesli asistana söyleyebileceği cümleler + beklenen YOL.

Kalıcı test verisidir (``tests/test_yonlendirme_seti.py`` koşturur; ``tools/yonlendirme_raporu.py`` tablo basar).
Cümleler gündelik, dolaylı, devrik, dolgu kelimeli ve konuşma tanıma (STT) hatalıdır; ``KULLANICI`` işaretliler
kullanıcının bugüne kadar gerçekten söylediği (yanlış yönlendirilmiş) cümlelerdir, birebir.

Yollar (yanıttan ``yol_bul`` ile çıkarılır):
  acil       (a) 112 şablonu (``data.emergency``)
  kurum      (b) kurum sorusunun sabit yanıtı (``data.institutionInfo``)
  gecmis     (c) geçmiş muayene/reçete: hekim onaylı özetten okuma (``data.history``, ilaç odağı/konusu yok)
  ilac       (d) kayıtlı bir ilaç hakkında soru: dağarcık + hekim talimatı, ya da "hangi ilacınız?" (``history``
                 odağı/konusu var)
  spesifik   (e) kişiye özel karar → sabit yönlendirme (doz, bırakma, tahlil, teşhis, ilaç önerisi)
  tavsiye    (f) şikâyet + tavsiye isteği → öz bakım bilgisi (+ "hangi şikâyet?" / randevu reddi)
  bilgi      (g) genel sağlık bilgi sorusu → yerel LLM ya da dağarcık genel bilgisi
  poliklinik (h) yalnız şikâyet → poliklinik önerisi / listesi
  randevu    (i) randevu akışı (poliklinik/hekim/gün/saat/seçim/onay, mevcut randevu şablonu)
  sohbet     selam, yardım, teşekkür, veda

``once``: aynı oturumda bu cümleden önce söylenenler (bağlam; yolları denetlenmez).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Vaka:
    metin: str
    yol: str
    once: tuple[str, ...] = ()
    kullanici: bool = False   # kullanıcının gerçek (sesli) cümlesi, birebir
    not_: str = ""


def V(metin: str, yol: str, *once: str, kullanici: bool = False, not_: str = "") -> Vaka:
    return Vaka(metin, yol, tuple(once), kullanici, not_)


T1 = "Midem çok kullanıyor, ne yapabilirim"
T2 = "Randevu bakmak değil de, doktordan önce yapabileceğin bir şey var mı? Doktora gitmeden."
T3 = ("Bide bu lambasına ne iyi gelir, doktora gitmek istemiyorum, önce bir çayda da iyi gelebilir mi, bunları "
      "öğrenmek istiyorum")
TANSIYON = "Bu tansiyonumu düşünen ilacı kullandığım zaman, dikkat etmem, yememem gereken şeyler var mı?"

VAKALAR: tuple[Vaka, ...] = (
    # ------------------------------------------------------------------ kullanıcının gerçek cümleleri
    V("amlodipin kullanırken bir alerjimin olmaması gerekiyor mu", "ilac", kullanici=True),
    V(T1, "tavsiye", kullanici=True),
    V(T2, "tavsiye", T1, kullanici=True),
    V(T3, "tavsiye", T1, T2, kullanici=True),
    V("Ya en son randevu, bu doktor bana ne yazmıştı?", "gecmis", kullanici=True),
    V(TANSIYON, "ilac", kullanici=True, not_="'düşünen' = STT'nin 'düşüren'i"),

    # ------------------------------------------------------------------ (a) acil
    V("Nefes alamıyorum", "acil"),
    V("göğsüm çok ağrıyor sol koluma da vuruyor", "acil"),
    V("babam bayıldı uyanmıyor", "acil"),
    V("annemin yüzü kaydı konuşamıyor", "acil"),
    V("tansiyon ilacımı içtim yüzüm şişti", "acil", not_="acil > ilaç bağlamı"),
    V("amlodipin içtikten sonra nefesim daraldı", "acil"),
    V("kan kusuyorum ne yapabilirim", "acil", not_="acil > tavsiye"),
    V("midem bulanıyor ve göğsüm ağrıyor", "acil"),
    V("kendime zarar vermek istiyorum", "acil"),
    V("oğlum bütün ilaçları içti", "acil"),
    V("mefes alamıyorum yardım edin", "acil", not_="STT"),
    V("göğsümde şiddetli bir ağrı var, randevu alabilir miyim", "acil", not_="acil > randevu"),
    V("ensem sertleşti ateşim de var", "acil"),
    V("karnımda dayanılmaz bir ağrı var", "acil"),
    V("dilim şişti şeker hapını yeni içmiştim", "acil"),
    V("nefes alamıyorum", "acil", "kardiyoloji", not_="randevu akışı ortasında"),
    V("imdat ambulans çağırın", "acil"),
    V("göğsüm sıkışıyor ne iyi gelir", "acil"),

    # ------------------------------------------------------------------ (b) kurum
    V("çağrı merkezinizin numarası ne", "kurum"),
    V("adresiniz neresi acaba", "kurum"),
    V("hastaneye nasıl gelebilirim", "kurum"),
    V("cumartesi açık mısınız", "kurum"),
    V("muayene ücreti ne kadar", "kurum"),
    V("SGK geçiyor mu sizde", "kurum"),
    V("özel sigortam geçerli mi", "kurum"),
    V("kaçta açılıyorsunuz", "kurum"),
    V("telefon numaranız ne", "kurum", "dahiliye", not_="randevu akışı ortasında"),
    V("yol tarifi alabilir miyim", "kurum"),

    # ------------------------------------------------------------------ (c) geçmiş muayene / reçete
    V("son muayenemde hangi ilaçlar yazıldı", "gecmis"),
    V("geçen sefer doktor ne verdi", "gecmis"),
    V("ilaçlarım neler", "gecmis"),
    V("en son ne zaman muayene oldum", "gecmis"),
    V("en son hangi doktora gittim", "gecmis"),
    V("doktor ne demişti", "gecmis"),
    V("kardiyolojide ne yazmışlardı", "gecmis"),
    V("Elif Hanım ne yazmıştı", "gecmis"),
    V("bana hangi ilaçları vermişlerdi", "gecmis"),
    V("reçetemde neler var", "gecmis"),
    V("dahiliyede bana ne yazdılar", "gecmis"),
    V("teşhisim neydi", "gecmis"),
    V("hangi ilaçları kullanıyordum", "gecmis"),
    V("aspirin ne için yazılmıştı", "gecmis", not_="kayıtta olmayan ilaç: dürüst 'görünmüyor'"),
    V("en son gittiğimde doktor ne dedi", "gecmis"),
    V("şey, geçen randevuda bana ne yazılmıştı", "gecmis"),

    # ------------------------------------------------------------------ (d) kayıtlı ilaç — adıyla
    V("Metforminin yan etkisi var mı", "ilac"),
    V("metformini aç karnına mı içeceğim", "ilac"),
    V("amlodipinle alkol alabilir miyim", "ilac"),
    V("demir ilacımı çayla içebilir miyim", "ilac"),
    V("Metformini unutursam ne olur", "ilac"),
    V("amlodipin hamilelikte kullanılır mı", "ilac"),
    V("Metformin ne işe yarıyor", "ilac"),
    V("amlodipin uyku yapar mı", "ilac"),
    V("metformin ile amlodipini birlikte alabilir miyim", "ilac"),
    V("amlodipini ne kadar süre kullanacağım", "ilac"),
    V("metforminin dozunu artırayım mı", "ilac"),
    V("Metformini bırakabilir miyim", "ilac"),
    V("Metforminle araba kullanabilir miyim", "ilac"),
    V("amlodipin kullanırken nelere dikkat etmeliyim", "ilac"),
    V("amlodipinin zararı var mı", "ilac"),
    V("peki yan etkisi", "ilac", "Amlodipin ne işe yarıyor", not_="takip sorusu"),
    V("bunu içerken alkol alabilir miyim", "ilac", "metformin ne işe yarar"),
    V("yan etkisi var mı", "ilac", not_="ilaç adı yok: 'hangi ilacınız?'"),
    V("Metformin", "ilac", "yan etkisi var mı", not_="'hangi ilacınız?' sorusunun yanıtı"),

    # ------------------------------------------------------------------ (d) kayıtlı ilaç — TARİFLE (Bölüm 1)
    V("tansiyon ilacım ne işe yarıyor", "ilac"),
    V("tansiyon ilacımın yan etkisi var mı", "ilac"),
    V("şeker hapımı yemekle mi alayım", "ilac"),
    V("şeker apımı unuttum ne yapayım", "ilac", not_="STT: hapımı"),
    V("tansiyom ilacını greyfurtla içebilir miyim", "ilac", not_="STT: tansiyon"),
    V("demir ilacım kabızlık yapar mı", "ilac"),
    V("kansızlık ilacımı sütle içebilir miyim", "ilac"),
    V("kalp doktorunun verdiği ilacın yan etkileri neler", "ilac"),
    V("kardiyolojide yazılan ilaçla alkol olur mu", "ilac"),
    V("en son yazılan ilaç ne işe yarıyor", "ilac"),
    V("sabah içtiğim ilaç aç karnına mı", "ilac", not_="iki ilaca uyar: hangisi sorulur"),
    V("akşam içtiğim ilacı unutursam ne olur", "ilac"),
    V("kolesterol ilacım ne zaman alınır", "ilac", not_="kayıtta yok: dürüst yanıt"),
    V("dahiliyede yazılan ilacı nasıl kullanacağım", "ilac", not_="iki ilaca uyar"),
    V("şeker için kullandığım ilaçla alkol içebilir miyim", "ilac"),
    V("tansiyon ilacım uyku yapar mı", "ilac"),
    V("tansiyon ilacımı içerken nelerden uzak durmalıyım", "ilac"),
    V("şeker ilacımın zararı var mı", "ilac"),
    V("tansiyon ilacımı kullanırken araba kullanabilir miyim", "ilac"),
    V("tansiyon hapı bana dokunur mu", "ilac"),
    V("kalbim için verilen ilacı unuttum", "ilac"),
    V("tansiyonumu düşüren ilacı hamileyken kullanabilir miyim", "ilac"),
    V("ya şu şeker hastalığı için verilen hap var ya, onu aç karnına mı içiyoruz", "ilac", not_="dolgu, devrik"),
    V("tansiyon ilacını ne zamana kadar içeceğim", "ilac"),
    V("kalp ilacımı neyle almamalıyım", "ilac"),

    # ------------------------------------------------------------------ (e) kişiye özel karar
    V("aspirin dozunu artırabilir miyim", "spesifik"),
    V("kolesterol tahlilim 240 çıktı, ne demek", "spesifik"),
    V("bende ne var sizce", "spesifik"),
    V("parolü kaç tane içmeliyim", "spesifik"),
    V("hamileyim parol kullanabilir miyim", "spesifik"),
    V("başım ağrıyor parol içsem iyi gelir mi", "spesifik"),
    V("midem ağrıyor hangi ilacı almalıyım", "spesifik"),
    V("aspirin ile klopidogrel birlikte alınır mı", "spesifik"),
    V("ramipril bırakabilir miyim", "spesifik"),
    V("tansiyonum 160 çıktı normal mi", "spesifik"),
    V("bana bir ilaç önerir misin", "spesifik"),
    V("kanser miyim acaba", "spesifik"),

    # ------------------------------------------------------------------ (f) şikâyet + tavsiye isteği
    V("başım ağrıyor ne yapayım", "tavsiye"),
    V("boğazım ağrıyor evde ne yapabilirim", "tavsiye"),
    V("grip oldum ne iyi gelir", "tavsiye"),
    V("karnım ağrıyor ne yapayım", "tavsiye"),
    V("uyuyamıyorum, ne yapsam", "tavsiye"),
    V("doktora gitmeden yapabileceğim bir şey var mı", "tavsiye", not_="konu yok: 'hangi şikâyet?'"),
    V("baş ağrısı", "tavsiye", "doktora gitmeden yapabileceğim bir şey var mı"),
    V("sırtım tutuldu ne iyi gelir", "tavsiye"),
    V("öksürüğüm geçmiyor ballı süt iyi gelir mi", "tavsiye"),
    V("midem yanıyor evde ne yapabilirim", "tavsiye"),
    V("randevu istemiyorum, başım ağrıyor ne yapabilirim", "tavsiye"),
    V("istemiyorum", "tavsiye", "midem bulanıyor", not_="öneriye ret"),
    V("evet anlat", "tavsiye", "midem bulanıyor", "istemiyorum"),
    V("ishal oldum ne yemeliyim", "tavsiye"),
    V("burnum tıkalı ne iyi gelir", "tavsiye"),
    V("üşüttüm galiba evde neler yapabilirim", "tavsiye"),
    V("doktora gitmek istemiyorum, midem bulanıyor bir önerin var mı", "tavsiye"),
    V("dişim ağrıyor ne yapabilirim", "tavsiye"),
    V("bir çay iyi gelir mi", "tavsiye", "midem bulanıyor"),

    # ------------------------------------------------------------------ (g) genel sağlık bilgisi
    V("aspirin ne işe yarar", "bilgi"),
    V("baş ağrısına ne iyi gelir", "bilgi"),
    V("grip ile soğuk algınlığının farkı ne", "bilgi"),
    V("Parol ne işe yarar", "bilgi"),
    V("ibuprofen aç karnına içilir mi", "bilgi"),
    V("kafein zararlı mı", "bilgi"),
    V("tansiyon neden yükselir", "bilgi"),
    V("D vitamini eksikliğinin belirtileri nelerdir", "bilgi"),
    V("migren nedir", "bilgi"),
    V("kolesterolüm yüksek nelere dikkat etmeliyim", "bilgi"),
    V("şeker hastalığı nasıl anlaşılır", "bilgi"),
    V("zatürre bulaşıcı mı", "bilgi"),
    V("uykusuzluğa ne iyi gelir", "bilgi"),
    V("tansiyonum yüksek, ne yemeliyim", "bilgi"),
    V("tansiyon hastaları nelere dikkat etmeli", "bilgi"),

    # ------------------------------------------------------------------ (h) yalnız şikâyet → poliklinik önerisi
    V("Kalbim çarpıyor", "poliklinik"),
    V("başım ağrıyor", "poliklinik"),
    V("gözüm bulanık görüyor", "poliklinik"),
    V("kulağım ağrıyor", "poliklinik"),
    V("cildimde döküntü var", "poliklinik"),
    V("çocuğumun ateşi var", "poliklinik"),
    V("tansiyonum yükseldi", "poliklinik", not_="ilaç bağlamı yok: şikâyet"),
    V("dizim çok ağrıyor", "poliklinik"),
    V("idrarda yanma var", "poliklinik"),
    V("hangi bölüme gitmeliyim", "poliklinik"),
    V("sürekli yorgunum", "poliklinik"),
    V("midem bulanıyor", "poliklinik"),
    V("belim tutuldu", "poliklinik"),
    V("şekerim yüksek çıktı galiba", "poliklinik"),
    V("nefesim daralıyor", "poliklinik"),
    V("şeker ilacım için randevu almak istiyorum", "poliklinik", not_="ilaç bağlamı + randevu isteği"),

    # ------------------------------------------------------------------ (i) randevu akışı
    V("kardiyolojiden randevu almak istiyorum", "randevu"),
    V("yarın sabah dahiliye", "randevu"),
    V("göz doktoruna randevu", "randevu"),
    V("kadın doğuma randevu almak istiyorum", "randevu"),
    V("randevu almak istiyorum", "randevu"),
    V("yarın sabah", "randevu", "kardiyoloji"),
    V("ikincisi olsun", "randevu", "kardiyoloji", "yarın sabah"),
    V("Evet, bakalım", "randevu", "Kalbim çarpıyor"),
    V("tamam randevu alayım", "randevu", T1),
    V("randevum ne zaman", "randevu"),
    V("randevumu iptal etmek istiyorum", "randevu"),
    V("cuma öğleden sonra kbb", "randevu"),
    V("nöroloji olsun", "randevu"),
    V("daha geç bir saat var mı", "randevu", "kardiyoloji", "yarın sabah"),
    V("fark etmez", "randevu", "dahiliye"),
    V("ortopediye randevu lazım belim ağrıyor", "randevu"),
    V("kalp doktoruna görünmek istiyorum", "randevu"),
    V("yarın için cildiye", "randevu"),
    V("haftaya salı kardiyolojiden randevu alabilir miyim", "randevu"),
    V("Selin Hanım olsun", "randevu", "kadın doğuma randevu almak istiyorum"),

    # ------------------------------------------------------------------ yollar arası sınırlar
    V("tansiyon ilacım bitti, randevu alabilir miyim", "poliklinik",
      not_="ilaç + randevu isteği: geçmiş okunmaz, ilacın polikliniği onayla önerilir"),
    V("şeker ilacımı içtim ama başım dönüyor", "ilac", not_="ilaç bağlamında belirti: ilaç (yan etki)"),
    V("evet onaylıyorum", "randevu", "kardiyoloji", "yarın sabah", "ikincisi olsun"),
    V("tansiyon ilacım ne işe yarıyor", "ilac", "kardiyoloji", "yarın sabah",
      not_="randevu akışı ortasında ilaç sorusu"),
    V("peki alkol", "ilac", "tansiyon ilacım ne işe yarıyor", not_="tarifle anılan ilaca takip"),
    V("başım ağrıyor, ne iyi gelir, doktora gitmeden", "tavsiye"),
    V("kalbim çarpıyor ne yapmalıyım", "spesifik",
      not_="ciddi belirti: tavsiye yok; 'yorum yapamam' + Kardiyoloji önerisi + 112 notu"),
    V("hamileyim midem bulanıyor ne yapabilirim", "poliklinik", not_="gebelik: tavsiye yok"),

    # ------------------------------------------------------------------ ikinci tur: daha dağınık / gündelik biçimler
    V("annem düştü kafasını çarptı kan durmuyor", "acil"),
    V("kalbim sıkışıyor soğuk soğuk terliyorum", "acil"),
    V("eşim nöbet geçiriyor", "acil"),
    V("nefesim kesiliyor sanki", "acil"),
    V("yanlışlıkla çok fazla hap içtim", "acil"),
    V("tansiyonum düşüyor bayılacak gibi oluyorum", "acil"),
    V("hastaneniz hangi semtte", "kurum"),
    V("pazar günü çalışıyor musunuz", "kurum"),
    V("sizi telefonla nasıl ararım", "kurum"),
    V("muayene parası ne kadar tutuyor", "kurum"),
    V("doktorum bana en son hangi ilacı vermişti", "gecmis"),
    V("geçen ay kardiyolojide ne dediler", "gecmis"),
    V("en son muayenemde doktor bana ne yazdı", "gecmis"),
    V("hangi doktora gitmiştim en son", "gecmis"),
    V("Metformini sabah mı akşam mı içmeliyim", "ilac"),
    V("amlodipini greyfurt suyuyla içsem olur mu", "ilac"),
    V("metformin böbreğe zarar verir mi", "ilac"),
    V("demir hapı dışkıyı siyah yapar mı", "ilac"),
    V("amlodipin içerken spor yapabilir miyim", "ilac"),
    V("tansiyon ilacımı sabah mı akşam mı içeyim", "ilac"),
    V("şeker ilacını içince midem bulanıyor normal mi", "ilac"),
    V("kardiyoloji doktorunun yazdığı hap ne için", "ilac"),
    V("bu ilacı ne zamana kadar kullanacağım", "ilac"),
    V("doktorun verdiği şeker ilacı tok karnına mı", "ilac"),
    V("tansiyon ilacımı unuttum iki tane içeyim mi", "ilac"),
    V("bu ağrı kesiciden günde kaç tane içebilirim", "spesifik"),
    V("kan değerlerim çıktı bakabilir misin", "spesifik"),
    V("antibiyotik kullanmam gerekir mi", "spesifik"),
    V("midem ekşiyor doktora gitmeden ne iyi gelir", "tavsiye"),
    V("boğazım gıcık gıcık ediyor ne yapsam geçer", "tavsiye"),
    V("başım zonkluyor evde ne yapabilirim", "tavsiye"),
    V("halsizim ne yapmam lazım", "tavsiye"),
    V("gece uyuyamıyorum bir önerin var mı", "tavsiye"),
    V("kabız oldum ne yapabilirim", "tavsiye"),
    V("regl ağrım var ne iyi gelir", "tavsiye"),
    V("grip aşısı ne zaman yapılır", "bilgi"),
    V("aspirin kanı sulandırır mı", "bilgi"),
    V("C vitamini gribe iyi gelir mi", "bilgi"),
    V("kolesterol için hangi yiyecekler iyi", "bilgi"),
    V("son zamanlarda çok unutkanım", "poliklinik"),
    V("ayağım burkuldu", "poliklinik"),
    V("sık sık idrara çıkıyorum", "poliklinik"),
    V("gözlerim kızarıyor", "poliklinik"),
    V("kulağımda çınlama var", "poliklinik"),
    V("doktor randevusu almak istiyorum belim için", "poliklinik"),
    V("dahiliyeye yarın öğleden sonra bakabilir misin", "randevu"),
    V("en erken kardiyoloji", "randevu"),
    V("göze haftaya", "randevu"),
    V("randevu alacaktım kardiyolojiden", "randevu"),
    V("ilki", "randevu", "kardiyoloji", "yarın sabah"),
    V("başka bir gün olsun", "randevu", "kardiyoloji", "yarın sabah"),
    V("sağ ol", "sohbet"),

    # ------------------------------------------------------------------ şikâyet + soru: poliklinik önerisi son çare
    V("tansiyonum çıktı, tuz yemem zararlı mı", "bilgi"),
    V("midem yanıyor, kahve içmem doğru mu", "tavsiye"),
    V("kolesterolüm yüksek yumurta yiyebilir miyim", "bilgi"),
    V("şekerim var, tatlı yiyebilir miyim", "bilgi"),
    V("başım ağrıyor bu neden olur", "spesifik"),
    V("gözlerim kuruyor damla kullanabilir miyim", "spesifik"),
    V("boğazım ağrıyor, soğuk su içmek zararlı mı", "tavsiye"),
    V("başım ağrıyor ilaç içsem geçer mi", "spesifik"),
    V("Ramipril sabah mı akşam mı alınır", "bilgi", not_="kayıtta olmayan ilaç: randevu saati sanılmamalı"),
    V("dizim ağrıyor merdiven çıkmak zararlı mı", "tavsiye"),

    # ------------------------------------------------------------------ ayarlanmadan önce denenen yeni cümleler
    # (genelleme denemesi: ilk koşuda 20/22; "çamaşır suyu içti" acili ve terimsiz tahlil sorusu genel kuralla düzeldi)
    V("tansiyon için içtiğim ilaç uyku yapar mı", "ilac"),
    V("şeker hapı kullanırken araba kullanabilir miyim", "ilac"),
    V("demir ilacımı çayla içsem olur mu", "ilac"),
    V("kalp ilacımı unuttum ne yapayım", "ilac"),
    V("sabah içtiğim hapın yan etkisi ne", "ilac", not_="iki ilaca uyuyor: hangisi sorulur"),
    V("kardiyoloji doktorunun verdiği ilaçla alkol olur mu", "ilac"),
    V("mide ilacımın yan etkisi var mı", "ilac", not_="kayıtta yok: dürüst yanıt"),
    V("tansiyonumu düşüren hap ne işe yarıyor", "ilac"),
    V("yüksek tansiyonda neye dikkat etmeli", "bilgi"),
    V("midem bulanıyor, limon iyi gelir mi", "tavsiye"),
    V("dişim ağrıyor, sıcak su içmek zararlı mı", "tavsiye"),
    V("bacağım şişti kırmızı oldu", "poliklinik"),
    V("gözüm seğiriyor", "poliklinik"),
    V("annem yere düştü bilinci kapandı", "acil"),
    V("çocuğum çamaşır suyu içti", "acil"),
    V("hastaneye nasıl ulaşırım", "kurum"),
    V("muayene ücreti ne kadar", "kurum"),
    V("tahlil sonucum yüksek çıktı ne demek", "spesifik"),
    V("bu ağrı için hangi hapı içeyim", "spesifik"),
    V("dahiliyeden randevu istiyorum", "randevu"),
    V("geçen ay doktor bana ne demişti", "gecmis"),
    V("kolesterol hapım ne zaman bitecek", "ilac", not_="kayıtta yok: dürüst yanıt"),

    # ------------------------------------------------------------------ sohbet
    V("merhaba", "sohbet"),
    V("teşekkürler", "sohbet"),
    V("neler yapabilirsin", "sohbet"),
    V("görüşürüz", "sohbet"),
)
