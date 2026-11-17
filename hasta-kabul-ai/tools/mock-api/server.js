// Sahte HastaKabul API'si — yalnız GELİŞTİRME ve TEST için (gerçek backend .NET 9 + PostgreSQL).
// Gerçek backend sözleşmesini birebir taklit eder (hasta-kabul-backend/src, 2026-11-15 okundu):
//   POST /api/v1/voice-sessions                 → 200 {sessionId, verified, attempts, maxAttempts, status}
//   GET  /api/v1/voice-sessions/{id}            → 200 aynı görünüm (açılıştan 15 dk sonra ACTIVE/VERIFIED → EXPIRED)
//   GET  /api/v1/voice-sessions/{id}/medical-history?limit=3 → X-Service-Key; oturum VERIFIED/COMPLETED ve süresi
//        dolmamış olmalı (aksi 409 VOICE_SESSION_NOT_VERIFIED); limit 1–5 (aksi 400 VALIDATION_FAILED);
//        {visits:[{appointmentId, scheduledAt, clinicName, doctorDisplayName, diagnosisPlain, medications[], warnings[]}]}
//        yalnız hekim onaylı özetler, yeniden eskiye; demo hastada 2 onaylı + 1 onaysız (dönmez)
//   POST /api/v1/voice-sessions/{id}/verify {tc}→ 200 + handoff; 3. hatada HANDED_OFF
//   GET  /api/v1/slots?clinic=&date=YYYY-MM-DD  → X-Service-Key; [{slotId, startTime(UTC "Z"), doctorDisplayName, doctorId}]
//   POST /api/v1/appointments {voiceSessionId, slotId} → X-Service-Key; oturum VERIFIED şart; SLOT_TAKEN
//   GET  /api/v1/kb                             → X-Service-Key; [{id, term, title, body, kind, synonyms}] (16 kayıt)
//   PUT  /api/v1/summaries/{id}                 → X-Service-Key; ai-simplify callback'i. {errorCode} → FAILED,
//        aksi halde diagnosisPlain zorunlu → READY; APPROVED kayıt → 409 INVALID_STATUS_TRANSITION;
//        errorCode > 40 ya da diagnosisPlain > 2000 karakter → 500 (gerçek backend'de DB sütun sınırı)
//   GET  /api/v1/summaries/{id}                 → gerçek backend'de hekim JWT'si ister; burada hep 401
// Hata zarfı: {error:{code,message,requestId}}; durum eşlemesi ErrorHandlingMiddleware ile aynı. Demo
// sağlamlaştırmasından (2026-11-16) beri HER hata bu zarfla döner (bozuk JSON 400 VALIDATION_FAILED, gövdesiz
// 401/404 yok, beklenmeyen hata 500 INTERNAL_ERROR). Ayrıca: slotlar bugün dahil 8 gün (hafta sonu dahil;
// 09:00–12:00 ve 13:30–16:30), geçmiş slot listelenmez ve randevusu 409 SLOT_IN_PAST; hastanın etkin randevusuyla
// saat çakışması (poliklinik fark etmez) ya da aynı İstanbul gününde aynı poliklinikten ikinci randevu 409
// APPOINTMENT_CONFLICT; biçimsiz TC 400 VALIDATION_FAILED (sayaç artmaz), attempts yalnız başarısız denemeleri
// sayar; süresi dolmuş oturumda verify 409 INVALID_STATUS_TRANSITION.
// Demo TC 10000000146 (backend README'de açık, sentetik). Veri bellek içidir; yeniden başlatınca sıfırlanır.
//
// Yalnız sahte sunucuda olan test uçları (gerçek backend'de YOK):
//   POST /__mock/reset                 → demo verisini yeniden üretir
//   POST /__mock/slots/{slotId}/take   → slotu "başka hasta aldı" yapar (SLOT_TAKEN denemesi için)
//   POST /__mock/clinical-entries {diagnosis, medications[]}
//        → backend ClinicalEntryService gibi PENDING özet açar ve SIMPLIFY_URL/simplify'ı çağırır
//          (X-Service-Key, X-Request-Id, 10 sn); 2xx değilse seed yedeğine ("dispepsi") ya da
//          FAILED/LLM_UNAVAILABLE'a düşer. Yanıt: {clinicalEntryId, summaryId, summaryStatus}
//   GET  /__mock/summaries[/{id}]      → özet(ler)i okur (+ callbackCount)
//   POST /__mock/summaries/{id}/approve → READY özeti APPROVED yapar (409 denemesi için)
//   POST /__mock/sessions/{id}/expire  → oturumun süresini hemen doldurur (15 dk beklemeden deneme için)
//
// Çalıştırma: SERVICE_KEY=<anahtar> node server.js   (PORT varsayılanı 3000)
"use strict";

const http = require("http");
const crypto = require("crypto");

