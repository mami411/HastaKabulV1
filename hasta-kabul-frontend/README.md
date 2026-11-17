# MedTech Cep — özel hastane mobil uygulaması (ön yüz)

Bu ön yüz özel klinik ve hastaneler için markalanabilir bir hasta uygulamasıdır; ilk örnek kurum kurgusal
**MedTech Özel Hastanesi**'dir (uygulama **MedTech Cep**, asistan **MedTech Asistan**, demo çağrı merkezi
0850 000 00 00). Ortak kurum bilgisi: `..\KURUM.md`.

Telefonda çalışan bir hasta uygulaması: hasta T.C. kimlik numarasıyla giriş yapar; ardından ya normal bir
uygulama gibi dokunarak (randevu al, randevularım, muayenelerim, ilaçlarım) ya da ayrı **Asistan** sayfasında
sesle/yazarak (poliklinik → gün → saat → açık onay → erişim kodu, son muayene ve ilaç soruları) kullanır.
İki kanal aynı randevu kurallarını ve aynı veriyi kullanır: birinde alınan randevu diğerinde görünür. Masaüstünde açılınca ortalanmış telefon genişliğinde bir uygulama
çerçevesi olarak görünür (sunum/projeksiyon için).

React 19 + TypeScript + Vite. Dış font, CDN ya da üçüncü taraf isteği yoktur (çevrimdışı çalışır, KVKK).

## Komutlar

```bash
npm install
npm run dev        # http://localhost:5173 — yalnız bu bilgisayar; arka uçlara doğrudan gider
npm run dev:mobil  # https://<bilgisayar-ip>:5174 — telefon demo modu (aşağıda)
npm run lint       # oxlint
npm run build      # tsc + vite build → dist/
npm run build:mobil && npm run preview:mobil  # telefon modunun derlenmiş hali (dist-mobil/, aynı vekil ve port)
```

Ekran görüntüleri: `docs/ekranlar/` (MedTech görünümü `medtech-*.png`; diğerleri markalama öncesi).

```bash
npm run ikonlar    # public/brand/logo*.svg → PNG uygulama ikonları (yerel Chrome/Edge ile)
```

Arka uçlar: hasta kabul API'si `:3000`, ses asistanı/orkestratör `:8002` (`/orchestrate`, `/stt`, `/tts`).
Hepsini birlikte açmak için `..\tools\hepsini-baslat.ps1`. Demo hasta: sentetik TC `10000000146`.

## Yapı

