import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  getAppointments,
  getProfile,
  getVisits,
  type PatientAppointment,
  type PatientProfile,
  type Visit,
} from "../services/api";
import type { Assistant } from "./useAssistant";

/** Asistansız sayfalarda gösterilecek anlaşılır hata metni. */
export const API_UNREACHABLE =
  "Hastane sistemine şu an ulaşılamıyor. Bağlantınızı kontrol edip birazdan tekrar deneyin.";

type Loadable<T> = { data: T | null; loading: boolean; error: string };

const idle = <T,>(): Loadable<T> => ({ data: null, loading: false, error: "" });

/**
 * Oturum bittiyse (süre doldu, oturum bulunamadı) girişe döner ve true verir; aksi halde ekranda
 * gösterilecek metni döndürür. Asistansız sayfaların tüm API hataları buradan geçer.
 */
export function useSessionErrors(a: Assistant) {
  const { sessionId, sessionLost } = a;
  const lostRef = useRef(sessionLost);
  useEffect(() => {
    lostRef.current = sessionLost;
  });

  return useCallback(
    (error: unknown, forSessionId = sessionId): string | null => {
      if (error instanceof ApiError) {
        if (error.code === "VOICE_SESSION_NOT_VERIFIED") {
          lostRef.current(forSessionId, "expired");
          return null;
        }
        if (error.code === "VOICE_SESSION_NOT_FOUND") {
          lostRef.current(forSessionId, "ended");
          return null;
        }
        if (error.status === 0 || error.status >= 500) return API_UNREACHABLE;
        return error.message;
      }
      return API_UNREACHABLE;
    },
    [sessionId],
  );
}

/**
 * Ana sayfa, Randevularım, İlaçlarım ve asistan sayfasının paylaştığı hasta verisi. Her sayfa açılışta
 * yeniler (asistanda alınan randevu diğer sayfalarda, diğerlerinde alınan asistan sayfasında görünür;
 * kaynak backend'dir). Veri yalnız bellekte durur; çıkışta kabuk kaldırılınca silinir.
 */
export function usePatientData(a: Assistant) {
  const { sessionId, sessionLost } = a;
  const handleError = useSessionErrors(a);
  const [profile, setProfile] = useState<PatientProfile | null>(null);
  const [appointments, setAppointments] = useState<Loadable<PatientAppointment[]>>(idle);
  const [visits, setVisits] = useState<Loadable<Visit[]>>(idle);

  const loadAppointments = useCallback(async () => {
    if (!sessionId) return;
    setAppointments((current) => ({ ...current, loading: true, error: "" }));
    try {
      const result = await getAppointments(sessionId);
      setAppointments({ data: result.upcoming, loading: false, error: "" });
    } catch (error) {
      const message = handleError(error, sessionId);
      setAppointments((current) => ({ ...current, loading: false, error: message ?? "" }));
    }
  }, [sessionId, handleError]);

  const loadVisits = useCallback(async () => {
    if (!sessionId) return;
    setVisits((current) => ({ ...current, loading: true, error: "" }));
    try {
      const result = await getVisits(sessionId, 10);
      setVisits({ data: result.visits, loading: false, error: "" });
    } catch (error) {
      const message = handleError(error, sessionId);
      setVisits((current) => ({ ...current, loading: false, error: message ?? "" }));
    }
  }, [sessionId, handleError]);

  // Karşılama adı ve oturum süresi: oturum başına bir kez.
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    getProfile(sessionId)
      .then((value) => {
        if (!cancelled) setProfile(value);
      })
      .catch((error: unknown) => {
        if (!cancelled) handleError(error, sessionId); // ad gösterilemezse karşılama adsız kalır
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, handleError]);

  // Backend oturumu 15 dk sonra kapanır: süre sunucu saatiyle bilinir, dolunca girişe dönülür
  // (asistansız sayfalarda orkestratör yanıtı beklenmez).
  const remaining = profile?.sessionRemainingSeconds;
  const lostRef = useRef(sessionLost);
  useEffect(() => {
    lostRef.current = sessionLost;
  });
  useEffect(() => {
    if (remaining === undefined || !sessionId) return;
    const timer = window.setTimeout(
      () => lostRef.current(sessionId, "expired"),
      Math.max(0, remaining) * 1000 + 500,
    );
    return () => window.clearTimeout(timer);
  }, [remaining, sessionId]);

  return { profile, appointments, visits, loadAppointments, loadVisits, handleError };
}

export type PatientData = ReturnType<typeof usePatientData>;

/* ---------------------------------------------------------------- *
 * Tarih/saat biçimleri (Europe/Istanbul; API UTC döner)
 * ---------------------------------------------------------------- */

const ZONE = "Europe/Istanbul";
const dayKeyFormat = new Intl.DateTimeFormat("en-CA", {
  timeZone: ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
const timeFormat = new Intl.DateTimeFormat("tr-TR", { timeZone: ZONE, hour: "2-digit", minute: "2-digit" });
const longDateFormat = new Intl.DateTimeFormat("tr-TR", {
  timeZone: ZONE,
  day: "numeric",
  month: "long",
  weekday: "long",
});
const dateFormat = new Intl.DateTimeFormat("tr-TR", {
  timeZone: ZONE,
  day: "numeric",
  month: "long",
  year: "numeric",
});

/** Istanbul günü "YYYY-AA-GG". */
export const dayKey = (value: Date | string) => dayKeyFormat.format(new Date(value));
export const formatTime = (value: string) => timeFormat.format(new Date(value));
/** "1 Ekim 2026" */
export const formatDate = (value: string) => dateFormat.format(new Date(value));

/** "Bugün", "Yarın" ya da "18 Kasım Çarşamba". */
export function relativeDay(value: string): string {
  const key = dayKey(value);
  const today = dayKey(new Date());
  const tomorrow = dayKey(new Date(Date.now() + 24 * 3600 * 1000));
  if (key === today) return "Bugün";
  if (key === tomorrow) return "Yarın";
  return longDateFormat.format(new Date(value));
}

/** "Yarın 09:15" / "18 Kasım Çarşamba 09:15" */
export const formatWhen = (value: string) => `${relativeDay(value)} ${formatTime(value)}`;

/** Gün seçicisi için "YYYY-AA-GG" → Istanbul öğlen anı (gün adı/ay hesaplamak için). */
export const dayKeyToDate = (key: string) => new Date(`${key}T12:00:00+03:00`);

export const STATUS_LABEL: Record<string, string> = {
  SCHEDULED: "Randevulu",
  CHECKED_IN: "Geldi, bekliyor",
  IN_EXAM: "Muayenede",
  COMPLETED: "Tamamlandı",
  NO_SHOW: "Gelmedi",
};
