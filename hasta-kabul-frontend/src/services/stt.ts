import { VOICE_URL } from "../config";

/** STT hatası: ``status`` 0 = servise ulaşılamadı; 400/413 = ses anlaşılamadı/çok uzun (servis çalışıyor). */
export class SttError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "SttError";
    this.status = status;
  }
}

/** Kaydın türüne uygun dosya adı (iOS Safari webm yerine mp4/aac kaydeder). */
function fileNameFor(blob: Blob): string {
  if (blob.type.includes("mp4") || blob.type.includes("aac")) return "recording.m4a";
  if (blob.type.includes("ogg")) return "recording.ogg";
  return "recording.webm";
}

/** Sesi yazıya çevirir (ai-voice /stt); ses saklanmaz. */
export async function transcribe(blob: Blob): Promise<string> {
  const formData = new FormData();
  formData.append("file", blob, fileNameFor(blob));
  let response: Response;
  try {
    response = await fetch(`${VOICE_URL}/stt`, { method: "POST", body: formData });
  } catch {
    throw new SttError(0, "STT ağ hatası");
  }
  if (!response.ok) throw new SttError(response.status, `STT HTTP ${response.status}`);
  const result: { text?: string } = await response.json();
  return (result.text ?? "").trim();
}