const PORT = Number(process.env.PORT || 3000);
const SIMPLIFY_URL = (process.env.SIMPLIFY_URL || "http://localhost:8001").replace(/\/$/, "");
const SIMPLIFY_TIMEOUT_MS = 10000;
const SERVICE_KEY = process.env.SERVICE_KEY || process.env.MOCK_SERVICE_KEY || "";
const DEMO_TC = "10000000146";
const MAX_ATTEMPTS = 3;
const SESSION_LIFETIME_MS = 15 * 60 * 1000;
const ISTANBUL_OFFSET_MS = 3 * 60 * 60 * 1000; // Türkiye 2016'dan beri sabit UTC+03

if (!SERVICE_KEY) {
  console.warn("UYARI: SERVICE_KEY tanımlı değil; X-Service-Key isteyen uçlar 500 döner (gerçek backend gibi).");
}

// ---------------------------------------------------------------- yardımcılar
const uuid = () => crypto.randomUUID();
const GUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// ErrorHandlingMiddleware.MapStatusCode ile aynı; listede olmayan kod → 400.
const CODE_TO_STATUS = {
  VALIDATION_FAILED: 400, UNAUTHORIZED: 401, FORBIDDEN: 403, TC_NOT_FOUND: 404, NOT_FOUND: 404,
  NO_APPOINTMENT_TODAY: 404, VOICE_SESSION_NOT_FOUND: 404, SLOT_TAKEN: 409, INVALID_STATUS_TRANSITION: 409,
  VOICE_SESSION_NOT_VERIFIED: 409, VOICE_SESSION_EXHAUSTED: 409, SUMMARY_NOT_APPROVED: 409, LLM_UNAVAILABLE: 503,
  APPOINTMENT_CONFLICT: 409, SLOT_IN_PAST: 409, INTERNAL_ERROR: 500,
};
const SLOT_DAYS = 8;            // bugün dahil
const SLOT_MINUTES = 15;

class DomainError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

function tcWellFormed(tc) {
  if (typeof tc !== "string" || !/^\d{11}$/.test(tc) || tc[0] === "0") return false;
  const d = [...tc].map(Number);
  const odd = d[0] + d[2] + d[4] + d[6] + d[8];
  const even = d[1] + d[3] + d[5] + d[7];
  const d10 = ((odd * 7 - even) % 10 + 10) % 10;
  const d11 = d.slice(0, 10).reduce((a, b) => a + b, 0) % 10;
  return d[9] === d10 && d[10] === d11;
}

/** Istanbul takvim gününün [başlangıç, bitiş) UTC aralığı (backend IstanbulTime.DayRangeUtc). */
function dayRangeUtc(ymd) {
  const [y, m, d] = ymd.split("-").map(Number);
  const start = Date.UTC(y, m - 1, d) - ISTANBUL_OFFSET_MS;
  return [start, start + 24 * 60 * 60 * 1000];
}
function istanbulToday(nowMs = Date.now()) {
  return new Date(nowMs + ISTANBUL_OFFSET_MS).toISOString().slice(0, 10);
}
function addDays(ymd, n) {
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}
/** .NET UTC DateTime JSON biçimi: 2026-11-16T06:00:00Z */
const isoZ = (ms) => new Date(ms).toISOString().replace(".000Z", "Z");

