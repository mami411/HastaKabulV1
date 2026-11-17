# Demo kılavuzu — MedTech Cep (MedTech Özel Hastanesi)

Tüm veriler sentetiktir; MedTech kurgusal bir özel hastanedir (çağrı merkezi 0850 000 00 00 demo numarasıdır).
Demo hastası TC: `10000000146` (Ayşe H.). Kurum ayarları: `KURUM.md`.

## 1. Başlatma

```powershell
powershell -ExecutionPolicy Bypass -File C:\Dev\HastaKabul\tools\hepsini-baslat.ps1 -ResetDemo
```

- Soğuk başlangıç ~16–17 sn, servisler açıkken ~9 sn. Betik eski kodla çalışan servisleri kendisi yeniden başlatır ve
  LLM/STT/TTS'i ısıtır ("4b Isitma" adımı). Bitince: `http://localhost:5173`.
- **Her sunum öncesi `-ResetDemo` ile başlatın.** Önceki provalarda alınan randevular duruyorsa "yarın Kardiyoloji"
  çakışmaya düşer (aşağıya bakın).
- Durdurma: `tools\hepsini-durdur.ps1` (~2 sn).

## 2. İki dakikalık ön kontrol

1. Betik son satırı: `Hepsi hazir: PostgreSQL 5433, API ..., ai-simplify 8001, ai-voice 8002, uygulama http://localhost:5173`.
2. Tarayıcıda 5173'ü açın, TC ile giriş → Ana sayfa "Günaydın, Ayşe H." ve yaklaşan randevuyu gösteriyor mu?
   Alttaki ortadaki **Asistan** düğmesine dokunun, mikrofon izni verin → karşılama cümlesi sesli okunuyor mu?
3. Bir kez "Kalbim çarpıyor" deyin → Kardiyoloji önerisi gelmeli. Sonra **Çıkış**.
4. Sesin hoparlörden geldiğini ve mikrofonun doğru cihaz olduğunu kontrol edin.

## 3. Senaryo (provada doğrulanan cümleler ve yanıtlar)

| # | Söyleyin / yapın | Beklenen yanıt |
|---|---|---|
| 1 | Tuş takımıyla `11111111111` → Giriş | "Girdiğiniz numara geçerli bir TC kimlik numarası değil…" (ağa istek gitmez) |
| 2 | `10000000146` → Giriş, sonra alt çubukta **Asistan** | Ana sayfa açılır; Asistan sayfasında "Hoş geldiniz, kimliğiniz doğrulandı. Ben MedTech Asistan. … Dahiliye, Kardiyoloji, Göz Hastalıkları ve Kulak Burun Boğaz dahil 11 polikliniğimiz var." |
| 3 | "Kalbim çarpıyor" | Kardiyoloji önerisi + "ani ve şiddetliyse 112" notu |
| 4 | "Evet, bakalım" | "Kardiyoloji için hangi gün olsun?" |
| 5 | "Yarın sabah" | "Yarın 09:00, 09:15 ve 09:30 uygun…" + saat ızgarası |
| 6 | "İkincisi olsun" | Onay kartı: Kardiyoloji, yarın 09:15, Doç. Dr. Elif K. |
| 7 | "Evet, onaylıyorum" (ya da "Seçimimi onayla" düğmesi) | "Randevunuz oluşturuldu… Erişim kodunuz ……" |
| 8 | "Son muayenemde hangi ilaçlar yazıldı?" | 1 Ekim Kardiyoloji, Amlodipin + "Önceki muayenelerinizde de yazılanlar: Metformin, Demir sülfat; ayrıntısı ekranda" (kart + ses) |
| 9 | "Metformini ne zaman kullanacağım?" | 21 Temmuz Dahiliye, Metformin kullanımı |
| 10 | "Bu ilacı bırakabilir miyim?" | Metformin hakkında genel bilgi: hekim talimatı + "…hekiminize danışmadan bırakmayın." + "hekiminize ya da eczacınıza danışın" |
| 11 | "Nefes alamıyorum" | 112 kartı (her adımda önceliklidir) |
| 12 | "Teşekkürler, görüşürüz" | "Görüşmek üzere, sağlıklı günler dilerim. MedTech Özel Hastanesi olarak her zaman yanınızdayız." |
| 13 | **Çıkış** | Giriş ekranı; TC, kod, ilaç ekranda/bellekte kalmaz |

