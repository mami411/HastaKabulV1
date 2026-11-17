import { useCallback, useEffect, useRef, useState } from "react";
import { CalendarDays, CalendarPlus, House, LogOut, Mic, Pill } from "lucide-react";
import { BRAND } from "../brand";
import type { Assistant } from "../hooks/useAssistant";
import { usePatientData } from "../hooks/usePatientData";
import { Logo } from "./Logo";
import { AppointmentsPage } from "./pages/AppointmentsPage";
import { AssistantPage } from "./pages/AssistantPage";
import { BookingPage } from "./pages/BookingPage";
import { HomePage } from "./pages/HomePage";
import { MedicationsPage } from "./pages/MedicationsPage";

/** visits: Randevularım sayfasının "Geçmiş muayeneler" sekmesiyle açılışı. */
export type Page = "home" | "book" | "appointments" | "visits" | "meds" | "assistant";

const PAGES: readonly Page[] = ["home", "book", "appointments", "visits", "meds", "assistant"];

const TITLES: Record<Page, string> = {
  home: "Ana sayfa",
  book: "Randevu al",
  appointments: "Randevularım",
  visits: "Muayenelerim",
  meds: "İlaçlarım",
  assistant: BRAND.assistantName,
};

/** Alt gezinme: ortada (asistan) yuvarlak, vurgulu düğme. */
const NAV: { page: Page; label: string; icon: typeof House }[] = [
  { page: "home", label: "Ana sayfa", icon: House },
  { page: "book", label: "Randevu al", icon: CalendarPlus },
  { page: "assistant", label: "Asistan", icon: Mic },
  { page: "appointments", label: "Randevularım", icon: CalendarDays },
  { page: "meds", label: "İlaçlarım", icon: Pill },
];

/** Kilit uyarısı (son 30 sn); asistan sayfasında aynı şerit konuşma alanında gösterilir. */
export function IdleBanner({ a }: { a: Assistant }) {
  if (a.idleRemaining === null) return null;
  return (
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
  );
}

const readPage = (state: unknown): Page => {
  const page = (state as { page?: unknown } | null)?.page;
  return PAGES.find((item) => item === page) ?? "home";
};

/**
 * Girişten sonraki uygulama: ana sayfa, asistansız randevu, randevularım/muayenelerim, ilaçlarım ve
 * ayrı bir sayfa olarak sesli asistan. Sayfa geçişleri tarayıcı geçmişine yazılır (telefonun geri
 * tuşu uygulamadan çıkmak yerine önceki sayfaya döner). Çıkışta bu bileşen kaldırılır; veri bellekte
 * kalmaz.
 */
export function AppShell({ a }: { a: Assistant }) {
  const [page, setPage] = useState<Page>("home");
  const pageRef = useRef<Page>("home");
  const data = usePatientData(a);

  useEffect(() => {
    window.history.replaceState({ page: "home" }, "");
    const onPop = (event: PopStateEvent) => {
      pageRef.current = readPage(event.state);
      setPage(pageRef.current);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((next: Page) => {
    if (pageRef.current === next) return;
    pageRef.current = next;
    window.history.pushState({ page: next }, "");
    setPage(next);
  }, []);

  // Yeni sayfa başa kaysın; odak sayfa başlığına gitsin (ekran okuyucu yeni sayfayı duysun).
  useEffect(() => {
    document.querySelector<HTMLElement>(".page-scroll")?.scrollTo({ top: 0 });
    document.querySelector<HTMLElement>("[data-page-title]")?.focus({ preventScroll: true });
  }, [page]);

  if (page === "assistant") {
    return <AssistantPage a={a} data={data} onBack={() => navigate("home")} />;
  }

  return (
    <>
      <header className="app-header">
        <div className="brand-mini">
          <Logo size={36} />
          <div>
            <strong className="brand-name">{BRAND.shortName}</strong>
            <span className="header-sub">{TITLES[page]}</span>
          </div>
        </div>
        <button type="button" className="logout" onClick={a.logout}>
          <LogOut size={18} aria-hidden="true" />
          Çıkış
        </button>
      </header>

      <main className="page-scroll" aria-label={TITLES[page]}>
        {page === "home" && <HomePage data={data} navigate={navigate} />}
        {page === "book" && <BookingPage a={a} navigate={navigate} />}
        {(page === "appointments" || page === "visits") && (
          <AppointmentsPage
            key={page}
            data={data}
            navigate={navigate}
            initialTab={page === "visits" ? "past" : "upcoming"}
          />
        )}
        {page === "meds" && <MedicationsPage data={data} navigate={navigate} />}
      </main>

      {a.idleRemaining !== null && (
        <div className="shell-banner">
          <IdleBanner a={a} />
        </div>
      )}

      <nav className="tabbar" aria-label="Ana gezinme">
        {NAV.map(({ page: target, label, icon: Icon }) => {
          const current = page === target || (target === "appointments" && page === "visits");
          return (
            <button
              key={target}
              type="button"
              className={`tab${target === "assistant" ? " tab-assistant" : ""}${current ? " current" : ""}`}
              aria-current={current ? "page" : undefined}
              onClick={() => navigate(target)}
            >
              <span className="tab-icon">
                <Icon size={target === "assistant" ? 24 : 22} aria-hidden="true" />
              </span>
              <span className="tab-label">{label}</span>
            </button>
          );
        })}
      </nav>
    </>
  );
}