// ---------------------------------------------------------------- demo verisi (DemoDataSeeder ile aynı şekil)
let db;
function seed() {
  // MedTech Özel Hastanesi demo kataloğu (backend DemoKatalog.cs ile aynı kodlar/hekimler; kurgusal).
  const clinics = [
    { id: uuid(), code: "DAHILIYE", name: "Dahiliye", synonyms: ["ic hastaliklari", "dahiliye poliklinigi", "ic hastaliklari poliklinigi"] },
    { id: uuid(), code: "KARDIYOLOJI", name: "Kardiyoloji", synonyms: ["kalp", "kalp damar", "kardiyoloji poliklinigi"] },
    { id: uuid(), code: "ORTOPEDI", name: "Ortopedi", synonyms: ["kemik", "ortopedi ve travmatoloji"] },
    { id: uuid(), code: "GOZ", name: "Göz Hastalıkları", synonyms: ["göz", "göz polikliniği", "goz", "goz poliklinigi"] },
    { id: uuid(), code: "KBB", name: "Kulak Burun Boğaz", synonyms: ["kbb", "kulak burun bogaz"] },
    { id: uuid(), code: "DERMATOLOJI", name: "Dermatoloji", synonyms: ["cildiye", "cilt"] },
    { id: uuid(), code: "KADIN_DOGUM", name: "Kadın Hastalıkları ve Doğum", synonyms: ["kadin dogum", "jinekoloji"] },
    { id: uuid(), code: "COCUK_SAGLIGI", name: "Çocuk Sağlığı ve Hastalıkları", synonyms: ["cocuk", "pediatri"] },
    { id: uuid(), code: "NOROLOJI", name: "Nöroloji", synonyms: ["noroloji"] },
    { id: uuid(), code: "UROLOJI", name: "Üroloji", synonyms: ["uroloji"] },
    { id: uuid(), code: "GENEL_CERRAHI", name: "Genel Cerrahi", synonyms: ["genel cerrahi", "cerrahi"] },
  ];
  // [klinik kodu, unvanlı tam ad, oda]. Görünen ad: unvan + ad + soyadının baş harfi ("Doç. Dr. Elif K.").
  const roster = [
    ["DAHILIYE", "Uzm. Dr. Mehmet Demir", "204"], ["KARDIYOLOJI", "Doç. Dr. Elif Kaya", "112"],
    ["ORTOPEDI", "Op. Dr. Ahmet Yıldırım", "305"], ["GOZ", "Uzm. Dr. Zeynep Arslan", "118"],
    ["KBB", "Op. Dr. Tolga Aksoy", "210"], ["DERMATOLOJI", "Uzm. Dr. Deniz Kara", "215"],
    ["KADIN_DOGUM", "Prof. Dr. Nermin Tunç", "301"], ["KADIN_DOGUM", "Op. Dr. Selin Ergin", "302"],
    ["COCUK_SAGLIGI", "Uzm. Dr. Burak Şen", "120"], ["COCUK_SAGLIGI", "Doç. Dr. Ceren Uysal", "121"],
    ["NOROLOJI", "Doç. Dr. Kerem Bilgin", "220"], ["UROLOJI", "Op. Dr. Serkan Tok", "310"],
    ["GENEL_CERRAHI", "Prof. Dr. Levent Sarı", "315"], ["DAHILIYE", "Uzm. Dr. Sevgi Akın", "206"],
  ];
  const displayOf = (full) => { const p = full.split(" "); return [...p.slice(0, -1), `${p[p.length - 1][0]}.`].join(" "); };
  const doctors = roster.map(([code, fullName, room]) => ({
    id: uuid(), clinicId: clinics.find((c) => c.code === code).id, fullName, room, displayName: displayOf(fullName),
  }));
  const slots = [];
  const today = istanbulToday();
  for (let i = 0; i < SLOT_DAYS; i++) {
    const [dayStart] = dayRangeUtc(addDays(today, i));
    for (const doc of doctors) {
      // Sabah 09:00–12:00, öğleden sonra 13:30–16:30 (başlangıçlar), hafta sonu dahil.
      for (const [from, to] of [[9 * 60, 12 * 60], [13 * 60 + 30, 16 * 60 + 30]]) {
        for (let minute = from; minute < to; minute += SLOT_MINUTES) {
          slots.push({ id: uuid(), clinicId: doc.clinicId, doctorId: doc.id, startMs: dayStart + minute * 60000, isTaken: false });
        }
      }
    }
  }
  // Bugünün ilk iki slotu her hekimde dolu (seed randevuları).
  const todayRange = dayRangeUtc(today);
  for (const doc of doctors) {
    slots.filter((s) => s.doctorId === doc.id && s.startMs >= todayRange[0] && s.startMs < todayRange[1])
      .sort((a, b) => a.startMs - b.startMs).slice(0, 2).forEach((s) => { s.isTaken = true; });
  }
  db = {
    clinics, doctors, slots,
    patients: [{ id: uuid(), patientRef: uuid(), tc: DEMO_TC }],
    sessions: new Map(),
    appointments: [],
    accessCodes: new Set(["246802"]),
    kb: KB_SEED.map(([term, title, body, kind, synonyms]) => ({ id: uuid(), term, title, body, kind, synonyms })),
    summaries: new Map(),
    history: seedHistory(today, clinics, doctors),
  };
}

/** DemoDataSeeder geçmiş ziyaretleri (demo hasta): ~118 gün önce Dahiliye ve ~46 gün önce Kardiyoloji onaylı,
 * ~9 gün önce Ortopedi onaysız (hiçbir zaman dönmez). */
