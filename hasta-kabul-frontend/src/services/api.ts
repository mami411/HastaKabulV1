/**
 * Hasta Kabul API istemcisi.
 *
 * Tarayıcının çağırmasına izin verilen uçlar burada toplanır. Orkestratörün X-Service-Key ile çağırdığı
 * uçlar (`/slots`, `POST /appointments`, `/medical-history`) burada yoktur; servis anahtarı tarayıcıya
 * konmaz. Asistansız sayfalar oturuma bağlı hasta uçlarını kullanır: `/voice-sessions/{id}/(profile|
 * clinics|slot-days|slots|appointments|visits)`. Kimlik, TC tuş takımıyla doğrulanan oturumdur; hasta
 * oturumdan bulunur, istek hasta kimliği taşımaz.
 */

import { API_URL } from "../config";

/** Sözleşmedeki ortak hata zarfı: { error: { code, message, requestId } } */
export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    requestId: string;
  };
};

export class ApiError extends Error {
  readonly code: string;
  readonly requestId: string;
  readonly status: number;

  constructor(status: number, code: string, message: string, requestId: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export type VoiceSession = {
  sessionId: string;
  verified: boolean;
  attempts: number;
  maxAttempts: number;
  status: string;
};

export type VoiceVerification = VoiceSession & {
  /** Deneme hakkı bittiğinde true; görüşme operatöre aktarılmalı. */
  handoff: boolean;
};

/** Her istekte yeni bir X-Request-Id üretilir; tüm servisler aynı kimliği taşır. */
function newRequestId(): string {
  if (crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");

  return Array.from(crypto.getRandomValues(new Uint8Array(16)))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;

  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Request-Id": newRequestId(),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Sunucuya ulaşılamadı.", "");
  }

  if (!response.ok) {
    // Hata gövdesi okunamazsa da anlamlı bir kod döndür.
    let body: Partial<ApiErrorBody> = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      /* gövde boş ya da JSON değil */
    }

    throw new ApiError(
      response.status,
      body.error?.code ?? "UNKNOWN",
      body.error?.message ?? "Beklenmeyen bir hata oluştu.",
      body.error?.requestId ?? "",
    );
  }

  if (response.status === 204) return undefined as T;

  return (await response.json()) as T;
}

/* ---------------------------------------------------------------- *
 * Ses oturumu — TC el sıkışması
 * ---------------------------------------------------------------- */

export function createVoiceSession(): Promise<VoiceSession> {
  return request<VoiceSession>("/api/v1/voice-sessions", { method: "POST" });
}

/** Oturumun güncel durumu (ACTIVE, VERIFIED, EXPIRED...); 409 sonrası ayrım için. */
export function getVoiceSession(sessionId: string): Promise<VoiceSession> {
  return request<VoiceSession>(`/api/v1/voice-sessions/${sessionId}`);
}

/**
 * TC kimlik numarası biçim denetimi (11 hane, ilk hane 0 değil, 10. ve 11. hane algoritması).
 * Yalnız biçimi denetler; kimin numarası olduğunu backend doğrular. Biçimi bozuk numara gönderilmez.
 */
export function isValidTcFormat(tc: string): boolean {
  if (!/^[1-9]\d{10}$/.test(tc)) return false;
  const d = [...tc].map(Number);
  const odd = d[0] + d[2] + d[4] + d[6] + d[8];
  const even = d[1] + d[3] + d[5] + d[7];
  const tenth = (((odd * 7 - even) % 10) + 10) % 10;
  const eleventh = d.slice(0, 10).reduce((sum, value) => sum + value, 0) % 10;
  return d[9] === tenth && d[10] === eleventh;
}

/**
 * TC doğrudan API'ye gönderilir; orkestratör bu isteği hiç görmez.
 * Yanıt yalnızca doğrulandı mı, kaçıncı deneme ve HANDOFF gerekip gerekmediğini söyler.
 */
export function verifyVoiceSession(sessionId: string, tc: string): Promise<VoiceVerification> {
  return request<VoiceVerification>(`/api/v1/voice-sessions/${sessionId}/verify`, {
    method: "POST",
    body: JSON.stringify({ tc }),
  });
}

