import { useEffect, useRef, useState } from "react";
import { BRAND } from "../brand";
import { IDLE_TIMEOUT_SEC, IDLE_WARNING_SEC, VOICE_LOOP_DEFAULT } from "../config";
import {
  ApiError,
  createVoiceSession,
  endVoiceSession,
  getVoiceSession,
  isValidTcFormat,
  verifyVoiceSession,
} from "../services/api";
import {
  askOrchestrator,
  BOOKING_UNCERTAIN_MESSAGE,
  digitCount,
  endOrchestratorSession,
  keepOrchestratorAlive,
  readAiGeneralInfo,
  readAppointment,
  readChoices,
  readHistory,
  readExpect,
  readEmergency,
  readListen,
  readSuggestion,
  readSlots,
  UNREACHABLE_MESSAGE,
  type BookedAppointment,
  type HistoryData,
  type OrchestratorResponse,
  type OrchestratorSlot,
} from "../services/orchestrator";
import { SttError, transcribe } from "../services/stt";
import { playTTS, stopTTS, unlockAudio } from "../services/tts";
import {
  captureUtterance,
  voiceCaptureSupported,
  type CaptureHandle,
} from "../services/voiceCapture";

export const TC_LENGTH = 11;
/** Bu kadar rakamdan fazlasını içeren serbest metin gönderilmez (kimlik numarası yalnız tuş takımından). */
const MAX_FREE_TEXT_DIGITS = 9;
/** Art arda bu kadar sessiz dinlemeden sonra eller serbest döngü durur. */
const MAX_SILENT_ATTEMPTS = 2;
/**
 * Uygulama açıkken orkestratöre KEEPALIVE aralığı (ms). Orkestratörün boşta kalma süresinden (5 dk) çok
 * kısa olmalı: asistan dışındaki sayfalarda gezinmek hareketsizlik sayılmaz.
 */
const KEEPALIVE_MS = 60_000;

/**
 * Oturumu her yerde kapatır: orkestratör belleği (END) ve backend oturumu (/end). Çıkış, kilit, süre
 * dolumu ve sayfa kapanışında çağrılır; oturum kimliğini bilen biri artık hasta uçlarını çağıramaz.
 */
function closeSession(sessionId: string) {
  endOrchestratorSession(sessionId);
  endVoiceSession(sessionId);
}

/** Whisper'ın sessizlikte uydurduğu tipik altyazı kalıpları: kullanıcı sözü sayılmaz. */
const STT_HALLUCINATIONS =
  /(altyaz[ıi]|abone ol|izledi[ğg]iniz i[çc]in|bir sonraki videoda|m\.k\.)/i;

const SYSTEM_UNREACHABLE =
  "Hastane sistemine şu an ulaşılamıyor. İnternet bağlantınızı kontrol edip birazdan tekrar deneyin.";
const API_UNAVAILABLE_MESSAGE =
  "Hastane sistemine şu an ulaşılamıyor. Birazdan tekrar deneyebilirsiniz.";
const TC_FORMAT_INVALID =
  "Girdiğiniz numara geçerli bir TC kimlik numarası değil; kontrol edip yeniden girin.";
const NUMBER_WARNING =
  "Kimlik ya da uzun numaraları buraya yazmayın veya söylemeyin; kimlik numarası yalnızca giriş ekranındaki tuş takımından girilir.";

/** login: TC tuş takımı; app: giriş sonrası uygulama (ana sayfa, randevu, ilaçlar, asistan sayfası). */
export type Screen = "login" | "app";
/** Randevu akışının neresindeyiz (kartların etkin olup olmadığını belirler). */
export type BookingStep = "none" | "slots" | "confirm" | "done";
export type Connection = "online" | "demo" | "offline";
export type VoicePhase = "idle" | "listening" | "transcribing" | "thinking" | "speaking";

/** Sohbet içindeki kartlar: saat önerileri, onay özeti, randevu (erişim kodu), geçmiş muayene, uyarı. */
export type Card =
  | { kind: "slots"; slots: OrchestratorSlot[]; unavailable: boolean }
  | { kind: "confirm"; slotId: string; label: string; detail: string }
  | { kind: "appointment"; appointment: BookedAppointment }
  | { kind: "history"; history: HistoryData }
  | { kind: "warning"; text: string }
  /** Şikâyetten önerilen poliklinik: "Evet, bakalım" / "Başka bölüm" (randevu onayı DEĞİL). */
  | { kind: "suggestion"; clinicName: string }
  /** Acil ifade: 112 yönlendirmesi; akış durumu değişmez. */
  | { kind: "emergency" };

export type ChatMessage = {
  id: number;
  role: "assistant" | "user";
  text: string;
  /** Kullanıcı sözü sesle mi geldi (balonda küçük işaret). */
  voice?: boolean;
  /** Asistan metni yapay zekâ ile üretilmiş genel sağlık bilgisi (balon altında küçük etiket). */
  aiGeneralInfo?: boolean;
  cards: Card[];
};

export type LoginNoticeKind = "locked" | "expired" | "reverify" | "ended" | "attempts" | "logout";
export type LoginNotice = { kind: LoginNoticeKind; title: string; text: string };