function seedHistory(today, clinics, doctors) {
  const at = (days, h, mi) => isoZ(dayRangeUtc(addDays(today, -days))[0] + (h * 60 + mi) * 60000);
  return [
    { approved: true, appointmentId: uuid(), scheduledAt: at(118, 10, 15), clinicName: clinics[0].name, doctorDisplayName: doctors[0].displayName,
      diagnosisPlain: "Kan şekeriniz olması gerekenden yüksek seyrediyor (tip 2 diyabet). Ayrıca kanınızdaki demir deposu azalmış; halsizliğiniz bundan kaynaklanıyor olabilir. Düzenli ilaç kullanımı ve beslenme düzeniyle ikisi de kontrol altına alınabilir.",
      medications: [
        { name: "Metformin", purpose: "Kan şekerinizi dengeler.", usage: "Günde iki kez, sabah ve akşam yemekle birlikte birer tablet alın." },
        { name: "Demir sülfat", purpose: "Azalan demir deponuzu doldurur.", usage: "Günde bir kez, sabah aç karnına, portakal suyu gibi C vitamini içeren bir içecekle alın; süt ve çayla birlikte almayın." },
      ],
      warnings: ["Evde ölçtüğünüz kan şekeri değerlerini not edip kontrole getirin.", "Demir ilacı dışkınızın rengini koyulaştırabilir; bu beklenen bir durumdur.", "İlaçlarınızı kendi kararınızla bırakmayın."] },
    { approved: true, appointmentId: uuid(), scheduledAt: at(46, 9, 30), clinicName: clinics[1].name, doctorDisplayName: doctors[1].displayName,
      diagnosisPlain: "Tansiyonunuz sürekli olarak yüksek seyrediyor (yüksek tansiyon). Tuzu azaltmak ve ilacınızı her gün düzenli kullanmak kalbinizi ve damarlarınızı korur.",
      medications: [{ name: "Amlodipin", purpose: "Damarlarınızı gevşeterek tansiyonunuzu düşürür.", usage: "Günde bir kez, her gün aynı saatte bir tablet alın." }],
      warnings: ["Evde ölçtüğünüz tansiyon değerlerini kaydedin.", "Günlük tuz tüketiminizi azaltın."] },
    { approved: false, appointmentId: uuid(), scheduledAt: at(9, 11, 0), clinicName: clinics[2].name, doctorDisplayName: doctors[2].displayName,
      diagnosisPlain: "ONAY BEKLIYOR", medications: [{ name: "Parasetamol", purpose: "-", usage: "-" }], warnings: [] },
  ];
}
// seed() aşağıda, tüm sabitler (KB_SEED) tanımlandıktan sonra çağrılır.

// DemoDataSeeder.SeedKnowledgeBase ile aynı 16 kayıt (Türkçe karaktersiz, seed'deki gibi); id'ler her seed'de rastgele.
const KB_SEED = [
  ["Dispepsi", "Dispepsi (hazimsizlik)", "Midede yanma, sisme ve erken doyma ile giden hazimsizlik tablosu. Genellikle mide asidinin fazla uretilmesine baglidir.", "DIAGNOSIS", ["hazimsizlik", "mide sikayeti"]],
  ["Lansoprazol", "Lansoprazol", "Mide asidi uretimini azaltan ilac. Gunde bir kez, kahvaltidan yarim saat once ac karnina alinir.", "MEDICATION", ["lansor", "proton pompa inhibitoru"]],
  ["Gastrit", "Gastrit (mide iltihabi)", "Mide ic yuzeyinin tahris olmasi. Acili veya yanma hissi veren sikayetlere yol acar.", "DIAGNOSIS", ["mide iltihabi"]],
  ["Famotidin", "Famotidin", "Mide asidini azaltan ilac. Genellikle aksam yemeginden sonra alinir.", "MEDICATION", []],
  ["Hipertansiyon", "Hipertansiyon (yuksek tansiyon)", "Kan basincinin surekli yuksek seyretmesi. Tuz kisitlamasi ve duzenli ilac kullanimi gerektirir.", "DIAGNOSIS", ["yuksek tansiyon", "tansiyon"]],
  ["Amlodipin", "Amlodipin", "Damarlari gevseterek tansiyonu dusuren ilac. Gunde bir kez, her gun ayni saatte alinir.", "MEDICATION", []],
  ["Tip 2 Diyabet", "Tip 2 diyabet (seker hastaligi)", "Vucudun insulini yeterince kullanamamasi. Beslenme duzeni ve ilac ile kontrol altina alinir.", "DIAGNOSIS", ["seker hastaligi", "diyabet"]],
  ["Metformin", "Metformin", "Kan sekerini dengeleyen ilac. Mide sikayetini azaltmak icin yemekle birlikte alinir.", "MEDICATION", []],
  ["Demir Eksikligi Anemisi", "Demir eksikligi anemisi (kansizlik)", "Kandaki demir deposunun azalmasi. Halsizlik ve solukluk yapar.", "DIAGNOSIS", ["kansizlik", "anemi"]],
  ["Demir Sulfat", "Demir sulfat", "Demir deposunu dolduran ilac. C vitamini iceren meyve suyuyla, sut ve cay olmadan alinir.", "MEDICATION", ["demir ilaci"]],
  ["Migren", "Migren", "Zonklayici, genellikle tek tarafli bas agrisi. Isik ve sese hassasiyet eslik edebilir.", "DIAGNOSIS", []],
  ["Parasetamol", "Parasetamol", "Agri ve ates dusurucu ilac. Gunde en fazla belirtilen dozda ve tok karnina alinir.", "MEDICATION", ["agri kesici"]],
  ["Akut Sinuzit", "Akut sinuzit", "Burun cevresindeki sinuslerin iltihaplanmasi. Yuzde basinc hissi ve burun tikanikligi yapar.", "DIAGNOSIS", ["sinuzit"]],
  ["Amoksisilin", "Amoksisilin", "Bakteri kaynakli enfeksiyonlarda kullanilan antibiyotik. Kur yarida birakilmaz.", "MEDICATION", ["antibiyotik"]],
  ["Hipotiroidi", "Hipotiroidi (tiroid bezinin az calismasi)", "Tiroid hormonunun yetersiz uretilmesi. Halsizlik, usume ve kilo artisi yapabilir.", "DIAGNOSIS", ["tiroid"]],
  ["Levotiroksin", "Levotiroksin", "Eksik tiroid hormonunu yerine koyan ilac. Sabah ac karnina, bir bardak su ile alinir.", "MEDICATION", []],
];