Çakışma göstermek isterseniz: Çıkış → yeniden giriş → 3–7'yi tekrarlayın. Yanıt:
"Yarın Kardiyoloji polikliniğinde zaten bir randevunuz var; randevu oluşturulmadı. Başka bir gün seçebilirsiniz…"

**Asistansız kullanım (uygulama sayfaları):** girişten sonra alt çubuk: Ana sayfa · Randevu al · Asistan ·
Randevularım · İlaçlarım (Asistan sayfasından "Ana sayfaya dön" ile çıkılır).

| # | Yapın | Beklenen |
|---|---|---|
| A1 | **Randevu al** → Kardiyoloji → Yarın → 09:15 → "Randevuyu onayla" | "Randevunuz oluşturuldu" + 6 haneli erişim kodu |
| A2 | **Randevularım** | Bugün 09:00 Dahiliye (demo verisi) ve yarın 09:15 Kardiyoloji, kodlarıyla |
| A3 | Yeniden **Randevu al** → Kardiyoloji → Yarın → başka saat → onayla | Kırmızı uyarı "Aynı gün bu poliklinikte zaten randevunuz var…" + "Başka saat / Başka gün" |
| A4 | Randevularım → **Geçmiş muayeneler** | 1 Ekim Kardiyoloji ve 21 Temmuz Dahiliye (yalnız hekim onaylı özetler) |
| A5 | **İlaçlarım** | Amlodipin, Metformin, Demir sülfat: ne işe yarar, nasıl kullanılır, hangi muayenede yazıldı |
| A6 | **Asistan** → "yarın Kardiyoloji" → "sabah" → bir saat → "evet" | Asistan saatleri sunar; onayda uygulamadan alınan randevuyu görür: "Yarın Kardiyoloji polikliniğinde zaten bir randevunuz var; randevu oluşturulmadı…" |

Uygulamadan alınan randevu oturumu kapatmaz (aynı girişte asistan kullanılabilir); asistanda alınan randevu da
Randevularım'da görünür. Bu bölümü gösterdikten sonra sesli senaryodaki "yarın Kardiyoloji" adımları çakışmaya
düşer: önce sesli senaryoyu yapın ya da araya `-ResetDemo` koyun.

### MedTech poliklinikleri, hekim seçimi ve kurum soruları (2026-11-16 gerçek backend + 8002 ile denendi)

11 poliklinik: Dahiliye, Kardiyoloji, Ortopedi, Göz Hastalıkları, Kulak Burun Boğaz, Dermatoloji, Kadın Hastalıkları
ve Doğum, Çocuk Sağlığı ve Hastalıkları, Nöroloji, Üroloji, Genel Cerrahi. Dahiliye, Kadın Doğum ve Çocuk'ta iki
hekim var; asistan bu polikliniklerde hekim tercihini sorar. Çalışma saatleri: hafta içi 09:00–12:00 ve 13:00–17:00,
cumartesi/pazar 09:00–13:00 (her gün boş saat vardır). Asistan oturum başına bir randevu alır; her satır için
Çıkış → yeniden giriş.

