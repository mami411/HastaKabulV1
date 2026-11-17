import { useEffect, useRef, useState } from "react";
import { Delete, Eye, EyeOff, Lock, LogOut, ShieldCheck, TriangleAlert, Clock } from "lucide-react";
import { TC_LENGTH, type Assistant, type LoginNoticeKind } from "../hooks/useAssistant";
import { BRAND } from "../brand";
import { Logo } from "./Logo";

const NOTICE_ICON: Record<LoginNoticeKind, typeof Lock> = {
  locked: Lock,
  expired: Clock,
  reverify: ShieldCheck,
  ended: Clock,
  attempts: TriangleAlert,
  logout: LogOut,
};

const DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

/**
 * Giriş: mobil PIN ekranı gibi TC tuş takımı. Kimlik numarası yalnız buradan girilir;
 * mikrofon ve serbest metin bu ekranda yoktur. Fiziksel klavyeden rakam da kabul edilir (masaüstü demo).
 */
export function LoginScreen({ a }: { a: Assistant }) {
  const [revealed, setRevealed] = useState(false);
  const noticeRef = useRef<HTMLDivElement>(null);
  const { loginNotice, tcInput, tcError, busy } = a;
  const complete = tcInput.length === TC_LENGTH;

  // Fiziksel klavye: rakam, silme, Enter. Odak bir metin kutusundayken karışmaz.
  const keyHandler = useRef<(event: KeyboardEvent) => void>(() => undefined);
  useEffect(() => {
    keyHandler.current = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || busy) return;
      if (/^\d$/.test(event.key)) a.pressDigit(event.key);
      else if (event.key === "Backspace") a.pressBackspace();
      else if (event.key === "Enter" && complete) {
        event.preventDefault();
        void a.login();
      }
    };
  });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => keyHandler.current(event);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Bildirim (kilit, süre dolumu, çıkış) geldiğinde ekran okuyucu ve odak oraya gitsin.
  useEffect(() => {
    if (loginNotice) noticeRef.current?.focus();
  }, [loginNotice]);

  const NoticeIcon = loginNotice ? NOTICE_ICON[loginNotice.kind] : null;

  return (
    <main className="login" aria-labelledby="login-title">
      <div className="login-head">
        <Logo size={56} />
        <h1 id="login-title">{BRAND.appName}</h1>
        <p className="login-org">{BRAND.fullName}</p>
        <p className="login-tagline">{BRAND.slogan}</p>
      </div>

      {loginNotice && NoticeIcon ? (
        <div
          ref={noticeRef}
          tabIndex={-1}
          className={`login-notice notice-${loginNotice.kind}`}
          role="status"
        >
          <NoticeIcon size={20} aria-hidden="true" />
          <div>
            <strong>{loginNotice.title}</strong>
            <p>{loginNotice.text}</p>
          </div>
        </div>
      ) : (
        <p className="login-intro">
          Randevu alın, ilaçlarınızı ve son muayenenizi sorun; konuşarak ya da yazarak.
        </p>
      )}

      <section className="pin" aria-labelledby="tc-label">
        <div className="pin-head">
          <h2 id="tc-label">T.C. kimlik numaranız</h2>
          <button
            type="button"
            className="pin-reveal"
            onClick={() => setRevealed((value) => !value)}
            aria-pressed={revealed}
            aria-label={revealed ? "Numarayı gizle" : "Numarayı göster"}
          >
            {revealed ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
        </div>

        <div
          className="pin-dots"
          role="img"
          aria-label={`${tcInput.length} / ${TC_LENGTH} rakam girildi`}
        >
          {Array.from({ length: TC_LENGTH }, (_, index) => {
            const filled = index < tcInput.length;
            return (
              <span key={index} className={`pin-dot${filled ? " filled" : ""}`}>
                {filled && revealed ? tcInput[index] : ""}
              </span>
            );
          })}
        </div>

        <p className="pin-error" role="alert">
          {tcError}
        </p>

        <div className="keypad">
          {DIGITS.map((digit) => (
            <button key={digit} type="button" onClick={() => a.pressDigit(digit)} disabled={busy}>
              {digit}
            </button>
          ))}
          <span aria-hidden="true" />
          <button type="button" onClick={() => a.pressDigit("0")} disabled={busy}>
            0
          </button>
          <button
            type="button"
            className="key-delete"
            onClick={a.pressBackspace}
            disabled={busy || tcInput.length === 0}
            aria-label="Son rakamı sil"
          >
            <Delete size={24} />
          </button>
        </div>

        <button
          type="button"
          className="btn-primary login-submit"
          onClick={() => void a.login()}
          disabled={busy || !complete}
        >
          {busy ? "Doğrulanıyor…" : "Giriş yap"}
        </button>
      </section>

      <p className="login-privacy">
        <ShieldCheck size={16} aria-hidden="true" />
        Kimlik numaranız yalnızca {BRAND.shortName} hasta kayıt sistemine iletilir; bu cihazda saklanmaz ve
        sesli asistana gönderilmez.
      </p>
    </main>
  );
}