| Dosya | Görev |
| --- | --- |
| `src/App.tsx` | Uygulama çerçevesi; giriş ↔ uygulama (`AppShell`) |
| `src/hooks/useAssistant.ts` | Oturum, TC doğrulama, orkestratör yanıtları, ses döngüsü, kilit, çıkış; `enterAssistant`/`leaveAssistant` |
| `src/hooks/usePatientData.ts` | Asistansız sayfaların verisi (profil, randevular, muayeneler), oturum hatası → girişe dönüş, oturum süresi sayacı, İstanbul saatli tarih yardımcıları |
| `src/components/AppShell.tsx` | Giriş sonrası iskelet: başlık + Çıkış, alt sekme çubuğu, sayfa geçişleri (geri tuşu için `history.pushState`), hareketsizlik uyarısı |
| `src/components/pages/HomePage.tsx` | Ana sayfa: kurum karşılaması (maskeli ad), en yakın randevu, hızlı erişim, asistan girişi, çağrı merkezi kısayolu (`tel:`, demo notu), 112 notu |
| `src/components/pages/BookingPage.tsx` | Asistansız randevu: poliklinik (ikonlu ızgara; 4'ten çok poliklinikte arama kutusu, ad/kod/hekim adında, yalnız istemcide) → (birden çok hekim varsa unvanlı hekim kartları + "Fark etmez") → gün → saat → özet + açık onay → erişim kodu; çakışma/dolu/geçmiş saat uyarıları |
| `src/components/pages/AppointmentsPage.tsx` | Randevularım (yaklaşan) / Geçmiş muayeneler (hekim onaylı özet) sekmeleri |
| `src/components/pages/MedicationsPage.tsx` | İlaçlarım: ne işe yarar, nasıl kullanılır, hangi muayenede yazıldı |
| `src/components/pages/AssistantPage.tsx` | Sesli asistan sayfası (alt çubuksuz tam ekran); açılınca asistanı başlatır, çıkınca sesi/dinlemeyi durdurur |
| `src/components/pages/common.tsx` | Sayfa başlığı, yükleniyor/hata satırı, randevu ve muayene kartları |
| `src/components/LoginScreen.tsx` | PIN benzeri TC tuş takımı, kilit/süre/çıkış bildirimleri |
| `src/components/ChatScreen.tsx` | Asistan sayfasının içeriği: başlık (Ana sayfaya dön, bağlantı durumu, Çıkış), hızlı eylemler, sohbet akışı |
| `src/components/Cards.tsx` | Sohbet kartları: saat önerileri, onay özeti, randevu + erişim kodu, geçmiş muayene, poliklinik önerisi, acil durum (112), uyarı |
| `src/components/Composer.tsx` | Altta sabit konuşma alanı: büyük mikrofon, durum, hızlı seçenekler, yazı modu |
| `src/services/*` | `api` (backend), `orchestrator` (+ `mockOrchestrator`), `stt`, `tts`, `voiceCapture` (VAD) |
| `src/config.ts` | Ortam değişkenleri |
| `src/brand.ts` | **Kurum markası, tek yer:** tam ad, kısa ad, uygulama ve asistan adı, slogan, çağrı merkezi, renk paleti, logo yolu; manifest ve CSS değişkenleri buradan üretilir |
| `src/clinics.ts` | Poliklinik adından ikon (bilinmeyen → steteskop), Türkçe harf duyarsız arama anahtarı, hekim unvanı/baş harfleri |
| `public/brand/logo.svg`, `logo-tam.svg` | Kurum işareti (favicon + uygulama içi logo) ve köşesiz tam zeminli hali (maskable/Apple ikonu) |
| `scripts/ikonlari-uret.mjs` | `npm run ikonlar`: işaretten PNG ikonları üretir |
| `src/App.css`, `src/pages.css` | Ortak görünüm; `pages.css` uygulama iskeleti ve sayfalar |
| `vite.config.ts` → `kurumMarkasi` | `brand.ts`'ten index.html başlığı/açıklama/tema rengi/favicon, `:root` renk değişkenleri ve `/manifest.webmanifest` (dev'de ara katman, derlemede dosya) |
| `public/icon*.png`, `public/apple-touch-icon.png` | Ana ekran ikonları (`npm run ikonlar` çıktısı) |

## Kurum markası ve yeniden markalama

Kurum adı, renkler ve logo bileşenlere yazılı değildir; hepsi `src/brand.ts` ve `public/brand/` altındadır.
Başka bir klinik için:

1. `src/brand.ts`: `fullName`, `shortName`, `appName`, `assistantName`, `slogan`, `description`,
   `callCenter` (`display`, `tel`, `note` — gerçek numarada `note`'u boş bırakın) ve `colors`.
   Renklerde karşıtlığı koruyun: beyaz yazı `brand`/`brand-strong` üzerinde, `ink*` metinleri `paper` ve
   `surface` üzerinde en az 4.5:1.
2. `public/brand/logo.svg` (yuvarlak köşeli işaret) ve `logo-tam.svg` (köşesiz, işaret ortadaki %60–70'lik
   güvenli alanda) dosyalarını değiştirin; `npm run ikonlar`.
3. `npm run build`. Başlık, tema rengi, favicon, manifest ve CSS değişkenleri otomatik güncellenir.

Durum renkleri (uyarı, hata, başarı, dinleme) kurumdan bağımsızdır ve `src/index.css`'te kalır.

Tasarım: derin hastane mavisi (`#1a4f8c`) güven ve kurumsallık için ana renk; turkuaz (`#0b6c63`) ikincil
vurgu (işaretteki artı, çağrı merkezi, hekim unvanı). Tipografi dış font indirmez (çevrimdışı, KVKK):
Windows'ta Segoe UI Variable (başlıklarda Display), diğer platformlarda sistem yazı tipi. İşaret: nabız
tepesi yükselen bir "M" ve artı; MedTech için çizilmiş kurgusal bir işarettir.

## Güvenlik davranışları

- TC yalnız giriş ekranındaki tuş takımından girilir ve doğrudan backend'e gider; orkestratör TC'yi görmez.
  TC tarayıcıda saklanmaz (localStorage/sessionStorage/çerez yok); sayfalar yalnız maskeli adı gösterir.
- Giriş sonrası sayfalar backend'in hasta uçlarını ses oturumu kimliğiyle çağırır
  (`/api/v1/voice-sessions/{id}/profile|clinics|slot-days|slots|appointments|visits`); oturum doğrulanmamış
  ya da süresi dolmuşsa (`VOICE_SESSION_NOT_VERIFIED`/`NOT_FOUND`) uygulama nedenini söyleyerek girişe döner.
- Orkestratöre `START` + `TC_VERIFIED` girişte değil, **Asistan sayfası ilk açıldığında** gider (orkestratörün
  hareketsizlik sayacı asistan kullanılmadan işlemesin diye). Asistan sayfasından çıkınca okuma ve dinleme durur.
- Asistansız randevu da açık onayla oluşur ("Randevuyu onayla"). Sonuç bilinmiyorsa (ağ/5xx) "doğrulanamadı"
  gösterilir ve Randevularım'a bakılması istenir; `APPOINTMENT_CONFLICT` kırmızı uyarı + "Başka saat/gün",
  `SLOT_TAKEN`/`SLOT_IN_PAST` saat listesini yeniler.
  Giriş ekranında mikrofon ve metin kutusu yoktur; 10+ rakam içeren yazılı/sesli söz gönderilmez.
- TC biçimi (11 hane, kontrol haneleri) istemcide denetlenir; bozuk numara ağa hiç gitmez. Sunucu yine de
  `400 VALIDATION_FAILED` dönerse aynı uyarı gösterilir, deneme hakkı düşmez. "Kalan deneme hakkı" yalnız
  başarısız eşleşmeleri sayar; 3 başarısızlıkta giriş kapanır.
- Doğrulama `409 INVALID_STATUS_TRANSITION` dönerse oturum durumu `GET /voice-sessions/{id}` ile okunur:
  `VERIFIED` ise sohbete geçilir, değilse (ör. `EXPIRED`) yeni oturum açılıp TC yeniden istenir.
- Randevu yalnız açık onayla oluşur: "Seçimimi onayla" düğmesi `CONFIRM` olayı gönderir. Onay isteği hiçbir
  zaman sahte yanıtla karşılanmaz; sonuç bilinmiyorsa "doğrulanamadı" uyarısı gösterilir.
- Sahte orkestratöre yalnız servis erişilemezken ve yan etkisiz olaylarda düşülür; ekranda "ÖRNEK VERİ".
- **Çıkış** sohbeti, kartları (erişim kodu, ilaç bilgisi), çalan sesi ve dinlemeyi siler; orkestratöre `END`,
  backend'e `POST /voice-sessions/{id}/end` gider (kilit, süre dolumu ve sayfa kapanışında da; `keepalive`,
  hata yutulur). Böylece çıkıştan sonra oturum kimliğiyle hasta uçları çağrılamaz. Gecikmeli yanıtlar nesil
  sayacıyla yok sayılır.
- Asistan bir kez açıldıysa, uygulama açıkken dakikada bir orkestratöre `KEEPALIVE` gider; yalnız son 2 dakikada
  dokunma/tuş/konuşma olduysa ve istek sürmüyorsa. Asistan dışındaki sayfalarda gezinmek orkestratörde 5 dk
  boşta kalma (`SESSION_IDLE`) sayılmaz; boştaki cihaz ise orkestratörü canlı tutmaz.
- Hareketsizlik kilidi (varsayılan 5 dk, `VITE_IDLE_TIMEOUT_SEC`): son 30 sn uyarı, sonra her şey silinir ve
  "Oturum kilitlendi" ekranında TC ile yeniden giriş istenir. Backend oturumu 15 dk sonra biter; asistan
  `SESSION_EXPIRED` / `SESSION_IDLE` / `VOICE_SESSION_ENDED` (restart) döndürünce uygulama nedenini söyleyerek
  girişe döner.
- Asistan konuşurken mikrofon kapalıdır; kullanıcı bir seçeneğe dokunursa okuma kesilir.
- Poliklinik önerisi ("Evet, bakalım" / "Başka bölüm") düz metin `evet`/`hayır` gönderir, `CONFIRM` değil.
  Acil belirti algılanırsa `tel:112` düğmeli uyarı kartı çıkar; akış sürer.