| Söyleyin | Beklenen |
|---|---|
| "Kadın doğuma randevu almak istiyorum" | "Kadın Hastalıkları ve Doğum polikliniğimizde Prof. Dr. Nermin T. ve Op. Dr. Selin E. hasta kabul ediyor. Hangi hekimimizden randevu istersiniz? Fark etmezse 'fark etmez' diyebilirsiniz." + hekim düğmeleri |
| "Selin Hanım olsun" (ya da "ikincisi", "operatör doktor", ya da "Op. Dr. Selin E." düğmesine dokunun) | "Tercihinizi not aldım: Op. Dr. Selin E. Kadın Hastalıkları ve Doğum için hangi gün olsun?…" → sonra yalnız onun saatleri |
| Hekim sorusunda listede olmayan ad: "Zümrüt Hanım" | "Bu poliklinikte bu adla bir hekimimiz görünmüyor. Prof. Dr. Nermin T. ve Op. Dr. Selin E. arasından seçebilir ya da 'fark etmez' diyebilirsiniz." (LLM'e gitmez) |
| "Çocuğumun ateşi var" → "Evet, bakalım" → "Uzm. Dr. Burak Ş." düğmesi ya da "Burak Bey olsun" | Hekim sorusu → "Tercihinizi not aldım: Uzm. Dr. Burak Ş. Çocuk Sağlığı ve Hastalıkları için hangi gün olsun?…" |
| "Çocuk doktoru" → "fark etmez" | Hekim sorusu → "Çocuk Sağlığı ve Hastalıkları için hangi gün olsun?…" (iki hekimin saatleri) |
| "Göz doktoruna randevu" / "kabebe randevusu" / "cildiye" / "nöroloji" / "üroloji" / "genel cerrahi" | "<Poliklinik> için hangi gün olsun?…" (tek hekimli: hekim sorulmaz) |
| "Kulağım ağrıyor" / "cildimde döküntü var" / "gözüm bulanık görüyor" | "Bu şikâyet için genellikle Kulak Burun Boğaz / Dermatoloji / Göz Hastalıkları polikliniğine başvurulur…" |
| "Çocuğumun ateşi var" | Çocuk Sağlığı önerisi + "Çocuğunuzda nefes darlığı, bilinç bulanıklığı ya da havale varsa… 112" |
| "Çağrı merkezinizin numarası ne" | "MedTech Özel Hastanesi çağrı merkezi numarası 0850 000 00 00…" |
| "Muayene ücreti ne kadar" / "SGK geçiyor mu" | Ücret/sigorta bilgisi verilmez: "…0850 000 00 00 numaralı çağrı merkezimizi arayabilirsiniz." |
| "Adresiniz nedir" / "Cumartesi açık mısınız" | "MedTech Özel Hastanesi adresi: Örnek Mahallesi, Şifa Sokağı No: 1, İstanbul. Yol tarifi için…" / "Polikliniklerimiz hafta içi 09:00 ile 17:00 arasında, cumartesi ve pazar 09:00 ile 13:00 arasında hasta kabul eder;…" |
| Randevu akışının ortasında kurum sorusu (ör. hekim sorusundayken "çağrı merkezinizin numarası ne") | Kurum yanıtı + "Randevunuza devam edelim." + o adımın sorusu ("Hangi hekimimizi tercih edersiniz?…" / "Hangi gün gelmek istersiniz?…" / "Hangi saati istersiniz?…"); seçimler ve düğmeler korunur |

Asistan sayfasında 4'ten fazla seçenek (11 poliklinik) düğme olarak sarılır; ilk ~3,5 satır görünür, kalanı (Nöroloji,
Üroloji, Genel Cerrahi) seçenek alanı kaydırılınca görünür. Randevu al sayfasında arama kutusu poliklinik ve hekim
adında arar ("nöro", "selin").

### İlaç soruları (söylenecek cümleler)

Yanıtlar LLM'den değil, ilaç bilgi dağarcığından (`hasta-kabul-ai/ses_servisi/alanlar/ilac_dagarcigi.py`, demo
amaçlı genel bilgi; gerçek kullanımdan önce hekim/eczacı gözden geçirmeli) ve hekim onaylı özetten şablonla kurulur.
Hepsi "…hakkında genel bilgi: …" ile başlar, "…hekiminize ya da eczacınıza danışın." ile biter. Gerçek backend ile
2026-11-16'da yazılı olarak denendi (ilk üç cümle ayrıca Piper sesi → Whisper ile); tablo 2026-11-16 çapraz
denetimde tarayıcıda (390×844, Asistan sayfası yazı modu) yeniden oynandı, yanıtlar tabloya uyuyor.

