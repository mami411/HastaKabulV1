/**
 * Sahte orkestratör: yalnız ses orkestratörüne ağ düzeyinde ulaşılamadığında ve yalnız
 * yan etkisiz olaylar (START, TC_VERIFIED, SLOT_SELECTED) için kullanılır. Randevu
 * oluşmuş gibi bir yanıt (DONE) ASLA üretmez; onay isteği hiçbir zaman buraya gelmez.
 */

type Slot = {
  slotId: string;
  label: string;
};

type OrchestratorInput = {
  sessionId: string;
  userText?: string;
  event?: "START" | "TC_VERIFIED" | "SLOT_SELECTED" | "CONFIRM" | "END" | "KEEPALIVE";
  data?: {
    slotId?: string;
  };
};

type OrchestratorResponse = {
  action: "ASK_TC" | "OFFER_SLOTS" | "CONFIRM" | "HANDOFF" | "DONE" | null;
  data: Record<string, unknown>;
  message: string;
};

const mockSlots: Slot[] = [
  { slotId: "slot-1", label: "09:30" },
  { slotId: "slot-2", label: "10:15" },
  { slotId: "slot-3", label: "11:00" },
];

const NOT_AVAILABLE =
  "Asistana ulaşılamadığı için şu an randevu oluşturulamıyor. Lütfen birazdan tekrar deneyin.";

export async function mockOrchestrator(
  input: OrchestratorInput,
): Promise<OrchestratorResponse> {
  if (input.event === "START") {
    return {
      action: "ASK_TC",
      data: {},
      message: "Lütfen kimlik numaranızı girin.",
    };
  }

  if (input.event === "TC_VERIFIED") {
    return {
      action: "OFFER_SLOTS",
      data: { slots: mockSlots },
      message: "Size uygun bir randevu saati seçin. (Örnek saatler; randevu oluşturulamaz.)",
    };
  }

  if (input.event === "SLOT_SELECTED") {
    const slot = mockSlots.find((item) => item.slotId === input.data?.slotId);

    if (!slot) {
      return { action: null, data: {}, message: NOT_AVAILABLE };
    }

    return {
      action: "CONFIRM",
      data: { slotId: slot.slotId, label: slot.label, slots: mockSlots },
      message: `${slot.label} örnek saati seçildi. ${NOT_AVAILABLE}`,
    };
  }

  // Onay ve serbest metin için sahte yanıt yoktur; eylem verilmez.
  return { action: null, data: {}, message: NOT_AVAILABLE };
}
