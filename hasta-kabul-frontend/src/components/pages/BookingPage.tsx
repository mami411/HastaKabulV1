import { useEffect, useRef, useState } from "react";
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  Clock,
  RotateCw,
  Search,
  TriangleAlert,
  UsersRound,
  X,
} from "lucide-react";
import { clinicIcon, doctorInitials, fold, splitDoctorName } from "../../clinics";
import type { Assistant } from "../../hooks/useAssistant";
import {
  dayKey,
  dayKeyToDate,
  formatTime,
  relativeDay,
  useSessionErrors,
} from "../../hooks/usePatientData";
import {
  ApiError,
  createAppointment,
  getClinics,
  getSlotDays,
  getSlots,
  type CreatedAppointment,
  type PatientClinic,
  type PatientDoctor,
  type PatientSlot,
  type SlotDay,
} from "../../services/api";
import type { BookedAppointment } from "../../services/orchestrator";
import { AppointmentCard } from "../Cards";
import type { Page } from "../AppShell";
import { Loading, PageTitle } from "./common";

type Step = "clinic" | "doctor" | "day" | "time" | "confirm" | "result" | "uncertain";

/** Adım başlıkları; hekim adımı yalnız poliklinikte birden çok hekim varsa gösterilir. */
const STEP_TITLE: Record<Step, string> = {
  clinic: "Poliklinik seçin",
  doctor: "Hekim seçin",
  day: "Gün seçin",
  time: "Saat seçin",
  confirm: "Randevu özeti",
  result: "Randevunuz oluşturuldu",
  uncertain: "Sonuç doğrulanamadı",
};

const UNCERTAIN_TEXT =
  "Randevu isteğiniz gönderildi ama sonucu doğrulanamadı. Aynı saati yeniden almaya çalışmayın; " +
  "Randevularım sayfasından kontrol edin.";

type Notice = { tone: "warn" | "error"; text: string; code?: string };

const weekdayShort = new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", weekday: "short" });
const dayMonth = new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", day: "numeric", month: "short" });
const longDay = new Intl.DateTimeFormat("tr-TR", {
  timeZone: "Europe/Istanbul",
  day: "numeric",
  month: "long",
  weekday: "long",
});

function dayLabel(key: string): { top: string; bottom: string } {
  const date = dayKeyToDate(key);
  const today = dayKey(new Date());
  const tomorrow = dayKey(new Date(Date.now() + 24 * 3600 * 1000));
  const top = key === today ? "Bugün" : key === tomorrow ? "Yarın" : weekdayShort.format(date);
  return { top, bottom: dayMonth.format(date) };
}

/**
 * Asistansız randevu: poliklinik → (birden çok hekim varsa) hekim → gün → saat → özet ve açık onay →
 * sonuç (erişim kodu). Randevu yalnız "Randevuyu onayla" düğmesiyle oluşur; kurallar (çakışma, dolu ya
 * da geçmiş saat) asistanla aynı backend servisinden gelir.
 */