// SeedFallbackSummaryProvider ile aynı tek kayıt (yalnız "dispepsi").
const FALLBACK_DISPEPSI = {
  diagnosisPlain: "Mideniz fazla asit uretiyor ve bu yuzden yanma, sisme gibi sikayetler oluyor. Ciddi bir hastalik degil; ilacinizi duzenli kullanirsaniz sikayetleriniz azalir.",
  medications: [{ name: "Lansoprazol", purpose: "Mide asidini azaltir.", usage: "Gunde bir kez, sabah kahvaltidan yarim saat once ac karnina alin." }],
  warnings: ["Sikayetleriniz iki hafta icinde gecmezse hekiminize tekrar basvurun.", "Ilaci kendi kararinizla birakmayin."],
  unknownTerms: [], sourceIds: [],
};

function summaryView(s) {
  const titles = new Map(db.kb.map((e) => [e.id, e.title]));
  const view = {
    summaryId: s.id, appointmentId: s.appointmentId, patientRef: s.patientRef, status: s.status,
    diagnosisPlain: s.diagnosisPlain, medications: s.medications, warnings: s.warnings, unknownTerms: s.unknownTerms,
    sources: s.sourceIds.map((id) => ({ id, title: titles.get(id) ?? id })),
    errorCode: s.failureCode, updatedAt: new Date(s.updatedAtMs).toISOString(),
  };
  for (const k of Object.keys(view)) if (view[k] === null || view[k] === undefined) delete view[k]; // WhenWritingNull
  return view;
}

function markReady(s, b) {
  if (s.status === "APPROVED") throw new DomainError("INVALID_STATUS_TRANSITION", "'Approved' durumundan 'Ready' durumuna gecilemez.");
  s.diagnosisPlain = b.diagnosisPlain;
  s.medications = (b.medications || []).map((m) => ({ name: m?.name ?? null, purpose: m?.purpose ?? null, usage: m?.usage ?? null }));
  s.warnings = b.warnings || []; s.unknownTerms = b.unknownTerms || []; s.sourceIds = b.sourceIds || [];
  s.failureCode = null; s.status = "READY"; s.updatedAtMs = Date.now();
}
function markFailed(s, code) {
  if (s.status === "APPROVED") throw new DomainError("INVALID_STATUS_TRANSITION", "'Approved' durumundan 'Failed' durumuna gecilemez.");
  s.failureCode = code; s.status = "FAILED"; s.updatedAtMs = Date.now();
}

/** Backend ClinicalEntryService.CreateAsync + DispatchSimplificationAsync taklidi. */
async function createClinicalEntry(body, requestId) {
  if (typeof body.diagnosis !== "string" || !body.diagnosis.trim()) throw new DomainError("VALIDATION_FAILED", "Tani bos olamaz.");
  const medications = Array.isArray(body.medications) ? body.medications.map(String) : [];
  const s = {
    id: uuid(), appointmentId: uuid(), patientRef: db.patients[0].patientRef, requestId, status: "PENDING",
    diagnosisPlain: null, medications: [], warnings: [], unknownTerms: [], sourceIds: [], failureCode: null,
    updatedAtMs: Date.now(), callbackCount: 0,
  };
  db.summaries.set(s.id, s);
  const payload = { patientRef: s.patientRef, diagnosis: body.diagnosis.trim(), medications, requestId, summaryId: s.id };
  let accepted = false;
  try {
    const r = await fetch(`${SIMPLIFY_URL}/simplify`, {
      method: "POST", body: JSON.stringify(payload), signal: AbortSignal.timeout(SIMPLIFY_TIMEOUT_MS),
      headers: { "Content-Type": "application/json", "X-Service-Key": SERVICE_KEY, "X-Request-Id": requestId },
    });
    accepted = r.ok; // backend gibi: yalnız 2xx'e bakılır, gövde okunmaz
    if (!accepted) console.warn(JSON.stringify({ service: "mock-api", event: "simplify-rejected", status: r.status, requestId }));
  } catch (e) {
    console.warn(JSON.stringify({ service: "mock-api", event: "simplify-unreachable", error: e.name, requestId }));
  }
  if (!accepted) {
    if (payload.diagnosis.toLowerCase().includes("dispepsi")) markReady(s, FALLBACK_DISPEPSI);
    else markFailed(s, "LLM_UNAVAILABLE");
  }
  return { clinicalEntryId: uuid(), summaryId: s.id, summaryStatus: s.status };
}