| Söyleyin | Konu | Beklenen yanıtın özü |
|---|---|---|
| "Amlodipin kullanırken bir alerjimin olmaması gerekiyor mu?" | alerji | Amlodipine/benzer tansiyon ilaçlarına alerjisi olanlar, tansiyonu çok düşük olanlar, ciddi aort darlığı olanlar kullanmamalı; böyle bir durumda ilacı kendiniz bırakmadan hekime danışın; yüzde/dilde şişme ya da nefes darlığında 112 |
| "Peki yan etkisi?" (önceki soruya bağlanır) | yan etki | Ayak bileğinde şişlik, baş ağrısı, yüzde kızarma…; yüz/dil şişmesi ya da nefes darlığında 112 |
| "Bunu içerken alkol alabilir miyim?" | alkol | Alkol tansiyon düşürücü etkiyi artırabilir, baş dönmesi… |
| "Aç karnına mı?" | aç/tok | Önce hekim talimatı, sonra "aç ya da tok alınabilir; greyfurt önerilmez" |
| "Unutursam ne olur?" | unutma | Hekim talimatı + "o dozu atlayın…, çift doz almayın" |
| "Hamileyken kullanabilir miyim?" | hamilelik | Güvenliği yeterince bilinmiyor; hekiminize danışın |
| "Başka ilaçlarla birlikte alabilir miyim?" | etkileşim | Bazı mantar ilaçları, antibiyotikler… tüm ilaçlarınızı söyleyin |
| "Ne kadar süre kullanacağım?" | süre | Hekim talimatı + "tansiyonunuz düzelse bile danışmadan bırakmayın" |
| "Metforminin dozunu artırayım mı?" | doz | Hekim talimatı + "dozu kendiniz değiştirmeyin; fazla aldıysanız 114, kötüyseniz 112" |
| "Demir ilacımı çayla içebilir miyim?" | yiyecek-içecek | Hekim talimatı (süt ve çayla almayın) + genel bilgi |
| "Yan etkisi var mı?" (yeni oturumda, ilaç adı yok) | — | "Hangi ilacınız için soruyorsunuz? Kayıtlarınızda Amlodipin, Metformin ve Demir sülfat görünüyor." → "Metformin" deyin |
| "Aspirinin yan etkisi var mı?" (kayıtta yok) | genel bilgi | Yerel modelin 2–3 cümlelik genel bilgisi + "Bu genel bir bilgidir… doktorunuza danışın."; balonun altında "Genel bilgi · doktorunuza danışın" etiketi. Hastanın kaydına bağlanmaz |
| "Amlodipin içtim, yüzüm şişti" | acil | 112 kartı |

### İlacı adı yerine tarifiyle sorma (2026-11-16 gerçek backend + 8002 ile denendi)

Hasta ilacın adını hatırlamayınca "tansiyon ilacım", "şeker hapım", "kardiyolojide yazılan ilaç", "sabah içtiğim
hap" der. Tarif hekim onaylı kayıtlarla kodda eşleştirilir (LLM'e gitmez): tek ilaca uyarsa ilacın adı söylenip konu
yanıtı verilir; birden çoğuna ya da hiçbirine uyarsa hangisi olduğu sorulur. Hastalık kelimesi ("tansiyon") ilaç
bağlamında şikâyet sayılmaz, poliklinik önerilmez. Yanıtlar yazılı olarak denendi; ilk iki cümle ayrıca Piper sesi →
Whisper ile (STT ikincisini "Şeker hapının…" yazdı, yanıt aynı).

