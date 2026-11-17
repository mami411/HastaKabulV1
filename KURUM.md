# Kurum bilgisi — MedTech Özel Hastanesi

MedTech kurgusal bir özel hastanedir; ad, işaret, renkler ve çağrı merkezi numarası demo içindir.
Ortak değerler: tam ad **MedTech Özel Hastanesi**, kısa ad **MedTech**, uygulama **MedTech Cep**,
asistan **MedTech Asistan**, çağrı merkezi **0850 000 00 00** (demo numarası, gerçek değil).

## Ön yüz

Marka tek yerde: `hasta-kabul-frontend/src/brand.ts` (`BRAND`) + işaret dosyası
`hasta-kabul-frontend/public/brand/logo.svg`.

| Alan | Değer | Nerede görünür |
| --- | --- | --- |
| `fullName` | MedTech Özel Hastanesi | Giriş ekranı, ana sayfa karşılaması, tarayıcı başlığı, manifest `name` |
| `shortName` | MedTech | Başlık çubuğu, giriş gizlilik notu, çağrı merkezi kartı, hata metinleri |
| `appName` | MedTech Cep | Giriş başlığı, tarayıcı başlığı, manifest `short_name`, ana ekran simgesi adı |
| `assistantName` | MedTech Asistan | Asistan sayfası başlığı, ana sayfa asistan düğmesi, Randevularım notu |
| `slogan` | Sağlığınız elinizin altında | Giriş ekranı |
| `callCenter` | 0850 000 00 00 · `tel:+908500000000` · "Demo numarası, gerçek bir hat değildir." | Ana sayfa çağrı merkezi kartı; 3 başarısız girişte ve "randevu sonucu doğrulanamadı" metninde |
| `colors` | Ana `#1a4f8c` (derin hastane mavisi), koyu `#123a69`, ikincil `#0b6c63` (şifa turkuazı), zemin `#f3f6fa`, metin `#0f1f33`, odak `#c2410c` | CSS değişkenleri (`--brand`, `--accent`, `--ink`…), tema rengi, manifest renkleri |

- İşaret: lacivert-mavi yuvarlak kare içinde, orta tepesi nabız çizgisi gibi yükselen beyaz bir "M" ve sağ üstte
  turkuaz bir artı. Özgün çizimdir; gerçek bir kurumun logosu değildir.
- Tarayıcı başlığı, açıklama, tema rengi, favicon, renk değişkenleri ve PWA manifest'i derlemede
  `vite.config.ts` içindeki `kurumMarkasi` eklentisiyle `brand.ts`'ten üretilir (elle düzenlenen manifest yoktur).
- PNG ikonlar (`public/icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png`)
  `npm run ikonlar` ile `public/brand/logo.svg` ve `logo-tam.svg`'den üretilir.
- Poliklinik listesi API'den gelir; ön yüzde sabit poliklinik listesi yoktur. İkon, poliklinik adından tahmin
  edilir (`src/clinics.ts`), bilinmeyen poliklinikte steteskop. Hekim adındaki unvan (`Uzm. Dr.`, `Op. Dr.`,
  `Doç. Dr.`, `Prof. Dr.`…) hekim seçiminde ayrı satırda gösterilir; ad backend'in verdiği `displayName`dir.

**Başka bir kurum için:** `brand.ts` değerlerini ve `public/brand/logo.svg` + `logo-tam.svg`'yi değiştirin,
`npm run ikonlar`, `npm run build`. Bileşenlerde kurum adı ya da kurum rengi yazılı değildir.

## Backend

Tek ayar noktası: `hasta-kabul-backend/src/HastaKabul.Api/appsettings.json` → `"Kurum"` bölümü
(`KurumOptions`, `HastaKabul.Application/Common/KurumOptions.cs`). Alan yoksa kod MedTech varsayılanını kullanır;
açılışta logda `Kurum: ...` satırı görünür. Ortam değişkeniyle de verilebilir: `Kurum__TamAd=...`,
`Kurum__CalismaSaatleri__Cumartesi__0=09:00-13:00`.

| Alan | Varsayılan | Etkisi |
| --- | --- | --- |
| `TamAd` / `KisaAd` / `UygulamaAdi` / `AsistanAdi` | MedTech Özel Hastanesi / MedTech / MedTech Cep / MedTech Asistan | Açılış logu (API yanıtlarına eklenmedi) |
| `CagriMerkezi` | 0850 000 00 00 | Açılış logu |
| `CalismaSaatleri.HaftaIci` | `["09:00-12:00", "13:00-17:00"]` | Demo slot üretimi (15 dk) |
| `CalismaSaatleri.Cumartesi` / `Pazar` | `["09:00-13:00"]` | Demo slot üretimi; boş gün kalmaz |

Geçersiz ya da boş blok listesi o gün için varsayılana düşer (hiçbir gün boş kalmaz). Saat değişikliği
`-ResetDemo` ile yeniden üretilen demo slotlarına yansır.

