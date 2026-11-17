import { VOICE_URL } from "../config";

/**
 * Seslendirme (ai-voice /tts) ve çalma.
 *
 * Mobil tarayıcılar (özellikle iOS Safari) kullanıcı dokunuşu olmadan başlatılan sesi engeller.
 * Bu yüzden tek bir <audio> öğesi kullanılır ve ilk dokunuşta (giriş, mikrofon) `unlockAudio`
 * ile "kilidi açılır"; sonraki yanıtlar aynı öğeyle dokunuş beklemeden çalınabilir.
 */

let audioElement: HTMLAudioElement | null = null;

function sharedAudio(): HTMLAudioElement {
  audioElement ??= new Audio();
  return audioElement;
}

/** 50 ms'lik sessiz WAV (kilit açmak için); dış dosya gerekmez. */
function silentWavUrl(): string {
  const sampleRate = 8000;
  const samples = 400;
  const buffer = new ArrayBuffer(44 + samples * 2);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) =>
    [...text].forEach((char, i) => view.setUint8(offset + i, char.charCodeAt(0)));
  write(0, "RIFF");
  view.setUint32(4, 36 + samples * 2, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, samples * 2, true);
  return URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
}

/** Çalan (ya da indirilmekte olan) tek seslendirme; çıkışta ve kilitte kesilir. */
let current: { controller: AbortController; stop: () => void } | null = null;

/**
 * Kullanıcı dokunuşu içinde çağrılır: paylaşılan ses öğesini sessiz bir çalmayla etkinleştirir.
 * Bir seslendirme sürüyorsa dokunulmaz.
 */
export function unlockAudio(): void {
  if (current) return;
  const audio = sharedAudio();
  const url = silentWavUrl();
  audio.src = url;
  void audio
    .play()
    .catch(() => undefined)
    .finally(() => URL.revokeObjectURL(url));
}

/**
 * Süren seslendirmeyi hemen keser (indirme iptal, çalma durur, blob URL bırakılır).
 * KVKK: çıkışta ya da kilitte önceki yanıt (geçmiş muayene, erişim kodu) okunmaya devam etmemeli.
 */
export function stopTTS(): void {
  const active = current;
  current = null;
  if (!active) return;
  active.controller.abort();
  active.stop();
}

export async function playTTS(text: string): Promise<void> {
  stopTTS(); // aynı anda tek ses
  const controller = new AbortController();
  let stopPlayback: () => void = () => undefined;
  const handle = { controller, stop: () => stopPlayback() };
  current = handle;

  try {
    const response = await fetch(`${VOICE_URL}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`TTS servisi hata verdi: ${response.status}`);
    }

    const audioBlob = await response.blob();
    if (controller.signal.aborted) throw new Error("Seslendirme iptal edildi.");
    const audioUrl = URL.createObjectURL(audioBlob);
    const audio = sharedAudio();
    audio.src = audioUrl;

    try {
      await new Promise<void>((resolve, reject) => {
        stopPlayback = () => reject(new Error("Seslendirme iptal edildi."));
        audio.onended = () => resolve();
        audio.onerror = () => reject(new Error("Ses oynatılamadı."));
        if (controller.signal.aborted) {
          reject(new Error("Seslendirme iptal edildi."));
          return;
        }
        audio.play().catch(reject);
      });
    } finally {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      URL.revokeObjectURL(audioUrl);
    }
  } finally {
    if (current === handle) current = null;
  }
}
