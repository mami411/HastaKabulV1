/**
 * Konuşma algılamalı (VAD) tek seferlik ses kaydı.
 *
 * Mikrofon açılır; WebAudio enerji ölçümüyle konuşma başlangıcı ve sonundaki sessizlik
 * algılanır. Konuşma başladıktan sonra VAD_SILENCE_MS kadar sessizlik olunca kayıt kendiliğinden
 * biter ("Gönder" adımı yoktur). Hiç konuşulmazsa VAD_NO_SPEECH_MS sonunda vazgeçilir; kayıt en
 * fazla VAD_MAX_MS sürer. Eşik ortam gürültüsüne göre ilk ölçümlerle ayarlanır.
 *
 * Ses yalnız ai-voice /stt ucuna gider (stt.ts); burada saklanmaz, loglanmaz.
 */

const num = (value: unknown, fallback: number) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
};

export const VAD_CONFIG = {
  /** Konuşma sonrası bu kadar sessizlik olunca kayıt biter (ms). */
  silenceMs: num(import.meta.env.VITE_VAD_SILENCE_MS, 1300),
  /** Hiç konuşma algılanmazsa vazgeçme süresi (ms). */
  noSpeechMs: num(import.meta.env.VITE_VAD_NO_SPEECH_MS, 7000),
  /** Bir kaydın azami süresi (ms). */
  maxMs: num(import.meta.env.VITE_VAD_MAX_MS, 15000),
  /** Konuşma sayılması için gereken kesintisiz ses süresi (ms); öksürük/tık sesini eler. */
  minSpeechMs: num(import.meta.env.VITE_VAD_MIN_SPEECH_MS, 250),
  /** RMS enerji alt eşiği (0–1); gürültülü ortamda eşik ölçülen gürültünün katına çıkar. */
  threshold: num(import.meta.env.VITE_VAD_THRESHOLD, 0.02),
};

export type CaptureResult =
  | { kind: "speech"; blob: Blob }
  | { kind: "silence" }
  | { kind: "aborted" };

export type CaptureHandle = {
  /** Kaydı hemen bitirir; konuşma algılandıysa o ana kadarki ses döner. */
  finish: () => void;
  /** Kaydı iptal eder (ses atılır). */
  abort: () => void;
  result: Promise<CaptureResult>;
};

export function voiceCaptureSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined" &&
    typeof AudioContext !== "undefined"
  );
}

const CALIBRATION_MS = 300;
const TICK_MS = 50;

/**
 * Tek bir konuşmayı dinler. ``onSpeechStart`` konuşma algılanınca bir kez çağrılır (hareketsizlik
 * sayacını canlı tutmak ve durum göstermek için). Mikrofon açılamazsa Promise hata ile reddedilir.
 */
export async function captureUtterance(
  onSpeechStart?: () => void,
): Promise<CaptureHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });

  let context: AudioContext | null = null;
  let source: MediaStreamAudioSourceNode;
  let analyser: AnalyserNode;
  let recorder: MediaRecorder;
  try {
    context = new AudioContext();
    source = context.createMediaStreamSource(stream);
    analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    source.connect(analyser);
    const mimeType = "audio/webm;codecs=opus";
    recorder = new MediaRecorder(
      stream,
      MediaRecorder.isTypeSupported(mimeType) ? { mimeType } : undefined,
    );
  } catch (error) {
    // Kurulum yarıda kaldıysa mikrofon açık kalmasın (kayıt göstergesi yanık kalırdı).
    stream.getTracks().forEach((track) => track.stop());
    void context?.close().catch(() => undefined);
    throw error;
  }
  const audioContext = context;
  // Mobil tarayıcılarda dokunuşsuz (eller serbest döngü) açılan bağlam askıda başlayabilir.
  // Beklenmez: dokunuşsuz resume bazı tarayıcılarda hiç sonuçlanmayabilir.
  if (audioContext.state === "suspended") void audioContext.resume().catch(() => undefined);
  const samples = new Float32Array(analyser.fftSize);
  const chunks: Blob[] = [];
  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  };

  let settle: (result: CaptureResult) => void = () => {};
  const result = new Promise<CaptureResult>((resolve) => {
    settle = resolve;
  });

  const startedAt = performance.now();
  let noiseFloor = 0;
  let calibrationCount = 0;
  let loudSince: number | null = null;
  let lastLoud = 0;
  let speechStarted = false;
  let finished = false;

  function cleanup() {
    window.clearInterval(timer);
    source.disconnect();
    stream.getTracks().forEach((track) => track.stop());
    void audioContext.close().catch(() => undefined);
  }

  function end(kind: "speech" | "silence" | "aborted", manual = false) {
    if (finished) return;
    finished = true;
    // Elle bitirmede (kullanıcı "bitti" dedi) eşik altı kalmış kısık ses de gönderilir.
    const keep = kind === "speech" && (speechStarted || manual);
    if (recorder.state === "inactive") {
      cleanup();
      settle(
        keep && chunks.length > 0
          ? { kind: "speech", blob: new Blob(chunks, { type: recorder.mimeType || "audio/webm" }) }
          : { kind: kind === "aborted" ? "aborted" : "silence" },
      );
      return;
    }
    recorder.onstop = () => {
      cleanup();
      // Elle erken bitirildi ve henüz ses verisi yok: boş dosya STT'ye gönderilmez (400 → yanıltıcı
      // "servise ulaşılamadı" notu yerine "Sizi duyamadım").
      if (keep && chunks.reduce((total, chunk) => total + chunk.size, 0) > 0) {
        settle({ kind: "speech", blob: new Blob(chunks, { type: recorder.mimeType || "audio/webm" }) });
      } else {
        chunks.length = 0;
        settle({ kind: kind === "aborted" ? "aborted" : "silence" });
      }
    };
    recorder.stop();
  }

  function rms(): number {
    analyser.getFloatTimeDomainData(samples);
    let sum = 0;
    for (const value of samples) sum += value * value;
    return Math.sqrt(sum / samples.length);
  }

  const timer = window.setInterval(() => {
    if (finished) return;
    const now = performance.now();
    const elapsed = now - startedAt;
    const level = rms();

    if (elapsed < CALIBRATION_MS && !speechStarted) {
      noiseFloor = (noiseFloor * calibrationCount + level) / (calibrationCount + 1);
      calibrationCount += 1;
      return;
    }

    const threshold = Math.max(VAD_CONFIG.threshold, noiseFloor * 3);
    if (level >= threshold) {
      loudSince ??= now;
      lastLoud = now;
      if (!speechStarted && now - loudSince >= VAD_CONFIG.minSpeechMs) {
        speechStarted = true;
        onSpeechStart?.();
      }
    } else {
      loudSince = null;
    }

    if (speechStarted && now - lastLoud >= VAD_CONFIG.silenceMs) end("speech");
    else if (!speechStarted && elapsed >= VAD_CONFIG.noSpeechMs) end("silence");
    else if (elapsed >= VAD_CONFIG.maxMs) end(speechStarted ? "speech" : "silence");
  }, TICK_MS);

  recorder.start(250);

  return {
    finish: () => end("speech", true),
    abort: () => end("aborted"),
    result,
  };
}