Poliklinik ve hekim kataloğu kodda: `HastaKabul.Infrastructure/Persistence/Seed/DemoKatalog.cs` (11 poliklinik,
14 kurgusal hekim; unvan `FullName`in başında, görünen ad "Doç. Dr. Elif K." biçiminde). `/slots` yanıtına geriye
uyumlu `doctorId` alanı eklendi.

## Asistan (hasta-kabul-ai)

Tek ayar noktası: `hasta-kabul-ai/kurum.json` (başka dosya için süreç ortamında `KURUM_DOSYASI=<yol>`).
Okuyan: `ortak/kurum.py` (`KURUM`). Bozuk dosya ya da boş alan varsayılana (MedTech) düşer.

| Alan | Kullanıldığı yer |
| --- | --- |
| `tam_ad`, `asistan_adi` | Karşılama, kimlik sonrası karşılama, veda, yardım şablonları |
| `cagri_merkezi` | Sistem hatası, devir, sonuç belirsiz, iptal, ücret/sigorta/adres yanıtları |
| `adres`, `calisma_saatleri` | "Adresiniz nedir", "Cumartesi açık mısınız" yanıtları (sabit metin, LLM'siz) |
| `okunuslar` | Yalnız sesli okunuşa uygulanır: `{"MedTech": "Med tek"}` (Piper "MedTech"i "med-teç-h" okuyor) |

- Kurum adı şablonlarda EKSİZ kullanılır ("MedTech'e" yok): başka kurumda ünlü uyumu bozulmasın.
- Ücret ve sigorta/SGK sorularına bilgi uydurulmaz; çağrı merkezine yönlendirilir. Ödeme/provizyon akışı yoktur.
- Telefon numarası sesli okunurken gruplanır ("sıfır sekiz yüz elli, sıfır sıfır sıfır, …"); unvanlar açılır
  ("Doç. Dr." → "Doçent Doktor").
- Poliklinik adları/eş anlamlıları ve şikâyet kalıpları `ses_servisi/alanlar/hasta_kabul.py`'de
  (`POLIKLINIKLER`, `SIKAYET_KALIPLARI`); kodlar backend kataloğuyla aynı olmalıdır.
- Şablonlar modül yüklenirken kurulur: `kurum.json` değişince ses servisini yeniden başlatın
  (`hepsini-baslat.ps1` eski süreci kendisi yeniler).

**Başka bir kurum için (backend + asistan):** `appsettings.json` `Kurum` bölümü, `kurum.json`, gerekirse
`DemoKatalog.cs` + `POLIKLINIKLER`; sonra `hepsini-baslat.ps1 -ResetDemo`.

## Yeni bir klinik için sıra (kontrol listesi)

Kurum bilgisi üç yerde ayrı tutulur (ön yüz derlemede, backend ve asistan çalışırken okur); üçü birlikte değişmelidir.

1. `hasta-kabul-frontend/src/brand.ts`: `fullName`, `shortName`, `appName`, `assistantName`, `slogan`,
   `description`, `callCenter` (`display` ile `tel` aynı numara: `0850 000 00 00` → `+908500000000`), `colors`.
2. `hasta-kabul-frontend/public/brand/logo.svg` ve `logo-tam.svg` → `npm run ikonlar`.
3. `hasta-kabul-backend/src/HastaKabul.Api/appsettings.json` → `Kurum` (aynı adlar ve numara, `CalismaSaatleri`).
4. `hasta-kabul-ai/kurum.json` → aynı adlar ve numara; `adres`; `calisma_saatleri` cümlesi 3. adımdaki blokların
   saatlerini yazmalı ("09:00", "12:00", "13:00", "17:00"…); `okunuslar` (kurum adı TTS'te yanlış okunuyorsa).
5. Poliklinik/hekim değişiyorsa: `DemoKatalog.cs` ve `ses_servisi/alanlar/hasta_kabul.py` `POLIKLINIKLER`
   (kod ve ad iki yerde birebir aynı), gerekirse `SIKAYET_KALIPLARI`; ön yüzde ikon için `src/clinics.ts`
   `ICON_RULES` (bilinmeyen ad steteskop alır, liste API'den geldiği için başka değişiklik gerekmez).
6. Denetim: `py -3 tools/kurum-denetle.py` (adlar, numara, `tel`, saatler ve poliklinik
   kodları/adları üç kaynakta tutarlı mı; çıkış kodu 1 = fark var). Aynı denetim AI testlerinde de çalışır
   (`tests/test_kurum_tutarlilik.py`).
7. `npm run build`, `hepsini-baslat.ps1 -ResetDemo` (asistan şablonları yükleme anında kurulduğu için ses servisi
   yeniden başlamalı; betik bunu kendisi yapar). Tarayıcıda giriş ekranı, ana sayfa çağrı merkezi kartı, Randevu al
   ızgarası ve asistan karşılaması yeni adla görünmeli.

Kodda kurum adı geçen başka yer yoktur; şablonlar EKSİZ yazıldığından (ör. "{tam_ad} çağrı merkezi numarası")
yeni adın ünlü uyumu için metin düzeltmesi gerekmez. Ücret, sigorta anlaşması gibi bilgiler bilerek yapılandırmada
yoktur (asistan çağrı merkezine yönlendirir).
