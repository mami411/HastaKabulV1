# hasta-kabul-ai — HastaKabul yapay zekâ servisleri

Bu klasör tek bir Python projesidir:

| Paket | Ne yapar |
| --- | --- |
| `ortak/` | Servislerden bağımsız ortak katman: **LLM sağlayıcıları** (`ortak.llm`), ortam değişkeni okuma (`ortak.ayarlar`), Türkçe yardımcılar (`ortak.turkce`), güvenlik filtresi çekirdeği (`ortak.guvenlik`) |
| `ses_servisi/` | **ai-voice / orkestratör** (port 8002): `/orchestrate`, `/stt`, `/tts`, `/health` |
| `sade_servisi/` | **ai-simplify** (port 8001): `POST /simplify`, `/health` — tanı/ilaçları hastanın anlayacağı sade Türkçeye çevirir |
| `ses_servisi/alanlar/hasta_kabul.py` | Konu modülü: istem, şemalar, poliklinikler, yasak ifadeler, yedek ve sabit cümleler |
| `kurum.json`, `ortak/kurum.py` | Kurum ayarı (varsayılan kurgusal **MedTech Özel Hastanesi**): ad, asistan adı, çağrı merkezi, adres, çalışma saatleri, sesli okunuşlar. Ayrıntı: kökteki `KURUM.md` |
| `tests/` | pytest; ağ, model ve ücretli çağrı gerektirmez (sahte LLM + sahte API) |
| `tools/mock-api/server.js` | Gerçek backend sözleşmesini taklit eden sahte API (yalnız geliştirme) |
| `tools/llm_olcum.py`, `tools/uctan_uca.py` | Model seçimi ölçümü ve gerçek modellerle uçtan uca deneme |

İki servis aynı `ortak` paketini ve aynı Ollama modelini paylaşır (ayrıntı: aşağıda "ai-simplify").

## Mimari ilkeler

- **Varsayılan tam yerel, ücretsiz:** STT = yerel Whisper (faster-whisper, GPU varsa CUDA),
  TTS = Piper (Türkçe ses), LLM = Ollama. Hasta verisi makineden çıkmaz.
- **LLM yalnız serbest metni anlamak için:** poliklinik seçimi, gün/saat tercihi, onay/ret,
  konu dışı soru. Randevu akışı deterministik bir durum makinesidir; LLM kapalıyken de
  düğmelerle (START → TC_VERIFIED → OFFER_SLOTS → SLOT_SELECTED → onay) tamamlanır, serbest
  metin kural tabanlı çözümleyiciye düşer. Tek istisna: genel sağlık/ilaç bilgi sorusunda yerel LLM kısa genel
  bilgi yazar (kodda denetlenir, kapanış koddan; `SAGLIK_BILGI_LLM`, aşağıda "Genel sağlık bilgisi").
- **Sağlayıcı ayrımı:** konuşma döngüsü hiçbir SDK'yı görmez; `ortak.llm.LLMSaglayici`
  arayüzünün iki uygulaması var: OpenAI uyumlu (Ollama/Groq/Gemini aynı kod) ve Anthropic
  (opsiyonel; paket kurulu değilse içe aktarma patlamaz).
- **Güvenlik filtresi:** LLM metni kullanıcıya gitmeden önce denetlenir: randevu API'den
  başarılı dönmeden "oluşturuldu/alındı" yok, "onaylandı" hiç yok, tıbbi tavsiye yok, kimlik
  istemek/tekrarlamak yok. Karşılaştırma Türkçe kurallı küçük harfle (`I→ı`, `İ→i`) yapılır.
- **TC bu servise gelmez:** arayüz TC'yi doğrudan API'ye yollar; orkestratör doğrulamayı
  `GET /voice-sessions/{id}` ile okur (`TC_VERIFIED` olayına güvenmez). Metinde 11 haneli
  sayı geçerse LLM'e gitmeden maskelenir; STT çıktısında da maskelenir.
- **Saat dilimi:** API UTC konuşur; ekran ve ses Europe/Istanbul'dur. Yerel saat asla `Z`
  ile etiketlenmez. LLM istemine tarihle birlikte haftanın günü ve 7 günlük takvim verilir.
- **Slot kilidi yok:** müsaitlik sorgusu yan etkisizdir; rezervasyon yalnız açık onayda
  `POST /appointments` ile yapılır.
- **Hata yedeği:** LLM/STT/TTS/API hatası sessizliğe dönmez; Türkçe yedek yanıt + uygun HTTP
  kodu. Sistem hatasında kullanıcıya "dolu/boş saat yok" denmez.
- **KVKK/log:** log satırı yalnız yöntem, yol, durum, süre, istek kimliği taşır. Kullanıcı
  sözü, transkript, ad ve TC loglanmaz; teşhis logu varsayılan kapalı (`TESHIS_LOGU=0`).

## Kurulum (Windows)

```powershell
cd C:\Dev\HastaKabul\hasta-kabul-ai
py -V:Astral/CPython3.12.14 -m venv .venv          # ML paketleri için Python 3.12
.venv\Scripts\python.exe -m pip install -r requirements.txt
# GPU (CUDA 12) ile Whisper için ek kütüphaneler (yoksa CPU'da çalışır):
.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
copy .env.example .env      # SERVICE_KEY'i backend'deki değerle doldurun
```

