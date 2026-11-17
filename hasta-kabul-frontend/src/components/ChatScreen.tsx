import { useEffect, useRef } from "react";
import { ChevronLeft, LogOut, Mic, Sparkles } from "lucide-react";
import { BRAND } from "../brand";
import type { Assistant, Connection } from "../hooks/useAssistant";
import {
  AppointmentCard,
  ConfirmCard,
  EmergencyCard,
  HistoryCard,
  SlotsCard,
  SuggestionCard,
  WarningCard,
} from "./Cards";
import { Composer } from "./Composer";
import { QUICK_ACTIONS } from "../quickActions";
import type { PatientAppointment } from "../services/api";
import { UpcomingAppointment } from "./pages/common";

const CONNECTION_LABEL: Record<Connection, string> = {
  online: "Bağlı",
  demo: "Örnek veri",
  offline: "Bağlantı sorunu",
};

const prefersReducedMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/**
 * Sesli asistan sayfası: sohbet akışı, kartlar ve altta konuşma alanı (mikrofon / yazı). Uygulamanın
 * diğer sayfalarından ayrıdır; başlıktaki geri düğmesi ana sayfaya döner. Yaklaşan randevular
 * backend'den gelir (uygulamada alınanlar da görünür).
 */
export function ChatScreen({
  a,
  onBack,
  upcoming,
}: {
  a: Assistant;
  onBack: () => void;
  upcoming: PatientAppointment[];
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const { messages, bookingStep, selectedSlot, working, phase, connection } = a;
  const thinking = phase === "thinking" || phase === "transcribing";

  // Yeni mesajda ve "düşünüyorum" göstergesinde sohbet alta kaysın.
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    // Son tur (ör. uzun geçmiş muayene kartı) ekrana sığmıyorsa sonuna değil başına gidilir.
    const turns = element.querySelectorAll<HTMLElement>(".turn");
    const last = turns[turns.length - 1];
    const tall = last && !thinking && last.offsetHeight > element.clientHeight - 24;
    element.scrollTo({
      top: tall ? last.offsetTop - 12 : element.scrollHeight,
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  }, [messages.length, thinking]);

  // Etkin kartlar: en son saat önerisi ve en son onay özeti (randevu adımındaysa).
  let lastSlotsId = -1;
  let lastConfirmId = -1;
  for (const message of messages) {
    if (message.cards.some((card) => card.kind === "slots")) lastSlotsId = message.id;
    if (message.cards.some((card) => card.kind === "confirm")) lastConfirmId = message.id;
  }
  const slotsActive = bookingStep === "slots" || bookingStep === "confirm";

  return (
    <>
      <header className="app-header">
        <div className="brand-mini">
          <button type="button" className="back-icon" onClick={onBack} aria-label="Ana sayfaya dön">
            <ChevronLeft size={24} aria-hidden="true" />
          </button>
          <div>
            <strong data-page-title tabIndex={-1}>
              {BRAND.assistantName}
            </strong>
            <span className={`conn conn-${connection}`}>
              <i aria-hidden="true" />
              {CONNECTION_LABEL[connection]}
            </span>
          </div>
        </div>
        <button type="button" className="logout" onClick={a.logout}>
          <LogOut size={18} aria-hidden="true" />
          Çıkış
        </button>
      </header>

      {connection === "demo" && (
        <p className="demo-banner" role="status">
          <strong>ÖRNEK VERİ</strong> Asistana ulaşılamadı; gösterilen saatler örnektir ve randevu
          oluşturulamaz.
        </p>
      )}

      <div className="thread" ref={scrollRef} role="log" aria-live="polite" aria-label="Sohbet">
        <section className="intro" aria-labelledby="intro-title">
          <h2 id="intro-title">Size nasıl yardımcı olabilirim?</h2>
          <p>
            Ben {BRAND.assistantName}. Aşağıdan seçin ya da mikrofona dokunup söyleyin.
          </p>
          {upcoming.length > 0 && (
            <div className="intro-upcoming">
              <span>Yaklaşan randevunuz</span>
              <UpcomingAppointment appointment={upcoming[0]} compact />
            </div>
          )}
          <div className="quick-grid">
            {QUICK_ACTIONS.map(({ label, text, hint, icon: Icon }) => (
              <button
                key={label}
                type="button"
                className="quick-tile"
                onClick={() => void a.sendQuick(text)}
                disabled={working}
              >
                <Icon size={22} aria-hidden="true" />
                <strong>{label}</strong>
                <span>{hint}</span>
              </button>
            ))}
          </div>
        </section>

        {messages.map((message) => (
          <div key={message.id} className={`turn turn-${message.role}`}>
            {message.text && (
              <p className={`bubble bubble-${message.role}`}>
                {message.voice && (
                  <Mic size={13} className="bubble-voice" aria-label="Sesle söylendi" />
                )}
                {message.text}
              </p>
            )}
            {message.text && message.aiGeneralInfo && (
              <p className="bubble-note">
                <Sparkles size={12} aria-hidden="true" />
                Genel bilgi · doktorunuza danışın
              </p>
            )}
            {message.cards.map((card, index) => {
              const key = `${message.id}-${index}`;
              switch (card.kind) {
                case "slots":
                  return (
                    <SlotsCard
                      key={key}
                      slots={card.slots}
                      unavailable={card.unavailable}
                      active={slotsActive && message.id === lastSlotsId}
                      selectedSlot={selectedSlot}
                      demo={connection === "demo"}
                      busy={working}
                      onSelect={(slot) => void a.selectSlot(slot)}
                    />
                  );
                case "confirm":
                  return (
                    <ConfirmCard
                      key={key}
                      label={card.label}
                      detail={card.detail}
                      active={
                        bookingStep === "confirm" &&
                        message.id === lastConfirmId &&
                        card.slotId === selectedSlot
                      }
                      busy={working}
                      onConfirm={() => void a.confirmAppointment(card.slotId)}
                    />
                  );
                case "appointment":
                  return <AppointmentCard key={key} appointment={card.appointment} />;
                case "history":
                  return <HistoryCard key={key} history={card.history} />;
                case "warning":
                  return <WarningCard key={key} text={card.text} />;
                case "emergency":
                  return <EmergencyCard key={key} />;
                case "suggestion":
                  return (
                    <SuggestionCard
                      key={key}
                      clinicName={card.clinicName}
                      // Yalnız en son yanıttaki öneri yanıtlanabilir.
                      active={message.id === messages[messages.length - 1]?.id}
                      busy={working}
                      onAnswer={(text) => void a.sendQuick(text)}
                    />
                  );
                default:
                  return null;
              }
            })}
          </div>
        ))}

        {thinking && (
          <div className="turn turn-assistant" aria-hidden="true">
            <p className="bubble bubble-assistant typing">
              <i />
              <i />
              <i />
            </p>
          </div>
        )}
      </div>

      <Composer a={a} />
    </>
  );
}
