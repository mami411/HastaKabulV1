/**
 * Ses orkestratörü istemcisi (ai-voice, :8002).
 *
 * Orkestratör yalnızca metinle çalışır ve slot/randevu çağrılarını kendi
 * X-Service-Key'i ile API'ye yapar. Sahte orkestratöre YALNIZCA yan etkisiz
 * olaylarda (START, TC_VERIFIED, SLOT_SELECTED) ve servise hiç ulaşılamadığında
 * düşülür; bu ekranda belirtilir. Onay (CONFIRM) ve serbest metin hiçbir zaman
 * sahte yanıtla karşılanmaz: istek sunucuya ulaşıp randevu oluşturmuş olabilir,
 * bu yüzden sonuç "doğrulanamadı" olarak bildirilir.
 * Servis 4xx/5xx döndüğünde yanıttaki Türkçe metin ve eylem kullanılır.
 */

import { BRAND } from "../brand";
import { ORCHESTRATOR_TIMEOUT_MS, ORCHESTRATOR_URL } from "../config";
import { mockOrchestrator } from "./mockOrchestrator";

export type OrchestratorEvent =
  | "START"
  | "TC_VERIFIED"
  | "SLOT_SELECTED"
  | "CONFIRM"
  /** Oturum bitti (çıkış/kilit): orkestratör oturum belleğini ve sağlık verisini siler. */
  | "END"
  /** Uygulama açık, kullanıcı başka sayfada: orkestratörün boşta kalma saati ileri alınır. */
  | "KEEPALIVE";

export type OrchestratorAction =
  | "ASK_TC"
  | "OFFER_SLOTS"
  | "CONFIRM"
  | "HANDOFF"
  | "DONE";

const ACTIONS: readonly OrchestratorAction[] = [
  "ASK_TC",
  "OFFER_SLOTS",
  "CONFIRM",
  "HANDOFF",
  "DONE",
];

/** Sahte yanıta düşmenin güvenli olduğu (yan etkisiz) olaylar. */
const MOCK_SAFE_EVENTS: readonly OrchestratorEvent[] = [
  "START",
  "TC_VERIFIED",
  "SLOT_SELECTED",
];

export type OrchestratorSlot = {
  slotId: string;
  label: string;
  /** Poliklinik · hekim (ör. "Kardiyoloji · Elif K."); yoksa boş. */
  detail?: string;
};

export type OrchestratorInput = {
  sessionId: string;
  userText?: string;
  event?: OrchestratorEvent;
  data?: {
    slotId?: string;
  };
};

export type OrchestratorResponse = {
  /** Hatalı isteklerde eylem gelmeyebilir; arayüz bulunduğu adımda kalır. */
  action: OrchestratorAction | null;
  /** OFFER_SLOTS → { slots }, CONFIRM → { slotId, label }, DONE → { accessCode, label, ... }, HANDOFF → { reason } */
  data: Record<string, unknown>;
  message: string;
  /** Gerçek servis yerine sahte orkestratör yanıt verdiyse true. */
  simulated: boolean;
  /** Servise ulaşılamadı ya da zaman aşımı oldu (sonuç bilinmiyor). */
  unreachable?: boolean;
  /** Orkestratör hata döndürdüyse kod (SLOT_TAKEN, API_UNAVAILABLE, LLM_UNAVAILABLE...). */
  errorCode?: string;
};

/** Yanıttaki replyText/message alan adı farkını tek yerde soğurur. */
type RawResponse = {
  action?: string | null;
  data?: Record<string, unknown>;
  replyText?: string;
  message?: string;
  error?: { code?: string; message?: string };
};

function normalize(raw: RawResponse, status: number): OrchestratorResponse {
  const action = ACTIONS.find((item) => item === raw.action) ?? null;
  const errorCode = raw.error?.code ?? (status >= 400 ? "UNKNOWN" : undefined);

  return {
    action,
    data: raw.data ?? {},
    message:
      raw.replyText ??
      raw.message ??
      (errorCode ? "İşleminiz şu an tamamlanamadı. Lütfen tekrar deneyin." : ""),
    simulated: false,
    errorCode,
  };
}

export const UNREACHABLE_MESSAGE =
  "Asistana şu an ulaşılamıyor. Bağlantınızı kontrol edip birazdan tekrar deneyin.";

export const BOOKING_UNCERTAIN_MESSAGE =
  "Randevu işleminizin sonucu doğrulanamadı. Aynı saati tekrar almaya çalışmayın; randevunuzu Randevularım " +
  `sayfasından ya da ${BRAND.shortName} çağrı merkezini (${BRAND.callCenter.display}) arayarak kontrol edebilirsiniz.`;

