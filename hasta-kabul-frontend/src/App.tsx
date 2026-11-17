import { AppShell } from "./components/AppShell";
import { LoginScreen } from "./components/LoginScreen";
import { useAssistant } from "./hooks/useAssistant";
import "./App.css";
import "./pages.css";

/**
 * Kurumun cep uygulaması (ad, renk ve logo: src/brand.ts).
 * Telefonda tam ekran; masaüstünde ortalanmış telefon genişliğinde bir uygulama çerçevesi.
 * Giriş (TC tuş takımı) → uygulama: ana sayfa, asistansız randevu, randevularım/muayenelerim,
 * ilaçlarım ve ayrı bir sayfa olarak sesli asistan (sesle/yazıyla).
 */
function App() {
  const assistant = useAssistant();

  return (
    <div className="stage">
      <div className={`app app-${assistant.screen}`}>
        {assistant.screen === "login" ? (
          <LoginScreen a={assistant} />
        ) : (
          <AppShell a={assistant} />
        )}
      </div>
    </div>
  );
}

export default App;