GPU notu: cuDNN 9 `ctranslate2` tekerleğinin içinde gelir; GPU için yalnız cuBLAS 12 gerekir.
`requirements-gpu.txt` indirilemezse (PyPI bu makinede ~30 KB/sn idi) mevcut bir cuBLAS 12
klasörü gösterilebilir: `STT_CUDA_DLL_DIZINLERI=C:\Users\<kullanici>\AppData\Local\Programs\Ollama\lib\ollama\cuda_v12`
(Ollama'nın kendi kopyası; birden çok klasör `;` ile). İkisi de yoksa STT CPU int8'e düşer
(large-v3-turbo CPU'da 3 sn'lik kayıt için ~7,5 sn — kiosk için yavaş).

Modeller:

```powershell
# LLM (Ollama kurulu olmalı)
ollama pull qwen3:4b-instruct-2507-q4_K_M
# Piper Türkçe ses (modeller\piper\ altına .onnx + .onnx.json)
.venv\Scripts\python.exe -m piper.download_voices tr_TR-dfki-medium --download-dir modeller\piper
# Whisper modeli ilk açılışta modeller\whisper\ altına kendiliğinden iner (~1,6 GB).
```

## Çalıştırma

```powershell
.venv\Scripts\python.exe -m ses_servisi          # http://127.0.0.1:8002
```

`GET /health` bileşenlerin durumunu verir (STT/TTS hazır mı, hangi cihaz, LLM modeli).
Whisper ve Piper açılışta arka planda yüklenir; hazır olmadan gelen istek ilk seferde biraz bekler.

Varsayılan olarak 3000'de **gerçek backend** çalışır (`C:\Dev\HastaKabul\tools\hepsini-baslat.ps1`; `.env`'deki
`SERVICE_KEY` = `tools\hastakabul-local.env`'deki `SERVICE_KEY`). Sahte API'ye dönme adımları:
`hasta-kabul-backend\README.md` → "Geliştirme için sahte API'ye dönme".

Sahte API (gerçek backend çalıştırılamıyorsa; yalnız geliştirme):

```powershell
$env:SERVICE_KEY = "<.env'deki SERVICE_KEY>"; node tools\mock-api\server.js   # :3000, demo TC 10000000146
```

## Test

```powershell
.venv\Scripts\python.exe -m pytest
```

Testler ağ, model indirme ya da ücretli çağrı yapmaz: LLM için `ortak.llm.SahteSaglayici`,
API için `tests/sahte_api.py` (httpx MockTransport), STT/TTS için sahte nesneler kullanılır.

Gerçek modellerle uçtan uca deneme (Ollama + sahte API + servis ayaktayken):

```powershell
.venv\Scripts\python.exe tools\uctan_uca.py
.venv\Scripts\python.exe tools\llm_olcum.py qwen3:4b-instruct-2507-q4_K_M qwen2.5:3b
```

## Profil değiştirme

Yalnız `.env` değişir, kod değişmez.

| Profil | Ayar | Not |
| --- | --- | --- |
| **Tam yerel** (varsayılan) | `LLM_TABAN_URL=http://localhost:11434/v1`, `SES_LLM_MODEL=<ollama modeli>`, `STT_SAGLAYICI=yerel` | Veri dışarı çıkmaz; 6 GB GPU yeterli |
| Hibrit (bulut LLM) | `LLM_TABAN_URL=https://api.groq.com/openai/v1`, `SES_LLM_MODEL=…`, `LLM_API_ANAHTARI=…`, `LLM_JSON_MODU=nesne` | Kullanıcı cümlesi buluta gider (TC maskeli). KVKK değerlendirmesi gerekir |
| Hibrit (Gemini) | `LLM_TABAN_URL=https://generativelanguage.googleapis.com/v1beta/openai`, `SES_LLM_MODEL=…`, `LLM_API_ANAHTARI=…` | Aynı uyarı |
| Anthropic | `LLM_SAGLAYICI=anthropic`, `SES_LLM_MODEL=…`, `LLM_API_ANAHTARI=…` + `pip install anthropic` | Aynı uyarı |
| Bulut STT | `STT_SAGLAYICI=bulut`, `STT_BULUT_URL=…`, `STT_BULUT_ANAHTAR=…`, `STT_MODEL=whisper-large-v3-turbo` | Ses kaydı buluta gider |
| LLM'siz | `SES_LLM_ETKIN=0` | Yalnız kural tabanlı anlama; düğme akışı tam çalışır |

Ücretsiz katman limitleri ve model adları sağlayıcı sayfalarından güncel olarak doğrulanmalıdır.

## Ortam değişkenleri

Tam liste ve açıklamalar: [`.env.example`](.env.example). Özet:

| Değişken | Varsayılan | Açıklama |
| --- | --- | --- |
| `SES_HOST`, `SES_PORT` | 127.0.0.1, 8002 | Dinlenen adres |
| `CORS_IZINLI_KAYNAKLAR` | 5173/5174/5175 (localhost ve 127.0.0.1) | Virgülle ayrılmış |
| `HASTAKABUL_API_URL` | http://localhost:3000 | Backend |
| `SERVICE_KEY` | — | Backend ile aynı servis anahtarı (yalnız bu serviste) |
| `LLM_*` / `SES_LLM_*` | bkz. `ortak/llm/fabrika.py` | Sağlayıcı, taban adres, model, anahtar, zaman aşımı… |
| `ANLAMA_ZAMAN_ASIMI_SN` | 12 | Aşılırsa kural tabanlı yedek |
| `STT_*` | large-v3-turbo, auto | Whisper modeli, cihaz, sınırlar |
| `TTS_*` | modeller/piper/tr_TR-dfki-medium.onnx | Piper ses modeli, hız |
| `ARAMA_GUN_SAYISI`, `SUNULAN_SLOT_SAYISI` | 3, 6 | Slot arama |
| `TESHIS_LOGU` | 0 | 1: niyet/poliklinik/gün loglanır (metin asla) |
| `ALTERNATIF_GUN_SAYISI` | 7 | İstenen günde yer yoksa en yakın seçenek için taranan gün sayısı |
| `RANDEVU_UFUK_GUN` | 8 | Backend'in slot açtığı gün sayısı (bugün dahil); ötesi istenirse "yalnız önümüzdeki N gün" şablonu. Arama/alternatif gün sayıları da bununla sınırlanır |
| `GECMIS_SESLI_OKUMA` | 1 | 0: geçmiş muayene/ilaç bilgisi sesli okunmaz, yalnız ekrandaki kartta gösterilir |
| `GECMIS_IKINCI_FAKTOR` | 0 | 1: geçmiş sorgusu ek doğrulama ister; ek doğrulama ucu henüz olmadığından **kapalı başarısız** olur (bilgi verilmez) |
| `GECMIS_LIMIT` | 3 | `medical-history?limit=` (backend 1–5 kabul eder) |
| `ILAC_KONU_LLM` | 0 | 1: kuralın konusunu çıkaramadığı ilaç sorusunda LLM'e (maskeli cümle) konu etiketi sorulur. Varsayılan kapalı: yerel modelle 10 denemenin 4'ünde yanlış konu verdi ("araba"→alkol, "başım dönüyor"→kullanım); kapalıyken yanıt "elimde onaylı bilgi yok" |
| `SAGLIK_BILGI_LLM` | 1 | Genel sağlık/ilaç bilgi sorusu yerel LLM'le kısa genel bilgi olarak yanıtlanır (bkz. "Genel sağlık bilgisi"). 0: eski davranış ("tıbbi yorum yapamıyorum"). LLM yoksa yol zaten kapalıdır |
| `SAGLIK_BILGI_ZAMAN_ASIMI_SN` | 10 | Bilgi yanıtı için LLM süresi; aşılırsa sabit "güvenilir bilgi veremiyorum" metni |
| `SAGLIK_LLM_MODEL` | — | İsteğe bağlı ayrı model (boşsa anlama modeli). Diğer ayarlar `SAGLIK_LLM_*`, yoksa `LLM_*` |
| `OTURUM_BOSTA_SN` | 300 | Son istekten bu kadar saniye sonra gelen onay reddedilir ve geçmiş muayene kilitlenir (`SESSION_IDLE`, kimlik yeniden). Kişisel cihaz (mobil) için 300; ortak kullanılan cihazda kısaltın |

## HTTP sözleşmesi

`POST /orchestrate` — gövde `{sessionId, event?: START|TC_VERIFIED|SLOT_SELECTED|CONFIRM|END|KEEPALIVE, userText?, data?: {slotId}}`

`KEEPALIVE`: hasta uygulamasında kullanıcı asistan dışındaki sayfalarda etkinken boşta kalma sayacını yeniler; durum, seçim ve onay sorusu değişmez, API'ye gidilmez. Yanıt `200 {action: null, data: {alive, listen: false}}`. Süre zaten aşılmışsa oturumu kurtarmaz (`alive: false`, geçmiş kilitli kalır); bellekte olmayan oturumu kurmaz; END'den sonra 409 `VOICE_SESSION_ENDED`.

`END`: kiosk oturumu bitirdi (sıfırlama, hareketsizlik, sayfa kapanışı). Orkestratör oturum belleğini —
geçmiş muayene verisi dahil — hemen siler; yanıt `200 {action: null, data: {ended: true}}`. Kapatılan
oturuma sonradan (gecikmeli) gelen istek oturumu yeniden kurmaz: `409 VOICE_SESSION_ENDED` + `HANDOFF`,
`data.restart: true`. Boşta kalma süresi (`OTURUM_BOSTA_SN`) bir kez aşılmış oturumda geçmiş muayene sorusu
verilmez: `409 SESSION_IDLE` + `HANDOFF`, `restart: true` (kimlik yeniden doğrulanır). Sesli "evet" yalnız bir
önceki yanıt seçimi okuyup onay sorduysa randevu oluşturur; arada başka bir yanıt verildiyse seçim önce
yeniden okunur.

Yanıt: `{action, replyText, message, data, requestId}`; `message` = `replyText` (eski istemciler için).
`replyText` koddaki şablonlardan (`alanlar/hasta_kabul.py`) + API verisinden kurulur. Tek istisna genel sağlık
bilgi yoludur (`SAGLIK_BILGI_LLM`): orada yerel LLM'in yazdığı, kodda denetlenmiş 2–4 cümle + koddan sabit
kapanış okunur ve `data.aiGeneralInfo: true` döner.

Her yanıtta `data` şu konuşma ipuçlarını da taşır:

| Alan | Değer | Anlamı |
| --- | --- | --- |
| `expect` | `tc` \| `poliklinik` \| `hekim` \| `gun` \| `saat` \| `secim` \| `onay` | Asistanın şu an beklediği yanıt. `hekim` (yeni): çok hekimli poliklinikte hekim tercihi soruldu; `choices` hekimlerin unvanlı görünen adları + "Fark etmez", `slots` boş |
| `listen` | bool | Asistan konuştuktan sonra mikrofon kendiliğinden açılsın mı (TC adımında, DONE/HANDOFF'ta ve art arda 2 anlaşılmamadan sonra `false`) |
| `choices` | `string[]` | Ekranda dokunulabilir hızlı seçenekler (poliklinik adları — 11'in tamamı / hekim adları + "Fark etmez" / "Bugün", "Yarın", "En erken" / "Sabah", "Öğleden sonra"…). Liste kısaltılmaz; istemci hepsini göstermeli |
| `history` | `{visits: [{date, clinicName, doctorDisplayName, diagnosisPlain, medications: [{name, purpose, usage}], warnings}], focus: [ilaç adı], spoken, topic?}` | Yalnız geçmiş muayene/ilaç sorusunda; uygulamada kart olarak gösterilir. `topic` (ek, isteğe bağlı): ilaç konu sorusunda konu kodu — `yan_etki`, `alerji`, `yemek`, `alkol`, `unutma`, `hamilelik`, `etkilesim`, `sure`, `doz`, `amac`, `kullanim` ya da `tanimsiz`; `focus` boşsa asistan "hangi ilacınız?" diye sordu |
| `suggestion` | `{clinic: "KARDIYOLOJI", clinicName: "Kardiyoloji"}` | (Ek, isteğe bağlı) Şikâyetten poliklinik önerisi yapıldı (tavsiye yanıtındaki ısrarsız randevu teklifinde de); `expect` yine `poliklinik`, `choices`'ta önerilen ilk sırada. Sonraki "evet/olur/bakalım" yalnız polikliniği seçer — randevu onayı değildir |
| `institutionInfo` | `cagri_merkezi` \| `adres` \| `calisma_saatleri` \| `ucret` \| `sigorta` | (Ek, isteğe bağlı) `replyText` kurum sorusunun sabit yanıtı (`kurum.json`'dan; ücret/sigortada bilgi verilmez, çağrı merkezine yönlendirilir). Durum ve seçim değişmez |
| `emergency` | `true` | (Ek, isteğe bağlı) Acil durum ifadesi algılandı; `replyText` sabit 112 / acil servis şablonu. Durum ve seçim değişmez |
| `aiGeneralInfo` | `true` | (Ek, isteğe bağlı) `replyText` yerel yapay zekânın yazdığı genel sağlık bilgisidir (kapanış koddan); arayüz "Genel bilgi · doktorunuza danışın" etiketi gösterir. Sabit metinlerde (dağarcık, yönlendirme, "veremiyorum") yoktur. Durum ve seçim değişmez |

| action | data |
| --- | --- |
| `ASK_TC` | `{}` |
| `OFFER_SLOTS` | `{slots: [{slotId, label: "Pzt 16 Kas 09:00", detail: "Kardiyoloji · Elif K."}]}` |
| `CONFIRM` | `{slotId, label, detail, slots}` |
| `DONE` | `{appointmentId, accessCode, slotId, scheduledAt (UTC, API'den), label (yerel), longLabel, clinicName, doctorDisplayName, status}` |
| `HANDOFF` | `{reason: TC_ATTEMPTS_EXHAUSTED | SESSION_NOT_FOUND | SESSION_EXPIRED | SESSION_NOT_VERIFIED | BOOKING_UNCERTAIN}`; `SESSION_EXPIRED`'da ayrıca `restart: true` → kiosk yeni oturum açıp TC adımına döner |

Hata durumlarında gövde ayrıca `error: {code, message, requestId}` ve `data.errorCode` taşır;
`action` ve `replyText` yine doludur, böylece kiosk sessiz kalmaz:

| HTTP | code | action | Ne zaman |
| --- | --- | --- | --- |
| 400 | `VALIDATION_FAILED` | `null` / `OFFER_SLOTS` | Geçersiz gövde / sunulmamış slot |
| 404 | `VOICE_SESSION_NOT_FOUND` | `HANDOFF` | API oturumu bilmiyor |
| 409 | `VOICE_SESSION_NOT_VERIFIED` | `ASK_TC` / `HANDOFF` | Doğrulanmamış oturum; doğrulanmış oturumda (randevu/geçmiş sorgusu) süre doldu → `HANDOFF` + `restart` |
| 409 | `SLOT_TAKEN` | `OFFER_SLOTS` | Saat başkasına gitti (eşzamanlı rezervasyon dahil); güncel saatler gelir |
| 409 | `SLOT_EXPIRED` | `OFFER_SLOTS` | Saat geçti ya da çok yaklaştı (backend `SLOT_IN_PAST` da buna eşlenir) |
| 409 | `APPOINTMENT_CONFLICT` | `OFFER_SLOTS` | Hastanın aynı saatte başka randevusu var (`expect: "saat"`) ya da aynı gün aynı poliklinikte randevusu var (`expect: "gun"`); randevu oluşmaz, seçim kaldırılır, gün/saat yeniden sorulur |
| 503 | `API_UNAVAILABLE` | mevcut adım | Backend'e ulaşılamadı (ağ, 5xx, zaman aşımı, yanlış anahtar) |
| 503 | `LLM_UNAVAILABLE` | mevcut adım | LLM yok ve kural tabanlı çözümleyici de anlamadı |

`POST /stt` — multipart `file` (webm/opus, ogg, wav, mp3) → `{text, masked, durationSec, requestId}`;
hata: 400/413 `VALIDATION_FAILED`, 503/504 `STT_FAILED` (+ `replyText`).

`POST /tts` — `{text}` → `audio/wav`; hata: 400/413 `VALIDATION_FAILED`, 503/504 `TTS_FAILED` (+ `replyText`).
TTS önbelleği yalnız konu modülündeki sabit cümleleri tutar; hasta verili metin (geçmiş, erişim kodu) önbelleğe
girmez (`X-Onbellek` bu yüzden "az önce okundu mu" kâhini olamaz).

Yanıtlarda `Server-Timing` başlığı bileşen sürelerini verir (`llm`, `api`, `stt`, `tts`, `toplam`).

## LLM'e ne gider, ne gitmez (KVKK sınırı)

İki ayrı yük vardır: **anlama** (aşağıda) ve **genel sağlık bilgisi** (bkz. "Genel sağlık bilgisi"; yalnız kimliği
maskelenmiş soru gider, ilaç adı ve sağlık kelimeleri açıktır, hasta kaydı hiç girmez).

Anlama yükünde LLM yalnız **anlama** yapar: maskelenmiş kullanıcı cümlesinden niyet + yapılandırılmış alanlar (poliklinik
kodu, gün, saat aralığı, sıra, ilaç adı) çıkarır. Önce kural tabanlı çözümleyici çalışır; LLM'e yalnız kural
"belirsiz" kaldığında gidilir. Kullanıcının kendi sağlık verisini taşıyan cümleler ("şeker hastasıyım",
"Metformin kullanıyorum", "kalbim için…") ve bağlamsız tavsiye istekleri ("bırakabilir miyim") kural
"belirsiz" kalsa da LLM'e **gitmez** (`SAGLIK_VERISI_IFADELERI`, `ILAC_TAVSIYE_GUCLU` → sabit şablon); ilaç adı
eşleştirmesi yalnız kodda yapılır.

Kural katmanı ayrıca (hepsi LLM'siz, şablonla):
- **Şikâyet → poliklinik önerisi** (`SIKAYET_KALIPLARI`): "gözüm bulanık görüyor" → "Bu şikâyet için genellikle
  Göz Hastalıkları polikliniğine başvurulur. … randevu bakmamı ister misiniz?"; teşhis yoktur, teyit istenir.
  Kısa "evet/olur/bakalım" yalnız polikliniği seçer; randevu yine ayrı açık onayla oluşur. Hem şikâyet hem
  poliklinik söylenirse poliklinik esastır; birden çok polikliniğe işaret eden ya da listede olmayan şikâyette
  poliklinik listesi (kısa sesli özetle: "… dahil 11 polikliniğimiz var") söylenir. MedTech kataloğundaki 11
  poliklinik için kalıplar var: "kulağım ağrıyor" → KBB, "cildimde döküntü var" → Dermatoloji, "hamileyim" → Kadın
  Doğum, "çocuğumun ateşi var" → Çocuk Sağlığı (`SIKAYET_ONCELIKLI`), "başım ağrıyor"/"migrenim var" → Nöroloji,
  "idrarda yanma" → Üroloji. Poliklinik adlarında halk adları ve STT yazımları da tutar ("cildiye", "kadın doğum",
  "çocuk doktoru", "kabebe", "ka be be", "neroloji"; büyük harfli "İÇ HASTALIKLARI" da).
- **Hekim seçimi** (`HEKIM_*`): seçilen poliklinikte `/slots` yanıtında birden çok hekim (`doctorId`) varsa bir kez
  "… polikliniğimizde Prof. Dr. Nermin T. ve Op. Dr. Selin E. hasta kabul ediyor. Hangi hekimimizden…" sorulur
  (`expect: "hekim"`). Ad ("Selin Hanım"), sıra ("ikincisi"), tek adaya uyan unvan ("profesör olsun") ya da
  "fark etmez" kodda eşlenir, LLM'e gitmez; hekim adları backend'den gelir. "En erken" denirse hekim sorulmaz.
  Seçilen hekimin ufukta boş saati yoksa soru yenilenir. `doctorId` gelmezse hekimler görünen addan ayrılır.
  Ekrandaki hekim düğmesi unvanlı adı aynen gönderir ("Op. Dr. Selin E.") ve aynı yoldan eşlenir. Adaylara uymayan
  bir kişi adı ("Zümrüt Hanım", maskede `[ad]`) LLM'e gönderilmez; `HEKIM_BULUNAMADI` ile hekimler yeniden sayılır.
  Ad eşlemesi ham metinde yapılır; ham metin yalnız bu eşlemede kullanılır (LLM'e, loga, yanıta gitmez).
- **Kurum soruları** (`KURUM_SORU_KALIPLARI` → `KURUM_YANITLARI`): çağrı merkezi, adres, çalışma saatleri, muayene
  ücreti, sigorta/SGK. Her adımda (TC öncesi ve onay beklerken dahil; onaydaki seçim korunur) sabit şablon; acil
  önceliklidir. Ücret ve sigorta için bilgi uydurulmaz, ödeme/provizyon akışı yoktur. Randevu akışının ortasında
  (hekim/gün/saat/saat seçimi beklenirken) yanıtın sonuna "Randevunuza devam edelim." + o adımın sorusu eklenir
  (`KURUM_ADIM_HATIRLAT`); akış ortasında ve randevu alındıktan sonra çalışma saati ve çağrı merkezi yanıtı
  "poliklinik adını söyleyin / randevuyu bana aldırın" kapanışı olmadan verilir (`KURUM_YANITLARI_AKISTA`).
- **Sesli okunuş** (`tts_on_isleme`): unvanlar açılır ("Doç. Dr." → "Doçent Doktor"), telefon numarası gruplanır,
  `kurum.json` `okunuslar` uygulanır ("MedTech" → "Med tek"; yalnız sese, ekrandaki metin değişmez).
- **Acil durum** (`ACIL_IFADELERI`, dar liste): "nefes alamıyorum", "göğsümde şiddetli ağrı var"… → her adımda
  öncelikli sabit 112 şablonu, randevu akışına girmez. "Acil randevu", "acele" yakalanmaz.
- **Sohbet**: "merhaba", "ne yapabilirsin", "teşekkürler", "görüşürüz"; **mevcut randevular** ("randevularım",
  "randevumu iptal et"): backend'de bu uç olmadığından dürüst sabit şablon. Yükü kuran tek yer `ses_servisi/llm_siniri.py::llm_yuku_kur`; oturum
nesnesi almaz.

| Gider | Gitmez |
| --- | --- |
| Maskelenmiş kullanıcı cümlesi (TC/7+ haneli sayı → `[gizli numara]`, e-posta → `[gizli e-posta]`, kendini tanıtırken söylenen ad → `[ad]`; yalnız LLM kopyasında ayrıca 5+ haneli sayı / "kod"dan sonraki sayı → `[gizli numara]`, bilgi dağarcığındaki ilaç adları (ilaç konu sorusunda ayrıca hastanın kayıtlı ilaç adları) → `[ilaç]`, sağlık ifadeleri → `[sağlık bilgisi]`, büyük harfli ve bilinen alan kelimesi olmayan her kelime (ad, soyad, adres, marka) → `[ad]`) | Hasta adı, TC, patientRef, oturum kimliği, erişim kodu |
| Sabit sistem istemi (`SISTEM_ISTEMI`) | Randevu kayıtları, tanı, ilaç listesi, özet metinleri, hekim adı |
| Kişisel olmayan bağlam: bugünün tarihi/saati, 7 günlük takvim, poliklinik adları, diyalog adımının kapalı listeden sabit açıklaması | Önceki turların asistan yanıtları ve kullanıcı cümleleri (her çağrı tek kullanıcı mesajı) |

LLM çıktısı şemayla doğrulanır, metinde geçmeyen alanlar atılır (`_temellendir`), fazladan alanlar yok
sayılır; LLM'in "onay" niyeti randevu oluşturmaz (açık onay yalnız kapalı ifade listesi ya da düğme).
Kullanıcıya giden her cümle şablondan kurulur. Testler: `tests/test_llm_sizinti.py`.

Gerçek Ollama isteği örneği (2026-11-15, geçici kayıt vekiliyle yakalandı; sistem istemi kısaltıldı):

```
[system] Sen bir hastanenin mobil sağlık asistanı uygulamasındaki sesli asistanın ANLAMA bileşenisin. … Bugün: 15 Kasım Pazar 2026 …
         Poliklinikler … Diyalog adımı: Asistan hangi poliklinikten randevu istendiğini sordu. …
[user]   Ben [ad], bir bakılmasını rica ediyorum.
```

Geçmiş muayene / ilaç soruları (`GET /api/v1/voice-sessions/{id}/medical-history`) yalnız doğrulanmış
oturumda, yalnız hekim onaylı özetlerle yanıtlanır; veri yalnız oturum belleğinde tutulur (END, hareketsizlik
ya da devirde silinir), loglanmaz.

### İlaç soru konuları (LLM'siz yanıt)

Kayıtlı bir ilaç hakkındaki soru önce **kuralla konusuna ayrılır** (`ses_servisi/ilac_bilgisi.py::konu_bul`,
kalıplar `alanlar/hasta_kabul.py::ILAC_KONU_KALIPLARI`; STT/gündelik yazımlar dahil: "yanetkisi", "aleji",
"ac karnina", "unutursam ne olur"). Yanıt üç kaynaktan şablonla kurulur, LLM metni hiç kullanılmaz:

1. Hekim onaylı özet (backend): ilaç adı, muayene tarihi/polikliniği ve **kullanım talimatı** (doz/sıklık). Doz,
   aç/tok, unutma ve süre sorularında talimat genel bilgiden önce okunur; çelişkide talimat esastır.
2. Bilgi dağarcığı `ses_servisi/alanlar/ilac_dagarcigi.py`: ilaç başına, konu başına prospektüs düzeyinde genel
   cümleler. **Demo içeriğidir; gerçek kullanımdan önce hekim/eczacı tarafından gözden geçirilmelidir** (dosya
   başında yazılı). Kapsam: demo seed'indeki Amlodipin, Metformin, Demir sülfat, Parasetamol ve ek olarak
   Lansoprazol, Famotidin, Amoksisilin, Levotiroksin, İbuprofen.
3. Konu modülü şablonları (`ILAC_KONU_*`, `ILAC_HANGISI`, `ILAC_ETKILESIM_CIFT`, `ILAC_DOZ_GENEL`).

| Konu | Örnek | Yanıt |
| --- | --- | --- |
| `amac`, `kullanim` | "Metformin ne işe yarıyor", "nasıl kullanacağım" | Hekim onaylı özet (değişmedi) |
| `yan_etki`, `alerji`, `alkol`, `hamilelik`, `etkilesim` | "amlodipin kullanırken bir alerjimin olmaması gerekiyor mu" | "{tarih} tarihli {poliklinik} muayenenizde yazılan {ilaç} hakkında genel bilgi: …" + "hekiminize ya da eczacınıza danışın" |
| `yemek`, `unutma`, `sure`, `doz` | "aç karnına mı", "unutursam ne olur", "bırakabilir miyim", "dozunu artırayım mı" | Aynı + önce "Hekiminizin talimatı: {kullanım}"; dozda "kendiniz değiştirmeyin / 114 / 112" |
| `dikkat` (birleşik) | "nelere dikkat etmeliyim", "neyle almamalıyım", "nelerden uzak durmalıyım" | `yemek` + `alkol` dağarcık metinlerinin kısa birleşimi + talimat (`ILAC_BIRLESIK_KONULAR`); cümlede daha özel güçlü konu da varsa ("yan etkileri ve nelere dikkat") o konu. `history.topic` kapalı listede kalır (ilk alt konu: `yemek`) |

Gündelik ifadeler (2026-11-16): `yemek` ← "yememem gereken", "neyle almalı", "içecek", "besin"; `yan_etki` ← "dokunur
mu", "uyku yapar mı", "sakıncası", "sersemletir mi"; "araba kullanabilir miyim" dağarcıkta konu değildir (→ `tanimsiz`).

- **Takip sorusu:** son konuşulan ilaç oturum belleğinde (yalnız `gecmis` içindeki sıra; ad ayrıca tutulmaz)
  saklanır: "peki yan etkisi?", "bunu içerken alkol alabilir miyim" ona bağlanır. İlaç belli değilse
  "Hangi ilacınız için soruyorsunuz? Kayıtlarınızda … görünüyor." denir; bir sonraki turda söylenen ad o konuyla
  yanıtlanır.
- **Dürüstlük:** kayıtta olmayan ilaç → genel bilgi yolu açıksa genel bilgi (dağarcık ya da LLM; hastanın kaydına
  bağlanmaz), kapalıysa `ILAC_BULUNAMADI`; kayıtlı ama
  dağarcıkta olmayan ilaç ya da konu → "elimde onaylı bilgi yok"; iki ilacın birlikte kullanımı → ilaç çiftine
  özel bilgi yok şablonu; "X yerine başka ilaç" → sabit ret (`ILAC_TAVSIYE_RET`).
- **Acil:** ilaç bağlamında anlatılan ciddi belirti ("amlodipin içtim yüzüm şişti", "ilacı içince nefesim
  daraldı"; soru biçimi değil) → mevcut 112 şablonu (`data.emergency`). Yüz/dil/dudak şişmesi her bağlamda acildir.
- **LLM sınırı:** kural konuyu çıkaramazsa ("Metforminle araba kullanabilir miyim") yanıt `ILAC_KONU_TANIMSIZ`
  ("elimde onaylı bilgi yok"). Yalnız `ILAC_KONU_LLM=1` iken LLM'e ilaç adı `[ilaç]` yapılmış cümle gider ve ayrı
  şemada (`ILAC_KONU_SEMASI`, `ilac_konu` zorunlu) yalnız konu etiketi istenir; maskeden sonra sağlık ifadesi kalan
  cümle hiç gönderilmez.
- **Maskeleme:** `llm_icin_maskele` her LLM yükünde dağarcıktaki ilaç adlarını, ilaç biçimli kelimeleri (ek:
  -statin, -pril, -sartan, -olol, -dipin, -prazol…; bilinen markalar: aspirin, coraspin, glifor, eliquis…) ve
  `SAGLIK_VERISI_IFADELERI`'ni gizler. Geçmiş henüz belleğe alınmamışken ilaç adayı taşıyan ve kuralın
  "belirsiz" dediği cümlede önce hekim onaylı geçmiş alınır, hastanın kayıtlı ilaç adları da `[ilaç]` yapılır
  (adlar yalnız maske için kullanılır, yüke girmez); geçmiş alınamazsa cümle LLM'e hiç gitmez.
- **İlaç listesi:** "ilaçlarım neler" / "son muayenemde hangi ilaçlar" son muayeneyi okur ve önceki onaylı
  muayenelerde yazılan farklı ilaçların adlarını da anar ("Önceki muayenelerinizde de yazılanlar: Metformin,
  Demir sülfat; ayrıntısı ekranda") — İlaçlarım sayfasıyla tutarlı.
- **Gündelik geçmiş soruları** (2026-11-16): "Ya en son randevu, bu doktor bana ne yazmıştı?", "geçen sefer doktor ne
  verdi", "geçen randevuda ne yazıldı" → son muayene listesi; "en son ne zaman muayene oldum / hangi doktora gittim"
  → son onaylı muayenenin tarihi, polikliniği, hekimi ve ilaç adları (`GECMIS_SON_ZIYARET`); "doktor ne demişti" →
  hekim onaylı özet + uyarılar; "Elif Hanım ne yazmıştı" → o hekimin muayenesi (ad eşlemesi yalnız kodda; kayıtta
  yoksa `GECMIS_HEKIM_YOK`). "Randevu" geçmiş zamanla ("son randevumda", "geçen randevu") geçmiş muayenedir;
  "randevum ne zaman" değildir. "Kayıtta olmayan ilaç" (`ILAC_BULUNAMADI`) yalnız cümlede gerçek bir ilaç adayı
  varsa söylenir: `gecmis.GUNDELIK_KOKLERI` (randevu, doktor, sefer, demiş, gittiğim…) ve fiil çekim sonları
  (-mıştı, -dılar, -diğimde…) aday sayılmaz, kayıtlı ilaçla bulanık eşleşmeye de giremez ("demişti" ≠ Demir).
  Testler: `tests/test_gecmis_gundelik.py`.
- **Bilinen sınır:** kayıtta olmayan, biçim ipucu taşımayan uydurma/az bilinen ad ("zorbatil nedir") genel anlama
  yolunda maskesiz LLM'e gidebilir (ilaç olduğu koddan anlaşılamaz).
- Sesli okuma kapalıysa (`GECMIS_SESLI_OKUMA=0`) ilaç adı sesli söylenmez (`GECMIS_EKRANDA`, `ILAC_HANGISI_EKRANDA`).

Testler: `tests/test_ilac_konulari.py`, `tests/test_capraz_denetim.py`.

### İlacı tarifle sorma ("tansiyonumu düşüren ilaç", "şeker hapım", "kardiyolojide yazılan ilaç")

Hasta ilacın adını değil tarifini söylediğinde (`ses_servisi/ilac_tarifi.py`, orkestratörde `_tarif_yolu`; tavsiye
yolundan, şikâyet → poliklinik önerisinden ve LLM anlamadan ÖNCE, acil ve kurum sorularından SONRA):

1. **Tarif bulma** (`tarif_bul`, yalnız kod): amaç kelimesi (`ILAC_AMAC_ANAHTARLARI`: tansiyon, şeker, demir/kansızlık,
   kalp, kolesterol, mide, ağrı, ateş, tiroid, antibiyotik, uyku, alerji, vitamin; STT biçimleri "tansyon", "apım",
   "düşünen" dahil) yanında 0–3 kelime içinde ilaç kelimesi / kullanım fiili / amaç fiili ("düşüren") olmalı;
   ya da poliklinik ("kardiyolojide yazılan"), sıra ("son muayenede", "ilk muayenemde", "eski ilacım"), zaman
   ("sabah içtiğim"). İlaç listesi isteği, ilaç önerisi isteği, "X yerine" ve randevu isteği tarif sayılmaz.
2. **Çözme** (`tarif_coz`): ölçütler hekim onaylı kayıtlarla kesiştirilir — amaç: dağarcık kaydının `amaclar`
   alanı ya da kayıttaki "ne işe yarar" metninin kökleri; poliklinik: muayenenin polikliniği; sıra: en yeni / önceki
   muayeneler; zaman: hekim talimatında o zaman sözcüğü. "Kalp" başka bir amaçla birlikte geçerse daraltmaz.
3. **Yanıt:** tek ilaç → o ilacın konu yanıtı (dağarcık + hekim talimatı; konu yoksa onaylı özet) ve ilacın adı
   söylenir ("Kayıtlarınızda buna uyan ilaç Amlodipin." yanıtta ad yoksa başa eklenir). Birden çok → `ILAC_TARIF_HANGISI`
   ("Kayıtlarınızda sabah kullanılan birden çok ilaç var: Metformin ve Demir sülfat. Hangisini…"); hiçbiri →
   `ILAC_TARIF_YOK` ("Hekim onaylı kayıtlarınızda kolesterol için bir ilaç görünmüyor. Kayıtlarınızda … var");
   tahmin yapılmaz. Konu dağarcıkta yoksa mevcut kural: LLM'e yalnız anonim genel soru gider, talimat koddan eklenir.
4. **İlaç bağlamı şikâyet değildir:** cümlede tarif ya da "ilacım/hapım" varsa hastalık/organ kelimesi ("tansiyon",
   "şeker", "kalp") poliklinik önerisine yol açmaz (`anlama.kural_tabanli_anla`, `ilac_kullanim_baglami`). Tersine,
   ilaç bağlamı olmayan şikâyet + yaşam tarzı sorusu ("tansiyonum çıktı, tuz yemem zararlı mı", "şekerim var tatlı
   yiyebilir miyim") ilaç sorusu sayılmaz (genel bilgi / tavsiye yolu).
5. Kimlik doğrulanmadan kayıt okunmaz ("önce kimlik"); tarif ve kayıtlar hiçbir LLM yüküne girmez.

Testler: `tests/test_ilac_tarifi.py`.

### Yönlendirme değerlendirme seti

`tests/yonlendirme_seti.py`: beklenen yolla etiketli 272 cümle (kullanıcının gerçek cümleleri dahil; acil, kurum,
geçmiş, ilaç, spesifik, tavsiye, bilgi, poliklinik, randevu, sohbet). `tests/test_yonlendirme_seti.py` her cümleyi
orkestratörden geçirip yanıt verisinden yolu çıkarır (sahte LLM; ağ yok) ve hiçbir cümlenin kendiliğinden randevu
oluşturmadığını da denetler. Öncelik: acil > ilaç bağlamı > geçmiş > tavsiye > şikâyet → poliklinik (soru varsa
poliklinik önerisi son çare). Tablo ve yanlışlar için:

```powershell
.venv\Scripts\python.exe tools\yonlendirme_raporu.py [--hepsi] [--json DOSYA]
```

### Genel sağlık bilgisi (yerel LLM, `SAGLIK_BILGI_LLM`)

"Aspirin ne işe yarar", "baş ağrısına ne iyi gelir", "grip ile soğuk algınlığının farkı ne" gibi kişiye özel
olmayan sorular kısa genel bilgiyle yanıtlanır (`ses_servisi/saglik_bilgisi.py`, orkestratörde
`_saglik_on_yolu`). Yol kuralla seçilir; niyet LLM'i kullanılmaz.

| Sıra | Durum | Yanıt |
| --- | --- | --- |
| 1 | Acil ifade (`ACIL_IFADELERI`; zehirlenme, aşırı doz, felç belirtisi, intihar ifadesi eklendi) | Sabit 112 şablonu, her şeyden önce |
| 2 | Hastanın **kayıtlı** ilacı adıyla soruluyor | Mevcut ilaç akışı (hekim onaylı özet + dağarcık). Dağarcıkta o konu yoksa ("Metforminle araba kullanabilir miyim") LLM'e yalnız soru gider; "Hekiminizin talimatı: …" koddan eklenir |
| 3 | Kişiye özel (`SAGLIK_SPESIFIK_*`): doz artır/azalt/kaç tane, bırak/başla, tahlil/ölçüm sonucu yorumu, "bende ne var", iki ilaç birlikte, "hamileyim/X kullanıyorum … içebilir miyim", ilaç önerisi | Sabit yönlendirme (`SAGLIK_SPESIFIK_GIRIS` + "doktorunuza ya da eczacınıza danışın; kötüyseniz 112"); LLM yok |
| 4 | Dağarcıkta o ilaç ve konu için hazır bilgi var ("ibuprofen aç karnına içilir mi", "Parol ne işe yarar" → parasetamol) | Dağarcık metni + sabit kapanış; LLM yok |
| 5 | Diğer bilgi soruları | Yerel LLM (2–4 cümle) → kod denetimi → + sabit kapanış `SAGLIK_BILGI_KAPANIS`; `data.aiGeneralInfo: true` |
| — | Şikâyet + tavsiye isteği ("başım ağrıyor ne yapayım", "randevu değil, doktora gitmeden…") | Tavsiye yolu (aşağıda) |
| — | Kendi şikâyeti için ilaç kararı ("başım ağrıyor parol içsem iyi gelir mi") | Sabit yönlendirme (`kisisel_ilac`; 2026-11-16) |
| — | Şikâyet anlatımı ("başım ağrıyor"), randevu/poliklinik, kendi kayıtları ("ilaçlarım"), adı söylenmeyen ilaca dönük soru ("yan etkisi var mı", "peki alkol?"), sağlık dışı | Değişmedi (mevcut akış) |

Yol kimlik adımında (TC beklenirken) ve devirde kullanılmaz; saat/onay beklenirken adım değişmez, onay
beklenirken yanıta "Seçtiğiniz randevu da onayınızı bekliyor." eklenir ve sonraki "evet" önce seçimi yeniden okur.

**LLM'e giden** (`llm_siniri.saglik_bilgi_yuku_kur`): sabit sistem istemi `SAGLIK_BILGI_ISTEMI` + `Soru: <maskeli
soru>`; bilinen marka için koddan "(Bilgi: Parol, parasetamol etken maddeli bir ilacın ticari adıdır.)". Maske:
TC/uzun sayı, e-posta, "kod"dan sonraki sayı, tanıtılan ad, ilaç/sağlık terimi olmayan büyük harfli kelime → `[ad]`.
TC içeren cümle bu yola hiç girmez. **Gitmeyen:** hasta adı, erişim kodu, muayene tarihleri, tanı/özet, reçete,
hekim talimatı, randevu, önceki turlar, ilacın bu hastaya yazıldığı bilgisi. Loglara soru/yanıt yazılmaz (yalnız ret
nedeni kodu). `.env`'de bulut sağlayıcı seçilirse bu soru da oraya gider (anlama yüküyle aynı sınır).

**Çıktı denetimi** (`yanit_denetle`, kodla zorlanır): boş/kesik, madde işareti/markdown, köşeli/açılı parantez,
Türkçe dışı yazı ya da İngilizce, ikiden çok parantez, rakam (112 hariç; "B12" gibi ada bitişik serbest), sayı +
birim, "günde N kez", "N saatte bir", teşhis ("sizde … var", "muhtemelen"), kişisel karar/izin ("bırakabilirsiniz",
"güvenlidir", "sorun olmaz", ilaç bağlamında "-ebilirsiniz" ve "kullanın/devam edin"), soruda geçmeyen ilaç adı,
model notu ("Not:", "bu soru"), değerlendirmede görülen riskli kesin iddialar ("bulaşıcı değildir", reflüde
"yemekten sonra yatın") → **ret** → sabit `SAGLIK_BILGI_VEREMIYORUM`. Modelin kendi "doktorunuza danışın" cümlesi
atılır; en çok 4 cümle / 450 karakter (112 cümlesi kırpılmaz). LLM hata/zaman aşımı → aynı sabit metin, çökme yok.

**Model:** varsayılan anlama modeli (`qwen3:4b-instruct-2507`). 2026-11-16 değerlendirmesinde (40+ soru)
`gemma3:4b` Türkçe ve tıbbi doğrulukta belirgin daha iyiydi (qwen: "gut sindirim sistemi hastalığıdır", "kansızlıkta
boğaz ağrısı"); `SAGLIK_LLM_MODEL=gemma3:4b` ile yalnız bu yol için seçilebilir. 6 GB GPU'da iki model birbirini
bellekten atar: model geçişinden sonraki ilk çağrı ~3–5 sn sürer. Her iki model de ara sıra yanlış bilgi üretir;
yanıt "genel bilgi" etiketlidir ve gerçek kullanım için uygun değildir (demo).

Testler: `tests/test_saglik_bilgisi.py`.

### Şikâyet için tavsiye isteği ("randevu değil, fikir ver")

Kullanıcı bir şikâyet için randevu değil fikir istediğinde (`ses_servisi/tavsiye.py`, orkestratörde `_tavsiye_yolu`;
kalıplar `alanlar/hasta_kabul.py` `TAVSIYE_*`). Yol kuralla seçilir; acil ve kurum soruları önce yakalanır.

| Sıra | Durum | Yanıt |
| --- | --- | --- |
| 1 | Acil (`ACIL_IFADELERI` + yeni `ACIL_BIRLIKTE`: göğüs ağrısı + bulantı/terleme/nefes/kola yayılma, ense sertliği + ateş/baş ağrısı, şiddetli karın ağrısı; "kanlı kusma") | Sabit 112 şablonu |
| 2 | Ciddi belirti (`SAGLIK_CIDDI_BELIRTILER`), ilaç adı/kelimesi, ilaç önerisi, teşhis ("ne olabilir", "ciddi mi"), ölçüm yorumu, çocuk/gebelik, poliklinik adı | Bu yol devreye girmez (mevcut akış) |
| 3 | Şikâyet + tavsiye işareti aynı cümlede ("midem bulanıyor ne yapabilirim", "başım ağrıyor ne yapayım") ya da takipte tavsiye işareti ("doktora gitmeden…", "çay iyi gelir mi") | Yerel LLM'in 2–3 cümlelik öz bakım bilgisi + (varsa) konuya özel koddan uyarı + "Şikâyetiniz sürer ya da artarsa bir doktora başvurun." + sabit kapanış + ısrarsız teklif "İsterseniz Dahiliye polikliniğinden randevu da bakabilirim." (`data.aiGeneralInfo`, `data.suggestion`; teklif konuşmada poliklinik başına bir kez, kullanıcı reddettiyse hiç) |
| 4 | Tavsiye işareti var, konu yok | "Hangi şikâyetiniz için soruyorsunuz?…" (poliklinik sorulmaz); sonraki tur şikâyet adı → 3 |
| 5 | Randevu reddi ("randevu istemiyorum", öneriye "istemiyorum"/"gerek yok") | "Peki, randevu bakmıyorum. İsterseniz mide bulantısı için evde … anlatabilirim." → "evet/anlat" → 3. Önceki önerinin 112 notu korunur (kalp) |
| — | Yalnız şikâyet ("midem bulanıyor"), genel bilgi sorusu ("baş ağrısına ne iyi gelir"), "hayır" ("Başka bölüm" düğmesi) | Değişmedi |

- **Bağlam:** son şikâyet oturumda yalnız **konu kodu** olarak tutulur (`Oturum.son_sikayet`; kullanıcının sözü değil;
  vedada/END'de silinir). Takip cümlesi şikâyeti söylemese ya da STT bozsa ("bu lambasına ne iyi gelir") konu buradan
  gelir. STT toleransı dar: yalnız "midem (çok) kullanıyor/bulunuyor" → bulantı (`TAVSIYE_STT_KALIPLARI`).
- **LLM'e giden** (`llm_siniri.saglik_tavsiye_yuku_kur`): kullanıcının cümlesi DEĞİL, kapalı listelerden kurulan
  `Konu: mide bulantısı` + `Soru: Mide bulantısı için evde neler yapılabilir? Bitki çayı iyi gelir mi?` (alt sorular
  `TAVSIYE_ALT_SORULARI`). Ad, TC, kod, hasta kaydı yüke giremez.
- **Denetim:** genel bilgi denetimlerinin hepsi + kesin tedavi iddiası ("kesinlikle geçirir", "tedavi eder", "doktora
  gerek yok") → ret; ev yöntemi sorulmadıysa modelin "…tedavi yerine geçmez" cümlesi atılır; en çok 3 cümle; sesli
  toplam ≤ 590 karakter (bütçe sabit metinlerden sonra hesaplanır).
- **Randevu akışı:** teklif sonrası "evet / tamam randevu alayım" yalnız polikliniği seçer; randevu yine açık onayla.
  Gün/saat/onay beklenirken tavsiye verilir, adım değişmez, teklif yapılmaz; onayda "evet" önce seçimi yeniden okur.
- **Anlaşılmayan cümle:** randevu akışı dışında (kullanıcı henüz randevu istemedi) "Hangi poliklinik?" yerine
  `ANLASILAMADI_NOTR`; tek başına "hayır/değil" → "Peki. Başka nasıl yardımcı olabilirim?". Akış ortasında eski davranış.
- `SAGLIK_BILGI_LLM=0` ise 3–5'teki tavsiye kısmı çalışmaz (eski "tıbbi yorum yapamam" yanıtı); ret yanıtı çalışır.

Testler: `tests/test_tavsiye.py` (kullanıcının 2026-11-16 konuşması birebir dahil).

Çok turlu gerçek model denemesi: `.venv\Scripts\python.exe tools\konusma_uctan_uca.py [--yakalama-url URL]`.

## Ortak LLM katmanı (diğer servisler için)

```python
from ortak.ayarlar import env_yukle
from ortak.llm import saglayici_olustur, Mesaj, AracTanimi, LLMHatasi, LLMZamanAsimi

env_yukle()
llm = saglayici_olustur("SADE_LLM")   # SADE_LLM_* yoksa LLM_* okunur
metin = await llm.metin_uret("…", sistem="…")
veri = await llm.json_uret([Mesaj.kullanici("…")], sistem="…", sema={...}, sema_adi="sonuc")
yanit = await llm.sohbet(mesajlar, araclar=[AracTanimi("ad", "açıklama", {...})])  # yanit.arac_cagrilari
await llm.kapat()
```

Hata sınıfları: `LLMYapilandirmaHatasi`, `LLMZamanAsimi`, `LLMBaglantiHatasi`, `LLMLimitHatasi`,
`LLMYanitHatasi` (hepsi `LLMHatasi`; `gecici` alanı yeniden denemeye uygunluğu söyler).
Testler için `SahteSaglayici([...])`.

## ai-simplify (sade_servisi, 8001)

Hekimin girdiği tanı ve ilaç satırlarını hastaya gidecek sade Türkçe özete çevirir. Çıktı hasta
portalına **hekim onayından sonra** gider; servis yalnız taslak üretir.

Çalıştırma: `.venv\Scripts\python.exe -m sade_servisi` → **http://127.0.0.1:8001** (`SADE_HOST`/`SADE_PORT`,
varsayılan `127.0.0.1`/`8001`; loglar `logs/sade-servisi.*.log`). Servis yalnız IPv4 geri döngüde dinler: backend'in
`Simplify:BaseUrl`/`SIMPLIFY_URL` değeri `http://127.0.0.1:8001` olmalıdır. `localhost` Windows'ta önce `::1`'e gider
ve reddedilen IPv6 denemesi her callback'e ~2 sn ekler.

**Uçlar**

- `POST /simplify` — `X-Service-Key` (`hmac.compare_digest`), gövde
  `{patientRef ≤64, diagnosis ≤1000, medications[her biri ≤500], requestId ≤128, summaryId (GUID)}` (`extra="forbid"`).
  30'dan fazla ilaç satırı 400 değil, 202 + `SIMPLIFY_FAILED` callback'i alır (backend 4xx'te kendi yedeğine düşer).
  Hemen `202 {accepted, summaryId, requestId, version}`; iş arka planda. Hatalar: 401 `UNAUTHORIZED`,
  400 `VALIDATION_FAILED` (yalnız alan/tür; girdi geri yansıtılmaz), 413, 503 `QUEUE_FULL` /
  `SERVICE_KEY_MISSING`.
- `GET /health` — LLM, KB kaynağı/kayıt sayısı, kuyruk ve sayaçlar.
- Callback: `PUT {HASTAKABUL_API_URL}/api/v1/summaries/{id}` — başarı
  `{diagnosisPlain, medications[{name,purpose,usage}], warnings, unknownTerms, sourceIds}`,
  hata `{errorCode}` (`LLM_UNAVAILABLE`, `SIMPLIFY_FAILED`). Her iş mutlaka bir callback ile biter.

**Güvenlik tasarımı (kodla zorlanır, LLM'e güvenilmez)**

1. KB: önce backend `GET /api/v1/kb` (servis anahtarlı, `SADE_KB_YENILEME_SN` ile yenilenir), olmazsa
   gömülü 16 kayıt. Eşleme deterministiktir (`eslesme.py`: `ortak.turkce` normalizasyonu, eş anlamlılar,
   sınırlı Levenshtein); LLM'e yalnız eşleşen kayıtlar gider. `sourceIds` koddan gelir.
2. Bilinmeyen tanı/ilaç `unknownTerms`'e yazılır; bilinmeyen ilaç için sabit "hekiminize sorun" şablonu.
3. **LLM yalnız tanı açıklamasını ve ilacın "ne işe yarar" cümlesini yeniden yazar.** Kullanım (doz, sıklık,
   zamanlama, süre) her zaman koddan gelir: hekimin kullanım ifadesi yalnız doz/sıklık/zamanlama sözlüğünden
   oluşuyorsa aynen ("Reçetede yazan: 30 mg 1x1 akşam. Yani günde 1 kez…"), KB'nin genel zamanlaması yalnız
   hekimin sıklığı ve zamanlamasıyla çelişmiyorsa eklenir.
4. LLM çıktısı denetlenir (`dogrulama.py`, `uretici.cikti_denetle`): **kaynak dağarcığı** (her içerik kelimesi o
   alanın kaynaklarında — eşleşen KB kaydı, onaylı sade metin — geçmeli; olumsuz kelimeler yalnız birebir
   eşleşir, Türkçe karakterli kaynağa karşı yazım harf harf aynı olmalı), doz/sayı, "en fazla N" sınırları,
   ilaç kümesi, yasak kalıplar (ağır dil, güvence, başka tedavi, teşhis, istem sızıntısı, "yarıda bırakın",
   "geçene kadar", izin veren "-abilirsiniz"), Markdown/HTML/bağlantı, yabancı karakter, kesik metin, tekrar.
   İhlalde bir kez yeniden denenir, sonra onaylı şablona düşülür (şüphede kuru ama doğru metin).
   Uyarılar yalnız onaylı şablonlardandır.
5. Hekimin tanı metni ve ilaç satırları LLM'e gitmez; VERİ bloğunda yalnız KB metinleri vardır (`<`/`>` kaçırılır).
   `unknownTerms` ve bilinmeyen ilaç adı hasta portalına da gidebilir: yalnız kısa, terim benzeri değerler
   aynen yazılır; cümle/talimat ya da görünmez Unicode karakterli girdi yerine sabit "Açıklanamayan …" yazılır.
6. Log'a `patientRef`, tanı, ilaç yazılmaz; yalnız requestId, summaryId, yol, sayılar, süre.

**LLM yokken** (`SADE_LLM_ETKIN=0` ya da erişilemez): tüm terimler KB'de varsa şablon özet,
yoksa `LLM_UNAVAILABLE`.

**Kuyruk**: SQLite (`SADE_KUYRUK_DOSYASI`, `veri/` git dışı). Yalnız işin gerektirdiği veri tutulur
(`patientRef` tutulmaz); gövde üretilince tanı/ilaç silinir, callback başarılı olunca kayıt silinir.
Callback 401/403/408/429/5xx/ağ hatasında üstel beklemeyle (`SADE_GERI_CAGRI_*`) yeniden denenir;
400'de bir kez `SIMPLIFY_FAILED` gönderilir; 404/409'da vazgeçilir. Servis yeniden başlarsa kuyruk kaldığı
yerden sürer. Üretim 3 kez başlayıp bitmediyse (süreç üretimde çöktü) iş `SIMPLIFY_FAILED` ile kapanır; işlerken
beklenmeyen hata veren iş ertelenir, kuyruğun başını tıkamaz. `secure_delete` açık; kuyruk boşalınca WAL kesilir.
Azami deneme (varsayılan 12, ≈1,3 saat) aşılırsa iş silinir ve özet backend'de PENDING kalır.
**Dosya izni:** kuyruk dosyası klasörün ACL'ini devralır; `C:\Dev` altında "Authenticated Users" okuyabilir.
Üretimde `SADE_KUYRUK_DOSYASI`'nı yalnız servis hesabının okuyabildiği bir klasöre verin
(ör. `icacls veri /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F" "SYSTEM:(OI)(CI)F"`).

**Ortam değişkenleri** (`.env.example`): `SADE_HOST`, `SADE_PORT`, `HASTAKABUL_API_URL`, `SERVICE_KEY`,
`SADE_API_ZAMAN_ASIMI_SN`, `SADE_LLM_ETKIN`, `SADE_LLM_*` (yoksa `LLM_*`), `SADE_URETIM_ZAMAN_ASIMI_SN`,
`SADE_AZAMI_TOKEN`, `SADE_KB_KAYNAGI` (`auto`|`gomulu`), `SADE_KB_YENILEME_SN`, `SADE_KUYRUK_DOSYASI`,
`SADE_AZAMI_KUYRUK`, `SADE_GERI_CAGRI_AZAMI_DENEME`, `SADE_GERI_CAGRI_TABAN_BEKLEME_SN`,
`SADE_GERI_CAGRI_AZAMI_BEKLEME_SN`, `SADE_AZAMI_TANI`, `SADE_AZAMI_ILAC`, `SADE_AZAMI_ILAC_UZUNLUGU`.

**Uçtan uca deneme**: sahte API (3000) ve servis ayaktayken
`.venv\Scripts\python.exe tools\sade_uctan_uca.py logs\sade-uctan\rapor.json [--kisa] [--vaka=dispepsi,karisik]`.
Sahte API'nin ek uçları: `GET /api/v1/kb`, `PUT /api/v1/summaries/{id}` (anahtarlı),
`POST /__mock/clinical-entries` (PENDING özet açar ve `/simplify` çağırır), `GET /__mock/summaries[/{id}]`,
`POST /__mock/summaries/{id}/approve`.

**Bilinen sınırlar**: Kaynak dağarcığı denetimi kaynakta geçen kelimelerin yeniden dizilmesiyle oluşan anlam
kaymalarını (ör. iki KB cümlesindeki kelimelerden yeni bir iddia kurmak) yakalayamaz; tek gerçek bariyer
hekim onayıdır.
Ollama ses servisiyle paylaşılır; özetleme sürerken sesli asistanın gecikmesi artabilir.

## Halisaha raporundan alınmayanlar

Telefon hattı, Pipecat ses hattı, akışlı (streaming) STT/TTS, VAD ile tur tespiti ve
kesintide araç kurtarma kiosk için anlamsızdır: kiosk HTTP tabanlıdır ve kullanıcı kaydı
bitirince tek dosya gönderir. Whisper'ın kendi VAD süzgeci yalnız sessizlik kırpmak ve
"Altyazı M.K." gibi hayalet transkriptleri önlemek için açıktır.
