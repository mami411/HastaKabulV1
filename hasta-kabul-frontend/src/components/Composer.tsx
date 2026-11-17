import { useEffect, useRef } from "react";
import { Keyboard, LoaderCircle, Mic, Repeat, SendHorizontal, Square } from "lucide-react";
import type { Assistant, VoicePhase } from "../hooks/useAssistant";
import { QUICK_ACTIONS } from "../quickActions";

const PHASE_TEXT: Record<VoicePhase, { title: string; sub: string }> = {
  idle: { title: "Konuşmak için dokunun", sub: "" },
  listening: { title: "Dinliyorum…", sub: "Bitirince susmanız yeterli" },
  transcribing: { title: "Düşünüyorum…", sub: "" },
  thinking: { title: "Düşünüyorum…", sub: "" },
  speaking: { title: "Konuşuyorum…", sub: "Durdurmak için dokunun" },
};

/** Ses dalgası / nabız göstergesi (hareket azaltma tercihinde CSS ile durağan). */
function VoiceMeter({ phase }: { phase: VoicePhase }) {
  if (phase === "idle") return null;
  return (
    <span className={`meter meter-${phase}`} aria-hidden="true">
      <i />
      <i />
      <i />
      <i />
      <i />
    </span>
  );
}

/**
 * Altta sabit konuşma alanı: hızlı seçenekler, durum satırı, büyük mikrofon düğmesi ve
 * yazı yazma seçeneği. Uyarı şeritleri (kilit sayacı, bağlantı hatası) de burada görünür.
 */
export function Composer({ a }: { a: Assistant }) {
  const mode = a.inputMode;
  const inputRef = useRef<HTMLInputElement>(null);
  const { phase, busy, micLocked, choices, bookingStep, hint, micDenied, systemError } = a;

  useEffect(() => {
    if (mode === "text") inputRef.current?.focus();
  }, [mode]);

  // Asistan seçenek sunduysa onlar, sunmadıysa (randevu adımı dışında) hızlı eylemler gösterilir.
  const chips =
    choices.length > 0
      ? choices.map((choice) => ({ label: choice, text: choice }))
      : bookingStep === "slots" || bookingStep === "confirm"
        ? []
        : QUICK_ACTIONS.map(({ label, text }) => ({ label, text }));

  const micDisabled = phase !== "listening" && phase !== "speaking" && micLocked;
  const micLabel =
    phase === "listening"
      ? "Konuşmam bitti, gönder"
      : phase === "speaking"
        ? "Sesli yanıtı durdur"
        : micDisabled
          ? "Lütfen bekleyin"
          : "Konuşmaya başla";

  const statusText = PHASE_TEXT[phase];
  const idleSub = hint || (micDenied ? "Mikrofon izni gerekli; yazarak da devam edebilirsiniz." : "");

  return (
    <footer className="composer">
      {a.idleRemaining !== null && (
        <div className="banner banner-idle" role="alert">
          {/* Geri sayım görsel; ekran okuyucu uyarıyı bir kez duyar */}
          <p>
            <span className="sr-only">Oturumunuz birazdan güvenliğiniz için kilitlenecek.</span>
            <span aria-hidden="true">
              Oturumunuz {a.idleRemaining} sn içinde güvenliğiniz için kilitlenecek.
            </span>
          </p>
          <button type="button" onClick={a.stayActive}>
            Devam et
          </button>
        </div>
      )}

      {systemError && (
        <div className="banner banner-error" role="alert">
          <p>
            {systemError.message}
            <small>Kod: {systemError.code}</small>
          </p>
          <button type="button" onClick={() => void a.retryAfterError()} disabled={busy}>
            Tekrar dene
          </button>
        </div>
      )}

      {chips.length > 0 && (
        <div
          className={`chips${chips.length > 4 ? " chips-many" : ""}`}
          role="group"
          aria-label={chips.length > 4 ? `Hızlı yanıtlar, ${chips.length} seçenek` : "Hızlı yanıtlar"}
        >
          {chips.map((chip) => (
            <button
              key={chip.label}
              type="button"
              className={`chip${chip.label === a.suggestedClinic ? " chip-suggested" : ""}`}
              onClick={() => void a.sendQuick(chip.text)}
              disabled={a.working}
            >
              {chip.label}
            </button>
          ))}
        </div>
      )}

      {mode === "voice" ? (
        <>
          <div className="phase-line" role="status" aria-live="polite">
            <VoiceMeter phase={phase} />
            <strong>{phase === "idle" && micDisabled ? "Lütfen bekleyin" : statusText.title}</strong>
            {(phase === "idle" ? idleSub : statusText.sub) && (
              <span>{phase === "idle" ? idleSub : statusText.sub}</span>
            )}
          </div>
          <div className="voice-row">
            <button
              type="button"
              className="side-btn"
              onClick={() => a.setInputMode("text")}
            >
              <Keyboard size={22} aria-hidden="true" />
              <span>Yaz</span>
            </button>

            <button
              type="button"
              className={`mic mic-${phase}`}
              onClick={a.pressMic}
              disabled={micDisabled}
              aria-label={micLabel}
              title={micLabel}
            >
              {phase === "listening" ? (
                <Square size={26} fill="currentColor" aria-hidden="true" />
              ) : phase === "speaking" ? (
                <Square size={24} aria-hidden="true" />
              ) : phase === "thinking" || phase === "transcribing" ? (
                <LoaderCircle size={30} className="spin" aria-hidden="true" />
              ) : (
                <Mic size={32} aria-hidden="true" />
              )}
            </button>

            <button
              type="button"
              className="side-btn"
              onClick={a.toggleVoiceLoop}
              aria-pressed={a.voiceLoop}
              title={
                a.voiceLoop
                  ? "Eller serbest açık: asistan konuştuktan sonra mikrofon kendiliğinden açılır"
                  : "Eller serbest kapalı: konuşmak için her seferinde mikrofona dokunun"
              }
            >
              <Repeat size={22} aria-hidden="true" />
              <span>{a.voiceLoop ? "Eller serbest" : "Elle"}</span>
            </button>
          </div>
        </>
      ) : (
        <>
          {(phase !== "idle" || hint) && (
            <div className="phase-line compact" role="status" aria-live="polite">
              <VoiceMeter phase={phase} />
              <span>{phase === "idle" ? hint : statusText.title}</span>
              {phase === "speaking" && (
                <button type="button" className="mini-stop" onClick={a.stopSpeaking}>
                  <Square size={14} aria-hidden="true" /> Durdur
                </button>
              )}
            </div>
          )}
          <form
            className="text-row"
            onSubmit={(event) => {
              event.preventDefault();
              void a.sendDraft();
            }}
          >
            <button
              type="button"
              className="icon-btn"
              onClick={() => a.setInputMode("voice")}
              aria-label="Sesle konuşmaya dön"
            >
              <Mic size={22} aria-hidden="true" />
            </button>
            <input
              ref={inputRef}
              value={a.draft}
              onChange={(event) => a.setDraft(event.target.value)}
              placeholder="Mesajınızı yazın…"
              aria-label="Mesajınız"
              enterKeyHint="send"
              autoComplete="off"
            />
            <button
              type="submit"
              className="send-btn"
              aria-label="Mesajı gönder"
              disabled={a.working || !a.draft.trim()}
            >
              <SendHorizontal size={20} aria-hidden="true" />
            </button>
          </form>
        </>
      )}
    </footer>
  );
}
