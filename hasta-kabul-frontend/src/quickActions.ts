import { CalendarPlus, ClipboardList, Pill } from "lucide-react";

/** Girişten sonra sunulan hızlı eylemler: yazılı söz gibi gönderilir (kişisel veri içermez). */
export const QUICK_ACTIONS = [
  { label: "Randevu al", text: "Randevu almak istiyorum", hint: "Poliklinik ve saat seçin", icon: CalendarPlus },
  { label: "İlaçlarım", text: "İlaçlarım", hint: "Son reçetenizdeki ilaçlar", icon: Pill },
  { label: "Son muayenem", text: "Son muayenem", hint: "Hekim onaylı özet", icon: ClipboardList },
] as const;
