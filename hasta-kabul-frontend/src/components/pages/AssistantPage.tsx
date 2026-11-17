import { useEffect, useRef } from "react";
import type { Assistant } from "../../hooks/useAssistant";
import type { PatientData } from "../../hooks/usePatientData";
import { ChatScreen } from "../ChatScreen";

/**
 * Asistan ayrı bir sayfadır: açılınca (ilk kez) orkestratöre START + TC_VERIFIED gider, karşılama okunur;
 * çıkınca okuma durur ve mikrofon kapanır, sohbet bellekte kalır (çıkışta silinir).
 */
export function AssistantPage({ a, data, onBack }: { a: Assistant; data: PatientData; onBack: () => void }) {
  const assistantRef = useRef(a);
  useEffect(() => {
    assistantRef.current = a;
  });

  useEffect(() => {
    void assistantRef.current.enterAssistant();
    return () => assistantRef.current.leaveAssistant();
  }, []);

  // Yaklaşan randevu: açılışta ve asistanda randevu oluşunca backend'den yenilenir.
  const { loadAppointments } = data;
  const booked = a.bookingStep === "done";
  useEffect(() => {
    void loadAppointments();
  }, [booked, loadAppointments]);

  return <ChatScreen a={a} onBack={onBack} upcoming={data.appointments.data ?? []} />;
}