const NOTICES: Record<LoginNoticeKind, Omit<LoginNotice, "kind">> = {
  locked: {
    title: "Oturum kilitlendi",
    text: `${IDLE_TIMEOUT_SEC < 60 ? `${IDLE_TIMEOUT_SEC} saniye` : `${Math.round(IDLE_TIMEOUT_SEC / 60)} dakika`} işlem yapılmadığı için sohbetiniz güvenliğiniz gereği bu cihazdan silindi. Devam etmek için yeniden giriş yapın.`,
  },
  expired: {
    title: "Oturum süreniz doldu",
    text: "Güvenliğiniz için oturumlar 15 dakika sonra kapanır. Devam etmek için kimlik numaranızla yeniden giriş yapın.",
  },
  reverify: {
    title: "Yeniden doğrulama gerekiyor",
    text: "Bir süre işlem yapılmadığı için sağlık bilgilerinizi göstermeden önce kimliğinizi yeniden doğrulamamız gerekiyor.",
  },
  ended: {
    title: "Oturum kapandı",
    text: "Devam etmek için kimlik numaranızla yeniden giriş yapın.",
  },
  attempts: {
    title: "Giriş yapılamadı",
    text: `Kimliğiniz doğrulanamadı; ${BRAND.shortName} çağrı merkezini (${BRAND.callCenter.display}) arayabilir ya da daha sonra tekrar deneyebilirsiniz.`,
  },
  logout: {
    title: "Çıkış yaptınız",
    text: "Sohbetiniz, randevu kodunuz ve sağlık bilgileriniz bu cihazdan silindi.",
  },
};

/** Orkestratörün HANDOFF nedeni / hata kodu → giriş ekranında gösterilecek bildirim. */
function noticeForHandoff(reason: string, errorCode: string | undefined): LoginNoticeKind {
  if (reason === "TC_ATTEMPTS_EXHAUSTED") return "attempts";
  if (reason === "SESSION_IDLE" || errorCode === "SESSION_IDLE") return "reverify";
  if (reason === "SESSION_EXPIRED" || errorCode === "VOICE_SESSION_NOT_VERIFIED") return "expired";
  return "ended";
}

export type SystemError = { message: string; code: string; retry: "text" | "verified" };

/** Anlamlı bir söz mü? (boş, tek harf, yalnız noktalama ya da STT uydurması değil) */
function isMeaningful(text: string): boolean {
  const letters = text.match(/\p{L}/gu) ?? [];
  return letters.length >= 2 && !STT_HALLUCINATIONS.test(text);
}

