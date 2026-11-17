import { Check, CircleCheck, ChevronRight, Pill, TriangleAlert, CalendarClock, Phone, Stethoscope, Siren } from "lucide-react";
import type { BookedAppointment, HistoryData, OrchestratorSlot } from "../services/orchestrator";

/**
 * Tüm saatler aynı gün ve aynı poliklinik/hekimse ("Sal 17 Kas 09:00" + "Kardiyoloji · Elif K.")
 * gün ve hekim bir kez yazılır, saatler dokunulabilir bir ızgarada gösterilir.
 */
function compactSlots(slots: OrchestratorSlot[]) {
  if (slots.length < 2) return null;
  const parts = slots.map((slot) => /^(.*\S)\s+(\d{1,2}:\d{2})$/.exec(slot.label.trim()));
  if (parts.some((part) => !part)) return null;
  const day = parts[0]![1];
  const detail = slots[0].detail ?? "";
  const uniform = parts.every((part) => part![1] === day) && slots.every((slot) => (slot.detail ?? "") === detail);
  return uniform ? { day, detail, times: parts.map((part) => part![2]) } : null;
}

/** Asistanın önerdiği saatler; yalnız en son öneri kartı ve randevu adımındayken seçilebilir. */
export function SlotsCard({
  slots,
  unavailable,
  active,
  selectedSlot,
  demo,
  busy,
  onSelect,
}: {
  slots: OrchestratorSlot[];
  unavailable: boolean;
  active: boolean;
  selectedSlot: string;
  demo: boolean;
  busy: boolean;
  onSelect: (slot: OrchestratorSlot) => void;
}) {
  const compact = compactSlots(slots);
  return (
    <section className={`card slots-card${active ? "" : " inactive"}`} aria-label="Uygun saatler">
      <header className="card-head">
        <CalendarClock size={18} aria-hidden="true" />
        <h3>Uygun saatler</h3>
        {demo && <span className="tag tag-demo">ÖRNEK VERİ</span>}
      </header>
      {unavailable && (
        <p className="card-note">Saatler şu an alınamadı; birazdan tekrar deneyin.</p>
      )}
      {compact ? (
        <>
          <p className="slot-context">
            <strong>{compact.day}</strong>
            {compact.detail && <span>{compact.detail}</span>}
          </p>
          <ul className="slot-grid">
            {slots.map((slot, index) => {
              const selected = selectedSlot === slot.slotId;
              return (
                <li key={slot.slotId}>
                  <button
                    type="button"
                    className={`slot-time${selected ? " selected" : ""}`}
                    onClick={() => onSelect(slot)}
                    disabled={!active || busy}
                    aria-pressed={selected}
                    aria-label={`${slot.label}${slot.detail ? `, ${slot.detail}` : ""}`}
                  >
                    {compact.times[index]}
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <ul className="slot-list">
          {slots.map((slot) => {
            const selected = selectedSlot === slot.slotId;
            return (
              <li key={slot.slotId}>
                <button
                  type="button"
                  className={`slot${selected ? " selected" : ""}`}
                  onClick={() => onSelect(slot)}
                  disabled={!active || busy}
                  aria-pressed={selected}
                >
                  <span className="slot-text">
                    <strong>{slot.label}</strong>
                    {slot.detail && <span>{slot.detail}</span>}
                  </span>
                  {selected ? (
                    <Check size={20} aria-hidden="true" />
                  ) : (
                    <ChevronRight size={20} aria-hidden="true" />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {!active && slots.length > 0 && <p className="card-note">Bu öneri artık geçerli değil.</p>}
    </section>
  );
}

/** Onay özeti: düğme yalnız CONFIRM olayı gönderir; randevu sunucuda açık onayla oluşur. */
export function ConfirmCard({
  label,
  detail,
  active,
  busy,
  onConfirm,
}: {
  label: string;
  detail: string;
  active: boolean;
  busy: boolean;
  onConfirm: () => void;
}) {
  return (
    <section className={`card confirm-card${active ? "" : " inactive"}`} aria-label="Randevu özeti">
      <dl className="facts">
        <div>
          <dt>Tarih ve saat</dt>
          <dd>{label || "—"}</dd>
        </div>
        {detail && (
          <div>
            <dt>Poliklinik · hekim</dt>
            <dd>{detail}</dd>
          </div>
        )}
      </dl>
      {active ? (
        <button type="button" className="btn-primary" onClick={onConfirm} disabled={busy}>
          <CircleCheck size={20} aria-hidden="true" /> Seçimimi onayla
        </button>
      ) : null}
    </section>
  );
}

/** Oluşan randevu ve erişim kodu: çıkış yapılana kadar sohbette kalır. */
export function AppointmentCard({ appointment }: { appointment: BookedAppointment }) {
  const doctor = appointment.doctorDisplayName;
  return (
    <section className="card appointment-card" aria-label="Randevunuz oluşturuldu">
      <header className="card-head success">
        <CircleCheck size={20} aria-hidden="true" />
        <h3>Randevunuz oluşturuldu</h3>
      </header>
      <dl className="facts">
        <div>
          <dt>Tarih ve saat</dt>
          <dd>{appointment.longLabel || appointment.label}</dd>
        </div>
        {appointment.clinicName && (
          <div>
            <dt>Poliklinik</dt>
            <dd>
              {appointment.clinicName}
              {doctor ? ` · ${doctor}` : ""}
            </dd>
          </div>
        )}
      </dl>
      <div className="access-code">
        <span>Erişim kodu</span>
        <strong>{appointment.accessCode}</strong>
      </div>
      <p className="card-note">
        Kodu not alın. Güvenliğiniz için çıkış yaptığınızda bu cihazdan silinir.
      </p>
    </section>
  );
}

/** Geçmiş muayene/ilaç bilgisi: yalnız hekim onaylı özetler; oturum belleğinde, çıkışta silinir. */
export function HistoryCard({ history }: { history: HistoryData }) {
  return (
    <section className="card history-card" aria-label="Geçmiş muayeneleriniz">
      <header className="card-head">
        <Pill size={18} aria-hidden="true" />
        <h3>Geçmiş muayeneleriniz</h3>
      </header>
      {history.visits.length === 0 && (
        <p className="card-note">Hekiminizin onayladığı bir muayene özeti bulunmuyor.</p>
      )}
      {history.visits.map((visit, index) => (
        <article className="visit" key={`${visit.date}-${visit.clinicName}-${index}`}>
          <header>
            <strong>{visit.clinicName || "Muayene"}</strong>
            <span>
              {visit.date}
              {visit.doctorDisplayName ? ` · ${visit.doctorDisplayName}` : ""}
            </span>
          </header>
          {visit.diagnosisPlain && <p className="visit-diagnosis">{visit.diagnosisPlain}</p>}
          {visit.medications.length > 0 && (
            <ul className="meds">
              {visit.medications.map((med) => (
                <li key={med.name} className={history.focus.includes(med.name) ? "focus" : ""}>
                  <strong>{med.name}</strong>
                  {med.purpose && <span>{med.purpose}</span>}
                  {med.usage && <em>{med.usage}</em>}
                </li>
              ))}
            </ul>
          )}
          {visit.warnings.length > 0 && (
            <ul className="visit-warnings">
              {visit.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
        </article>
      ))}
      <p className="card-note">
        Bilgiler hekiminizin onayladığı özetlerden gelir. Sorunuz olursa hekiminize ya da
        eczacınıza danışın.
      </p>
    </section>
  );
}

/**
 * Şikâyetten önerilen poliklinik. "Evet, bakalım" / "Başka bölüm" yalnız "evet" / "hayır" metni gönderir;
 * bu bir randevu onayı DEĞİLDİR (CONFIRM olayı gönderilmez), yalnız saat aramasını başlatır.
 */
export function SuggestionCard({
  clinicName,
  active,
  busy,
  onAnswer,
}: {
  clinicName: string;
  active: boolean;
  busy: boolean;
  onAnswer: (text: string) => void;
}) {
  return (
    <section className={`card suggestion-card${active ? "" : " inactive"}`} aria-label="Önerilen poliklinik">
      <div className="suggestion-head">
        <Stethoscope size={20} aria-hidden="true" />
        <div>
          <span>Önerilen poliklinik</span>
          <strong>{clinicName}</strong>
        </div>
      </div>
      {active && (
        <div className="suggestion-actions">
          <button type="button" className="btn-primary" onClick={() => onAnswer("evet")} disabled={busy}>
            Evet, bakalım
          </button>
          <button type="button" className="btn-secondary" onClick={() => onAnswer("hayır")} disabled={busy}>
            Başka bölüm
          </button>
        </div>
      )}
    </section>
  );
}

/** Acil durum uyarısı: ciddi ama sakin; tek dokunuşla 112. Konuşma devam edebilir. */
export function EmergencyCard() {
  return (
    <section className="card emergency-card" role="alert" aria-label="Acil durum">
      <div className="emergency-head">
        <Siren size={22} aria-hidden="true" />
        <div>
          <strong>Acil bir durum olabilir</strong>
          <p>Bu uygulama acil yardım sağlamaz. Belirtileriniz ciddiyse hemen 112'yi arayın.</p>
        </div>
      </div>
      <a className="btn-emergency" href="tel:112">
        <Phone size={20} aria-hidden="true" /> 112'yi ara
      </a>
    </section>
  );
}

export function WarningCard({ text }: { text: string }) {
  return (
    <section className="card warning-card" role="alert">
      <TriangleAlert size={20} aria-hidden="true" />
      <p>{text}</p>
    </section>
  );
}
