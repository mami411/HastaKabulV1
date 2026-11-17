# Hasta Kabul Sistemi — Arka Uç API

KVKK uyumlu otonom hasta kabul ve sağlık okuryazarlığı sisteminin arka uç servisi.
Onion (soğan) mimarisiyle .NET 9 üzerinde yazıldı.

## Mimari

Bağımlılıklar tek yöne akar: dıştaki halka içini bilir, içteki dışını bilmez.

```
HastaKabul.Domain          → çekirdek: entity'ler, enum'lar, durum makineleri, hata kodları
      ↑
HastaKabul.Application     → port'lar (arayüzler), DTO sözleşmeleri, use-case servisleri
      ↑
HastaKabul.Infrastructure  → EF Core + PostgreSQL/pgvector, TC hash/şifreleme, JWT, AI istemcisi
      ↑
HastaKabul.Api             → controller'lar, SignalR hub, middleware, DI kökü
```

Domain hiçbir pakete bağlı değildir. Application yalnızca arayüz tanımlar
(`ITcHasher`, `ISimplifyClient`, `IRealtimeNotifier`, repository'ler); bunların
gerçeklenmesi Infrastructure'da durur ve API katmanında birbirine bağlanır.

### Katmanlardaki ana parçalar

| Katman | Dosya | Ne yapar |
| --- | --- | --- |
| Domain | `Appointments/Appointment.cs` | Randevu durum makinesi; geçersiz geçiş 409 |
| Domain | `Voice/VoiceSession.cs` | Deneme sayacı, 3. denemede HANDOFF |
| Domain | `Summaries/SimplifiedSummary.cs` | PENDING → READY → APPROVED/REJECTED |
| Domain | `Patients/Patient.cs` | `patientRef`, `tc_hash`, `tc_encrypted`, "Ayşe H." |
| Application | `Services/*.cs` | Kiosk, panel, özet, slot, ses oturumu, portal akışları |
| Application | `Common/IstanbulTime.cs` | UTC ↔ Europe/İstanbul gün sınırları |
| Infrastructure | `Security/TcProtection.cs` | HMAC-SHA256 + pepper, AES-GCM alan şifrelemesi |
| Infrastructure | `Persistence/Seed/DemoDataSeeder.cs`, `DemoKatalog.cs` | 12 hasta, 11 poliklinik / 14 hekim (MedTech kataloğu), bugüne göre randevular, 16 KB kaydı |
| Api | `Middleware/ErrorHandlingMiddleware.cs` | `{ error: { code, message, requestId } }` |
| Api | `Realtime/DoctorHub.cs` | `doctor:{doctorId}` kanalı, token ile doğrulanır |

## Çalıştırma

### Docker Compose (önerilen)

Depoda hazır bir `.env` yoksa örnekten kopyalayın:

```bash
cp .env.example .env
```

`JWT_SECRET`, `TC_PEPPER`, `TC_ENCRYPTION_KEY` ve `SERVICE_KEY` doldurulmalıdır.
Şifreleme anahtarı **32 baytlık base64** olmak zorunda:

```bash
openssl rand -base64 32
```

Sonra:

```bash
docker compose up --build
```

API `http://localhost:3000` üzerinde açılır. Migration'lar ve demo verisi
açılışta otomatik uygulanır.

> Veritabanı konteyneri dışarıya **5433** portundan açılır (`DB_HOST_PORT`),
> çünkü makinede kurulu bir PostgreSQL 5432'yi tutuyor olabilir. Konteynerler
> birbirine her zaman `db:5432` üzerinden konuşur.

### Yerel geliştirme (API host'ta, veritabanı konteynerde)

```bash
docker compose up -d db
```

`appsettings.Development.json` içindeki bağlantı dizesini 5433'e çevirin ya da
ortam değişkeni verin:

```bash
ConnectionStrings__Default="Host=localhost;Port=5433;Database=hasta_kabul;Username=postgres;Password=..." dotnet run --project src/HastaKabul.Api
```

```bash
dotnet run --project src/HastaKabul.Api
```

Swagger arayüzü: `http://localhost:3000/swagger`

Demo verisini her provadan önce sıfırlamak için:

```bash
dotnet run --project src/HastaKabul.Api -- --reset-demo --seed-only
```

### Yerel çalıştırma: Docker'sız, taşınabilir PostgreSQL 16 (Windows)

Docker ya da sistem genelinde PostgreSQL kurulumu gerekmez; yönetici izni de gerekmez.
Betikler `..\tools\` (proje kökü `C:\Dev\HastaKabul\tools`) altındadır ve Windows PowerShell 5.1 ile çalışır.

1. Resmi Windows ikililerini (`postgresql-16.x-windows-x64-binaries.zip`, EnterpriseDB) indirip
   `tools\pgsql\` olacak şekilde açın (`pgAdmin 4`, `StackBuilder`, `doc` klasörleri gerekmez).
2. Veritabanı:
   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\pg-start.ps1   # ilk seferde initdb + hasta_kabul
   powershell -ExecutionPolicy Bypass -File tools\pg-stop.ps1
   ```
   Küme `tools\pgdata\`, port **5433** (compose'taki host portu), yalnız `localhost`, scram-sha-256.
3. Backend (port `hastakabul-local.env`'deki `API_PORT`, şu an **3000**; demo verisi açılışta yüklenir):
   ```powershell
   powershell -ExecutionPolicy Bypass -File tools\backend-start.ps1                  # API_PORT, SIMPLIFY_URL
   powershell -ExecutionPolicy Bypass -File tools\backend-start.ps1 -ResetDemo       # demo verisini sıfırla
   powershell -ExecutionPolicy Bypass -File tools\backend-start.ps1 -Port 3000 -SimplifyUrl http://127.0.0.1:8001
   powershell -ExecutionPolicy Bypass -File tools\backend-stop.ps1 [-Port 3100]
   ```
   Betik projeyi `tools\run\api-<port>\` altına yayınlar (çalışan süreç `dotnet build/test`'i kilitlemez),
   `DOTNET_ROLL_FORWARD=Major` ile (.NET 10 runtime) başlatır ve `/health` yanıt verene kadar bekler.
   Log: `tools\logs\backend-<port>.log`, PostgreSQL logu: `tools\logs\postgres.log`.

Yerel geliştirme değerleri (veritabanı parolası, `SERVICE_KEY`, `API_PORT`, `SIMPLIFY_URL`, `PG_*`)
`tools\hastakabul-local.env` dosyasındadır; dosya ilk çalıştırmada rastgele parola ve servis anahtarıyla
üretilir, paylaşılmaz. Backend bu değerleri yalnız süreç ortamı olarak alır
(`ConnectionStrings__Default`, `ServiceKey`, `Simplify__ServiceKey`, `Simplify__BaseUrl`,
`Database__UsePgvector=false`, `ASPNETCORE_URLS`); ortam değişkenleri `appsettings*.json` (Local dahil)
değerlerini ezer. pgvector bu kurulumda yoktur: `Database:UsePgvector=false` → embedding `real[]`,
şema `EnsureCreated` ile kurulur.

AI servisleri bu backend'e bağlanacaksa onların `SERVICE_KEY` değeri buradaki `SERVICE_KEY` ile
aynı olmalı ve `HASTAKABUL_API_URL` backend'in adresini göstermeli. `SIMPLIFY_URL`, geri çağrıyı
**bu** backend'e yapan bir ai-simplify örneğini göstermelidir (aksi halde özet PENDING kalır);
ulaşılamazsa API yedek özete ya da `LLM_UNAVAILABLE`'a düşer.

#### Hepsini tek komutla açma / kapatma

Makine yeniden başladıktan sonra (PostgreSQL → backend 3000 → ai-simplify 8001 → ai-voice 8002 → kiosk Vite 5173):

```powershell
powershell -ExecutionPolicy Bypass -File tools\hepsini-baslat.ps1             # çalışanı yeniden başlatmaz
powershell -ExecutionPolicy Bypass -File tools\hepsini-baslat.ps1 -ResetDemo  # demo verisini sıfırlar (+ 8001'i yeniden açar)
powershell -ExecutionPolicy Bypass -File tools\hepsini-durdur.ps1 [-ViteKalsin] [-VeritabaniKalsin]
```

Notlar (gerçek backend'e geçişte bulundu):
- Poliklinik, bilgi tabanı ve yedek özet eşleşmesi kültürden bağımsızdır (`Domain/Common/TurkishText.cs`):
  süreç tr-TR kültüründe çalışır; `KARDIYOLOJI`, `kardiyoloji`, `iç hastalıkları`/`ic hastaliklari`, `göz`/`goz`
  aynı polikliniğe çözülür (`TurkishCultureTests`, kültür tr-TR'ye ayarlı).
- `SIMPLIFY_URL` `http://127.0.0.1:8001` olmalı: ai-simplify yalnız IPv4 dinler; `localhost` önce `::1`'e gider ve
  .NET Windows'ta reddedilen bağlantıda ~2 sn bekler (her yeni bağlantıda klinik giriş +2 sn).
- Bilgi tabanı kimlikleri sabittir (terimden türetilir), `--reset-demo` sonrası değişmez; ai-simplify'in
  önbelleği geçerli kalır.

#### Geliştirme için sahte API'ye dönme

Sahte API (`hasta-kabul-ai\tools\mock-api\server.js`) silinmedi; gerçek backend yerine 3000'i o tutar:

```powershell
powershell -ExecutionPolicy Bypass -File tools\backend-stop.ps1 -Port 3000
# (isterseniz gerçek backend'i 3100'de açık tutun: tools\backend-start.ps1 -Port 3100 -SimplifyUrl http://127.0.0.1:8001)
cd hasta-kabul-ai
$env:SERVICE_KEY = (Get-Content .env | Where-Object { $_ -like 'SERVICE_KEY=*' }).Substring(12)
$env:SIMPLIFY_URL = 'http://127.0.0.1:8001'
Start-Process node -ArgumentList 'tools\mock-api\server.js' -WindowStyle Hidden   # :3000, /__mock/* uçları
```

AI servislerinin `.env`'i değişmez (`HASTAKABUL_API_URL=http://localhost:3000`, aynı `SERVICE_KEY`); 8001/8002 yeniden
başlatılmaz. Geri dönmek için sahte API sürecini (3000'deki `node`) kapatıp `tools\hepsini-baslat.ps1` çalıştırın.
`hasta-kabul-ai\tools\*_uctan_uca.py` araçlarındaki `/__mock/*` adımları gerçek backend'de yoktur
(sıfırlama `-ResetDemo`; slot kapma = ikinci doğrulanmış oturumla `POST /appointments`; klinik giriş = hekim
`POST /doctors/sign-in` + `POST /appointments/{id}/clinical-entries`; özet = `GET /summaries/{id}` JWT ile).

Duman testi bu kurulumda: `API_URL` ortam değişkeniyle (`$env:API_URL='http://localhost:3100'`);
`SERVICE_KEY` verilmezse `tools\hastakabul-local.env`'den okunur. Her koşudan önce `backend-start.ps1 -ResetDemo`.

Ağır denemeler için ikinci örnek, **ayrı veritabanıyla** (3000'deki demo verisine dokunmaz; veritabanı yoksa
`EnsureCreated` oluşturur):

```powershell
powershell -ExecutionPolicy Bypass -File tools\backend-start.ps1 -Port 3100 -Database hasta_kabul_test -SimplifyUrl http://127.0.0.1:1 -ResetDemo
powershell -ExecutionPolicy Bypass -File tools\backend-stop.ps1 -Port 3100
```

## Demo öncesi kontrol listesi

1. **Başlat** (makine yeni açıldıysa ya da emin değilseniz):
   `powershell -ExecutionPolicy Bypass -File C:\Dev\HastaKabul\tools\hepsini-baslat.ps1 -ResetDemo`
   — PostgreSQL → API 3000 → ai-simplify 8001 → ai-voice 8002 → uygulama (Vite) 5173. `-ResetDemo` demo verisini
   bugüne göre yeniden üretir (idempotent; ai-simplify'a dokunmaz, KB kimlikleri sabit).
2. **Sağlık**: `curl http://localhost:3000/health` → `{"status":"ok"}`; 8001/8002 için `/health`.
3. **Duman testi** (isteğe bağlı, veriyi değiştirir; sonra yeniden `-ResetDemo`):
   `$env:API_URL='http://localhost:3000'; powershell -ExecutionPolicy Bypass -File hasta-kabul-backend\deploy\smoke-test.ps1`
4. **Demo hesapları ve kodları**
   - Demo hasta: **Ayşe H.**, TC `10000000146` — bugün Dahiliye 09:00 (Uzm. Dr. Mehmet D., Oda 204) randevusu var;
     geçmişte 2 onaylı (Dahiliye ~118 gün, Kardiyoloji ~46 gün önce) + 1 onaysız (Ortopedi ~9 gün önce) muayene.
   - Portal erişim kodu (bugünkü Dahiliye randevusu): `246802` — özet hekim onaylanınca açılır.
   - Hekimler (parolasız giriş, `GET /api/v1/doctors`; kurgusal, unvan görünen adın başında): Uzm. Dr. Mehmet D.
     ve Uzm. Dr. Sevgi A. (Dahiliye), Doç. Dr. Elif K. (Kardiyoloji), Op. Dr. Ahmet Y. (Ortopedi), Uzm. Dr. Zeynep A.
     (Göz Hastalıkları), Op. Dr. Tolga A. (KBB), Uzm. Dr. Deniz K. (Dermatoloji), Prof. Dr. Nermin T. ve Op. Dr.
     Selin E. (Kadın Hastalıkları ve Doğum), Uzm. Dr. Burak Ş. ve Doç. Dr. Ceren U. (Çocuk Sağlığı), Doç. Dr. Kerem B.
     (Nöroloji), Op. Dr. Serkan T. (Üroloji), Prof. Dr. Levent S. (Genel Cerrahi). Listedeki ilk altı hekimin
     (Mehmet D. … Deniz K.) bugün 2 randevusu var (09:00, 09:15; 12 demo hastası).
   - Başka kayıtlı hastalar: sentetik TC'ler (`10765432176`, `11530864222`, …); kayıtsız ama biçimi geçerli
     örnek: `12345678950` (deneme hakkından düşer); `11111111111` biçimce geçersizdir (400, hak düşmez).
5. **Bilinen sınırlar**
   - Slotlar bugün + 7 gün, her hekim için hafta içi 09:00–12:00 ve 13:00–17:00, cumartesi/pazar 09:00–13:00
     (`appsettings.json` → `Kurum:CalismaSaatleri`); günün geçmiş saatleri sunulmaz. Hafta içi 16:45'ten,
     hafta sonu 12:45'ten sonra "bugün" için boş saat yoktur, "yarın" vardır.
   - Aynı hasta aynı saate (poliklinik fark etmez) ya da aynı gün aynı polikliniğe ikinci randevu alamaz
     (409 `APPOINTMENT_CONFLICT`); demo hastasının bugün Dahiliye randevusu olduğu için "bugün dahiliye"
     isteği reddedilir — "yarın dahiliye" ya da başka poliklinik kullanın. Provalar arasında `-ResetDemo`.
   - Sadeleştirme servisi kapalıysa ya da 90 sn içinde geri çağrı yapmazsa özet seed bilgi tabanındaki
     8 tanı (dispepsi, gastrit, hipertansiyon, tip 2 diyabet, demir eksikliği anemisi, migren, akut sinüzit,
     hipotiroidi) için yedek metinle READY olur; ilaçlar hekimin yazdıklarından gelir. Diğer tanılar FAILED
     (`LLM_UNAVAILABLE`) olur.
   - Backend gece yarısını açık geçirirse bugünün demo randevuları yenilenmez: demo günü `-ResetDemo` ile
     başlatın (açılışta bayat veri de kendiliğinden sıfırlanır, bkz. `Demo:ResetIfStale`).
   - Hekim girişi parolasızdır, rate limit yoktur (P2).

### Test

Birim testleri (durum makineleri, özet yaşam döngüsü, maskeleme, TC format denetimi):

```bash
dotnet test
```

Uçtan uca duman testi — API ayakta olmalı; senaryonun 4–8. adımlarını gerçek
servislere karşı koşar ve hata yollarını da dener (409 `SLOT_TAKEN`,
409 `INVALID_STATUS_TRANSITION`, 401 anahtarsız callback, 409 `SUMMARY_NOT_APPROVED`,
3 denemeden sonra `HANDOFF`):

```bash
pwsh -File deploy/smoke-test.ps1
```

## Port planı

| Servis | Port |
| --- | --- |
| API | 3000 |
| Kiosk | 5173 |
| Hekim paneli | 5174 |
| Hasta portalı | 5175 |
| Sadeleştirme (ai-simplify) | 8001 |
| Ses orkestratörü (ai-voice) | 8002 |
| PostgreSQL (konteyner içi) | 5432 |
| PostgreSQL (host'a açılan) | 5433 |

CORS yalnızca 5173–5175 kaynaklarına izin verir.

## Uç noktalar

Tümü `/api/v1` altında, REST, çoğul kaynak adlarıyla. JSON camelCase, veritabanı
snake_case; dönüşüm yalnızca API katmanında yapılır. Tarih/saat her yerde ISO 8601 UTC.

### Kiosk

| Yöntem | Yol | Açıklama |
| --- | --- | --- |
| POST | `/kiosk/verify` | TC doğrular, karşılama bilgisini döner |
| POST | `/kiosk/check-in` | "Geldim" onayı → `CHECKED_IN` + WebSocket olayı |

`/kiosk/verify`: biçimi geçersiz TC 400 `VALIDATION_FAILED`, kayıtsız TC 404 `TC_NOT_FOUND`.

### Hekim paneli (JWT)

| Yöntem | Yol | Açıklama |
| --- | --- | --- |
| GET | `/doctors` | Hekim listesi (dropdown için) |
| POST | `/doctors/sign-in` | Kısa ömürlü JWT üretir |
| GET | `/appointments?doctorId&date` | Günün randevu listesi |
| PATCH | `/appointments/{id}/status` | `IN_EXAM`, `COMPLETED`, `NO_SHOW` |
| POST | `/appointments/{id}/clinical-entries` | Tanı + reçete; sadeleştirmeyi tetikler |
| GET | `/summaries/{id}` | Özet önizleme (yalnız randevunun hekimi; başkası 403 `FORBIDDEN`) |
| POST | `/summaries/{id}/approve` | Hekim onayı |
| POST | `/summaries/{id}/reject` | "Yeniden oluştur" |

Sadeleştirme akışı:
- Klinik giriş `CHECKED_IN` randevuyu `IN_EXAM`'e alır (panele `appointment.statusChanged`), PENDING özet açar ve
  ai-simplify'a gönderir. Servis isteği kabul etmezse özet hemen yedek metinle READY ya da `LLM_UNAVAILABLE` ile
  FAILED olur. `medications` boş/eksik olabilir; tanı en çok 400 karakter, en çok 20 ilaç satırı.
- Geri çağrı `Simplify:PendingTimeoutSeconds` (varsayılan **90**) içinde gelmezse özet okuma anında
  (`GET /summaries/{id}`, `GET /appointments`) aynı kuralla yedek READY ya da FAILED olur. Geç gelen gerçek sonuç
  FAILED/READY kaydı yine doldurabilir; READY kayda gelen hata bildirimi içeriği silmez.
- `reject`: yanıt `REJECTED` döner (karar kaydedilir), ardından **aynı özet** aynı girdiyle yeniden üretime gider
  (PENDING → READY/FAILED; `summary.ready` olayı gelir). APPROVED/REJECTED özete geri çağrı 409 alır.

### Ses asistanı

| Yöntem | Yol | Kimlik | Açıklama |
| --- | --- | --- | --- |
| POST | `/voice-sessions` | — | Oturum açar |
| GET | `/voice-sessions/{id}` | — | `{ sessionId, verified, attempts }` |
| POST | `/voice-sessions/{id}/verify` | — | TC doğrulama (arayüz çağırır, orkestratör görmez) |
| POST | `/voice-sessions/{id}/end` | — | Oturumu kapatır (uygulamada Çıkış, kilit, süre dolumu, sayfa kapanışı). Gövdesiz; 204. ACTIVE/VERIFIED/COMPLETED oturum `EXPIRED` olur (`voiceSession.ended` denetim kaydı); zaten kapalıysa da 204, bilinmeyen oturum 404. Sonrasında hasta uçları ve randevu 409 döner |
| GET | `/slots?clinic=&date=` | `X-Service-Key` | Uygun saatler: `[{ slotId, startTime, doctorDisplayName, doctorId }]` (`doctorId` geriye uyumlu ek alan; asistanın hekim seçimi için) |
| POST | `/appointments` | `X-Service-Key` | `{ voiceSessionId, slotId }` |
| GET | `/voice-sessions/{id}/medical-history?limit=3` | `X-Service-Key` | Oturumdaki hastanın hekim onaylı geçmiş özetleri |

TC doğrulama (`/verify`): biçimi geçersiz numara (11 hane değil / kontrol hanesi tutmuyor) **400
`VALIDATION_FAILED`** döner ve deneme hakkından düşmez; biçimi geçerli ama kayıtsız numara deneme sayılır
(`attempts` artar, 3'te `HANDED_OFF`); başarılı doğrulama deneme sayılmaz (`attempts` yalnız başarısız denemeleri sayar).

Randevu (`POST /appointments`) kuralları, sırayla: başlangıcı geçmiş slot 409 `SLOT_IN_PAST`; dolu slot (eşzamanlı
istek dahil, veritabanı benzersiz indeksi) 409 `SLOT_TAKEN`; hastanın aynı zaman aralığında (poliklinik fark etmez)
ya da aynı gün aynı poliklinikte etkin (SCHEDULED/CHECKED_IN/IN_EXAM) randevusu varsa 409 `APPOINTMENT_CONFLICT`.
Çakışmada oturum VERIFIED kalır; orkestratör başka saat önerebilir. `GET /slots` geçmiş saatleri döndürmez.

Ses oturumunun ömrü 15 dakikadır (oluşturulduğu andan). Süre dolunca `ACTIVE` **ve** `VERIFIED`
oturum `EXPIRED` olur; süresi dolmuş oturumla randevu ve geçmiş sorgusu
`409 VOICE_SESSION_NOT_VERIFIED` ile reddedilir.

`GET /voice-sessions/{id}/medical-history` (orkestratör çağırır): oturum `VERIFIED` ya da
`COMPLETED` (randevudan sonra da) ve süresi dolmamış olmalı. `limit` 1–5 (varsayılan 3). Yalnız
**APPROVED** özetler, randevu zamanına göre yeniden eskiye, randevu başına en güncel onaylı özet:

```json
{ "visits": [ { "appointmentId": "…", "scheduledAt": "2026-09-30T06:30:00Z", "clinicName": "Kardiyoloji",
    "doctorDisplayName": "Elif K.", "diagnosisPlain": "…",
    "medications": [ { "name": "Amlodipin", "purpose": "…", "usage": "…" } ], "warnings": [ "…" ] } ] }
```

Kayıt yoksa `visits: []`. Yanıtta TC, tam ad, `patientRef`, onaysız içerik ve `unknownTerms` yoktur.
Hatalar: 401 `UNAUTHORIZED` (anahtar), 404 `VOICE_SESSION_NOT_FOUND`, 409 `VOICE_SESSION_NOT_VERIFIED`
(doğrulanmamış, HANDED_OFF ya da süresi dolmuş), 400 `VALIDATION_FAILED` (limit). Her başarılı okuma
`audit_logs`'a `voiceSession.medicalHistoryRead` / aktör `voice-orchestrator` olarak yazılır (içerik yazılmaz).

### Hasta uygulaması (asistansız sayfalar)

Ön yüzün Ana sayfa / Randevu al / Randevularım / Muayenelerim / İlaçlarım sayfaları tarayıcıdan çağırır.
Kimlik, TC tuş takımıyla doğrulanmış **ses oturumudur** (yoldaki `{id}`); servis anahtarı ve JWT yoktur.
Oturum `VERIFIED` ya da `COMPLETED` ve süresi dolmamış olmalı; hasta oturumdan bulunur, istekte hasta
kimliği taşınmaz (başka hastanın verisine yol yoktur). Yanıtlarda TC, tam ad ve `patientRef` yoktur.

| Yöntem | Yol | Yanıt |
| --- | --- | --- |
| GET | `/voice-sessions/{id}/profile` | `{ displayName: "Ayşe H.", sessionExpiresAt, sessionRemainingSeconds }` |
| GET | `/voice-sessions/{id}/clinics` | `[{ code, name, doctors: [{ doctorId, displayName }] }]` |
| GET | `/voice-sessions/{id}/slot-days?clinic=&doctorId=` | Bugünden 8 gün: `[{ date: "YYYY-AA-GG", availableCount }]` (İstanbul günü) |
| GET | `/voice-sessions/{id}/slots?clinic=&date=YYYY-AA-GG&doctorId=` | `[{ slotId, startTime, doctorId, doctorDisplayName }]` — geçmiş saat dönmez |
| GET | `/voice-sessions/{id}/appointments` | `{ upcoming: [{ appointmentId, scheduledAt, clinicName, doctorDisplayName, room, status, accessCode }] }` — bugün ve sonrası, etkin durumlar |
| POST | `/voice-sessions/{id}/appointments` | Gövde `{ slotId }`; yanıt 200, sesli yolla aynı: `{ appointmentId, slotId, scheduledAt, doctorDisplayName, clinicName, status, accessCode }` |
| GET | `/voice-sessions/{id}/visits?limit=10` | `medical-history` ile aynı biçim (`{ visits: [...] }`), yalnız **APPROVED** özetler; `limit` 1–20 (varsayılan 10) |

- Randevu, sesli yolla **aynı** paylaşılan kurallardan geçer (`AppointmentBookingService.BookAsync`): 409
  `SLOT_IN_PAST`, `SLOT_TAKEN` (eşzamanlı dahil), `APPOINTMENT_CONFLICT`. Bu yüzden asistanda alınan randevu
  uygulamada (ve tersi) hem görünür hem çakışma sayılır.
- Fark: uygulamadan randevu oturumu **VERIFIED bırakır** (hasta aynı oturumda başka iş yapabilir); sesli yol
  eskisi gibi oturumu `COMPLETED` yapar.
- Hatalar: 404 `VOICE_SESSION_NOT_FOUND`, 409 `VOICE_SESSION_NOT_VERIFIED` (doğrulanmamış, HANDED_OFF, süresi
  dolmuş), 400 `VALIDATION_FAILED` (eksik `clinic`, bozuk `date`/`doctorId`/`slotId`/`limit`), 404 `NOT_FOUND`
  (bilinmeyen poliklinik ya da slot).
- Denetim: randevu `appointment.created` aktör `patient-app`; randevu listesi `voiceSession.appointmentsRead`
  (ayrıntı `upcoming=N`), muayene okuma `voiceSession.medicalHistoryRead`, ikisi de aktör `patient-app`
  (içerik yazılmaz).
- Orkestratörün kullandığı uçlar (`/slots`, `POST /appointments`, `/medical-history`) değişmedi.

### AI servisi geri çağrısı

| Yöntem | Yol | Kimlik |
| --- | --- | --- |
| PUT | `/summaries/{summaryId}` | `X-Service-Key` |
| GET | `/kb` | `X-Service-Key` |

`GET /kb` bilgi tabanının tamamını `[{ id, term, title, body, kind: DIAGNOSIS|MEDICATION, synonyms }]`
olarak verir; ai-simplify RAG eşlemesini bu kayıtlarla yapar ve callback'teki `sourceIds`
buradaki `id`'lerdir (kaynak başlıkları böylece çözülür).

### Hasta portalı

| Yöntem | Yol | Açıklama |
| --- | --- | --- |
| POST | `/portal/summary` | 6 haneli erişim kodu; yalnızca onaylı özet |

Portal yanıtındaki `summary` hastaya yöneliktir: `summaryId, appointmentId, status, diagnosisPlain, medications,
warnings, sources, updatedAt`. `patientRef`, `unknownTerms` ve `errorCode` portalda **yoktur** (hekim ucunda vardır).

## WebSocket

SignalR hub: `/hubs/doctor?access_token={jwt}`

| Olay | İçerik |
| --- | --- |
| `appointment.statusChanged` | `{ appointmentId, displayName, status, updatedAt }` |
| `summary.ready` | `{ appointmentId, summaryId, status, updatedAt }` — `status` READY, FAILED ya da (yeniden üretim başlarken) PENDING olabilir |

`appointment.statusChanged` kiosk check-in'de, panel durum güncellemesinde ve klinik girişin `CHECKED_IN` → `IN_EXAM`
otomatik geçişinde yayılır. `summary.ready` geri çağrıda (READY/FAILED), yedek özete düşüşte, zaman aşımında ve
ret sonrası yeniden üretimde yayılır.

Olaylarda TC ve tam ad bulunmaz. Kopma sonrası istemci önce
`GET /appointments?doctorId&date` ile tam listeyi çeker, sonra olayları dinler;
eski `updatedAt` yok sayılır.

## Hata formatı

```json
{
  "error": {
    "code": "TC_NOT_FOUND",
    "message": "Bu kimlik numarasıyla kayıt bulunamadı.",
    "requestId": "9f1c..."
  }
}
```

Sabit kod listesi `Domain/Common/ErrorCodes.cs` içinde tek yerde durur:
`TC_NOT_FOUND`, `NO_APPOINTMENT_TODAY`, `SLOT_TAKEN`, `SLOT_IN_PAST` (409), `APPOINTMENT_CONFLICT` (409),
`SUMMARY_NOT_APPROVED`, `INVALID_STATUS_TRANSITION`, `VOICE_SESSION_*`, `LLM_UNAVAILABLE`, `SIMPLIFY_FAILED`,
`INTENT_UNCLEAR`, `STT_FAILED`, `TTS_FAILED`, `VALIDATION_FAILED`, `NOT_FOUND`, `UNAUTHORIZED`, `FORBIDDEN`,
`INTERNAL_ERROR` (500).

Zarf **her** hata yanıtında kullanılır: iş kuralı hataları, model doğrulama (eski ASP.NET ProblemDetails yerine
400 `VALIDATION_FAILED`, mesajda alan adları), JWT yok/geçersiz/süresi dolmuş (401 `UNAUTHORIZED`), bilinmeyen yol
(404 `NOT_FOUND`), yanlış yöntem (405) ve içerik türü (415) (`VALIDATION_FAILED`), beklenmeyen hata (500
`INTERNAL_ERROR`). Mesajlar Türkçe karakterlidir ve kaçışlanmadan yazılır. `X-Request-Id` en çok 64 karakter
`[A-Za-z0-9._:-]` olarak alınır (fazlası kırpılır).

## KVKK sınırı

- TC yalnızca **tuş takımı → API → DB** hattında görünür. Yanıtlarda dönmez.
- Veritabanında `tc_hash` (HMAC-SHA256 + pepper) ve `tc_encrypted` (AES-GCM) tutulur;
  düz TC kolonu hiç açılmaz.
- Eşleme yalnızca hash üzerinden yapılır.
- AI servislerine giden beyaz liste: `patientRef`, `diagnosis`, `medications[]`,
  `requestId`, `summaryId`. Listede olmayan alan gönderilmez.
- `patientRef` rastgele UUID'dir, TC'den türetilmez.
- Log satırı yalnızca `service`, `requestId`, `path`, `method`, `status`, `ms` içerir.
  **TC hiç loglanmaz, maskeli bile.**
- Arayüze giden ad `displayName` = "Ayşe H."; tam ad arayüze ve loglara düşmez.

## Durum geçişleri

```
SCHEDULED ──→ CHECKED_IN ──→ IN_EXAM ──→ COMPLETED
     └──→ NO_SHOW
```

Bunların dışındaki her geçiş `409 INVALID_STATUS_TRANSITION` döner.

## Demo verisi

- MedTech Özel Hastanesi kataloğu (`Seed/DemoKatalog.cs`, kurgusal): 11 poliklinik — Dahiliye, Kardiyoloji,
  Ortopedi, Göz Hastalıkları, Kulak Burun Boğaz (`KBB`), Dermatoloji, Kadın Hastalıkları ve Doğum (`KADIN_DOGUM`),
  Çocuk Sağlığı ve Hastalıkları (`COCUK_SAGLIGI`), Nöroloji, Üroloji, Genel Cerrahi — eş anlamlılarıyla
  ("cildiye", "kadın doğum", "çocuk doktoru"…); 14 hekim (Dahiliye, Kadın Doğum ve Çocuk'ta ikişer), 12 hasta.
  Unvan `FullName`in başındadır ("Doç. Dr. Elif Kaya" → görünen ad "Doç. Dr. Elif K."); şema değişmedi.
- Bugün + 7 gün (hafta sonu dahil), her hekim için 15 dakikalık slotlar: hafta içi 09:00–12:00 ve 13:00–17:00,
  cumartesi ve pazar 09:00–13:00. Saatler `appsettings.json` → `Kurum:CalismaSaatleri`'nden okunur (geçersiz ya da
  boş blok o gün için varsayılana düşer, hiçbir gün boş kalmaz); kurum ayarları için kökteki `KURUM.md`.
  Açılışta ve backend açıkken saatte bir eksik slotlar tamamlanır (idempotent, veri silmez).
- Açılışta demo verisi bugüne ait değilse (dün yüklenmiş veritabanı) otomatik sıfırlanır
  (`Demo:ResetIfStale`, varsayılan `true`).
- Senaryodaki sabit hasta: **Ayşe H.**, TC `10000000146`, Dahiliye, Oda 204,
  portal erişim kodu `246802`
- 16 kayıtlık bilgi tabanı (Dispepsi + Lansoprazol dahil)
- Demo hastasının geçmiş muayeneleri (tamamlanmış randevu + klinik giriş + özet): ~118 gün önce
  Dahiliye (tip 2 diyabet, demir eksikliği; **APPROVED**), ~46 gün önce Kardiyoloji (hipertansiyon;
  **APPROVED**), ~9 gün önce Ortopedi (**READY**, hekim onayı yok — geçmiş ucunda görünmez)
- `kb_entries.embedding` kolonu `vector(1536)` ve nullable; P1'de pgvector
  aramasına geçilirken şema değişmez

LLM'e erişilemezse API seed'deki onaylanmış yedek özete düşer
(`Infrastructure/Ai/SeedFallbackSummaryProvider.cs`): seed KB'deki 8 tanının hepsi için Türkçe metin; ilaç
listesi hekimin girdisinden üretilir (KB'deki ilaca amaç/kullanım ipucu eklenir, doz yalnız hekimin yazdığı
"2x1" gibi ifadeden çıkarılır, bilinmeyen ilaç `unknownTerms`'e düşer). `sourceIds` sabit KB kimlikleridir.

## Sırada ne var

- `shared/openapi.yaml`: Swagger çıktısının dondurulup ortak klasöre taşınması
- Ses orkestratörünün `/stt` ve `/tts` uçlarıyla uçtan uca denenmesi
- (P1) WebSocket yeniden bağlanmada durum senkronizasyonu
- (P2) Rol tabanlı giriş, kiosk oturum zaman aşımı
