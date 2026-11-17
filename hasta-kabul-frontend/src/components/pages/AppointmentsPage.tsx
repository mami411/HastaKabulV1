import { useEffect, useState } from "react";
import { CalendarPlus } from "lucide-react";
import { BRAND } from "../../brand";
import type { PatientData } from "../../hooks/usePatientData";
import type { Page } from "../AppShell";
import { ErrorLine, Loading, PageTitle, UpcomingAppointment, VisitCard } from "./common";

type Tab = "upcoming" | "past";

/**
 * Randevularım / Muayenelerim: yaklaşan randevular (asistanda alınanlar dahil, kaynak backend) ve
 * yalnız hekim onaylı özetlerden gelen geçmiş muayeneler.
 */
export function AppointmentsPage({
  data,
  navigate,
  initialTab,
}: {
  data: PatientData;
  navigate: (page: Page) => void;
  initialTab: Tab;
}) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const { appointments, visits, loadAppointments, loadVisits } = data;

  useEffect(() => {
    if (tab === "upcoming") void loadAppointments();
    else void loadVisits();
  }, [tab, loadAppointments, loadVisits]);

  return (
    <div className="page">
      <PageTitle
        title={tab === "upcoming" ? "Randevularım" : "Muayenelerim"}
        sub={tab === "upcoming" ? "Bugün ve sonrasındaki randevularınız" : "Hekiminizin onayladığı muayene özetleri"}
      />

      <div className="segmented" role="tablist" aria-label="Randevular ve muayeneler">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "upcoming"}
          className={tab === "upcoming" ? "selected" : ""}
          onClick={() => setTab("upcoming")}
        >
          Yaklaşan
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "past"}
          className={tab === "past" ? "selected" : ""}
          onClick={() => setTab("past")}
        >
          Geçmiş muayeneler
        </button>
      </div>

      {tab === "upcoming" ? (
        <section className="list" aria-label="Yaklaşan randevular">
          {appointments.error && <ErrorLine text={appointments.error} onRetry={() => void loadAppointments()} />}
          {!appointments.data && !appointments.error && <Loading />}
          {appointments.data?.map((appointment) => (
            <UpcomingAppointment key={appointment.appointmentId} appointment={appointment} />
          ))}
          {appointments.data?.length === 0 && (
            <div className="card empty-card">
              <p>Yaklaşan bir randevunuz yok.</p>
              <button type="button" className="btn-secondary" onClick={() => navigate("book")}>
                <CalendarPlus size={18} aria-hidden="true" /> Randevu al
              </button>
            </div>
          )}
          {(appointments.data?.length ?? 0) > 0 && (
            <p className="page-note">
              Hastaneye geldiğinizde erişim kodunuzu karşılama bankosunda gösterebilirsiniz.{" "}
              {BRAND.assistantName} ile aldığınız randevular da burada görünür.
            </p>
          )}
        </section>
      ) : (
        <section className="list" aria-label="Geçmiş muayeneler">
          {visits.error && <ErrorLine text={visits.error} onRetry={() => void loadVisits()} />}
          {!visits.data && !visits.error && <Loading />}
          {visits.data?.map((visit) => <VisitCard key={visit.appointmentId} visit={visit} />)}
          {visits.data?.length === 0 && (
            <div className="card empty-card">
              <p>Hekiminizin onayladığı bir muayene özeti bulunmuyor.</p>
            </div>
          )}
          {(visits.data?.length ?? 0) > 0 && (
            <p className="page-note">
              Yalnız hekiminizin onayladığı özetler gösterilir. Sorunuz olursa hekiminize ya da eczacınıza
              danışın.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