// ---------------------------------------------------------------- HTTP yardımcıları
function send(res, status, body, extraHeaders = {}) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, X-Request-Id, X-Service-Key",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,OPTIONS",
    ...extraHeaders,
  });
  res.end(body === undefined ? "" : JSON.stringify(body));
}
function sendError(res, requestId, code, message) {
  send(res, CODE_TO_STATUS[code] ?? 400, { error: { code, message, requestId } }, { "X-Request-Id": requestId });
}
function requireServiceKey(req) {
  if (!SERVICE_KEY) throw new Error("ServiceKey tanimli degil (SERVICE_KEY).");
  if (req.headers["x-service-key"] !== SERVICE_KEY) throw new DomainError("UNAUTHORIZED", "Gecersiz servis anahtari.");
}

// ---------------------------------------------------------------- oturumlar
function sessionView(s) {
  return { sessionId: s.id, verified: s.status === "VERIFIED" || s.status === "COMPLETED", attempts: s.attempts, maxAttempts: MAX_ATTEMPTS, status: s.status };
}
function loadSession(id) {
  const s = db.sessions.get(id.toLowerCase());
  if (!s) throw new DomainError("VOICE_SESSION_NOT_FOUND", "Ses oturumu bulunamadi.");
  // Gerçek backend: ACTIVE ve VERIFIED oturum açılıştan 15 dk sonra EXPIRED olur; COMPLETED görünümde kalır
  // ama süresi dolduysa geçmiş sorgusu reddedilir.
  if ((s.status === "ACTIVE" || s.status === "VERIFIED") && Date.now() >= s.expiresAt) s.status = "EXPIRED";
  return s;
}
function ensureActive(s) {
  if (s.status === "HANDED_OFF") throw new DomainError("VOICE_SESSION_EXHAUSTED", "Dogrulama deneme hakki doldu, gorusme operatore aktarilmali.");
  if (s.status !== "ACTIVE") throw new DomainError("INVALID_STATUS_TRANSITION", `'${s.status}' durumundan 'Active' durumuna gecilemez.`);
}

