"""Testler için bellek içi sahte HastaKabul API'si (httpx.MockTransport).

Gerçek backend sözleşmesini taklit eder (tools/mock-api/server.js ile aynı kurallar):
hata zarfı (her hata, 404 dahil), X-Service-Key, SLOT_TAKEN, oturum VERIFIED şartı. Ağ kullanmaz.

Demo sağlamlaştırması (backend 2026-11-16): ``simdi`` verilirse başlangıcı geçmiş slot listelenmez ve
POST /appointments 409 SLOT_IN_PAST döner; hastanın etkin randevusuyla saati çakışan (poliklinik fark
etmez) ya da aynı İstanbul gününde aynı poliklinikten ikinci randevu 409 APPOINTMENT_CONFLICT; biçimsiz TC
400 VALIDATION_FAILED (deneme sayacı artmaz); süresi dolmuş oturumda verify 409 INVALID_STATUS_TRANSITION.
Bütün oturumlar aynı demo hastaya aittir (``hasta``).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx

ANAHTAR = "test-servis-anahtari"
ISTANBUL_FARK = timedelta(hours=3)

POLIKLINIKLER = {
    "DAHILIYE": ("Dahiliye", "Mehmet D."),
    "KARDIYOLOJI": ("Kardiyoloji", "Elif K."),
    "ORTOPEDI": ("Ortopedi", "Ahmet Y."),
    "GOZ": ("Göz Hastalıkları", "Zeynep A."),
    # Özel hastane (MedTech) branşları; Kadın Doğum ve Çocuk Sağlığı çok hekimli (HEKIMLER).
    "KBB": ("Kulak Burun Boğaz", "Op. Dr. Tolga A."),
    "DERMATOLOJI": ("Dermatoloji", "Uzm. Dr. Deniz K."),
    "KADIN_DOGUM": ("Kadın Hastalıkları ve Doğum", "Prof. Dr. Nermin T."),
    "COCUK_SAGLIGI": ("Çocuk Sağlığı ve Hastalıkları", "Uzm. Dr. Burak Ş."),
    "NOROLOJI": ("Nöroloji", "Doç. Dr. Kerem B."),
    "UROLOJI": ("Üroloji", "Op. Dr. Serkan T."),
    "GENEL_CERRAHI": ("Genel Cerrahi", "Prof. Dr. Levent S."),
}
# Çok hekimli poliklinikler: [(doctorId, görünen ad)]. Diğerlerinde tek hekim (POLIKLINIKLER[kod][1]).
HEKIMLER: dict[str, list[tuple[str, str]]] = {
    "KADIN_DOGUM": [("hekim-kd-1", "Prof. Dr. Nermin T."), ("hekim-kd-2", "Op. Dr. Selin E.")],
    "COCUK_SAGLIGI": [("hekim-cs-1", "Uzm. Dr. Burak Ş."), ("hekim-cs-2", "Doç. Dr. Ceren U.")],
}


def poliklinik_hekimleri(kod: str) -> list[tuple[str, str]]:
    return HEKIMLER.get(kod) or [(f"hekim-{kod.lower()}", POLIKLINIKLER[kod][1])]


def demo_ziyaretleri(bugun: date) -> list[dict]:
    """Demo hastanın geçmişi — gerçek backend seed'iyle (DemoDataSeeder) aynı: ~118 gün önce Dahiliye
    (Metformin + Demir sülfat, onaylı), ~46 gün önce Kardiyoloji (Amlodipin, onaylı), ~9 gün önce
    Ortopedi (onaysız; hiçbir zaman dönmez)."""
    def utc(gun: date, saat: int, dakika: int) -> str:
        return (datetime(gun.year, gun.month, gun.day, saat, dakika, tzinfo=timezone.utc) - ISTANBUL_FARK).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    return [
        {"onayli": True, "appointmentId": str(uuid.uuid4()),
         "scheduledAt": utc(bugun - timedelta(days=118), 10, 15),
         "clinicName": "Dahiliye", "doctorDisplayName": "Mehmet D.",
         "diagnosisPlain": ("Kan şekeriniz olması gerekenden yüksek seyrediyor (tip 2 diyabet). Ayrıca kanınızdaki "
                            "demir deposu azalmış; halsizliğiniz bundan kaynaklanıyor olabilir."),
         "medications": [
             {"name": "Metformin", "purpose": "Kan şekerinizi dengeler.",
              "usage": "Günde iki kez, sabah ve akşam yemekle birlikte birer tablet alın."},
             {"name": "Demir sülfat", "purpose": "Azalan demir deponuzu doldurur.",
              "usage": ("Günde bir kez, sabah aç karnına, portakal suyu gibi C vitamini içeren bir içecekle alın; "
                        "süt ve çayla birlikte almayın.")}],
         "warnings": ["Evde ölçtüğünüz kan şekeri değerlerini not edip kontrole getirin.",
                      "Demir ilacı dışkınızın rengini koyulaştırabilir; bu beklenen bir durumdur.",
                      "İlaçlarınızı kendi kararınızla bırakmayın."]},
        {"onayli": True, "appointmentId": str(uuid.uuid4()), "scheduledAt": utc(bugun - timedelta(days=46), 9, 30),
         "clinicName": "Kardiyoloji", "doctorDisplayName": "Elif K.",
         "diagnosisPlain": "Tansiyonunuz sürekli olarak yüksek seyrediyor (yüksek tansiyon).",
         "medications": [{"name": "Amlodipin", "purpose": "Damarlarınızı gevşeterek tansiyonunuzu düşürür.",
                          "usage": "Günde bir kez, her gün aynı saatte bir tablet alın."}],
         "warnings": ["Evde ölçtüğünüz tansiyon değerlerini kaydedin.", "Günlük tuz tüketiminizi azaltın."]},
        {"onayli": False, "appointmentId": str(uuid.uuid4()), "scheduledAt": utc(bugun - timedelta(days=9), 11, 0),
         "clinicName": "Ortopedi", "doctorDisplayName": "Ahmet Y.",
         "diagnosisPlain": "ONAYSIZ-OZET-METNI", "medications": [
             {"name": "Parasetamol", "purpose": "ONAYSIZ", "usage": "ONAYSIZ"}], "warnings": []},
    ]


@dataclass
class Slot:
    id: str
    poliklinik: str
    baslangic: datetime  # UTC
    alindi: bool = False
    hekim_id: str = ""
    hekim: str = ""


@dataclass
class SahteApi:
    gunler: list[date]
    slotlar: list[Slot] = field(default_factory=list)
    oturumlar: dict[str, dict] = field(default_factory=dict)
    randevular: list[dict] = field(default_factory=list)
    cagrilar: list[tuple[str, str]] = field(default_factory=list)
    hata_modu: str | None = None          # "ag", "500", "zaman_asimi"
    randevu_hata_modu: str | None = None  # yalnız POST /appointments için
    randevu_once_kaydet: bool = False     # zaman aşımından önce randevuyu yine de oluştur
    ziyaretler: list[dict] = field(default_factory=list)  # geçmiş muayeneler (onayli=False dönmez)
    gecmis_hata_modu: str | None = None    # yalnız medical-history için: "500", "ag"
    # Backend'de çözülemeyen poliklinik kodları (gerçek backend: 404 NOT_FOUND "Poliklinik bulunamadi").
    cozulemeyen_poliklinikler: set[str] = field(default_factory=set)
    # Gerçek backend saati: verilirse geçmiş slotlar listelenmez, geçmiş slota randevu 409 SLOT_IN_PAST.
    simdi: datetime | None = None
    slot_suresi: timedelta = timedelta(minutes=15)
    # Testin elle eklediği etkin randevular (çakışma kuralı için): [{"poliklinik", "baslangic"(UTC)}]
    onceki_randevular: list[dict] = field(default_factory=list)
    # POST /appointments her çağrıda bu (durum, kod, mesaj) hatasını döner (iş kuralı reddi benzetimi).
    randevu_hata_kodu: tuple[int, str, str] | None = None

    def __post_init__(self) -> None:
        for g in self.gunler:
            gun_baslangici = datetime(g.year, g.month, g.day, tzinfo=timezone.utc) - ISTANBUL_FARK
            for kod in POLIKLINIKLER:
                for hid, had in poliklinik_hekimleri(kod):
                    for dk in range(0, 180, 15):  # yerel 09:00–11:45
                        self.slotlar.append(Slot(str(uuid.uuid4()), kod,
                                                 gun_baslangici + timedelta(hours=9, minutes=dk), hekim_id=hid,
                                                 hekim=had))
        if not self.ziyaretler:
            self.ziyaretler = demo_ziyaretleri(self.gunler[0])

    # ---- test yardımcıları
    def oturum_ac(self, durum: str = "ACTIVE") -> str:
        oid = str(uuid.uuid4())
        self.oturumlar[oid] = {"status": durum, "attempts": 1 if durum == "VERIFIED" else 0}
        return oid

    def sure_doldur(self, oid: str) -> None:
        """Gerçek backend: oturum açılıştan 15 dk sonra süresi dolar. VERIFIED → EXPIRED; COMPLETED
        görünümde kalır ama geçmiş sorgusu 409 ile reddedilir."""
        o = self.oturumlar[oid]
        o["suresi_doldu"] = True
        if o["status"] in ("ACTIVE", "VERIFIED"):
            o["status"] = "EXPIRED"

    def slot_bul(self, slot_id: str) -> Slot:
        return next(s for s in self.slotlar if s.id == slot_id)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._isle)

    # ---- sözleşme
    @staticmethod
    def _hata(durum: int, kod: str, mesaj: str = "") -> httpx.Response:
        return httpx.Response(durum, json={"error": {"code": kod, "message": mesaj or kod, "requestId": "r"}})

    def _gorunum(self, oid: str) -> dict:
        o = self.oturumlar[oid]
        return {"sessionId": oid, "verified": o["status"] in ("VERIFIED", "COMPLETED"), "attempts": o["attempts"],
                "maxAttempts": 3, "status": o["status"]}

    def _isle(self, istek: httpx.Request) -> httpx.Response:
        yol = istek.url.path
        self.cagrilar.append((istek.method, yol))
        mod = self.randevu_hata_modu if (istek.method == "POST" and yol == "/api/v1/appointments") else self.hata_modu
        if self.randevu_hata_kodu and istek.method == "POST" and yol == "/api/v1/appointments":
            return self._hata(*self.randevu_hata_kodu)
        if mod == "ag":
            raise httpx.ConnectError("bağlantı reddedildi", request=istek)
        if mod == "500":
            return httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR", "message": "x", "requestId": "r"}})
        if mod in ("problem", "415"):
            # Gerçek backend: model doğrulama hatası hata zarfı değil ASP.NET ProblemDetails döner.
            durum = 415 if mod == "415" else 400
            return httpx.Response(durum, headers={"Content-Type": "application/problem+json"}, content=json.dumps(
                {"type": "https://tools.ietf.org/html/rfc9110#section-15.5.1", "title": "One or more validation "
                 "errors occurred.", "status": durum, "errors": {"slotId": ["The slotId field is required."]},
                 "traceId": "00-abc-01"}).encode())
        if mod == "400" and yol == "/api/v1/slots":
            return self._hata(400, "VALIDATION_FAILED", "date parametresi YYYY-MM-DD biciminde olmalidir.")
        if mod == "eszamanli" and yol == "/api/v1/appointments":
            # Güncel backend: aynı slota eşzamanlı ikinci istek benzersiz indekse takılır → 409 SLOT_TAKEN.
            slot = next((s for s in self.slotlar if s.id == json.loads(istek.content).get("slotId")), None)
            if slot is not None:
                slot.alindi = True
            self.randevu_hata_modu = None
            return self._hata(409, "SLOT_TAKEN", "Seçilen saat başka bir hasta tarafından alındı.")
        if mod == "cakisma" and yol == "/api/v1/appointments":
            # ESKİ backend (geriye uyum testi): aynı slota eşzamanlı iki istek → ikincisi Appointments.SlotId benzersiz
            # indeksine takılır (DbUpdateException) → 500 INTERNAL_ERROR (SLOT_TAKEN değil). Diğer
            # hastanın randevusu kalıcıdır, bu oturum VERIFIED kalır (işlem geri alınmıştır).
            slot = next((s for s in self.slotlar if s.id == json.loads(istek.content).get("slotId")), None)
            if slot is not None:
                slot.alindi = True
            self.randevu_hata_modu = None
            return httpx.Response(500, json={"error": {"code": "INTERNAL_ERROR", "message": "Beklenmeyen bir hata olustu.",
                                                       "requestId": "r"}})
        if mod == "zaman_asimi":
            if self.randevu_once_kaydet and yol == "/api/v1/appointments":
                self._randevu(json.loads(istek.content))
            raise httpx.ReadTimeout("zaman aşımı", request=istek)

        if yol.startswith("/api/v1/voice-sessions/") and yol.endswith("/medical-history") and istek.method == "GET":
            return self._gecmis(istek, yol.split("/")[4])
        if yol == "/api/v1/voice-sessions" and istek.method == "POST":
            oid = self.oturum_ac()
            return httpx.Response(201, json=self._gorunum(oid))
        if yol.startswith("/api/v1/voice-sessions/") and yol.endswith("/verify") and istek.method == "POST":
            return self._dogrula(yol.split("/")[4], json.loads(istek.content or b"{}"))
        if yol.startswith("/api/v1/voice-sessions/") and istek.method == "GET":
            oid = yol.rsplit("/", 1)[-1]
            if oid not in self.oturumlar:
                return self._hata(404, "VOICE_SESSION_NOT_FOUND")
            return httpx.Response(200, json=self._gorunum(oid))
        if yol in ("/api/v1/slots", "/api/v1/appointments"):
            if istek.headers.get("X-Service-Key") != ANAHTAR:
                return self._hata(401, "UNAUTHORIZED")
        if yol == "/api/v1/slots":
            kod = istek.url.params.get("clinic", "")
            gun = date.fromisoformat(istek.url.params["date"])
            if kod not in POLIKLINIKLER or kod in self.cozulemeyen_poliklinikler:
                return self._hata(404, "NOT_FOUND", f"Poliklinik bulunamadi: '{kod}'.")
            bas = datetime(gun.year, gun.month, gun.day, tzinfo=timezone.utc) - ISTANBUL_FARK
            liste = [s for s in self.slotlar
                     if s.poliklinik == kod and not s.alindi and bas <= s.baslangic < bas + timedelta(days=1)
                     and (self.simdi is None or s.baslangic > self.simdi)]
            liste.sort(key=lambda s: s.baslangic)
            return httpx.Response(200, json=[
                {"slotId": s.id, "startTime": s.baslangic.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "doctorDisplayName": s.hekim or POLIKLINIKLER[kod][1], "doctorId": s.hekim_id} for s in liste])
        if yol == "/api/v1/appointments" and istek.method == "POST":
            return self._randevu(json.loads(istek.content))
        return self._hata(404, "NOT_FOUND", "Kaynak bulunamadı.")

    def _dogrula(self, oid: str, govde: dict) -> httpx.Response:
        """POST /voice-sessions/{id}/verify {tc} (arayüzün işi; sözleşmeyi modellemek için)."""
        o = self.oturumlar.get(oid)
        if o is None:
            return self._hata(404, "VOICE_SESSION_NOT_FOUND")
        tc = str(govde.get("tc") or "")
        if not (len(tc) == 11 and tc.isdigit()):
            return self._hata(400, "VALIDATION_FAILED", "TC kimlik numarası 11 haneli olmalıdır.")  # sayaç artmaz
        if o.get("suresi_doldu") or o["status"] not in ("ACTIVE",):
            return self._hata(409, "INVALID_STATUS_TRANSITION", "Oturum bu işlem için uygun durumda değil.")
        if tc == "10000000146":
            o["status"] = "VERIFIED"
        else:
            o["attempts"] += 1  # yalnız başarısız denemeler sayılır
            if o["attempts"] >= 3:
                o["status"] = "HANDED_OFF"
        return httpx.Response(200, json=self._gorunum(oid))

    def _cakisma(self, slot: Slot) -> str | None:
        """Gerçek backend EnsureNoConflictAsync: önce saat çakışması, sonra aynı gün aynı poliklinik."""
        etkin = [*({"poliklinik": r["_poliklinik"], "baslangic": r["_baslangic"]} for r in self.randevular),
                 *self.onceki_randevular]
        bas, bit = slot.baslangic, slot.baslangic + self.slot_suresi
        if any(r["baslangic"] < bit and r["baslangic"] + self.slot_suresi > bas for r in etkin):
            return "Bu saatte başka bir randevunuz var; lütfen farklı bir saat seçin."
        gun = (slot.baslangic + ISTANBUL_FARK).date()
        if any(r["poliklinik"] == slot.poliklinik and (r["baslangic"] + ISTANBUL_FARK).date() == gun for r in etkin):
            return "Aynı gün bu poliklinikte zaten randevunuz var; lütfen başka bir gün seçin."
        return None

    def _gecmis(self, istek: httpx.Request, oid: str) -> httpx.Response:
        """GET /voice-sessions/{id}/medical-history?limit=3 (gerçek backend sözleşmesi)."""
        if self.gecmis_hata_modu == "ag":
            raise httpx.ConnectError("bağlantı reddedildi", request=istek)
        if self.gecmis_hata_modu == "500":
            return self._hata(500, "INTERNAL_ERROR")
        if istek.headers.get("X-Service-Key") != ANAHTAR:
            return self._hata(401, "UNAUTHORIZED")
        o = self.oturumlar.get(oid)
        if o is None:
            return self._hata(404, "VOICE_SESSION_NOT_FOUND")
        if o["status"] not in ("VERIFIED", "COMPLETED") or o.get("suresi_doldu"):
            return self._hata(409, "VOICE_SESSION_NOT_VERIFIED")
        ham = istek.url.params.get("limit", "3")
        if not ham.isdigit() or not 1 <= int(ham) <= 5:
            return self._hata(400, "VALIDATION_FAILED", "limit 1 ile 5 arasinda olmalidir.")
        limit = int(ham)
        onayli = sorted((z for z in self.ziyaretler if z["onayli"]), key=lambda z: z["scheduledAt"], reverse=True)
        return httpx.Response(200, json={"visits": [{k: v for k, v in z.items() if k != "onayli"}
                                                    for z in onayli[:limit]]})

    def _randevu(self, govde: dict) -> httpx.Response:
        o = self.oturumlar.get(govde.get("voiceSessionId", ""))
        if o is None:
            return self._hata(404, "VOICE_SESSION_NOT_FOUND")
        if o["status"] != "VERIFIED":
            return self._hata(409, "VOICE_SESSION_NOT_VERIFIED")
        slot = next((s for s in self.slotlar if s.id == govde.get("slotId")), None)
        if slot is None:
            return self._hata(404, "NOT_FOUND")
        if self.simdi is not None and slot.baslangic <= self.simdi:
            return self._hata(409, "SLOT_IN_PAST", "Seçilen saat geçmişte kaldı; lütfen ileri bir saat seçin.")
        if slot.alindi:
            return self._hata(409, "SLOT_TAKEN")
        cakisma = self._cakisma(slot)
        if cakisma:
            return self._hata(409, "APPOINTMENT_CONFLICT", cakisma)
        slot.alindi = True
        o["status"] = "COMPLETED"
        kod = f"{len(self.randevular) + 135790:06d}"
        r = {"appointmentId": str(uuid.uuid4()), "slotId": slot.id,
             "scheduledAt": slot.baslangic.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "doctorDisplayName": slot.hekim or POLIKLINIKLER[slot.poliklinik][1],
             "clinicName": POLIKLINIKLER[slot.poliklinik][0], "status": "SCHEDULED", "accessCode": kod}
        self.randevular.append({**r, "_poliklinik": slot.poliklinik, "_baslangic": slot.baslangic})
        return httpx.Response(200, json=r)
