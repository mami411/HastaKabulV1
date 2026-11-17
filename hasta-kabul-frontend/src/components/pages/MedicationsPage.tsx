import { useEffect } from "react";
import { ClipboardList, Pill } from "lucide-react";
import type { PatientData } from "../../hooks/usePatientData";
import { formatDate } from "../../hooks/usePatientData";
import type { Page } from "../AppShell";
import { ErrorLine, Loading, PageTitle } from "./common";

/**
 * İlaçlarım: geçmiş muayenelerde yazılan ilaçlar (yalnız hekim onaylı özetlerden), en yeni muayeneden
 * başlayarak. Her ilaçta ne işe yaradığı, nasıl kullanılacağı ve hangi muayenede yazıldığı yazılır.
 */
export function MedicationsPage({ data, navigate }: { data: PatientData; navigate: (page: Page) => void }) {
  const { visits, loadVisits } = data;

  useEffect(() => {
    void loadVisits();
  }, [loadVisits]);

  const medications =
    visits.data?.flatMap((visit) => visit.medications.map((med) => ({ med, visit }))) ?? [];

  return (
    <div className="page">
      <PageTitle title="İlaçlarım" sub="Muayenelerinizde yazılan ilaçlar" />

      {visits.error && <ErrorLine text={visits.error} onRetry={() => void loadVisits()} />}
      {!visits.data && !visits.error && <Loading />}

      {visits.data && medications.length === 0 && (
        <div className="card empty-card">
          <p>Hekim onaylı muayene özetlerinizde yazılmış bir ilaç bulunmuyor.</p>
        </div>
      )}

      <ul className="list" aria-label="İlaçlar">
        {medications.map(({ med, visit }) => (
          <li key={`${visit.appointmentId}-${med.name}`} className="card med-card">
            <header className="med-head">
              <span className="med-icon" aria-hidden="true">
                <Pill size={20} />
              </span>
              <h2>{med.name}</h2>
            </header>
            {med.purpose && (
              <div>
                <span className="field-label">Ne işe yarar</span>
                <p>{med.purpose}</p>
              </div>
            )}
            {med.usage && (
              <div>
                <span className="field-label">Nasıl kullanılır</span>
                <p className="med-usage">{med.usage}</p>
              </div>
            )}
            <p className="med-source">
              {formatDate(visit.scheduledAt)} · {visit.clinicName}
              {visit.doctorDisplayName ? ` · ${visit.doctorDisplayName}` : ""} muayenesinde yazıldı
            </p>
          </li>
        ))}
      </ul>

      {medications.length > 0 && (
        <>
          <button type="button" className="btn-secondary wide" onClick={() => navigate("visits")}>
            <ClipboardList size={18} aria-hidden="true" /> Muayene özetlerini gör
          </button>
          <p className="page-note">
            Bilgiler hekiminizin onayladığı özetlerden gelir. İlacınızı bırakmadan ya da dozunu
            değiştirmeden önce hekiminize ya da eczacınıza danışın.
          </p>
        </>
      )}
    </div>
  );
}