// ---------------------------------------------------------------- yönlendirme
async function route(req, res, body, requestId) {
  const url = new URL(req.url, "http://localhost");
  const path = url.pathname;
  let m;

  if (req.method === "GET" && (path === "/health" || path === "/api/v1/health")) return send(res, 200, { status: "ok" });

  // --- test uçları (yalnız sahte sunucu)
  if (req.method === "POST" && path === "/__mock/reset") { seed(); return send(res, 200, { reset: true }); }
  if (req.method === "POST" && (m = path.match(/^\/__mock\/slots\/([^/]+)\/take$/))) {
    const slot = db.slots.find((s) => s.id === m[1].toLowerCase());
    if (!slot) throw new DomainError("NOT_FOUND", "Slot yok.");
    slot.isTaken = true;
    return send(res, 200, { slotId: slot.id, isTaken: true });
  }

  if (req.method === "POST" && (m = path.match(/^\/__mock\/sessions\/([^/]+)\/expire$/))) {
    const s = loadSession(m[1]);
    s.expiresAt = Date.now() - 1;
    return send(res, 200, sessionView(loadSession(m[1])));
  }

  if (req.method === "POST" && path === "/__mock/clinical-entries") return send(res, 200, await createClinicalEntry(body, requestId));
  if (req.method === "GET" && path === "/__mock/summaries") {
    return send(res, 200, [...db.summaries.values()].map((s) => ({ ...summaryView(s), callbackCount: s.callbackCount })));
  }
  if ((m = path.match(/^\/__mock\/summaries\/([^/]+)(\/approve)?$/))) {
    const s = db.summaries.get(m[1].toLowerCase());
    if (!s) throw new DomainError("NOT_FOUND", "Ozet kaydi bulunamadi.");
    if (m[2] && req.method === "POST") {
      if (s.status !== "READY") throw new DomainError("INVALID_STATUS_TRANSITION", `'${s.status}' durumundan 'Approved' durumuna gecilemez.`);
      s.status = "APPROVED"; s.updatedAtMs = Date.now();
    }
    return send(res, 200, { ...summaryView(s), callbackCount: s.callbackCount });
  }

  // --- bilgi tabanı (X-Service-Key)
  if (req.method === "GET" && path === "/api/v1/kb") {
    requireServiceKey(req);
    return send(res, 200, db.kb);
  }

  // --- özet: AI callback (X-Service-Key); okuma hekim JWT'si ister
  if ((m = path.match(/^\/api\/v1\/summaries\/([^/]+)$/))) {
    if (!GUID_RE.test(m[1])) throw new DomainError("NOT_FOUND", "Kaynak bulunamadı.");
    if (req.method === "GET") throw new DomainError("UNAUTHORIZED", "Kimlik doğrulaması gerekli.");
    if (req.method === "PUT") {
      requireServiceKey(req);
      const s = db.summaries.get(m[1].toLowerCase());
      if (!s) throw new DomainError("NOT_FOUND", "Ozet kaydi bulunamadi.");
      s.callbackCount++;
      if (typeof body.errorCode === "string" && body.errorCode.trim()) {
        if (body.errorCode.length > 40) throw new Error("failure_code sutun siniri");
        markFailed(s, body.errorCode);
      } else {
        if (typeof body.diagnosisPlain !== "string" || !body.diagnosisPlain.trim()) throw new DomainError("VALIDATION_FAILED", "diagnosisPlain bos olamaz.");
        if (body.diagnosisPlain.length > 2000) throw new Error("diagnosis_plain sutun siniri");
        markReady(s, body);
      }
      return send(res, 200, summaryView(s));
    }
  }

  // --- ses oturumları
  if (req.method === "POST" && path === "/api/v1/voice-sessions") {
    const s = { id: uuid(), attempts: 0, status: "ACTIVE", patientId: null, expiresAt: Date.now() + SESSION_LIFETIME_MS };
    db.sessions.set(s.id, s);
    return send(res, 200, sessionView(s));
  }
  if (req.method === "GET" && (m = path.match(/^\/api\/v1\/voice-sessions\/([^/]+)\/medical-history$/))) {
    if (!GUID_RE.test(m[1])) throw new DomainError("NOT_FOUND", "Kaynak bulunamadı.");
    requireServiceKey(req);
    const raw = url.searchParams.get("limit") ?? "3";
    if (!/^\d+$/.test(raw) || Number(raw) < 1 || Number(raw) > 5) throw new DomainError("VALIDATION_FAILED", "limit 1 ile 5 arasinda olmalidir.");
    const s = loadSession(m[1]);
    const verified = (s.status === "VERIFIED" || s.status === "COMPLETED") && s.patientId && Date.now() < s.expiresAt;
    if (!verified) throw new DomainError("VOICE_SESSION_NOT_VERIFIED", "Gecmis muayeneler icin kimlik dogrulamasi gerekir.");
    const visits = db.history.filter((v) => v.approved)
      .sort((a, b) => b.scheduledAt.localeCompare(a.scheduledAt))
      .slice(0, Number(raw))
      .map(({ approved: _onay, ...v }) => v);
    return send(res, 200, { visits });
  }
  if ((m = path.match(/^\/api\/v1\/voice-sessions\/([^/]+)(\/verify)?$/))) {
    if (!GUID_RE.test(m[1])) throw new DomainError("NOT_FOUND", "Kaynak bulunamadı."); // rota kısıtı: artık zarflı 404
    if (!m[2] && req.method === "GET") return send(res, 200, sessionView(loadSession(m[1])));
    if (m[2] && req.method === "POST") {
      const s = loadSession(m[1]);
      ensureActive(s); // süresi dolmuş (EXPIRED) oturum → 409 INVALID_STATUS_TRANSITION
      // Biçimsiz TC deneme sayılmaz: 400 VALIDATION_FAILED, sayaç artmaz.
      if (!tcWellFormed(body.tc)) throw new DomainError("VALIDATION_FAILED", "TC kimlik numarası geçerli biçimde değil.");
      const patient = db.patients.find((p) => p.tc === body.tc);
      if (patient) { s.patientId = patient.id; s.status = "VERIFIED"; }
      else if (++s.attempts >= MAX_ATTEMPTS) s.status = "HANDED_OFF"; // yalnız başarısız denemeler sayılır
      return send(res, 200, { ...sessionView(s), handoff: s.status === "HANDED_OFF" });
    }
  }

  // --- slotlar (X-Service-Key)
  if (req.method === "GET" && path === "/api/v1/slots") {
    requireServiceKey(req);
    const date = url.searchParams.get("date") || "";
    const clinicParam = (url.searchParams.get("clinic") || "").trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(date))) throw new DomainError("VALIDATION_FAILED", "date parametresi YYYY-MM-DD biciminde olmalidir.");
    if (!clinicParam) throw new DomainError("VALIDATION_FAILED", "clinic parametresi zorunludur.");
    const needle = clinicParam.toLowerCase();
    const clinic = db.clinics.find((c) => c.code.toLowerCase() === needle || c.name.toLowerCase() === needle)
      || db.clinics.find((c) => c.synonyms.some((s) => s.toLowerCase() === needle));
    if (!clinic) throw new DomainError("NOT_FOUND", `Poliklinik bulunamadi: '${clinicParam}'.`);
    const [from, to] = dayRangeUtc(date);
    const list = db.slots
      .filter((s) => s.clinicId === clinic.id && !s.isTaken && s.startMs >= from && s.startMs < to && s.startMs > Date.now())
      .sort((a, b) => a.startMs - b.startMs)
      .map((s) => ({ slotId: s.id, startTime: isoZ(s.startMs), doctorDisplayName: db.doctors.find((d) => d.id === s.doctorId)?.displayName ?? "", doctorId: s.doctorId }));
    return send(res, 200, list);
  }

  // --- sesli randevu (X-Service-Key)
  if (req.method === "POST" && path === "/api/v1/appointments") {
    requireServiceKey(req);
    if (!GUID_RE.test(body.voiceSessionId || "")) throw new DomainError("VALIDATION_FAILED", "Gecersiz voiceSessionId.");
    if (!GUID_RE.test(body.slotId || "")) throw new DomainError("VALIDATION_FAILED", "Gecersiz slotId.");
    const s = db.sessions.get(body.voiceSessionId.toLowerCase());
    if (!s) throw new DomainError("VOICE_SESSION_NOT_FOUND", "Ses oturumu bulunamadi.");
    if (s.status === "VERIFIED" && Date.now() >= s.expiresAt) s.status = "EXPIRED";
    if (s.status !== "VERIFIED" || !s.patientId) throw new DomainError("VOICE_SESSION_NOT_VERIFIED", "Randevu olusturmak icin kimlik dogrulamasi gerekir.");
    const slot = db.slots.find((x) => x.id === body.slotId.toLowerCase());
    if (!slot) throw new DomainError("NOT_FOUND", "Secilen saat bulunamadi.");
    if (slot.startMs <= Date.now()) throw new DomainError("SLOT_IN_PAST", "Seçilen saat geçmişte kaldı; lütfen ileri bir saat seçin.");
    if (slot.isTaken) throw new DomainError("SLOT_TAKEN", "Seçilen saat başka bir hasta tarafından alındı.");
    ensureNoConflict(s.patientId, slot);
    slot.isTaken = true;
    let code;
    do { code = String(crypto.randomInt(0, 1000000)).padStart(6, "0"); } while (db.accessCodes.has(code));
    db.accessCodes.add(code);
    const appt = { id: uuid(), patientId: s.patientId, slotId: slot.id, clinicId: slot.clinicId, scheduledAtMs: slot.startMs, accessCode: code, status: "SCHEDULED" };
    db.appointments.push(appt);
    s.status = "COMPLETED";
    const doctor = db.doctors.find((d) => d.id === slot.doctorId);
    const clinic = db.clinics.find((c) => c.id === slot.clinicId);
    return send(res, 200, {
      appointmentId: appt.id, slotId: slot.id, scheduledAt: isoZ(appt.scheduledAtMs),
      doctorDisplayName: doctor?.displayName ?? "", clinicName: clinic?.name ?? "", status: "SCHEDULED", accessCode: code,
    });
  }

  throw new DomainError("NOT_FOUND", "Kaynak bulunamadı.");
}