export function useAssistant() {
  const [screen, setScreen] = useState<Screen>("login");
  const [loginNotice, setLoginNotice] = useState<LoginNotice | null>(null);
  const [tcInput, setTcInput] = useState("");
  const [tcError, setTcError] = useState("");

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [choices, setChoices] = useState<string[]>([]);
  /** Son yanıttaki poliklinik önerisi (çiplerde vurgulanır). */
  const [suggestedClinic, setSuggestedClinic] = useState("");
  const [bookingStep, setBookingStep] = useState<BookingStep>("none");
  const [selectedSlot, setSelectedSlot] = useState("");
  const [connection, setConnection] = useState<Connection>("online");
  const [systemError, setSystemError] = useState<SystemError | null>(null);
  /** Kısa durum notu (Sizi duyamadım, mikrofon açılamadı...); konuşma alanında gösterilir. */
  const [hint, setHint] = useState("");
  const [draft, setDraft] = useState("");

  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isSendingAudio, setIsSendingAudio] = useState(false);
  const [voiceLoop, setVoiceLoop] = useState(VOICE_LOOP_DEFAULT);
  /** Konuşma alanı: ses (büyük mikrofon) ya da yazı. Yazı modunda mikrofon kendiliğinden açılmaz. */
  const [inputMode, setInputModeState] = useState<"voice" | "text">("voice");
  /** Mikrofon izni gerçekten reddedildiyse true. */
  const [micDenied, setMicDenied] = useState(false);
  /** Kilitlenmeye kalan saniye (uyarı süresindeyken); değilse null. */
  const [idleRemaining, setIdleRemaining] = useState<number | null>(null);
  /** Doğrulanmış oturumun kimliği (asistansız sayfaların API çağrıları için); TC değildir. */
  const [sessionId, setSessionId] = useState("");

  /** Aynı karede iki dokunuşun iki istek göndermesini önler (state güncellemesi gecikmeli). */
  const inFlightRef = useRef(false);
  /** Çıkış/kilitte artar; eski oturumdan gecikmeli gelen yanıtlar yok sayılır. */
  const generationRef = useRef(0);
  const sessionRef = useRef("");
  const screenRef = useRef<Screen>("login");
  const voiceLoopRef = useRef(voiceLoop);
  const inputModeRef = useRef(inputMode);
  const captureRef = useRef<CaptureHandle | null>(null);
  const silentStreakRef = useRef(0);
  const lastActivityRef = useRef(0);
  const messageIdRef = useRef(0);
  /** Son gönderilen serbest metin: sistem hatasında "Tekrar dene" aynı sözü yeniden gönderir. */
  const lastTextRef = useRef("");
  /** Sohbette zaten gösterilen erişim kodları: sonraki yanıtlar aynı DONE verisini taşısa da kart yinelenmez. */
  const shownCodesRef = useRef(new Set<string>());
  /** Kullanıcı sesi kendisi durdurduysa "sesli yanıt çalınamadı" notu gösterilmez. */
  const userStoppedRef = useRef(false);
  /** Onay düğmesi işlenirken ikinci dokunuş yok sayılır. */
  const confirmingRef = useRef(false);
  /** Asistan sayfası ekranda mı: değilse ses çalınmaz, mikrofon açılmaz. */
  const assistantVisibleRef = useRef(false);
  /** Orkestratöre START + TC_VERIFIED gönderildi mi (asistan sayfası ilk açılışta). */
  const assistantStartedRef = useRef(false);

  useEffect(() => {
    screenRef.current = screen;
    voiceLoopRef.current = voiceLoop;
  });

  const busy = isProcessing || isSpeaking || isSendingAudio;
  /** İstek ya da ses tanıma sürüyor (yalnız okuma sürüyorsa değil): dokunmatik seçenekler kilitlenir. */
  const working = isSendingAudio || (isProcessing && !isSpeaking);
  const micLocked = screen !== "app" || isSendingAudio || isSpeaking || isProcessing;

  const phase: VoicePhase = isListening
    ? "listening"
    : isSendingAudio
      ? "transcribing"
      : isSpeaking
        ? "speaking"
        : isProcessing
          ? "thinking"
          : "idle";

  const isStale = (generation: number) => generation !== generationRef.current;

  /** Bir işlemi tek seferde çalıştırır; çıkış sonrası gelen sonucu yok saymak için nesil verir. */
  async function runExclusive(work: (generation: number) => Promise<void>) {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setIsProcessing(true);
    const generation = generationRef.current;
    try {
      await work(generation);
    } finally {
      // Çıkıştan sonra biten eski iş, yeni oturumun süren isteğinin kilidini açmasın.
      if (generation === generationRef.current) {
        inFlightRef.current = false;
        setIsProcessing(false);
      }
    }
  }

  function pushMessage(message: Omit<ChatMessage, "id" | "cards"> & { cards?: Card[] }) {
    messageIdRef.current += 1;
    const next: ChatMessage = { cards: [], ...message, id: messageIdRef.current };
    setMessages((current) => [...current, next]);
  }

  /** Süren dinlemeyi iptal eder (ses atılır). */
  function abortListening() {
    captureRef.current?.abort();
    captureRef.current = null;
    setIsListening(false);
  }

  /**
   * Her şeyi siler: sohbet, kartlar (erişim kodu, geçmiş muayene), çalan ses, dinleme;
   * orkestratöre END gönderilir ki sunucudaki oturum belleği de silinsin.
   */
  function resetAll() {
    generationRef.current += 1;
    inFlightRef.current = false;
    confirmingRef.current = false;
    assistantStartedRef.current = false;
    stopTTS();
    abortListening();
    silentStreakRef.current = 0;
    closeSession(sessionRef.current);
    sessionRef.current = "";
    setSessionId("");
    lastTextRef.current = "";
    shownCodesRef.current.clear();
    setIsSendingAudio(false);
    setIsProcessing(false);
    setIsSpeaking(false);
    setMessages([]);
    setChoices([]);
    setSuggestedClinic("");
    setBookingStep("none");
    setSelectedSlot("");
    setSystemError(null);
    setHint("");
    setDraft("");
    setTcInput("");
    setTcError("");
    setConnection("online");
    setIdleRemaining(null);
    setScreen("login");
    screenRef.current = "login";
    inputModeRef.current = "voice";
    setInputModeState("voice");
  }

  /** Oturumu bitirir ve giriş ekranında nedenini gösterir (çıkış, kilit, süre dolumu...). */
  function endSession(kind: LoginNoticeKind) {
    resetAll();
    setLoginNotice({ kind, ...NOTICES[kind] });
  }

  const endSessionRef = useRef(endSession);
  useEffect(() => {
    endSessionRef.current = endSession;
  });

  // Hareketsizlik kilidi: girişten sonraki tüm sayfalarda dokunma/tuş/konuşma yoksa önce uyarı, sonra kilit.
  useEffect(() => {
    if (screen !== "app") return;
    lastActivityRef.current = Date.now();
    const onActivity = () => {
      lastActivityRef.current = Date.now();
      setIdleRemaining(null);
    };
    const check = () => {
      if (inFlightRef.current) {
        lastActivityRef.current = Date.now(); // istek ya da okuma sürüyor
        return;
      }
      const idleSec = (Date.now() - lastActivityRef.current) / 1000;
      if (idleSec >= IDLE_TIMEOUT_SEC) {
        endSessionRef.current("locked");
      } else if (idleSec >= IDLE_TIMEOUT_SEC - IDLE_WARNING_SEC) {
        setIdleRemaining(Math.ceil(IDLE_TIMEOUT_SEC - idleSec));
      } else {
        setIdleRemaining(null);
      }
    };
    // Uygulama arka plandan dönünce (telefon kilidi, başka uygulama) süre hemen denetlenir.
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };
    window.addEventListener("pointerdown", onActivity);
    window.addEventListener("keydown", onActivity);
    document.addEventListener("visibilitychange", onVisible);
    const timer = window.setInterval(check, 1000);
    return () => {
      window.removeEventListener("pointerdown", onActivity);
      window.removeEventListener("keydown", onActivity);
      document.removeEventListener("visibilitychange", onVisible);
      window.clearInterval(timer);
    };
  }, [screen]);

  // Asistan açıldıktan sonra uygulamanın diğer sayfalarında gezinmek hareketsizlik değildir: oturum
  // kilitlenmemişken orkestratörün boşta kalma saati periyodik ileri alınır. Aksi halde 5 dk sonra asistana
  // dönen kullanıcı SESSION_IDLE ile tüm uygulamadan çıkarılırdı. Kilit (5 dk dokunma yok) END gönderir.
  useEffect(() => {
    if (screen !== "app") return;
    const timer = window.setInterval(() => {
      if (!assistantStartedRef.current || inFlightRef.current || !sessionRef.current) return;
      // Yalnız son aralıkta gerçek etkinlik (dokunma/tuş/konuşma) varsa: boştaki cihaz orkestratörü canlı
      // tutmaz, sunucudaki boşta kalma süresi yedek koruma olarak işler.
      // (İki aralık: etkinlikten sonra en az iki KEEPALIVE gider; iki KEEPALIVE arası 5 dk'yı aşmaz.)
      if (Date.now() - lastActivityRef.current > 2 * KEEPALIVE_MS) return;
      keepOrchestratorAlive(sessionRef.current);
    }, KEEPALIVE_MS);
    return () => window.clearInterval(timer);
  }, [screen]);

  // Mikrofon izni tarayıcıda açıkça reddedildiyse ipucu bunu söylesin.
  useEffect(() => {
    let status: PermissionStatus | null = null;
    navigator.permissions
      ?.query({ name: "microphone" as PermissionName })
      .then((result) => {
        status = result;
        setMicDenied(result.state === "denied");
        result.onchange = () => setMicDenied(result.state === "denied");
      })
      .catch(() => undefined);
    return () => {
      if (status) status.onchange = null;
    };
  }, []);

  // Sayfa kapanırken oturum orkestratörde de kapatılsın; önbellekten geri gelen sayfa temiz başlasın.
  useEffect(() => {
    const onHide = () => closeSession(sessionRef.current);
    const onShow = (event: PageTransitionEvent) => {
      if (event.persisted && sessionRef.current) endSessionRef.current("ended");
    };
    window.addEventListener("pagehide", onHide);
    window.addEventListener("pageshow", onShow);
    return () => {
      window.removeEventListener("pagehide", onHide);
      window.removeEventListener("pageshow", onShow);
    };
  }, []);

  async function speakResponse(text: string, generation = generationRef.current) {
    // Asistan sayfası kapalıyken gelen yanıt yalnız sohbete yazılır, okunmaz.
    if (isStale(generation) || !text || !assistantVisibleRef.current) return;
    setIsSpeaking(true);
    userStoppedRef.current = false;
    try {
      await playTTS(text);
    } catch {
      // Ses çalınamasa da akış durmaz: metin sohbette, döngü devam eder.
      if (!isStale(generation) && !userStoppedRef.current) {
        setHint("Sesli yanıt çalınamadı; yanıtları ekrandan okuyabilirsiniz.");
      }
    } finally {
      if (!isStale(generation)) setIsSpeaking(false);
    }
  }

  /** Okunmakta olan yanıtı keser (geçmiş muayene uzun olabilir); metin sohbette kalır. */
  function stopSpeaking() {
    userStoppedRef.current = true;
    stopTTS();
    setIsSpeaking(false);
  }

  /**
   * Asistan konuşurken kullanıcı bir seçeneğe dokunursa okuma kesilir ve süren yanıt işi
   * bitene kadar (en çok 1,5 sn) beklenir. Dönüş: yeni istek gönderilebilir mi.
   */
  async function interruptSpeech(): Promise<boolean> {
    if (isSendingAudio) return false;
    if (!isSpeaking) return !inFlightRef.current;
    stopSpeaking();
    abortListening();
    for (let waited = 0; inFlightRef.current && waited < 1500; waited += 30) {
      await new Promise((resolve) => window.setTimeout(resolve, 30));
    }
    return !inFlightRef.current;
  }

  /**
   * Orkestratör yanıtını tek yerde işler: sohbete asistan balonu + kartlar eklenir, yanıt okunur.
   * Randevu yalnız DONE + erişim kodu geldiğinde "oluşturuldu" sayılır.
   * Dönüş: yanıt sonrası mikrofon yeniden açılsın mı (orkestratörün data.listen ipucu).
   */
  async function applyOrchestratorResponse(
    response: OrchestratorResponse,
    generation: number,
  ): Promise<boolean> {
    if (isStale(generation)) return false;

    const apiDown = response.errorCode === "API_UNAVAILABLE";
    setConnection(
      response.simulated ? "demo" : response.unreachable || apiDown ? "offline" : "online",
    );
    if (response.unreachable) {
      // Asistana hiç ulaşılamadı: seslendirme de aynı serviste; yalnız uyarı şeridi gösterilir.
      setSystemError({
        message: UNREACHABLE_MESSAGE,
        code: response.errorCode ?? "UNKNOWN",
        retry: "text",
      });
      return false;
    }
    if (apiDown || response.data.retry === "TC_VERIFIED") {
      setSystemError({
        message: API_UNAVAILABLE_MESSAGE,
        code: response.errorCode ?? "UNKNOWN",
        retry: response.data.retry === "TC_VERIFIED" ? "verified" : "text",
      });
    } else {
      setSystemError(null);
    }

    setChoices(readChoices(response.data));
    const cards: Card[] = [];
    if (readEmergency(response.data)) cards.push({ kind: "emergency" });
    const suggestion = readSuggestion(response.data);
    setSuggestedClinic(suggestion?.clinicName ?? "");

    switch (response.action) {
      case "ASK_TC":
        // Oturum doğrulanmamış: sohbet silinir, kimlik yalnız giriş ekranındaki tuş takımından girilir.
        abortListening();
        stopTTS();
        setMessages([]);
        setChoices([]);
        setBookingStep("none");
        setSelectedSlot("");
        setTcInput("");
        setScreen("login");
        screenRef.current = "login";
        setLoginNotice({ kind: "ended", title: "Kimliğinizi doğrulayın", text: response.message });
        return false;

      case "OFFER_SLOTS": {
        const offered = readSlots(response.data);
        setSelectedSlot("");
        setBookingStep("slots");
        // Asistan henüz poliklinik/gün/saat soruyorsa yanıttaki genel saat listesi kart olarak
        // gösterilmez (soru ile kart çelişmesin); seçim beklenirken gösterilir.
        const expect = readExpect(response.data);
        const offering = expect === "" || expect === "secim" || expect === "onay";
        if ((offered.length > 0 && offering) || apiDown) {
          cards.push({ kind: "slots", slots: offered, unavailable: offered.length === 0 });
        }
        break;
      }

      case "CONFIRM": {
        const slotId = typeof response.data.slotId === "string" ? response.data.slotId : "";
        const offered = readSlots(response.data).find((slot) => slot.slotId === slotId);
        const label =
          typeof response.data.label === "string" ? response.data.label : (offered?.label ?? "");
        const detail =
          typeof response.data.detail === "string" ? response.data.detail : (offered?.detail ?? "");
        if (slotId) {
          setSelectedSlot(slotId);
          setBookingStep("confirm");
          // Acil (112) ya da veda yanıtının altına yeni bir "Seçimimi onayla" kartı eklenmez (çelişkili
          // görünür); bekleyen seçim önceki onay kartında durur.
          const sideReply = readEmergency(response.data) || response.data.listen === false;
          if (!sideReply) cards.push({ kind: "confirm", slotId, label, detail });
        }
        break;
      }

      case "HANDOFF": {
        const reason = typeof response.data.reason === "string" ? response.data.reason : "";
        if (reason === "BOOKING_UNCERTAIN") {
          setBookingStep("none");
          setSelectedSlot("");
          cards.push({ kind: "warning", text: BOOKING_UNCERTAIN_MESSAGE });
          break;
        }
        // Süre doldu / boşta kaldı / oturum kapandı (restart) ya da deneme hakkı bitti:
        // her şey silinir ve anlaşılır bir mesajla giriş ekranına dönülür.
        endSession(noticeForHandoff(reason, response.errorCode));
        return false;
      }

      case "DONE": {
        const booked = readAppointment(response.data);
        if (booked && !shownCodesRef.current.has(booked.accessCode)) {
          shownCodesRef.current.add(booked.accessCode);
          setBookingStep("done");
          setSelectedSlot("");
          cards.push({ kind: "appointment", appointment: booked });
        }
        break;
      }

      default:
        // Eylem yok (ör. anlaşılamayan söz, sistem hatası): bulunulan adımda kalınır.
        break;
    }

    if (suggestion) cards.push({ kind: "suggestion", clinicName: suggestion.clinicName });
    const history = readHistory(response.data);
    if (history) cards.push({ kind: "history", history });

    if (response.message || cards.length > 0) {
      pushMessage({
        role: "assistant",
        text: response.message,
        cards,
        aiGeneralInfo: readAiGeneralInfo(response.data),
      });
    }
    await speakResponse(response.message, generation);
    return readListen(response.data);
  }

  /** Yanıttan sonra, döngü açıksa ve orkestratör istiyorsa mikrofonu yeniden açar. */
  function maybeListen(listenHint: boolean, generation: number) {
    if (!listenHint || !voiceLoopRef.current || inputModeRef.current === "text") return;
    if (isStale(generation) || !assistantVisibleRef.current) return;
    // Uygulama arka plandayken (başka sekme/uygulama, ekran kilidi) mikrofon kendiliğinden açılmaz.
    if (document.visibilityState === "hidden") return;
    void listen(generation, false);
  }

  /**
   * Bir konuşmayı dinler, yazıya çevirir ve kendiliğinden gönderir ("Gönder" adımı yok).
   * Sessizlikte döngü bir kez daha dener; art arda MAX_SILENT_ATTEMPTS sessizlikte durur.
   */
  async function listen(generation: number, manual: boolean) {
    if (captureRef.current || inFlightRef.current || isStale(generation)) return;
    if (!sessionRef.current || screenRef.current !== "app" || !assistantVisibleRef.current) return;
    if (!voiceCaptureSupported()) {
      setHint("Tarayıcınız ses kaydını desteklemiyor. Mesajınızı yazabilirsiniz.");
      return;
    }
    if (manual) silentStreakRef.current = 0;

    let handle: CaptureHandle;
    try {
      handle = await captureUtterance(() => {
        lastActivityRef.current = Date.now(); // konuşma etkinlik sayılır
        setIdleRemaining(null);
      });
    } catch (error) {
      if (!isStale(generation)) {
        const name = error instanceof DOMException ? error.name : "";
        const denied = name === "NotAllowedError" || name === "SecurityError";
        setMicDenied(denied);
        setHint(
          denied
            ? "Mikrofon izni verilmedi. Tarayıcı ayarlarından izin verebilir ya da yazabilirsiniz."
            : "Mikrofon açılamadı. Mesajınızı yazabilirsiniz.",
        );
      }
      return;
    }
    setMicDenied(false);
    // Mikrofon açılırken başka bir istek başladıysa (ör. kullanıcı bir seçeneğe dokundu) ya da asistan
    // sayfasından çıkıldıysa dinlenmez: yanıt okunurken ve başka sayfadayken mikrofon kapalı kalmalı.
    if (isStale(generation) || inFlightRef.current || !assistantVisibleRef.current) {
      handle.abort();
      return;
    }
    captureRef.current = handle;
    setHint("");
    setIsListening(true);

    const result = await handle.result;
    if (captureRef.current === handle) captureRef.current = null;
    if (isStale(generation)) return;
    setIsListening(false);

    if (result.kind === "aborted") return;

    const retryOrStop = (note: string) => {
      silentStreakRef.current += 1;
      if (
        voiceLoopRef.current &&
        inputModeRef.current === "voice" &&
        silentStreakRef.current < MAX_SILENT_ATTEMPTS
      ) {
        void listen(generation, false);
      } else {
        setHint(`${note} Konuşmak için mikrofona dokunun ya da yazın.`);
      }
    };

    if (result.kind === "silence") {
      retryOrStop("Sizi duyamadım.");
      return;
    }

    setIsSendingAudio(true);
    let text = "";
    try {
      text = await transcribe(result.blob);
    } catch (error) {
      if (!isStale(generation)) {
        setIsSendingAudio(false);
        if (error instanceof SttError && (error.status === 400 || error.status === 413)) {
          // Servis çalışıyor, ses anlaşılamadı (boş/bozuk/çok uzun kayıt): "ulaşılamadı" denmez.
          retryOrStop("Sizi anlayamadım.");
        } else {
          setHint("Ses tanıma servisine ulaşılamadı. Mesajınızı yazabilirsiniz.");
        }
      }
      return;
    }
    if (isStale(generation)) return;
    setIsSendingAudio(false);

    if (digitCount(text) > MAX_FREE_TEXT_DIGITS) {
      // Kimlik/uzun numara sesle söylendi: metin gösterilmez, gönderilmez.
      silentStreakRef.current = 0;
      pushMessage({ role: "assistant", text: NUMBER_WARNING });
      await speakResponse(
        "Kimlik ya da uzun numaraları sesli söylemeyin; kimlik numarası yalnızca giriş ekranındaki tuş takımından girilir.",
        generation,
      );
      maybeListen(true, generation);
      return;
    }
    if (!isMeaningful(text)) {
      retryOrStop("Sizi anlayamadım.");
      return;
    }

    silentStreakRef.current = 0;
    await sendText(text, { voice: true, generation });
  }

  /** Serbest metni orkestratöre gönderir (sesli ya da yazılı); yanıttan sonra döngü sürer. */
  async function sendText(
    text: string,
    options: { voice?: boolean; echo?: boolean; generation?: number } = {},
  ) {
    const { voice = false, echo = true, generation = generationRef.current } = options;
    const userText = text.trim();
    if (!userText || !sessionRef.current || isStale(generation) || inFlightRef.current) return;

    if (screenRef.current !== "app" || digitCount(userText) > MAX_FREE_TEXT_DIGITS) {
      // Kimlik numarası serbest metinle orkestratöre hiç gitmez (yalnız tuş takımı → API).
      setDraft("");
      setHint(NUMBER_WARNING);
      return;
    }

    if (echo) pushMessage({ role: "user", text: userText, voice });
    setDraft("");
    setHint("");
    lastTextRef.current = userText;
    let listenHint = false;
    await runExclusive(async (gen) => {
      try {
        const response = await askOrchestrator({ sessionId: sessionRef.current, userText });
        listenHint = await applyOrchestratorResponse(response, gen);
      } catch {
        if (!isStale(gen)) setHint("Mesaj işlenemedi. Lütfen tekrar deneyin.");
      }
    });
    maybeListen(listenHint, generation);
  }

  /** Yazılı mesajı gönderir (konuşma alanındaki metin kutusu). */
  async function sendDraft() {
    if (!draft.trim() || !(await interruptSpeech())) return;
    abortListening();
    await sendText(draft);
  }

  /** Hızlı seçenek ya da hızlı eylem: yazılı söz gibi gönderilir (kişisel veri içermez). */
  async function sendQuick(text: string) {
    unlockAudio();
    if (!(await interruptSpeech())) return;
    abortListening();
    await sendText(text);
  }

  /** Mikrofon düğmesi: dinlerken "bitirdim", konuşurken "sesi durdur", aksi halde dinlemeye başla. */
  function pressMic() {
    if (captureRef.current) {
      captureRef.current.finish(); // o ana kadarki ses gönderilir
      return;
    }
    if (isSpeaking) {
      stopSpeaking();
      return;
    }
    if (micLocked) return;
    unlockAudio();
    void listen(generationRef.current, true);
  }

  function setInputMode(mode: "voice" | "text") {
    inputModeRef.current = mode;
    if (mode === "text") abortListening();
    setInputModeState(mode);
  }

  function toggleVoiceLoop() {
    setVoiceLoop((current) => {
      const next = !current;
      voiceLoopRef.current = next;
      if (!next) abortListening();
      return next;
    });
  }

  /**
   * API'nin doğruladığı oturumu orkestratöre bildirir. Orkestratör o an API'ye ulaşamazsa
   * "Tekrar dene" gösterilir; TC yeniden SORULMAZ (oturum API'de zaten doğrulanmıştır).
   */
  async function notifyVerified(generation: number): Promise<boolean> {
    const response = await askOrchestrator({ sessionId: sessionRef.current, event: "TC_VERIFIED" });
    return applyOrchestratorResponse(response, generation);
  }

  /** Doğrulama sonrası uygulamaya (ana sayfa) geçilir; asistan, sayfası ilk açıldığında başlatılır. */
  function enterApp() {
    setLoginNotice(null);
    setMessages([]);
    setChoices([]);
    setBookingStep("none");
    setSessionId(sessionRef.current);
    setScreen("app");
    screenRef.current = "app";
  }

  /**
   * Asistan sayfası açıldı. İlk açılışta orkestratöre START ve TC_VERIFIED gönderilir (karşılama okunur,
   * eller serbest açıksa mikrofon açılır). Orkestratör ilk istekten itibaren boşta kalma süresini saydığı
   * için bu, girişte değil asistan sayfasına ilk girişte yapılır.
   */
  async function enterAssistant() {
    assistantVisibleRef.current = true;
    if (assistantStartedRef.current || !sessionRef.current) return;
    const startGeneration = generationRef.current;
    let listenHint = false;
    await runExclusive(async (generation) => {
      assistantStartedRef.current = true;
      const start = await askOrchestrator({ sessionId: sessionRef.current, event: "START" });
      if (isStale(generation)) return;
      setConnection(start.simulated ? "demo" : "online");
      listenHint = await notifyVerified(generation);
    });
    maybeListen(listenHint, startGeneration);
  }

  /** Asistan sayfasından çıkıldı: okuma durur, dinleme iptal edilir; sohbet bellekte kalır. */
  function leaveAssistant() {
    assistantVisibleRef.current = false;
    userStoppedRef.current = true; // kesilen okuma için "çalınamadı" notu gösterilmez
    stopTTS();
    abortListening();
    setIsSpeaking(false);
  }

  /**
   * Asistansız sayfaların API çağrısı oturumun bittiğini söyledi (süre doldu, oturum bulunamadı):
   * her şey silinir ve girişe dönülür. Eski oturumdan gecikmeli gelen yanıt yok sayılır.
   */
  function sessionLost(forSessionId: string, kind: LoginNoticeKind) {
    if (!forSessionId || forSessionId !== sessionRef.current) return;
    endSession(kind);
  }

  /**
   * Giriş: gerekirse ses oturumu açılır, TC doğrudan API'ye gider; orkestratör TC'yi hiç görmez.
   * Doğrulama başarılıysa ana sayfaya geçilir; orkestratöre (START, TC_VERIFIED) asistan sayfası
   * ilk açıldığında yalnız olaylar iletilir.
   */
  async function login() {
    if (busy || inFlightRef.current) return;
    if (tcInput.length !== TC_LENGTH) {
      setTcError("Kimlik numarası 11 haneli olmalıdır.");
      return;
    }
    if (!isValidTcFormat(tcInput)) {
      // Biçimi bozuk numara ağa hiç gönderilmez; deneme hakkı etkilenmez.
      setTcError(TC_FORMAT_INVALID);
      return;
    }
    unlockAudio(); // telefon tarayıcısında sonraki sesli yanıtların çalabilmesi için
    const tc = tcInput;

    await runExclusive(async (generation) => {
      setTcError("");
      setLoginNotice(null);
      // Eski (süresi dolmuş) oturumla doğrulama reddedilirse bir kez yeni oturumla denenir.
      for (let round = 0; round < 2; round += 1) {
        try {
          if (!sessionRef.current) {
            const session = await createVoiceSession();
            if (isStale(generation)) return;
            sessionRef.current = session.sessionId;
          }

          const result = await verifyVoiceSession(sessionRef.current, tc);
          if (isStale(generation)) return;
          setTcInput("");

          if (result.handoff) {
            endSession("attempts");
            return;
          }
          if (!result.verified) {
            const remaining = Math.max(0, result.maxAttempts - result.attempts);
            setTcError(`Kimlik numarası doğrulanamadı. Kalan deneme hakkınız: ${remaining}.`);
            return;
          }

          enterApp();
          return;
        } catch (error) {
          if (isStale(generation)) return;
          if (error instanceof ApiError && error.code === "VALIDATION_FAILED") {
            // Sunucu biçimi geçersiz buldu: deneme hakkından düşülmedi, oturum sürer.
            setTcError(TC_FORMAT_INVALID);
            return;
          }
          if (error instanceof ApiError && error.code === "INVALID_STATUS_TRANSITION") {
            // İki olasılık: oturum zaten doğrulanmış (önceki bildirim yarıda kaldı) ya da süresi
            // dolmuş. Durum sunucudan okunur; yalnız VERIFIED ise uygulamaya geçilir.
            const status = await getVoiceSession(sessionRef.current)
              .then((session) => session.status)
              .catch(() => "");
            if (isStale(generation)) return;
            setTcInput("");
            if (status === "VERIFIED") {
              enterApp();
              return;
            }
            closeSession(sessionRef.current);
            sessionRef.current = "";
            setLoginNotice({ kind: "expired", ...NOTICES.expired });
            return;
          }
          const unreachable =
            !(error instanceof ApiError) || error.status === 0 || error.status >= 500;
          if (unreachable) {
            setTcError(SYSTEM_UNREACHABLE);
            return;
          }
          if (round === 0 && sessionRef.current && error.status === 404) {
            // Oturum bulunamadı (ör. sunucu yeniden başladı): sessizce yeni oturumla denenir.
            closeSession(sessionRef.current);
            sessionRef.current = "";
            continue;
          }
          // Backend mesajları anlaşılır Türkçe; kod yalnız teknik ayrıntıdır.
          setTcInput("");
          closeSession(sessionRef.current);
          sessionRef.current = "";
          setTcError(error.message || "Kimlik doğrulanamadı. Lütfen tekrar deneyin.");
          return;
        }
      }
    });
  }

  function pressDigit(digit: string) {
    setTcError("");
    setTcInput((current) => (current.length >= TC_LENGTH ? current : current + digit));
  }

  function pressBackspace() {
    setTcError("");
    setTcInput((current) => current.slice(0, -1));
  }

  async function selectSlot(slot: OrchestratorSlot) {
    unlockAudio();
    if (!(await interruptSpeech())) return;
    abortListening();
    const startGeneration = generationRef.current;
    let listenHint = false;
    pushMessage({ role: "user", text: slot.detail ? `${slot.label} · ${slot.detail}` : slot.label });

    await runExclusive(async (generation) => {
      try {
        const response = await askOrchestrator({
          sessionId: sessionRef.current,
          event: "SLOT_SELECTED",
          data: { slotId: slot.slotId },
        });
        // Seçim, orkestratör CONFIRM döndürünce işaretlenir.
        listenHint = await applyOrchestratorResponse(response, generation);
      } catch {
        if (!isStale(generation)) setHint("Randevu saati seçilemedi. Lütfen tekrar deneyin.");
      }
    });
    maybeListen(listenHint, startGeneration);
  }

  /**
   * Onay düğmesi açık bir CONFIRM olayı gönderir (serbest metin değil); randevu kaydını
   * orkestratör oluşturur. "Oluşturuldu" yalnız DONE + erişim kodu gelince gösterilir.
   * Bu istek ASLA sahte yanıtla karşılanmaz: ulaşılamazsa sonuç "doğrulanamadı" sayılır.
   */
  async function confirmAppointment(slotId: string) {
    if (bookingStep !== "confirm" || slotId !== selectedSlot || confirmingRef.current) return;
    // Çift dokunuş: ilk dokunuş okuma kesilmesini beklerken ikincisi ikinci "Onaylıyorum" eklemesin.
    confirmingRef.current = true;
    try {
      await confirmOnce(slotId);
    } finally {
      confirmingRef.current = false;
    }
  }

  async function confirmOnce(slotId: string) {
    unlockAudio();
    if (!(await interruptSpeech())) return;
    abortListening();
    const simulatedFlow = connection === "demo";
    const startGeneration = generationRef.current;
    let listenHint = false;
    // Uyarı şeridindeki "Tekrar dene" düğmeyle verilen onaydan önceki serbest sözü yeniden göndermesin;
    // onay kartı (düğmesi) yanıtla birlikte yeniden gelir.
    lastTextRef.current = "";
    pushMessage({ role: "user", text: "Onaylıyorum" });

    const uncertain = async (generation: number) => {
      setBookingStep("none");
      setSelectedSlot("");
      pushMessage({
        role: "assistant",
        text: "Randevu sonucunuzu doğrulayamadım.",
        cards: [{ kind: "warning", text: BOOKING_UNCERTAIN_MESSAGE }],
      });
      await speakResponse(BOOKING_UNCERTAIN_MESSAGE, generation);
    };

    await runExclusive(async (generation) => {
      try {
        const response = await askOrchestrator({
          sessionId: sessionRef.current,
          event: "CONFIRM",
          data: { slotId },
        });
        if (isStale(generation)) return;

        if (response.unreachable) {
          setConnection("offline");
          if (simulatedFlow) {
            // Örnek saatlerle ilerleniyordu: gerçek randevu isteği hiç yapılamazdı.
            setBookingStep("none");
            setSelectedSlot("");
            pushMessage({
              role: "assistant",
              text: "Asistana ulaşılamadığı için randevu oluşturulamadı. Lütfen birazdan tekrar deneyin.",
            });
            return;
          }
          await uncertain(generation);
          return;
        }
        listenHint = await applyOrchestratorResponse(response, generation);
      } catch {
        if (!isStale(generation)) await uncertain(generation);
      }
    });
    maybeListen(listenHint, startGeneration);
  }

  /** Uyarı şeridindeki "Tekrar dene": son sözü ya da TC_VERIFIED bildirimini yeniden gönderir. */
  async function retryAfterError() {
    if (busy || inFlightRef.current || !systemError) return;
    const startGeneration = generationRef.current;
    if (systemError.retry === "verified") {
      let listenHint = false;
      await runExclusive(async (generation) => {
        listenHint = await notifyVerified(generation);
      });
      maybeListen(listenHint, startGeneration);
      return;
    }
    const text = lastTextRef.current;
    if (!text) {
      setSystemError(null);
      return;
    }
    abortListening();
    await sendText(text, { echo: false });
  }

  function stayActive() {
    lastActivityRef.current = Date.now();
    setIdleRemaining(null);
  }

  return {
    screen,
    loginNotice,
    dismissLoginNotice: () => setLoginNotice(null),
    tcInput,
    tcError,
    pressDigit,
    pressBackspace,
    login,

    messages,
    choices,
    suggestedClinic,
    bookingStep,
    selectedSlot,
    connection,
    systemError,
    retryAfterError,
    hint,
    draft,
    setDraft,
    sendDraft,
    sendQuick,
    selectSlot,
    confirmAppointment,

    phase,
    busy,
    working,
    micLocked,
    micDenied,
    isListening,
    isSpeaking,
    voiceLoop,
    toggleVoiceLoop,
    pressMic,
    stopSpeaking,
    inputMode,
    setInputMode,

    idleRemaining,
    stayActive,
    logout: () => endSession("logout"),

    sessionId,
    sessionLost,
    enterAssistant,
    leaveAssistant,
  };
}

export type Assistant = ReturnType<typeof useAssistant>;
