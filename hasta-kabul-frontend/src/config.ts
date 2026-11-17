/**
 * Uygulama ayarları (Vite ortam değişkenleri) tek yerde.
 *
 * VITE_SAME_ORIGIN=1 (telefon demo modu, `npm run dev:mobil`): tüm istekler göreli adrese gider ve
 * Vite vekili (proxy) bunları arka uçlara iletir. Telefon yalnız ön yüzün HTTPS adresiyle konuşur;
 * CORS ve karışık içerik (https → http) sorunu çıkmaz.
 */

const env = import.meta.env;
const SAME_ORIGIN = env.VITE_SAME_ORIGIN === "1";

/** Boş ya da tanımsız değerde varsayılanı kullanır; aynı köken modunda göreli adres ("") döner. */
function baseUrl(value: string | undefined, fallback: string): string {
  if (SAME_ORIGIN) return "";
  return (value || fallback).replace(/\/+$/, "");
}

const positive = (value: unknown, fallback: number) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
};

/** Hasta kabul REST API'si (backend). */
export const API_URL = baseUrl(env.VITE_API_URL, "http://localhost:3000");
/** Ses orkestratörü (/orchestrate). */
export const ORCHESTRATOR_URL = baseUrl(env.VITE_ORCHESTRATOR_URL, "http://localhost:8002");
/** Konuşma tanıma ve seslendirme (/stt, /tts). */
export const VOICE_URL = baseUrl(env.VITE_VOICE_URL, "http://localhost:8002");

/** Orkestratör isteği zaman aşımı (ms); aşılırsa onay sonucu "doğrulanamadı" sayılır. */
export const ORCHESTRATOR_TIMEOUT_MS = positive(env.VITE_ORCHESTRATOR_TIMEOUT_MS, 30000);

/**
 * Hareketsizlik kilidi (sn): kişisel cihazda varsayılan 5 dk. Dolunca sohbet silinir ve
 * kimlik numarasıyla yeniden giriş istenir.
 */
export const IDLE_TIMEOUT_SEC = positive(env.VITE_IDLE_TIMEOUT_SEC, 300);
/** Kilitten önce uyarının gösterildiği süre (sn). */
export const IDLE_WARNING_SEC = Math.min(30, Math.max(5, Math.floor(IDLE_TIMEOUT_SEC / 3)));

/** Asistan konuştuktan sonra mikrofon kendiliğinden açılsın mı (eller serbest döngü). */
export const VOICE_LOOP_DEFAULT = env.VITE_VOICE_LOOP !== "0";