| Söyleyin | Yanıt (birebir ya da özü) |
|---|---|
| "Bu tansiyonumu düşüren ilacı kullandığım zaman, dikkat etmem, yememem gereken şeyler var mı?" (STT'nin "düşünen" yazdığı biçim de) | "1 Ekim tarihli Kardiyoloji muayenenizde yazılan Amlodipin hakkında genel bilgi: Hekiminizin talimatı: Günde bir kez, her gün aynı saatte bir tablet alın. Aç ya da tok alınabilir. Greyfurt ve greyfurt suyu ilacın kandaki düzeyini artırabileceği için önerilmez. Bu genel bir bilgidir, hekiminizin talimatı esastır; kendi durumunuz için hekiminize ya da eczacınıza danışın." |
| "Şeker hapımın yan etkisi var mı?" | "21 Temmuz tarihli Dahiliye muayenenizde yazılan Metformin hakkında genel bilgi: Özellikle başlangıçta bulantı, ishal…" |
| "Kansızlık için verilen ilacı neyle almamalıyım?" | Demir sülfat: hekim talimatı (süt ve çayla almayın) + "Süt, çay, kahve ve yumurta emilimi azaltır…" |
| "Kardiyolojide yazılan ilacı alkolle içebilir miyim?" | Amlodipin, alkol bilgisi |
| "Son muayenede yazılan ilaç ne işe yarıyor?" | "1 Ekim tarihli Kardiyoloji muayenenizde yazılan Amlodipin: Damarlarınızı gevşeterek tansiyonunuzu düşürür…" |
| "Sabah içtiğim hapın yan etkisi ne?" → "Metformin" | "Kayıtlarınızda sabah kullanılan birden çok ilaç var: Metformin ve Demir sülfat. Hangisini soruyorsunuz? Adını söyleyebilirsiniz." → Metformin yan etki bilgisi |
| "Kolesterol ilacımın yan etkisi var mı?" (kayıtta yok) | "Hekim onaylı kayıtlarınızda kolesterol için bir ilaç görünmüyor. Kayıtlarınızda Amlodipin, Metformin ve Demir sülfat var; bunlardan birini soruyorsanız adını söyleyebilirsiniz." |
| "Tansiyon ilacımla araba kullanabilir miyim?" (dağarcıkta konu yok) | "Amlodipin için bu soruya dair elimde onaylı bilgi yok; hekiminize ya da eczacınıza danışın. İsterseniz ne işe yaradığını, yan etkilerini…" |
| "Şeker ilacım için randevu almak istiyorum" | Geçmiş okunmaz: "Bu şikâyet için genellikle Dahiliye polikliniğine başvurulur. Dahiliye polikliniğinden randevu bakmamı ister misiniz?" |

Aynı gün aynı yolla denenen diğer düzeltmeler: "Kalbim sıkışıyor, soğuk soğuk terliyorum" (Piper → Whisper ile de) ve
"Çocuğum çamaşır suyu içti" → 112 kartı; "Sizi telefonla nasıl ararım" → çağrı merkezi; "Bende ne var sizce" →
"Belirtilere bakarak teşhis koyamam…"; "Gözlerim kuruyor damla kullanabilir miyim" → "Bir ilacın sizin durumunuza uygun
olup olmadığını söyleyemem…"; "Antibiyotik kullanmam gerekir mi" → "Size ilaç öneremem…"; "Tansiyonum çıktı, tuz yemem
zararlı mı" → genel bilgi (ilaç sorusu değil); "Boğazım ağrıyor, soğuk su içmek zararlı mı" → öz bakım bilgisi + KBB
teklifi; "Son zamanlarda çok unutkanım" → Nöroloji önerisi; "Metformini sabah mı akşam mı içmeliyim" → hekim talimatı
(randevu saati sanılmaz).

### Geçmiş muayene soruları — gündelik biçimler (2026-11-16 gerçek backend + 8002 ile denendi)

Yanıtlar yalnız hekim onaylı özetten, şablonla (LLM'siz) kurulur; geçmiş kartı ekranda.

| Söyleyin | Beklenen |
|---|---|
| "Ya en son randevu, bu doktor bana ne yazmıştı?" / "Geçen sefer doktor ne verdi?" / "Bana ne yazdılar?" / "Geçen randevuda ne yazıldı?" | 8. adımla aynı: "1 Ekim tarihli Kardiyoloji muayenenizde 1 ilaç yazılmış. Amlodipin: … Önceki muayenelerinizde de yazılanlar: Metformin, Demir sülfat; ayrıntısı ekranda…" |
| "En son ne zaman muayene oldum?" / "En son hangi doktora gittim?" | "Hekim onaylı kayıtlarınıza göre son muayeneniz 1 Ekim tarihinde Kardiyoloji polikliniğinde, Doç. Dr. Elif K. ile yapılmış. Bu muayenede yazılan ilaçlar: Amlodipin. Önceki muayeneleriniz: 21 Temmuz Dahiliye; ayrıntısı ekranda…" |
| "Doktor ne demişti?" | "1 Ekim tarihli Kardiyoloji muayenenizin özeti: …" + hekimin uyarıları (yorum eklenmez) |
| "Kardiyolojide ne yazmışlardı?" / "Elif Hanım ne yazmıştı?" | Yalnız Kardiyoloji muayenesi (Amlodipin) |
| "Ortopedide ne yazmışlardı?" / "Zeynep Hanım ne yazmıştı?" | "Ortopedi için hekim onaylı bir kayıt görünmüyor…" / "…bu adla bir hekimin muayenesi görünmüyor…" |
| "Aspirin ne için yazılmıştı?" (kayıtta yok) | Değişmedi: "Kayıtlarınızda bu ilaç görünmüyor…" |
| "Randevum ne zaman?" | Değişmedi: "Bu sürümde mevcut randevularınızı gösteremiyorum…" |

### Genel sağlık soruları (yapay zekâ ile kısa genel bilgi)

Kişiye özel olmayan sorular yerel modelle (Ollama, makine dışına çıkmaz) yanıtlanır; modele yalnız kimliği
maskelenmiş soru gider, hasta kaydı gitmez. Model metni kodda denetlenir (doz, sayı, teşhis, "kullanabilirsiniz"
gibi kişisel karar → reddedilir) ve kapanış her zaman koddan eklenir. Yanıt her seferinde birebir aynı olmayabilir.

| Söyleyin | Beklenen |
|---|---|
| "Baş ağrısına ne iyi gelir?" | Kısa genel bilgi (dinlenme, sıvı…) + "Bu genel bir bilgidir, size özel bir tavsiye değildir; kendi durumunuz için doktorunuza danışın." + etiket. 2026-11-16 denetimde (gemma3:4b) kelimesi kelimesine: "Baş ağrısı genellikle gerginlik veya stresle ilgilidir. Bol su içmek ve dinlenmek bazı kişilerde baş ağrısını hafifletebilir. Bu genel bir bilgidir, size özel bir tavsiye değildir; kendi durumunuz için doktorunuza danışın." |
| "Grip ile soğuk algınlığının farkı ne?" | Kısa karşılaştırma + aynı kapanış |
| "Parol ne işe yarar?" | Dağarcıktan (LLM'siz): "Parasetamol hakkında genel bilgi: Hafif ve orta şiddetli ağrıları ve ateşi azaltmak için kullanılır…" |
| "Metforminle araba kullanabilir miyim?" (kayıtlı ilaç) | Kısa genel bilgi + "Hekiminizin talimatı: Günde iki kez…" (koddan) + kapanış + geçmiş kartı |
| "Aspirin dozunu artırabilir miyim?" | Sabit: "İlaç dozu ve kullanım sıklığı kişiye göre belirlenir; bu konuda size özel bilgi veremem. Lütfen doktorunuza ya da eczacınıza danışın…" (etiket yok) |
| "Kolesterol tahlilim 240 çıktı, ne demek?" | Sabit: "Tahlil ve ölçüm sonuçlarını yorumlayamam…" |
| "Başım ağrıyor ne yapayım" | 2026-11-16'dan beri tavsiye yolu (aşağıdaki "Randevu değil fikir isteyince" bölümü) |
| "Başım ağrıyor" | Değişmedi: genel bilgi değil, poliklinik önerisi (artık Nöroloji + "ani başlayan çok şiddetli baş ağrısı… 112" notu) |
| "Göğsüm çok ağrıyor, aspirin içeyim mi?" | 112 kartı (acil her şeyden önce) |

### Randevu değil fikir isteyince (şikâyet için tavsiye)

Şikâyetle birlikte "ne yapabilirim / ne iyi gelir / doktora gitmeden" denirse asistan önce genel öz bakım bilgisi
verir, sonra randevuyu yalnız bir kez ve ısrarsız teklif eder. Randevu istenmediği söylenirse poliklinik sorusu
gelmez; konu oturumda hatırlanır. Modele kullanıcının cümlesi değil, koddan kurulan "Konu: mide bulantısı / Mide
bulantısı için evde neler yapılabilir?" sorusu gider. 2026-11-16 gerçek backend + 8002 (gemma3:4b) ile denendi;
model metni her seferinde birebir aynı olmayabilir.

| Söyleyin | Beklenen |
|---|---|
| "Midem çok bulanıyor, ne yapabilirim?" | Kısa öz bakım bilgisi (az az sıvı, hafif yiyecek, dinlenme) + "Şikâyetiniz sürer ya da artarsa bir doktora başvurun." + "Bu genel bir bilgidir…" + "İsterseniz Dahiliye polikliniğinden randevu da bakabilirim." + Dahiliye öneri kartı ve "Genel bilgi" etiketi |
| "Randevu bakmak değil de, doktordan önce yapabileceğim bir şey var mı?" | Mide bulantısı için yine kısa bilgi; randevu teklifi ve poliklinik sorusu YOK |
| "Bir de bu bulantısına ne iyi gelir, bir çay da iyi gelebilir mi?" | Aynı konu + "bitki çaylarını bazı kişiler rahatlatıcı bulur ama bunlar bir tedavi yerine geçmez" türü temkinli cümle |
| "Tamam, randevu alayım." | Dahiliye hekim sorusu (randevu yine açık onayla) |
| "Midem bulanıyor" → "İstemiyorum" → "Evet, anlat" | Dahiliye önerisi → "Peki, randevu bakmıyorum. İsterseniz mide bulantısı için evde dikkat edilebilecek genel önerileri anlatabilirim." → kısa bilgi |
| "Doktora gitmeden yapabileceğim bir şey var mı?" (konu yok) | "Hangi şikâyetiniz için soruyorsunuz?…" → "Baş ağrısı" → bilgi + "Ani başlayan çok şiddetli baş ağrısı… 112" notu |
| "Boğazım ağrıyor evde ne yapabilirim" / "Karnım ağrıyor ne yapayım" / "Grip oldum ne iyi gelir" | Konuya göre kısa bilgi (+ karın ağrısında "çok şiddetliyse… acil servis" notu) + ilgili poliklinik teklifi |
| "Midem bulanıyor ve göğsüm ağrıyor" / "Kan kusuyorum ne yapabilirim" | 112 kartı (her şeyden önce) |
| "Başım ağrıyor, Parol içsem iyi gelir mi?" | Sabit: "Bir ilacın sizin durumunuza uygun olup olmadığını söyleyemem…" |
| "Çocuğumun ateşi var ne yapabilirim" | Değişmedi: Çocuk Sağlığı önerisi + 112 notu (çocuk/gebelikte tavsiye verilmez) |

Model yanıtı denetimden geçmezse ya da yanıt vermezse: "Bu konuda size şu an güvenilir bir bilgi veremiyorum. Lütfen
doktorunuza ya da eczacınıza danışın." Ayar: `SAGLIK_BILGI_LLM=0` eski davranışa döner.

İpucu: ilaç adını bir kez söyledikten sonra "peki…", "bunu…" diye devam edebilirsiniz; asistan son konuşulan ilacı
kullanır ve yanıtta ilacın adını söyler.

**Hekim tarafı (isteğe bağlı, API):** hekim girişi → bugünkü Dahiliye randevusunu kabul → tanı + reçete girişi →
yerel model ~3 sn'de sade özeti READY yapar → hekim onaylar → hasta "Son muayenemde hangi ilaçlar yazıldı?" deyince
yeni özeti duyar. (Provada "Akut Sinüzit / Amoksisilin + Parasetamol" ile doğrulandı.) Bunu yaptıktan sonra
8. adımın yanıtı değişir; sunumdan önce `-ResetDemo` yapın.

## 4. Konuşma ipuçları

- Tek başına **"evet"** yerine **"evet, bakalım"** / **"evet, onaylıyorum"** deyin (kısa "evet" bazen yanlış
  duyuluyor). Onayda emin değilseniz düğmeye basın.
- Asistan konuşurken değil, konuşması bitip mikrofon açıldıktan sonra konuşun.
- Asistan aynı oturumda ikinci randevu almaz; "Yeni bir randevu için Çıkış'a dokunup yeniden giriş yapın" der.
  **Randevu al** sayfası ise aynı girişte ikinci randevuya izin verir (aynı saat / aynı gün aynı poliklinik yine
  `APPOINTMENT_CONFLICT`).

## 5. Bir şey ters giderse

| Belirti | Ne yapın |
|---|---|
| Ses yanlış anlaşıldı | Aynı cümleyi tekrar söyleyin ya da klavye simgesiyle yazı moduna geçip yazın |
| Mikrofon açılmıyor / ses yok | Sayfayı yenileyin (oturum kapanır, yeniden giriş) |
| "Şu an sisteme ulaşamıyorum" | `hepsini-baslat.ps1` (parametresiz) çalıştırın, yeniden giriş yapın |
| Saatler beklenenden farklı / çakışma | `hepsini-baslat.ps1 -ResetDemo` |
| Her şey bozuldu | `hepsini-durdur.ps1` → `hepsini-baslat.ps1 -ResetDemo` (~20 sn) |

## 6. Bilinen sınırlar

- Sesli tur başına 5–20 sn sürer; çoğu, önceki yanıtın sesli okunması ve sessizlik algılamadır (model ~1 sn).
- Konuşma tanıma yerel Whisper'dır; gürültülü ortamda hata artar. Gerçek telefon ve gerçek insan sesiyle
  prova bu kılavuzda doğrulanmadı (yalnız sentetik Piper sesleriyle).
- Ortam gürültüsü anlamlı bir cümleye çevrilirse 5 dk hareketsizlik kilidi gecikebilir.
- Süreler (tarayıcıda doğrulandı): 5 dk dokunmazsanız "Oturum kilitlendi"; girişten 15 dk sonra "Oturum süreniz
  doldu". Asistanı açıp diğer sayfalarda gezinmek asistanı kilitlemez (7,5 dk gezinme sonrası soru yanıtlandı).
  Çıkış, kilit ve süre dolumu backend oturumunu da kapatır.
- Ana sayfa, saati geçmiş bugünkü randevuyu (demo verisinde bugün 09:00 Dahiliye) gün bitene kadar "Yaklaşan"
  gösterir.
- "Ramipril sabah mı akşam mı" gibi kayıtta olmayan ilaçla saat sorusu randevu saati tercihi sanılabilir.
- Genel sağlık bilgisi küçük (4B) yerel modelden gelir; denetime rağmen ara sıra yanlış ya da bozuk Türkçe cümle
  çıkabilir (değerlendirmede görüldü: qwen modelinde "gut sindirim sistemi hastalığıdır" gibi). Sunumda yukarıdaki
  tablodaki soruları kullanın. Bu makinede sağlık yanıtı için `gemma3:4b` etkin (8002 `/health` →
  `saglikBilgisi.model`); anlama modeli qwen olarak kalır, model geçişinde ilk yanıt ~3–5 sn gecikebilir.
- Randevu al sayfasında aynı gün aynı poliklinikte çakışma uyarısında "Başka saat" düğmesi de görünür; aynı güne
  başka saat de çakışır, doğrusu "Başka gün"dür.
- Tavsiye yanıtları da küçük yerel modelden gelir; canlı denemede ara sıra bozuk Türkçe ("nemli havadaki olmamak")
  ve tartışmalı cümleler ("idrar yanmasında su bölgeyi soğutabilir") görüldü. Konuşma tanıma şikâyet kelimesini
  bozarsa ("bu lambasına") konu yalnız önceki turdan bulunur; ilk cümlede bozulan şikâyet çoğu zaman anlaşılmaz.
- `npm run dev:mobil` (https://<bilgisayar-ip>:5174) telefonla deneme içindir; sunumda 5173 kullanın.

## 7. KVKK sorusuna iki cümlelik yanıt

TC kimlik numarası tuş takımıyla girilir ve yalnız hastanenin kendi sunucusunda doğrulanır; dil modeline ve ses
servisine gitmez, model yalnızca maskelenmiş cümleleri görür ve tüm yapay zekâ bileşenleri yerel makinede çalışır. Hastaya
okunan kendi sağlık bilgisi (ilaçları, muayeneleri) hekimin onayladığı özettir; genel sağlık sorularında ise yerel
modele yalnızca kimliği maskelenmiş soru gider (hasta kaydı gitmez), yanıt kodda denetlenip "genel bilgi · doktorunuza
danışın" diye etiketlenir.