- Asistanın hızlı seçenekleri (`data.choices`) kesilmez (üst sınır 24): 4'ten fazlaysa sarılır, en çok ~3,5 satır
  görünür, gerisi dikey kaydırılır (`.chips-many`; 11 poliklinikte Nöroloji, Üroloji, Genel Cerrahi de düğmedir).
  `data.expect: "hekim"` tanınır; hekim adları + "Fark etmez" düğme olarak gelir, dokunulan ad aynen gönderilir.
- Asistanın bilinmeyen hata kodları (ör. `APPOINTMENT_CONFLICT`, `SLOT_IN_PAST`) akışı durdurmaz: yanıt metni
  gösterilir, `action`/`data.expect`'e göre devam edilir.

## Ortam değişkenleri

`.env.example` dosyasına bakın. Önemlileri: `VITE_API_URL`, `VITE_ORCHESTRATOR_URL`, `VITE_VOICE_URL`,
`VITE_SAME_ORIGIN` (1 = göreli adres + Vite vekili), `VITE_IDLE_TIMEOUT_SEC` (300), `VITE_VOICE_LOOP`,
`VITE_VAD_*`. Telefon modu için `.env.mobil` hazırdır (`VITE_SAME_ORIGIN=1`).

## Telefonda demo

Telefon tarayıcısı mikrofona yalnız HTTPS sayfada izin verir. `npm run dev:mobil` bunu sağlar:

- Vite ağa açılır (`--host`), öz imzalı yerel sertifikayla HTTPS sunar (`@vitejs/plugin-basic-ssl`), port 5174.
- Yalnız `/api/v1/voice-sessions` (oluştur, durum, `/verify`) ve oturuma bağlı hasta uçları
  (`/voice-sessions/{id}/profile|clinics|slot-days|slots|appointments|visits|end`) → `127.0.0.1:3000`;
  `/orchestrate` `/stt` `/tts` → `127.0.0.1:8002` Vite vekiliyle iletilir. Servis anahtarlı
  `/medical-history`, hekim girişi, hekim randevu listesi, özetler gibi diğer backend uçları bilerek vekillenmez (telefon modu ağa açık dinler; parolasız hekim girişi LAN'a açılmasın).
  Telefon yalnız `https://<bilgisayar-ip>:5174` ile konuşur; CORS ve karışık içerik sorunu olmaz,
  arka uçlar ağa açılmadan `127.0.0.1`'de kalır. (Hedefler `HK_API_TARGET`, `HK_VOICE_TARGET`, port
  `HK_MOBIL_PORT` ile değiştirilebilir.)

Adımlar:

1. Arka uçları başlatın (`..\tools\hepsini-baslat.ps1`).
2. Bu klasörde `npm run dev:mobil`. Çıktıdaki **Network** satırı adresi verir
   (ör. `https://192.168.1.20:5174`). Bilgisayarın IP'si: `ipconfig` → Wi-Fi IPv4.
3. Telefon ve bilgisayar **aynı Wi-Fi** ağında olmalı (misafir ağları cihazları birbirinden yalıtabilir).
4. Telefonda adresi açın. Sertifika öz imzalı olduğu için **güvenlik uyarısı** çıkar:
   Chrome'da "Gelişmiş → … adresine ilerle", Safari'de "Ayrıntıları göster → bu web sitesini ziyaret et".
5. Açılmazsa **Windows Güvenlik Duvarı** gelen bağlantıyı engelliyor olabilir: Node.js ilk açıldığında
   çıkan izin penceresinde "Özel ağlar"a izin verin ya da yönetici olarak 5174/TCP için gelen kuralı ekleyin.
   Ağ profili "Ortak" ise özel ağ kuralı işlemez.
6. Giriş yapın (TC `10000000146`), mikrofon iznini verin. İsterseniz tarayıcı menüsünden
   "Ana ekrana ekle" ile uygulama gibi tam ekran açabilirsiniz.

iOS Safari: sesli yanıtın çalabilmesi için ilk dokunuşta (Giriş, mikrofon) ses kilidi açılır; telefon
sessiz moddaysa ses duyulmaz. Kayıt iOS'ta webm yerine mp4/aac olarak gönderilir.