/**
 * Çıkış: oturum backend'de de kapatılır (POST /voice-sessions/{id}/end → 204). Bundan sonra oturum
 * kimliğini bilen biri hasta uçlarını çağıramaz. Sayfa kapanırken de gitsin diye keepalive; sonuç
 * beklenmez, hata yok sayılır (oturum en geç 15 dk sonra kendiliğinden kapanır). Gövdesiz ve özel başlıksız
 * "basit" istektir: CORS ön isteği gerekmez, sayfa kapanırken de güvenle gider.
 */
export function endVoiceSession(sessionId: string): void {
  if (!sessionId) return;
  void fetch(`${API_URL}/api/v1/voice-sessions/${encodeURIComponent(sessionId)}/end`, {
    method: "POST",
    keepalive: true,
  }).catch(() => undefined);
}

/* ---------------------------------------------------------------- *
 * Hasta uygulaması (asistansız sayfalar) — oturuma bağlı uçlar
 * ---------------------------------------------------------------- */

export type PatientProfile = {
  /** Maskeli ad ("Ayşe H."); tam ad ve TC hiç gelmez. */
  displayName: string;
  sessionExpiresAt: string;
  /** Sunucu saatiyle oturumun kapanmasına kalan süre (sn). */
  sessionRemainingSeconds: number;
};

export type PatientDoctor = { doctorId: string; displayName: string };
export type PatientClinic = { code: string; name: string; doctors: PatientDoctor[] };
/** date: Europe/Istanbul günü (YYYY-AA-GG). */
export type SlotDay = { date: string; availableCount: number };
export type PatientSlot = {
  slotId: string;
  startTime: string;
  doctorId: string;
  doctorDisplayName: string;
};
export type PatientAppointment = {
  appointmentId: string;
  scheduledAt: string;
  clinicName: string;
  doctorDisplayName: string;
  room: string;
  /** SCHEDULED | CHECKED_IN | IN_EXAM */
  status: string;
  accessCode: string;
};
/** Asistandaki randevu yanıtıyla aynı biçim (erişim kodu dahil). */
export type CreatedAppointment = {
  appointmentId: string;
  slotId: string;
  scheduledAt: string;
  doctorDisplayName: string;
  clinicName: string;
  status: string;
  accessCode: string;
};
export type VisitMedication = { name: string; purpose: string; usage: string };
/** Hekim onaylı özet; onaysız içerik bu uçtan hiç gelmez. */
export type Visit = {
  appointmentId: string;
  scheduledAt: string;
  clinicName: string;
  doctorDisplayName: string;
  diagnosisPlain: string;
  medications: VisitMedication[];
  warnings: string[];
};

const sessionPath = (sessionId: string, path: string) =>
  `/api/v1/voice-sessions/${encodeURIComponent(sessionId)}/${path}`;

export function getProfile(sessionId: string): Promise<PatientProfile> {
  return request<PatientProfile>(sessionPath(sessionId, "profile"));
}

export function getClinics(sessionId: string): Promise<PatientClinic[]> {
  return request<PatientClinic[]>(sessionPath(sessionId, "clinics"));
}

export function getSlotDays(sessionId: string, clinic: string, doctorId?: string): Promise<SlotDay[]> {
  const query = new URLSearchParams({ clinic });
  if (doctorId) query.set("doctorId", doctorId);
  return request<SlotDay[]>(sessionPath(sessionId, `slot-days?${query}`));
}

export function getSlots(
  sessionId: string,
  clinic: string,
  date: string,
  doctorId?: string,
): Promise<PatientSlot[]> {
  const query = new URLSearchParams({ clinic, date });
  if (doctorId) query.set("doctorId", doctorId);
  return request<PatientSlot[]>(sessionPath(sessionId, `slots?${query}`));
}

export function getAppointments(sessionId: string): Promise<{ upcoming: PatientAppointment[] }> {
  return request<{ upcoming: PatientAppointment[] }>(sessionPath(sessionId, "appointments"));
}

/** Randevu yalnız açık onayla (özet ekranındaki düğme) oluşturulur; asistanla aynı kurallar. */
export function createAppointment(sessionId: string, slotId: string): Promise<CreatedAppointment> {
  return request<CreatedAppointment>(sessionPath(sessionId, "appointments"), {
    method: "POST",
    body: JSON.stringify({ slotId }),
  });
}

export function getVisits(sessionId: string, limit = 10): Promise<{ visits: Visit[] }> {
  return request<{ visits: Visit[] }>(sessionPath(sessionId, `visits?limit=${limit}`));
}
