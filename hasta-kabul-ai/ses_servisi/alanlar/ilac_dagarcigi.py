"""İlaç bilgi dağarcığı: ilaç başına, konu başına hazırlanmış GENEL bilgi cümleleri.

!!! UYARI — DEMO AMAÇLI İÇERİK !!!
Bu dosyadaki metinler bir demo için, kamuya açık prospektüs/kullanma talimatı düzeyindeki genel bilgiler
özetlenerek hazırlanmıştır. Bir hekim ya da eczacı tarafından GÖZDEN GEÇİRİLMEMİŞTİR. Gerçek hastalarla
kullanılmadan önce her cümle bir hekim/eczacı tarafından doğrulanmalı, güncel kullanma talimatlarıyla
karşılaştırılmalı ve onaylanmalıdır.

İlkeler (kodla da zorlanır, bkz. ``ses_servisi/ilac_bilgisi.py``):
- Metinler geneldir; kişiye özel karar içermez ("sizin için uygundur" denmez). Her yanıt "hekiminize ya da
  eczacınıza danışın" kapanışıyla biter.
- Doz (mg, kaç tablet, günde kaç kez) BURADA YOKTUR: doz ve sıklık her zaman hekimin onayladığı özetten
  okunur. Buradaki zamanlama bilgisi hekim talimatıyla çelişirse hekim talimatı esastır; yanıtta hekim
  talimatı önce okunur.
- Bir ilaç ya da konu burada yoksa yanıt uydurulmaz: "elimde onaylı bilgi yok" şablonu kullanılır.
- Bu metinler LLM'e GİTMEZ; LLM bu dosyayı görmez. Kullanıcıya giden cümleler yalnız buradan ve konu
  modülündeki şablonlardan kurulur.

Konu kodları: amac, yan_etki, alerji, yemek, alkol, unutma, hamilelik, etkilesim, sure, doz (doz yalnız ek
not; genel doz uyarısı konu modülündedir). "kullanim" (nasıl/ne zaman) her zaman hekim talimatıdır.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEMO_UYARISI = ("Demo amaçlı genel bilgidir; gerçek kullanımdan önce bir hekim/eczacı tarafından gözden "
                "geçirilmelidir.")

# Sesli yanıtın taşınabilir kalması için her konu metni bu sınırı aşmaz (test edilir).
AZAMI_KONU_METNI = 300

_UNUTMA_GENEL = ("Hatırladığınızda alın; bir sonraki doz zamanına az kaldıysa unutulan dozu atlayın. "
                 "Unutulan dozu telafi etmek için çift doz almayın.")
_HAMILELIK_GENEL = ("Hamileyseniz, hamile kalmayı planlıyorsanız ya da emziriyorsanız bu ilacı kullanmaya "
                    "devam etmeden önce hekiminize danışın.")


@dataclass(frozen=True)
class IlacKaydi:
    ad: str                          # görünen ad
    adlar: tuple[str, ...]           # kayıttaki ilaç adı bunlardan biriyle başlamalı (normalize, ASCII katlı)
    konular: dict[str, str] = field(default_factory=dict)
    # Yalnız cümlede ilaç bağlamı ve LLM maskesi için ek kökler ("demir ilacım"); kayıt eşleşmesinde
    # KULLANILMAZ (ör. "Demir (III) hidroksit" demir sülfatın bilgisini almamalı).
    metin_adlari: tuple[str, ...] = ()
    # Kullanıcının ilacı ADIYLA değil amacıyla anmasında ("tansiyon ilacım", "şeker hapım") eşleme anahtarları
    # (``hasta_kabul.ILAC_AMAC_ANAHTARLARI``). Hekim onaylı özetteki "ne işe yarar" metni de ayrıca aranır.
    amaclar: tuple[str, ...] = ()


ILACLAR: tuple[IlacKaydi, ...] = (
    # ------------------------------------------------------------------ demo seed'inde yazılanlar
    IlacKaydi("Amlodipin", ("amlodipin",), {
        "amac": "Yüksek tansiyonun ve bazı göğüs ağrısı (anjina) türlerinin tedavisinde kullanılır; "
                "damarları gevşeterek kan basıncını düşürür.",
        "yan_etki": "Sık görülenler ayak bileklerinde şişlik, baş ağrısı, yüzde kızarma ya da sıcaklık hissi, "
                    "baş dönmesi, yorgunluk ve çarpıntıdır. Yüz, dil ya da boğazda şişme veya nefes darlığı "
                    "olursa hemen 112'yi arayın.",
        "alerji": "Amlodipine ya da benzer tansiyon ilaçlarına alerjisi olanlar, tansiyonu çok düşük olanlar ve "
                  "ciddi aort darlığı olanlar kullanmamalıdır. Böyle bir durumunuz ya da karaciğer hastalığınız "
                  "varsa ilacı kendiniz bırakmadan hekiminize danışın. Yüzde, dilde şişme ya da nefes darlığı "
                  "olursa 112'yi arayın.",
        "yemek": "Aç ya da tok alınabilir. Greyfurt ve greyfurt suyu ilacın kandaki düzeyini artırabileceği "
                 "için önerilmez.",
        "alkol": "Alkol tansiyon düşürücü etkiyi artırarak baş dönmesi ve bayılma hissine yol açabilir. "
                 "Alkol kullanımınızı hekiminizle konuşun.",
        "unutma": "Bir dozu almayı unuttuysanız o dozu atlayın ve bir sonraki dozu her zamanki saatinde alın. "
                  "Unutulan dozu telafi etmek için çift doz almayın.",
        "hamilelik": "Hamilelikte ve emzirmede güvenliği yeterince bilinmemektedir. Hamileyseniz, hamile kalmayı "
                     "planlıyorsanız ya da emziriyorsanız hekiminize danışın.",
        "etkilesim": "Bazı mantar ilaçları, bazı antibiyotikler, HIV ilaçları, rifampisin, sarı kantaron, "
                     "diltiazem ve yüksek doz simvastatin ile etkileşebilir. Kullandığınız tüm ilaçları ve "
                     "bitkisel ürünleri hekiminize ya da eczacınıza söyleyin.",
        "sure": "Tansiyon ilaçları çoğunlukla uzun süre kullanılır. Tansiyonunuz düzelse bile hekiminize "
                "danışmadan bırakmayın.",
    }, amaclar=("tansiyon", "kalp")),
    IlacKaydi("Metformin", ("metformin",), {
        "amac": "Tip 2 diyabette kan şekerini düşürmek ve dengede tutmak için kullanılır.",
        "yan_etki": "Özellikle başlangıçta bulantı, ishal, karın ağrısı, iştahsızlık ve ağızda metalik tat "
                    "görülebilir; çoğu zamanla azalır. Uzun kullanımda B12 vitamini düşebilir. Şiddetli kusma, "
                    "karın ağrısı, kas krampı ya da nefes almada güçlük olursa hemen 112'yi arayın.",
        "alerji": "Metformine alerjisi olanlar, ciddi böbrek ya da karaciğer sorunu olanlar, ağır enfeksiyon "
                  "veya sıvı kaybı yaşayanlar ve çok alkol kullananlar genellikle kullanmamalıdır. Böyle bir "
                  "durumunuz varsa ilacı kendiniz bırakmadan hekiminize danışın; ilaçlı görüntüleme ya da "
                  "ameliyattan önce de söyleyin.",
        "yemek": "Yemekle birlikte ya da yemekten hemen sonra alınır; bu, mide ve bağırsak şikâyetlerini "
                 "azaltır.",
        "alkol": "Alkol, laktik asidoz denen ciddi bir yan etkinin ve kan şekeri düşmesinin riskini artırır. "
                 "Metformin kullanırken alkolden kaçınılması önerilir.",
        "unutma": "Unutulan dozu telafi etmek için çift doz almayın; bir sonraki dozu her zamanki saatinde "
                  "yemekle alın.",
        "hamilelik": "Hamilelikte şeker tedavisi değiştirilebilir. Hamileyseniz, hamile kalmayı "
                     "planlıyorsanız ya da emziriyorsanız hekiminize danışın.",
        "etkilesim": "İlaçlı (iyotlu kontrast maddeli) görüntüleme, idrar söktürücüler, kortizonlu ilaçlar, "
                     "bazı tansiyon ilaçları ve bazı ağrı kesicilerle etkileşebilir. Kullandığınız tüm "
                     "ilaçları hekiminize ya da eczacınıza söyleyin.",
        "sure": "Diyabet tedavisi genellikle uzun sürelidir. Kan şekeriniz düzelse bile hekiminize danışmadan "
                "bırakmayın.",
    }, amaclar=("seker",)),
    IlacKaydi("Demir sülfat", ("demir sulfat",), metin_adlari=("demir",), konular={
        "amac": "Demir eksikliğini ve buna bağlı kansızlığı tedavi etmek ya da önlemek için kullanılır.",
        "yan_etki": "Bulantı, mide ağrısı, kabızlık ya da ishal görülebilir. Dışkının koyu ya da siyah renkte "
                    "olması beklenen bir durumdur. Döküntü, yüzde şişme ya da nefes darlığı olursa hemen "
                    "112'yi arayın.",
        "alerji": "Demir ilaçlarına alerjisi olanlar, vücudunda demir birikimi olanlar ve demir eksikliğine "
                  "bağlı olmayan kansızlığı olanlar genellikle kullanmamalıdır. Böyle bir durumunuz, mide "
                  "ülseriniz ya da bağırsak hastalığınız varsa ilacı kendiniz bırakmadan hekiminize danışın.",
        "yemek": "Aç karnına alındığında daha iyi emilir; portakal suyu gibi C vitamini içeren içecekler "
                 "emilimi artırır. Süt, çay, kahve ve yumurta emilimi azaltır; bunlarla birlikte almayın.",
        "alkol": "Alkol mide şikâyetlerini artırabilir; uzun süreli yoğun alkol kullanımı vücutta demir "
                 "birikimine katkıda bulunabilir. Alkol kullanımınızı hekiminizle konuşun.",
        "unutma": _UNUTMA_GENEL,
        "hamilelik": "Demir ilaçları hamilelikte ve emzirmede sık kullanılır, ancak doz ve süreyi hekim "
                     "belirler. Hamileyseniz ya da emziriyorsanız hekiminize söyleyin.",
        "etkilesim": "Mide asidini azaltan ilaçlar, bazı antibiyotikler, tiroid ilacı levotiroksin ve "
                     "kalsiyum ile aynı anda alındığında emilim azalabilir; araya zaman koymak gerekebilir. "
                     "Hekiminize ya da eczacınıza danışın.",
        "sure": "Kan değerleri düzelse bile demir depolarının dolması için tedavi genellikle birkaç ay sürer. "
                "Süreyi hekiminiz belirler; kendiniz bırakmayın.",
        "doz": "Çocuklarda zehirlenme yapabilir; çocukların ulaşamayacağı yerde saklayın.",
    }, amaclar=("demir",)),
    IlacKaydi("Parasetamol", ("parasetamol", "asetaminofen"), {
        "amac": "Hafif ve orta şiddetli ağrıları ve ateşi azaltmak için kullanılır.",
        "yan_etki": "Önerilen dozda yan etkisi genellikle azdır; nadiren döküntü ya da kaşıntı görülebilir. "
                    "Yüksek dozda karaciğere ciddi zarar verebilir. Yüzde şişme ya da nefes darlığı olursa "
                    "hemen 112'yi arayın.",
        "alerji": "Parasetamole alerjisi olanlar ve ağır karaciğer hastalığı olanlar kullanmamalıdır. Böbrek "
                  "hastalığınız varsa, düzenli alkol kullanıyorsanız ya da kilonuz düşükse kullanmadan önce "
                  "hekiminize danışın.",
        "yemek": "Genellikle aç ya da tok alınabilir.",
        "alkol": "Alkolle birlikte karaciğer hasarı riski artar. Parasetamol kullanırken alkol almamanız "
                 "önerilir.",
        "unutma": "Düzenli kullanıyorsanız hatırladığınızda alın; iki doz arasında hekiminizin ya da kullanma "
                  "talimatının belirttiği süreyi bekleyin. Çift doz almayın.",
        "hamilelik": "Hamilelikte ve emzirmede yalnız hekim önerisiyle, en kısa süre kullanılmalıdır. "
                     "Hekiminize danışın.",
        "etkilesim": "Parasetamol içeren başka ilaçlarla, örneğin bazı soğuk algınlığı ilaçlarıyla birlikte "
                     "alınmamalıdır. Kan sulandırıcı varfarin ve bazı sara ilaçlarıyla etkileşebilir.",
        "sure": "Hekiminiz başka bir süre söylemediyse ağrı için 5 günden, ateş için 3 günden uzun "
                "kullanmayın.",
        "doz": "Günlük en yüksek dozu aşmak karaciğere zarar verebilir.",
    }, amaclar=("agri", "ates")),
    # ------------------------------------------------------------------ yaygın ilaçlar (bilgi tabanı + ek)
    IlacKaydi("Lansoprazol", ("lansoprazol",), {
        "amac": "Mide asidinin üretimini azaltır; reflü, mide ve onikiparmak bağırsağı ülseri gibi "
                "durumlarda kullanılır.",
        "yan_etki": "Baş ağrısı, bulantı, ishal, kabızlık, karın ağrısı ve gaz görülebilir. Uzun kullanımda "
                    "magnezyum ve B12 vitamini düşebilir. Şiddetli ya da geçmeyen ishal olursa hekiminize "
                    "başvurun.",
        "alerji": "Lansoprazole ya da benzer mide ilaçlarına alerjisi olanlar ve HIV ilacı atazanavir "
                  "kullananlar kullanmamalıdır. Böyle bir durumunuz ya da karaciğer hastalığınız varsa ilacı "
                  "kendiniz bırakmadan hekiminize danışın.",
        "yemek": "Genellikle kahvaltıdan önce, aç karnına alınır.",
        "alkol": "Alkol mide asidini ve şikâyetleri artırabilir; tedavi süresince alkolden kaçınmanız "
                 "önerilir.",
        "unutma": _UNUTMA_GENEL,
        "hamilelik": _HAMILELIK_GENEL,
        "etkilesim": "Atazanavir, varfarin, teofilin, takrolimus, bazı mantar ilaçları ve digoksin ile "
                     "etkileşebilir. Mide koruyucu antasitlerle arasına zaman koymak gerekebilir.",
        "sure": "Süre hastalığa göre değişir. Şikâyetleriniz geçse de hekiminizin söylediği süre boyunca "
                "kullanın; bırakmadan önce danışın.",
    }, amaclar=("mide",)),
    IlacKaydi("Famotidin", ("famotidin",), {
        "amac": "Mide asidini azaltır; ülser, reflü ve mide yanmasında kullanılır.",
        "yan_etki": "Baş ağrısı, baş dönmesi, kabızlık ya da ishal görülebilir. Döküntü, yüzde şişme ya da "
                    "nefes darlığı olursa hemen 112'yi arayın.",
        "alerji": "Famotidine ya da benzer mide ilaçlarına alerjisi olanlar kullanmamalıdır. Böyle bir "
                  "durumunuz ya da böbrek hastalığınız varsa ilacı kendiniz bırakmadan hekiminize danışın.",
        "yemek": "Aç ya da tok alınabilir.",
        "alkol": "Alkol mide şikâyetlerini artırabilir; alkolden kaçınmanız önerilir.",
        "unutma": _UNUTMA_GENEL,
        "hamilelik": _HAMILELIK_GENEL,
        "etkilesim": "Bazı mantar ilaçlarının ve HIV ilacı atazanavirin emilimini azaltabilir; antasitler "
                     "famotidinin emilimini azaltabilir. Hekiminize ya da eczacınıza danışın.",
        "sure": "Süreyi hekiminiz belirler; şikâyetleriniz geçse de bırakmadan önce danışın.",
    }, amaclar=("mide",)),
    IlacKaydi("Amoksisilin", ("amoksisilin", "amoxicillin"), {
        "amac": "Bakterilerin yol açtığı enfeksiyonlarda kullanılan bir antibiyotiktir; grip ve nezle gibi "
                "virüs enfeksiyonlarına etkisi yoktur.",
        "yan_etki": "İshal, bulantı, döküntü ve kaşıntı görülebilir. Şiddetli ya da kanlı ishal olursa "
                    "hekiminize başvurun. Yüz, dil ya da boğazda şişme veya nefes darlığı olursa hemen "
                    "112'yi arayın.",
        "alerji": "Penisiline ya da amoksisiline alerjisi olanlar kullanmamalıdır. Daha önce bir antibiyotiğe "
                  "alerjiniz olduysa ilacı kendiniz bırakmadan ya da kullanmadan önce hekiminize danışın.",
        "yemek": "Aç ya da tok alınabilir.",
        "alkol": "Alkol mide şikâyetlerini artırabilir ve iyileşmeyi olumsuz etkileyebilir; tedavi süresince "
                 "alkolden kaçınmanız önerilir.",
        "unutma": _UNUTMA_GENEL,
        "hamilelik": _HAMILELIK_GENEL,
        "etkilesim": "Kan sulandırıcı varfarin, metotreksat ve gut ilacı allopurinol ile etkileşebilir. "
                     "Kullandığınız tüm ilaçları hekiminize ya da eczacınıza söyleyin.",
        "sure": "Kendinizi iyi hissetseniz bile hekiminizin söylediği süre boyunca kullanın; erken bırakmak "
                "enfeksiyonun geri gelmesine yol açabilir.",
    }, amaclar=("antibiyotik",)),
    IlacKaydi("Levotiroksin", ("levotiroksin", "levothyroxine"), {
        "amac": "Tiroid bezi yeterli hormon üretmediğinde eksik tiroid hormonunu yerine koyar.",
        "yan_etki": "Doğru dozda yan etkisi genellikle azdır. Çarpıntı, titreme, terleme, uykusuzluk ya da "
                    "kilo kaybı olursa hekiminize başvurun. Göğüs ağrısı olursa hemen 112'yi arayın.",
        "alerji": "Levotiroksine alerjisi olanlar, tedavi edilmemiş böbreküstü bezi yetmezliği olanlar ve "
                  "yakın zamanda kalp krizi geçirenler genellikle kullanmamalıdır. Böyle bir durumunuz ya da kalp "
                  "hastalığınız varsa ilacı kendiniz bırakmadan hekiminize danışın.",
        "yemek": "Genellikle sabah aç karnına, kahvaltıdan yarım saat ile bir saat önce bir bardak suyla "
                 "alınır. Soya, kahve ve süt emilimi azaltabilir.",
        "alkol": "Bilinen önemli bir etkileşimi yoktur; yine de alkol kullanımınızı hekiminizle konuşun.",
        "unutma": _UNUTMA_GENEL,
        "hamilelik": "Hamilelikte tedaviye genellikle devam edilir ve doz ayarı gerekebilir. Hamile "
                     "kalırsanız hekiminize hemen haber verin.",
        "etkilesim": "Demir, kalsiyum ve mide ilaçları (antasitler) emilimini azaltır; araya zaman konması "
                     "gerekebilir, ne kadar ara vereceğinizi hekiminize ya da eczacınıza sorun. Kan sulandırıcılar "
                     "ve diyabet ilaçlarıyla da etkileşebilir.",
        "sure": "Çoğunlukla uzun süre, çoğu zaman ömür boyu kullanılır. Hekiminize danışmadan bırakmayın.",
    }, amaclar=("tiroid",)),
    IlacKaydi("İbuprofen", ("ibuprofen",), {
        "amac": "Ağrı, ateş ve iltihabı azaltmak için kullanılan bir ağrı kesicidir.",
        "yan_etki": "Mide ağrısı, hazımsızlık, bulantı ve ishal görülebilir. Siyah dışkı ya da kanlı kusma "
                    "mide kanamasına işaret edebilir; böyle olursa ya da nefes darlığı olursa hemen 112'yi "
                    "arayın.",
        "alerji": "İbuprofene, aspirine ya da diğer ağrı kesicilere alerjisi olanlar, mide ülseri ya da "
                  "kanaması olanlar ve ağır kalp, karaciğer veya böbrek yetmezliği olanlar kullanmamalıdır. "
                  "Böyle bir durumunuz varsa kullanmadan önce hekiminize danışın.",
        "yemek": "Tok karnına ya da yemekle alınması mide şikâyetlerini azaltır.",
        "alkol": "Alkol mide kanaması riskini artırır; ibuprofen kullanırken alkolden kaçınmanız önerilir.",
        "unutma": "Gerektiğinde kullanılıyorsa unutulan doz için bir şey yapmanız gerekmez. Çift doz almayın.",
        "hamilelik": "Hamileliğin son üç ayında kullanılmamalıdır; diğer dönemlerde ve emzirmede yalnız "
                     "hekim önerisiyle kullanılır. Hamileyseniz kullanmadan önce hekiminize danışın.",
        "etkilesim": "Kan sulandırıcılar, aspirin ve diğer ağrı kesiciler, tansiyon ilaçları, idrar "
                     "söktürücüler ve kortizonlu ilaçlarla etkileşebilir. Hekiminize ya da eczacınıza danışın.",
        "sure": "Hekiminiz başka bir süre söylemediyse ağrı için 5 günden, ateş için 3 günden uzun "
                "kullanmayın.",
    }, amaclar=("agri", "ates")),
)
