import { useEffect, useState } from "react";
import { CalendarDays, CalendarPlus, ChevronRight, ClipboardList, Headset, Mic, Phone, Pill } from "lucide-react";
import { BRAND, CALL_CENTER_HREF } from "../../brand";
import type { PatientData } from "../../hooks/usePatientData";
import type { Page } from "../AppShell";
import { ErrorLine, Loading, UpcomingAppointment } from "./common";

const TILES: { page: Page; label: string; hint: string; icon: typeof Pill }[] = [
  { page: "book", label: "Randevu al", hint: "Poliklinik, gün ve saat seçin", icon: CalendarPlus },
  { page: "appointments", label: "Randevularım", hint: "Yaklaşan randevular", icon: CalendarDays },
  { page: "visits", label: "Muayenelerim", hint: "Hekim onaylı özetler", icon: ClipboardList },
  { page: "meds", label: "İlaçlarım", hint: "Ne işe yarar, nasıl kullanılır", icon: Pill },
];

function greeting(): string {
  const hour = Number(
    new Intl.DateTimeFormat("tr-TR", { timeZone: "Europe/Istanbul", hour: "numeric", hourCycle: "h23" }).format(
      new Date(),
    ),
  );
  if (hour < 6) return "İyi geceler";
  if (hour < 12) return "Günaydın";
  if (hour < 18) return "İyi günler";
  return "İyi akşamlar";
}

/**
 * Ana sayfa: kurum karşılaması (maskeli ad), en yakın randevu, hızlı erişimler, asistan girişi, çağrı
 * merkezi kısayolu ve 112 notu.
 */
export function HomePage({ data, navigate }: { data: PatientData; navigate: (page: Page) => void }) {
  const { profile, appointments, loadAppointments } = data;

  useEffect(() => {
    void loadAppointments();
  }, [loadAppointments]);

  const next = appointments.data?.[0];
  // Karşılama ve tarih sayfa açılışında bir kez hesaplanır.
  const [{ hello, today }] = useState(() => ({
    hello: greeting(),
    today: new Intl.DateTimeFormat("tr-TR", {
      timeZone: "Europe/Istanbul",
      day: "numeric",
      month: "long",
      weekday: "long",
    }).format(new Date()),
  }));

  return (
    <div className="page">
      <section className="welcome" aria-labelledby="welcome-title">
        <p className="welcome-org">{BRAND.fullName}</p>
        <h1 id="welcome-title" data-page-title tabIndex={-1}>
          {profile ? `${hello}, ${profile.displayName}` : hello}
        </h1>
        <p className="welcome-date">{today}</p>
      </section>

      <section aria-labelledby="next-title" className="section">
        <div className="section-head">
          <h2 id="next-title">Yaklaşan randevunuz</h2>
          {(appointments.data?.length ?? 0) > 1 && (
            <button type="button" className="link-btn" onClick={() => navigate("appointments")}>
              Tümü ({appointments.data!.length}) <ChevronRight size={16} aria-hidden="true" />
            </button>
          )}
        </div>
        {next ? (
          <UpcomingAppointment appointment={next} />
        ) : appointments.error ? (
          <ErrorLine text={appointments.error} onRetry={() => void loadAppointments()} />
        ) : !appointments.data ? (
          <Loading />
        ) : (
          <div className="card empty-card">
            <p>Yaklaşan bir randevunuz yok.</p>
            <button type="button" className="btn-secondary" onClick={() => navigate("book")}>
              <CalendarPlus size={18} aria-hidden="true" /> Randevu al
            </button>
          </div>
        )}
      </section>

      <section aria-labelledby="quick-title" className="section">
        <h2 id="quick-title" className="section-title">
          Hızlı erişim
        </h2>
        <div className="home-grid">
          {TILES.map(({ page, label, hint, icon: Icon }) => (
            <button key={page} type="button" className="quick-tile" onClick={() => navigate(page)}>
              <Icon size={22} aria-hidden="true" />
              <strong>{label}</strong>
              <span>{hint}</span>
            </button>
          ))}
        </div>
      </section>

      <button type="button" className="assistant-cta" onClick={() => navigate("assistant")}>
        <span className="assistant-cta-icon" aria-hidden="true">
          <Mic size={24} />
        </span>
        <span>
          <strong>{BRAND.assistantName}</strong>
          <span>Konuşarak ya da yazarak randevu alın, ilaçlarınızı sorun</span>
        </span>
        <ChevronRight size={20} aria-hidden="true" />
      </button>

      <a className="call-card" href={CALL_CENTER_HREF}>
        <span className="call-card-icon" aria-hidden="true">
          <Headset size={22} />
        </span>
        <span className="call-card-text">
          <span>{BRAND.shortName} çağrı merkezi</span>
          <strong>{BRAND.callCenter.display}</strong>
          {BRAND.callCenter.note && <small>{BRAND.callCenter.note}</small>}
        </span>
        <span className="call-card-action">
          <Phone size={16} aria-hidden="true" /> Ara
        </span>
      </a>

      <p className="emergency-note">
        <Phone size={15} aria-hidden="true" />
        <span>
          Acil bir durumda bu uygulamayı beklemeyin: <a href="tel:112">112'yi arayın</a>.
        </span>
      </p>
    </div>
  );
}