/** Backend AppointmentBookingService.EnsureNoConflictAsync: önce saat çakışması, sonra aynı gün aynı poliklinik. */
function ensureNoConflict(patientId, slot) {
  const dur = SLOT_MINUTES * 60000;
  const active = db.appointments.filter((a) => a.patientId === patientId && a.status === "SCHEDULED");
  if (active.some((a) => a.scheduledAtMs < slot.startMs + dur && a.scheduledAtMs + dur > slot.startMs)) {
    throw new DomainError("APPOINTMENT_CONFLICT", "Bu saatte başka bir randevunuz var; lütfen farklı bir saat seçin.");
  }
  const [dayStart, dayEnd] = dayRangeUtc(istanbulToday(slot.startMs));
  if (active.some((a) => a.clinicId === slot.clinicId && a.scheduledAtMs >= dayStart && a.scheduledAtMs < dayEnd)) {
    throw new DomainError("APPOINTMENT_CONFLICT", "Aynı gün bu poliklinikte zaten randevunuz var; lütfen başka bir gün seçin.");
  }
}

seed();
http.createServer((req, res) => {
  if (req.method === "OPTIONS") return send(res, 204);
  const requestId = /^[A-Za-z0-9]{8,64}$/.test(req.headers["x-request-id"] || "") ? req.headers["x-request-id"] : uuid().replace(/-/g, "");
  const started = Date.now();
  let raw = "";
  req.on("data", (c) => { raw += c; });
  req.on("end", async () => {
    let body = {};
    try { body = raw ? JSON.parse(raw) : {}; } catch {
      // Güncel backend: bozuk JSON da hata zarfıyla 400 VALIDATION_FAILED (eskiden ProblemDetails idi).
      return sendError(res, requestId, "VALIDATION_FAILED", "Geçersiz JSON gövdesi.");
    }
    try {
      await route(req, res, body || {}, requestId);
    } catch (e) {
      if (e instanceof DomainError) sendError(res, requestId, e.code, e.message);
      else send(res, 500, { error: { code: "INTERNAL_ERROR", message: "Beklenmeyen bir hata olustu.", requestId } });
    } finally {
      // Gövde loglanmaz (TC/tanı içerebilir); yalnız yöntem, yol, durum, süre.
      console.log(JSON.stringify({ service: "mock-api", requestId, method: req.method, path: req.url.split("?")[0], status: res.statusCode, ms: Date.now() - started }));
    }
  });
}).listen(PORT, () => console.log(`Sahte HastaKabul API http://localhost:${PORT} (demo TC ${DEMO_TC})`));
