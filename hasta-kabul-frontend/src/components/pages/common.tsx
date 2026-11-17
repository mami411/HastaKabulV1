import { CalendarClock, LoaderCircle, RotateCw, TriangleAlert } from "lucide-react";
import type { PatientAppointment, Visit } from "../../services/api";
import { formatDate, formatTime, relativeDay, STATUS_LABEL } from "../../hooks/usePatientData";

/** Sayfa başlığı: sayfa değişince odak buraya gelir (ekran okuyucu yeni sayfayı duyar). */
export function PageTitle({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="page-title">
      <h1 data-page-title tabIndex={-1}>
        {title}
      </h1>
      {sub && <p>{sub}</p>}
    </div>
  );
}

export function Loading({ text = "Yükleniyor…" }: { text?: string }) {
  return (
    <p className="state-line" role="status">
      <LoaderCircle size={18} className="spin" aria-hidden="true" /> {text}
    </p>
  );
}

export function ErrorLine({ text, onRetry }: { text: string; onRetry?: () => void }) {
  return (
    <div className="card warning-card" role="alert">
      <TriangleAlert size={20} aria-hidden="true" />
      <p>{text}</p>
      {onRetry && (
        <button type="button" className="retry-btn" onClick={onRetry}>
          <RotateCw size={16} aria-hidden="true" /> Tekrar dene
        </button>
      )}
    </div>
  );
}

/** Yaklaşan randevu kartı (ana sayfa, Randevularım, asistan sayfası). */
export function UpcomingAppointment({
  appointment,
  showCode = true,
  compact = false,
}: {
  appointment: PatientAppointment;
  showCode?: boolean;
  compact?: boolean;
}) {
  const doctor = appointment.doctorDisplayName;
  return (
    <article className={`card upcoming-card${compact ? " compact" : ""}`}>
      <header className="upcoming-head">
        <span className="upcoming-icon" aria-hidden="true">
          <CalendarClock size={20} />
        </span>
        <div>
          <strong>
            {relativeDay(appointment.scheduledAt)} · {formatTime(appointment.scheduledAt)}
          </strong>
          <span>
            {appointment.clinicName}
            {doctor ? ` · ${doctor}` : ""}
            {appointment.room && !compact ? ` · Oda ${appointment.room}` : ""}
          </span>
        </div>
        <span className={`status-pill status-${appointment.status.toLowerCase()}`}>
          {STATUS_LABEL[appointment.status] ?? appointment.status}
        </span>
      </header>
      {showCode && !compact && (
        <p className="code-line">
          Erişim kodu <strong>{appointment.accessCode}</strong>
        </p>
      )}
    </article>
  );
}

/** Geçmiş muayene: hekim onaylı özet, ilaçlar ve uyarılar. */
export function VisitCard({ visit }: { visit: Visit }) {
  return (
    <article className="card visit-card">
      <header className="visit-card-head">
        <strong>{visit.clinicName || "Muayene"}</strong>
        <span>
          {formatDate(visit.scheduledAt)}
          {visit.doctorDisplayName ? ` · ${visit.doctorDisplayName}` : ""}
        </span>
      </header>
      <div>
        <span className="field-label">Hekim onaylı özet</span>
        <p className="visit-diagnosis">{visit.diagnosisPlain}</p>
      </div>
      {visit.medications.length > 0 && (
        <div>
          <span className="field-label">Yazılan ilaçlar</span>
          <ul className="meds">
            {visit.medications.map((med) => (
              <li key={med.name}>
                <strong>{med.name}</strong>
                {med.purpose && <span>{med.purpose}</span>}
                {med.usage && <em>{med.usage}</em>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {visit.warnings.length > 0 && (
        <ul className="visit-warnings">
          {visit.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </article>
  );
}