export function BookingPage({ a, navigate }: { a: Assistant; navigate: (page: Page) => void }) {
  const { sessionId } = a;
  const handleError = useSessionErrors(a);
  const [step, setStep] = useState<Step>("clinic");
  const [clinics, setClinics] = useState<PatientClinic[] | null>(null);
  const [clinic, setClinic] = useState<PatientClinic | null>(null);
  const [doctor, setDoctor] = useState<PatientDoctor | null>(null);
  const [days, setDays] = useState<SlotDay[] | null>(null);
  const [day, setDay] = useState("");
  const [slots, setSlots] = useState<PatientSlot[] | null>(null);
  const [slot, setSlot] = useState<PatientSlot | null>(null);
  const [result, setResult] = useState<CreatedAppointment | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [loadError, setLoadError] = useState("");
  /** Poliklinik araması (yalnız istemcide süzer; ağa gitmez). */
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  /** Yalnız en son isteğin yanıtı ekrana yazılır (hızlı geri/ileri geçişlerde). */
  const requestRef = useRef(0);

  async function load<T>(work: () => Promise<T>, apply: (value: T) => void) {
    const id = ++requestRef.current;
    setLoadError("");
    try {
      const value = await work();
      if (id === requestRef.current) apply(value);
    } catch (error) {
      if (id !== requestRef.current) return;
      const message = handleError(error, sessionId);
      if (message) setLoadError(message);
    }
  }

  const loadClinics = () => load(() => getClinics(sessionId), setClinics);
  const loadDays = (target: PatientClinic, who: PatientDoctor | null) => {
    setDays(null);
    return load(() => getSlotDays(sessionId, target.code, who?.doctorId), setDays);
  };
  const loadSlots = (target: PatientClinic, who: PatientDoctor | null, key: string) => {
    setSlots(null);
    return load(() => getSlots(sessionId, target.code, key, who?.doctorId), setSlots);
  };

  // Poliklinikler yalnız açılışta yüklenir; oturum değişirse kabuk zaten yeniden kurulur.
  const loadClinicsRef = useRef(loadClinics);
  useEffect(() => {
    void loadClinicsRef.current();
  }, []);

  // Adım değişince sayfa başa kaysın.
  useEffect(() => {
    document.querySelector<HTMLElement>(".page-scroll")?.scrollTo({ top: 0 });
  }, [step]);

  const multiDoctor = (clinic?.doctors.length ?? 0) > 1;
  const flow: Step[] = multiDoctor
    ? ["clinic", "doctor", "day", "time", "confirm"]
    : ["clinic", "day", "time", "confirm"];
  const stepIndex = flow.indexOf(step);

  function chooseClinic(target: PatientClinic) {
    setClinic(target);
    setDoctor(null);
    setDay("");
    setSlot(null);
    setNotice(null);
    if (target.doctors.length > 1) {
      setStep("doctor");
    } else {
      setStep("day");
      void loadDays(target, null);
    }
  }

  function chooseDoctor(who: PatientDoctor | null) {
    setDoctor(who);
    setNotice(null);
    setStep("day");
    if (clinic) void loadDays(clinic, who);
  }

  function chooseDay(key: string) {
    setDay(key);
    setSlot(null);
    setNotice(null);
    setStep("time");
    if (clinic) void loadSlots(clinic, doctor, key);
  }

  function chooseSlot(target: PatientSlot) {
    setSlot(target);
    setNotice(null);
    setStep("confirm");
  }

  function back() {
    setNotice(null);
    setLoadError("");
    requestRef.current += 1;
    const previous = flow[stepIndex - 1];
    if (!previous) return;
    setStep(previous);
    if (previous === "day" && clinic) void loadDays(clinic, doctor);
    if (previous === "time" && clinic && day) void loadSlots(clinic, doctor, day);
  }

  /** Çakışmadan sonra saat ya da gün adımına dönüş (liste yeniden yüklenir). */
  function goTo(target: "day" | "time") {
    setNotice(null);
    setStep(target);
    if (!clinic) return;
    if (target === "day") void loadDays(clinic, doctor);
    else void loadSlots(clinic, doctor, day);
  }

  function retryLoad() {
    if (step === "clinic") void loadClinics();
    else if (step === "day" && clinic) void loadDays(clinic, doctor);
    else if (step === "time" && clinic && day) void loadSlots(clinic, doctor, day);
  }

  function restart() {
    setClinic(null);
    setDoctor(null);
    setDay("");
    setSlot(null);
    setResult(null);
    setNotice(null);
    setQuery("");
    setStep("clinic");
    if (!clinics) void loadClinics();
  }

  /** Açık onay: tek istek; çift dokunuş ikinci randevu denemesi göndermez. */
  async function confirm() {
    if (!slot || !clinic || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setNotice(null);
    try {
      const created = await createAppointment(sessionId, slot.slotId);
      setResult(created);
      setStep("result");
    } catch (error) {
      if (!(error instanceof ApiError) || error.status === 0 || error.status >= 500) {
        // İstek sunucuya ulaşıp randevu oluşturmuş olabilir: sonuç "doğrulanamadı" sayılır.
        setStep("uncertain");
        return;
      }
      const message = handleError(error, sessionId);
      if (message === null) return; // oturum bitti, girişe dönüldü
      if (error.code === "SLOT_TAKEN" || error.code === "NOT_FOUND") {
        setNotice({ tone: "warn", text: "Bu saat az önce doldu. Lütfen başka bir saat seçin.", code: error.code });
        setStep("time");
        void loadSlots(clinic, doctor, day);
      } else if (error.code === "SLOT_IN_PAST") {
        setNotice({ tone: "warn", text: "Seçtiğiniz saat geçti. Lütfen ileri bir saat seçin.", code: error.code });
        setStep("time");
        void loadSlots(clinic, doctor, day);
      } else {
        // APPOINTMENT_CONFLICT ve diğerleri: backend'in Türkçe açıklaması özet ekranında gösterilir.
        setNotice({ tone: "error", text: message, code: error.code });
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  const booked: BookedAppointment | null = result
    ? {
        accessCode: result.accessCode,
        label: formatTime(result.scheduledAt),
        longLabel: `${longDay.format(new Date(result.scheduledAt))} ${formatTime(result.scheduledAt)}`,
        clinicName: result.clinicName,
        doctorDisplayName: result.doctorDisplayName,
      }
    : null;

  const showDoctorOnSlots = !doctor && multiDoctor;

  // Arama poliklinik adı, kodu ve hekim adlarında; Türkçe harf ve büyük/küçük farkı yok sayılır.
  const needle = fold(query.trim());
  const shownClinics =
    clinics && needle
      ? clinics.filter((item) =>
          fold([item.name, item.code, ...item.doctors.map((d) => d.displayName)].join(" ")).includes(needle),
        )
      : clinics;

  return (
    <div className="page">
      <PageTitle title="Randevu al" sub={STEP_TITLE[step]} />

      {stepIndex >= 0 && (
        <div className="stepper">
          {stepIndex > 0 ? (
            <button type="button" className="link-btn back-btn" onClick={back} disabled={submitting}>
              <ChevronLeft size={18} aria-hidden="true" /> Geri
            </button>
          ) : (
            <span />
          )}
          <span className="step-count" aria-label={`Adım ${stepIndex + 1} / ${flow.length}`}>
            {flow.map((item, index) => (
              <i key={item} className={index <= stepIndex ? "done" : ""} aria-hidden="true" />
            ))}
            <span aria-hidden="true">
              {stepIndex + 1}/{flow.length}
            </span>
          </span>
        </div>
      )}

      {stepIndex > 0 && step !== "confirm" && clinic && (
        <p className="choice-trail">
          {clinic.name}
          {doctor ? ` · ${doctor.displayName}` : ""}
          {day && step === "time" ? ` · ${relativeDay(dayKeyToDate(day).toISOString())}` : ""}
        </p>
      )}

      {notice && (
        <div className={`card ${notice.tone === "error" ? "conflict-card" : "warning-card"}`} role="alert">
          <TriangleAlert size={20} aria-hidden="true" />
          <div>
            <p>{notice.text}</p>
            {notice.code && <small>Kod: {notice.code}</small>}
          </div>
        </div>
      )}

      {loadError && (
        <div className="card warning-card" role="alert">
          <TriangleAlert size={20} aria-hidden="true" />
          <p>{loadError}</p>
          <button type="button" className="retry-btn" onClick={retryLoad}>
            <RotateCw size={16} aria-hidden="true" /> Tekrar dene
          </button>
        </div>
      )}

      {step === "clinic" &&
        (clinics ? (
          <>
            {clinics.length > 4 && (
              <div className="search-box">
                <Search size={18} aria-hidden="true" />
                <input
                  type="search"
                  name="poliklinik-ara"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Poliklinik ya da hekim arayın"
                  aria-label="Poliklinik ya da hekim arayın"
                  autoComplete="off"
                  enterKeyHint="search"
                  spellCheck={false}
                />
                {query && (
                  <button type="button" className="search-clear" onClick={() => setQuery("")} aria-label="Aramayı temizle">
                    <X size={18} aria-hidden="true" />
                  </button>
                )}
              </div>
            )}
            {needle && (
              <p className="sr-only" role="status">
                {shownClinics?.length ? `${shownClinics.length} poliklinik bulundu` : "Eşleşen poliklinik yok"}
              </p>
            )}
            {shownClinics && shownClinics.length > 0 ? (
              <ul className="clinic-grid" aria-label="Poliklinikler">
                {shownClinics.map((item) => {
                  const Icon = clinicIcon(item.name, item.code);
                  const count = item.doctors.length;
                  const doctors =
                    count === 0
                      ? "Hekim bilgisi yok"
                      : count === 1
                        ? item.doctors[0].displayName
                        : `${count} hekim`;
                  return (
                    <li key={item.code}>
                      <button type="button" className="clinic-tile" onClick={() => chooseClinic(item)}>
                        <span className="clinic-icon" aria-hidden="true">
                          <Icon size={22} />
                        </span>
                        <strong>{item.name}</strong>
                        <span>{doctors}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="card empty-card">
                <p>“{query.trim()}” ile eşleşen bir poliklinik bulunamadı.</p>
                <button type="button" className="btn-secondary" onClick={() => setQuery("")}>
                  Tüm poliklinikleri göster
                </button>
              </div>
            )}
          </>
        ) : (
          !loadError && <Loading text="Poliklinikler yükleniyor…" />
        ))}

      {step === "doctor" && clinic && (
        <ul className="choice-list" aria-label="Hekimler">
          <li>
            <button type="button" className="choice" onClick={() => chooseDoctor(null)}>
              <span className="choice-icon" aria-hidden="true">
                <UsersRound size={20} />
              </span>
              <span className="choice-text">
                <strong>Fark etmez</strong>
                <span>En erken uygun saatler, tüm hekimler</span>
              </span>
              <ChevronRight size={20} aria-hidden="true" />
            </button>
          </li>
          {clinic.doctors.map((item) => {
            const { title, name } = splitDoctorName(item.displayName);
            return (
              <li key={item.doctorId}>
                <button
                  type="button"
                  className="choice"
                  onClick={() => chooseDoctor(item)}
                  aria-label={`${item.displayName}, ${clinic.name}`}
                >
                  <span className="doctor-avatar" aria-hidden="true">
                    {doctorInitials(item.displayName)}
                  </span>
                  <span className="choice-text">
                    {title && <small className="doctor-title">{title}</small>}
                    <strong>{name}</strong>
                    <span>{clinic.name}</span>
                  </span>
                  <ChevronRight size={20} aria-hidden="true" />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {step === "day" &&
        (days ? (
          <>
            <ul className="day-grid" aria-label="Günler">
              {days.map((item) => {
                const label = dayLabel(item.date);
                const full = item.availableCount === 0;
                return (
                  <li key={item.date}>
                    <button
                      type="button"
                      className={`day-btn${day === item.date ? " selected" : ""}`}
                      onClick={() => chooseDay(item.date)}
                      disabled={full}
                      aria-label={`${label.top} ${label.bottom}, ${full ? "boş saat yok" : `${item.availableCount} boş saat`}`}
                    >
                      <strong>{label.top}</strong>
                      <span>{label.bottom}</span>
                      <em>{full ? "Dolu" : `${item.availableCount} boş`}</em>
                    </button>
                  </li>
                );
              })}
            </ul>
            {days.every((item) => item.availableCount === 0) && (
              <p className="page-note">
                Önümüzdeki günlerde bu poliklinikte boş saat yok. Başka bir poliklinik seçebilirsiniz.
              </p>
            )}
          </>
        ) : (
          !loadError && <Loading text="Günler yükleniyor…" />
        ))}

      {step === "time" &&
        (slots ? (
          slots.length > 0 ? (
            <ul className="time-grid" aria-label="Saatler">
              {slots.map((item) => (
                <li key={item.slotId}>
                  <button
                    type="button"
                    className={`slot-time${slot?.slotId === item.slotId ? " selected" : ""}`}
                    onClick={() => chooseSlot(item)}
                    aria-label={`${formatTime(item.startTime)}, ${item.doctorDisplayName}`}
                  >
                    {formatTime(item.startTime)}
                    {showDoctorOnSlots && <small>{splitDoctorName(item.doctorDisplayName).name}</small>}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="card empty-card">
              <p>Bu gün için boş saat kalmadı.</p>
              <button type="button" className="btn-secondary" onClick={back}>
                <CalendarDays size={18} aria-hidden="true" /> Başka gün seç
              </button>
            </div>
          )
        ) : (
          !loadError && <Loading text="Saatler yükleniyor…" />
        ))}

      {step === "confirm" && clinic && slot && (
        <section className="card confirm-card" aria-label="Randevu özeti">
          <dl className="facts">
            <div>
              <dt>Poliklinik</dt>
              <dd>{clinic.name}</dd>
            </div>
            <div>
              <dt>Hekim</dt>
              <dd>{slot.doctorDisplayName || "—"}</dd>
            </div>
            <div>
              <dt>Tarih ve saat</dt>
              <dd>
                {relativeDay(slot.startTime)} {formatTime(slot.startTime)}
              </dd>
            </div>
          </dl>
          {notice?.code !== "APPOINTMENT_CONFLICT" && (
            <p className="card-note">Onayladığınızda randevunuz oluşturulur ve bir erişim kodu verilir.</p>
          )}
          {notice?.code === "APPOINTMENT_CONFLICT" ? (
            <div className="confirm-actions">
              <button type="button" className="btn-secondary" onClick={() => goTo("time")}>
                <Clock size={18} aria-hidden="true" /> Başka saat
              </button>
              <button type="button" className="btn-secondary" onClick={() => goTo("day")}>
                <CalendarDays size={18} aria-hidden="true" /> Başka gün
              </button>
            </div>
          ) : (
            <button type="button" className="btn-primary" onClick={() => void confirm()} disabled={submitting}>
              <CircleCheck size={20} aria-hidden="true" />
              {submitting ? "Oluşturuluyor…" : "Randevuyu onayla"}
            </button>
          )}
        </section>
      )}

      {step === "result" && booked && (
        <>
          <AppointmentCard appointment={booked} />
          <div className="confirm-actions">
            <button type="button" className="btn-primary" onClick={() => navigate("appointments")}>
              Randevularım
            </button>
            <button type="button" className="btn-secondary" onClick={restart}>
              Yeni randevu
            </button>
          </div>
        </>
      )}

      {step === "uncertain" && (
        <>
          <div className="card warning-card" role="alert">
            <TriangleAlert size={20} aria-hidden="true" />
            <p>{UNCERTAIN_TEXT}</p>
          </div>
          <button type="button" className="btn-primary wide" onClick={() => navigate("appointments")}>
            Randevularımı kontrol et
          </button>
        </>
      )}
    </div>
  );
}