export async function askOrchestrator(
  input: OrchestratorInput,
): Promise<OrchestratorResponse> {
  let response: Response;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ORCHESTRATOR_TIMEOUT_MS);

  try {
    response = await fetch(`${ORCHESTRATOR_URL}/orchestrate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal: controller.signal,
    });
  } catch {
    clearTimeout(timer);
    if (input.event && MOCK_SAFE_EVENTS.includes(input.event)) {
      // Yan etkisiz olay: akış kesilmesin diye sahte yanıta düşülür (ekranda belirtilir).
      const fallback = await mockOrchestrator(input);
      return { ...fallback, simulated: true };
    }
    // Onay ya da serbest metin: istek sunucuya ulaşmış olabilir, sonuç bilinmiyor.
    return {
      action: null,
      data: {},
      message:
        input.event === "CONFIRM" ? BOOKING_UNCERTAIN_MESSAGE : UNREACHABLE_MESSAGE,
      simulated: false,
      unreachable: true,
      errorCode: "ORCHESTRATOR_UNREACHABLE",
    };
  }

  let raw: RawResponse = {};
  try {
    raw = (await response.json()) as RawResponse;
  } catch {
    /* gövde JSON değil */
  } finally {
    clearTimeout(timer);
  }

  return normalize(raw, response.status);
}

/** OFFER_SLOTS / CONFIRM yanıtındaki slot listesini güvenli biçimde çıkarır. */
export function readSlots(data: Record<string, unknown>): OrchestratorSlot[] {
  const slots = data.slots;
  if (!Array.isArray(slots)) return [];

  return slots.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];

    const { slotId, label, detail } = item as Partial<OrchestratorSlot>;
    if (typeof slotId !== "string" || typeof label !== "string") return [];

    return [{ slotId, label, detail: typeof detail === "string" ? detail : "" }];
  });
}

/** DONE yanıtındaki randevu bilgisi; erişim kodu yoksa randevu oluşmamıştır. */
export type BookedAppointment = {
  accessCode: string;
  label: string;
  /** Uzun tarih/saat ("16 Kasım Pazartesi 09:00"); yoksa label kullanılır. */
  longLabel: string;
  clinicName: string;
  doctorDisplayName: string;
};

export function readAppointment(
  data: Record<string, unknown>,
): BookedAppointment | null {
  const { accessCode, label, longLabel, clinicName, doctorDisplayName } = data;
  if (typeof accessCode !== "string" || !accessCode) return null;

  return {
    accessCode,
    label: typeof label === "string" ? label : "",
    longLabel: typeof longLabel === "string" ? longLabel : "",
    clinicName: typeof clinicName === "string" ? clinicName : "",
    doctorDisplayName:
      typeof doctorDisplayName === "string" ? doctorDisplayName : "",
  };
}

/** Orkestratörün o adımda beklediği yanıt türü (data.expect). */
export type ExpectedInput =
  | "tc"
  | "poliklinik"
  | "hekim"
  | "gun"
  | "saat"
  | "secim"
  | "onay"
  | "";

export function readExpect(data: Record<string, unknown>): ExpectedInput {
  const value = data.expect;
  return value === "tc" ||
    value === "poliklinik" ||
    value === "hekim" ||
    value === "gun" ||
    value === "saat" ||
    value === "secim" ||
    value === "onay"
    ? value
    : "";
}

/** Yanıttan sonra mikrofon kendiliğinden yeniden açılsın mı (data.listen). */
export function readListen(data: Record<string, unknown>): boolean {
  return data.listen === true;
}

/** Şikâyetten önerilen poliklinik (data.suggestion); yoksa null. */
export type ClinicSuggestion = { clinic: string; clinicName: string };

export function readSuggestion(data: Record<string, unknown>): ClinicSuggestion | null {
  const value = data.suggestion;
  if (typeof value !== "object" || value === null) return null;
  const { clinic, clinicName } = value as Record<string, unknown>;
  if (typeof clinicName !== "string" || !clinicName) return null;
  return { clinic: typeof clinic === "string" ? clinic : "", clinicName };
}

/** Acil durum ifadesi algılandı mı (data.emergency); yanıt metni 112 yönlendirmesidir. */
export function readEmergency(data: Record<string, unknown>): boolean {
  return data.emergency === true;
}

/**
 * Yanıt metni yerel yapay zekânın ürettiği genel sağlık bilgisi mi (data.aiGeneralInfo; geriye uyumlu ek
 * alan). Arayüz balonun altında "Genel bilgi · doktorunuza danışın" etiketi gösterir.
 */
export function readAiGeneralInfo(data: Record<string, unknown>): boolean {
  return data.aiGeneralInfo === true;
}

/**
 * Ekranda dokunulabilir hızlı seçenekler (poliklinik/hekim/gün/saat adları). Liste kesilmez (11 poliklinikte
 * son üçü kayboluyordu); yalnız bozuk/aşırı uzun yanıta karşı üst sınır vardır. Arayüz uzun listeyi sarar ve
 * kaydırır (Composer `.chips-many`).
 */
export const MAX_CHOICES = 24;

export function readChoices(data: Record<string, unknown>): string[] {
  const choices = data.choices;
  if (!Array.isArray(choices)) return [];
  const seen = new Set<string>();
  return choices
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .filter((item) => (seen.has(item) ? false : (seen.add(item), true)))
    .slice(0, MAX_CHOICES);
}

export type HistoryMedication = { name: string; purpose: string; usage: string };
export type HistoryVisit = {
  date: string;
  clinicName: string;
  doctorDisplayName: string;
  diagnosisPlain: string;
  medications: HistoryMedication[];
  warnings: string[];
};
export type HistoryData = {
  visits: HistoryVisit[];
  /** Soru belirli ilaçlarla ilgiliyse adları (kartta vurgulanır). */
  focus: string[];
  /** Metin sesli okundu mu (mahremiyet ayarında yalnız ekranda gösterilir). */
  spoken: boolean;
};

const str = (value: unknown) => (typeof value === "string" ? value : "");

/** Geçmiş muayene kartı verisi (data.history); biçim bozuksa null. */
export function readHistory(data: Record<string, unknown>): HistoryData | null {
  const history = data.history;
  if (typeof history !== "object" || history === null) return null;
  const raw = history as Record<string, unknown>;
  if (!Array.isArray(raw.visits)) return null;

  const visits = raw.visits.flatMap((item): HistoryVisit[] => {
    if (typeof item !== "object" || item === null) return [];
    const v = item as Record<string, unknown>;
    const medications = Array.isArray(v.medications)
      ? v.medications.flatMap((m): HistoryMedication[] => {
          if (typeof m !== "object" || m === null) return [];
          const med = m as Record<string, unknown>;
          return str(med.name) ? [{ name: str(med.name), purpose: str(med.purpose), usage: str(med.usage) }] : [];
        })
      : [];
    const warnings = Array.isArray(v.warnings)
      ? v.warnings.filter((w): w is string => typeof w === "string" && w.length > 0)
      : [];
    return [
      {
        date: str(v.date),
        clinicName: str(v.clinicName),
        doctorDisplayName: str(v.doctorDisplayName),
        diagnosisPlain: str(v.diagnosisPlain),
        medications,
        warnings,
      },
    ];
  });

  return {
    visits,
    focus: Array.isArray(raw.focus) ? raw.focus.filter((f): f is string => typeof f === "string") : [],
    spoken: raw.spoken !== false,
  };
}

/**
 * Oturumu orkestratörde kapatır (END). Sonucu beklenmez; sayfa kapanırken de gitsin diye keepalive.
 * Hata yutulur: orkestratör zaten hareketsiz oturumu kendisi siler.
 */
export function endOrchestratorSession(sessionId: string): void {
  if (!sessionId) return;
  void fetch(`${ORCHESTRATOR_URL}/orchestrate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessionId, event: "END" }),
    keepalive: true,
  }).catch(() => undefined);
}

/**
 * Uygulama açık ve kilitlenmemiş (kullanıcı asistan dışındaki sayfalarda geziyor): orkestratörün boşta
 * kalma saati ileri alınır (KEEPALIVE). Durum/seçim değişmez, yanıt beklenmez, hata yutulur. Asıl
 * hareketsizlik kilidi ön yüzdedir (5 dk dokunma yoksa END gider).
 */
export function keepOrchestratorAlive(sessionId: string): void {
  if (!sessionId) return;
  void fetch(`${ORCHESTRATOR_URL}/orchestrate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessionId, event: "KEEPALIVE" }),
  }).catch(() => undefined);
}

/** Metindeki rakam sayısı (TC benzeri girişi istemcide engellemek için). */
export function digitCount(text: string): number {
  return (text.match(/\d/g) ?? []).length;
}
