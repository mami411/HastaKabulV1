"""Mobil cep sağlık asistanı konuşma orkestratörü: deterministik durum makinesi + yuva doldurma diyaloğu.

Durumlar:  TC_BEKLENIYOR → SLOT_SECIMI → ONAY_BEKLENIYOR → TAMAMLANDI   (+ DEVIR)
SLOT_SECIMI içinde ``beklenen`` alt adımı: poliklinik → (hekim) → gün → saat (gün parçası) → seçim.
"hekim" yalnız çok hekimli poliklinikte sorulur (hekim adları /slots yanıtından; LLM'e gitmez).
Kurum soruları (çağrı merkezi, adres, çalışma saatleri, ücret, sigorta) her adımda sabit şablonla yanıtlanır.
Eksik bilgi neyse o sorulur; tek cümlede hepsi verilirse sorular atlanır.

Düğme yolu LLM'siz tamamlanır:
    START → ASK_TC → (arayüz TC'yi API'ye yollar) → TC_VERIFIED → OFFER_SLOTS
          → SLOT_SELECTED → CONFIRM → "Onaylıyorum" → POST /appointments → DONE

Kurallar:
- TC_VERIFIED olayına güvenilmez; doğrulama API'den (GET /voice-sessions/{id}) okunur.
- Müsaitlik sorgusu yan etkisizdir (kilit yok); rezervasyon yalnız açık onayda
  (kapalı onay listesi ya da düğmeden CONFIRM) POST /appointments ile yapılır.
- KVKK: kullanıcıya söylenen cümleler koddan gelir (konu modülündeki şablonlar + API verisi). TEK istisna
  genel sağlık/ilaç bilgi yoludur (``saglik_bilgisi``, ``SAGLIK_BILGI_LLM``): yerel LLM'e yalnız kimliği
  maskelenmiş soru gider; çıktı kodda denetlenir, sabit kapanış koddan eklenir, ``data.aiGeneralInfo`` işaretlenir.
  Hasta kaydı (ad, kod, muayene, reçete, randevu) bu yola hiç girmez. Şikâyet için tavsiye isteğinde
  (``tavsiye``, ``_tavsiye_yolu``) LLM'e kullanıcının cümlesi bile gitmez: konu ve soru kapalı listelerden kurulur.
- Geçmiş muayene verisi yalnız oturum belleğinde tutulur, loglanmaz; END olayında, boşta
  kalmada, devirde ve oturum ömrü dolunca silinir.
- Sistem hatası (ağ, 5xx, zaman aşımı) kullanıcıya "dolu/yok" diye yansıtılmaz (D4-b).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time as _time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from types import ModuleType
from typing import Any

from ortak.llm import LLMSaglayici
from ortak.turkce import ifade_gecer_mi, normalize

from .anlama import (
    Anlam,
    acil_mi,
    anla,
    ilac_konusu_sor,
    kural_tabanli_anla,
    kurum_sorusu,
    metindeki_poliklinik,
)
from .api_istemcisi import ApiHatasi, ApiOlcum, HastaKabulApi
from .gecmis import (
    Ilac,
    Ziyaret,
    _ad_eslesir_mi,
    gecmis_metni,
    hekim_suz,
    ilac_adayi_var_mi,
    ilac_bul,
    poliklinik_ziyaretleri,
    ziyaretleri_ayristir,
)
from .ilac_bilgisi import (
    ILAC_MASKESI,
    OZET_KONULARI,
    dagarcik_bilgisi,
    ilac_adlarini_maskele,
    konu_bul,
    konu_metni,
    soru_mu,
    veri_konusu,
)
from .ilac_tarifi import Tarif, tarif_bul, tarif_coz, tarif_metni
from .llm_siniri import AD_MASKESI, kisisel_maskele, saglik_tavsiye_yuku_kur
from .saglik_bilgisi import (
    SaglikKarari,
    _spesifik_neden,
    dagarcik_kaydi_bul,
    genel_bilgi_uret,
    ilac_adlari,
    saglik_karari,
    spesifik_metni,
    yanit_birlestir,
)
from .tavsiye import (
    alt_sorular,
    kisisel_sikayet_var,
    konu_adi,
    konu_poliklinigi,
    randevu_reddi,
    sikayet_konusu,
    tavsiye_engeli,
    tavsiye_istegi,
)
from .zaman import (
    SaatKaynagi,
    goreli_gun_metni,
    simdi_utc,
    slot_etiketi,
    slot_uzun_etiket,
    tarih_metni,
    utc_coz,
    yerel,
    yerel_gun,
    yerel_saat,
)

log = logging.getLogger("ses_servisi.orkestrator")

TC_BEKLENIYOR = "TC_BEKLENIYOR"
SLOT_SECIMI = "SLOT_SECIMI"
ONAY_BEKLENIYOR = "ONAY_BEKLENIYOR"
TAMAMLANDI = "TAMAMLANDI"
DEVIR = "DEVIR"

# CONFIRM: ekrandaki "Seçimimi onayla" düğmesinin açık onay olayı (data.slotId = ekranda seçili slot).
# END: kiosk ekranı sıfırlandı; oturumun bellekteki verisi (geçmiş muayene dahil) silinir.
# KEEPALIVE: hasta uygulaması açık ve kilitlenmemiş (kullanıcı asistan dışındaki sayfalarda geziyor); ön yüz
# bunu periyodik gönderir. Yalnız boşta kalma saatini ileri alır: durum, seçim, öneri değişmez, API'ye
# gidilmez, metin yoktur. Ön yüzün kendi hareketsizlik kilidi (5 dk, tüm sayfalar) dolunca END gelir.
OLAYLAR = ("START", "TC_VERIFIED", "SLOT_SELECTED", "CONFIRM", "END", "KEEPALIVE")

# Sesli okunan seçenek sayısı (ekranda en fazla ``sunulan_slot_sayisi`` gösterilir).
SESLI_SECENEK = 3
# Saat çakışmasında (APPOINTMENT_CONFLICT) çakışan başlangıca bu kadar yakın slotlar yeniden önerilmez
# (backend varsayılan slot süresi 15 dk).
CAKISMA_PENCERESI = timedelta(minutes=15)

_LLM_ADIMLARI = {"poliklinik": "POLIKLINIK_BEKLENIYOR", "gun": "GUN_BEKLENIYOR", "saat": "SAAT_BEKLENIYOR",
                 "secim": "SECIM_BEKLENIYOR", "hekim": "HEKIM_BEKLENIYOR"}
# Hekim görünen adındaki unvan kelimeleri (normalize): hekim ilk adından tanınır.
_UNVAN_KELIMELERI = frozenset({"prof", "doc", "dr", "uzm", "op"})
_GECMIS_NIYETLERI = ("gecmis_muayene", "ilac_sorusu")
# Son konuşulan ilaca geri gönderme ("peki yan etkisi?", "bunu içerken…", "kullanırken").
_TAKIP_ISARETLERI = ("peki", "bunu", "bunun", "bununla", "bunla", "buna", "onu", "onun", "onunla", "bu ilac",
                     "o ilac", "bu hap", "kullan", "icer", "icebil", "icsem", "alabil", "alsam", "alinir", "icilir")


# --------------------------------------------------------------------------
# Veri tipleri
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SlotBilgisi:
    slot_id: str
    baslangic: datetime  # UTC
    poliklinik: str      # kod
    hekim: str           # görünen ad ("Uzm. Dr. Mehmet D.")
    hekim_id: str = ""   # backend doctorId (eski backend vermezse boş; tercih görünen adla eşlenir)

    @property
    def hekim_anahtari(self) -> str:
        return self.hekim_id or self.hekim

    def disa(self, alan: ModuleType) -> dict[str, Any]:
        p = alan.poliklinik_bul(self.poliklinik)
        detay = " · ".join(x for x in (p.ad if p else self.poliklinik, self.hekim) if x)
        return {"slotId": self.slot_id, "label": slot_etiketi(self.baslangic), "detail": detay}


@dataclass
class Oturum:
    oturum_id: str
    durum: str = TC_BEKLENIYOR
    poliklinik: str | None = None
    gun: date | None = None
    saat_bas: time | None = None
    saat_bit: time | None = None
    gun_serbest: bool = False          # "gün fark etmez / en erken"
    saat_serbest: bool = False         # "saat fark etmez / en erken"
    beklenen: str | None = None        # poliklinik | hekim | gun | saat | secim (SLOT_SECIMI alt adımı)
    # Çok hekimli poliklinikte hekim tercihi: (anahtar = doctorId ya da görünen ad, görünen ad). Yalnız
    # ``hekim_poliklinik`` == ``poliklinik`` iken geçerlidir; poliklinik değişince kendiliğinden düşer.
    hekim: tuple[str, str] | None = None
    hekim_poliklinik: str | None = None  # hekim sorusunun (bir kez) ele alındığı poliklinik
    hekim_adaylari: list[tuple[str, str]] = field(default_factory=list)
    anlasilmama: int = 0               # art arda anlaşılmayan tur sayısı (sonsuz döngü yok)
    sunulan: dict[str, SlotBilgisi] = field(default_factory=dict)
    sesli: list[str] = field(default_factory=list)  # sesli okunan seçenekler (sıra sayıları bunlara göre)
    secili: str | None = None
    randevu: dict[str, Any] | None = None
    devir_nedeni: str | None = None
    son_sistem_hatasi: bool = False
    # Geçmiş muayene verisi: YALNIZ oturum belleğinde; loglanmaz, END/boşta/devirde silinir.
    gecmis: list[Ziyaret] | None = None
    # Boşta kalma süresi bir kez aşıldıysa ekrandaki kişi değişmiş olabilir: geçmiş muayene verisi
    # bu oturumda bir daha verilmez (kimlik yeniden doğrulanmalı; kiosk restart ile yeni oturum açar).
    gecmis_kilitli: bool = False
    # Son konuşulan ilaç: ``gecmis`` içindeki (ziyaret, ilaç) sırası — ad ayrıca tutulmaz. Zamirli takip
    # sorusu ("peki yan etkisi?") buna bağlanır. ``gecmis`` silinince bu da silinir.
    son_ilac: tuple[int, int] | None = None
    # "Hangi ilacınız için?" diye sorulan konu: YALNIZ bir sonraki metin turunda geçerlidir.
    bekleyen_konu: str | None = None
    # Sesli "evet/onaylıyorum" yalnız bir önceki asistan yanıtı seçimi okuyup onay sorduysa randevu
    # oluşturur. Arada başka bir yanıt (geçmiş, tıbbi, anlaşılmadı…) verildiyse seçim önce yeniden okunur.
    onay_soruldu: bool = False
    # Şikâyetten önerilen poliklinik (ör. "kalbim ağrıyor" → KARDIYOLOJI). YALNIZ bir sonraki metin turunda
    # geçerlidir: "evet/olur/bakalım" polikliniği SEÇER (randevu onayı değildir; durum SLOT_SECIMI'dir,
    # seçili saat yoktur). Her olayda ve her metin turunun başında silinir.
    oneri: str | None = None
    # Backend APPOINTMENT_CONFLICT bildirdi: aynı turda yeniden önerilmesin diye dışlananlar (yalnız bellek).
    haric_gunler: set[tuple[str, date]] = field(default_factory=set)   # (poliklinik, yerel gün)
    haric_saatler: list[datetime] = field(default_factory=list)        # çakışan randevu başlangıçları (UTC)
    son_erisim: float = field(default_factory=_time.monotonic)
    son_islem: datetime | None = None  # son işlenen isteğin zamanı (boşta kalma denetimi, saat kaynağıyla)
    kullanim: int = 0                  # şu an bu oturumu işleyen/bekleyen istek sayısı (atılmaz)
    kilit: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Tavsiye yolu (``tavsiye.py``). Son konuşulan şikâyet KONU KODU (kapalı listeden; kullanıcının sözü değil):
    # takip cümlesi şikâyeti tekrar etmese ya da STT bozsa da ("bu lambasına ne iyi gelir") konu bilinir.
    son_sikayet: str | None = None
    tavsiye_teklifi: bool = False      # "…evde neler yapılabileceğini anlatabilirim" dendi (yalnız sonraki tur)
    konu_soruldu: bool = False         # "Hangi şikâyetiniz için?" soruldu (yalnız sonraki tur)
    randevu_reddedildi: bool = False   # kullanıcı bu konuşmada randevu istemediğini söyledi: poliklinik sorulmaz
    randevu_istendi: bool = False      # kullanıcı randevu niyeti gösterdi (anlaşılmayan cümlede adım sorusu)
    tavsiye_poliklinik: str | None = None  # tavsiye yanıtında teklif edilen poliklinik ("tamam randevu alayım")
    teklif_edilen: set[str] = field(default_factory=set)  # tavsiye yanıtında randevusu teklif edilen poliklinikler

    def saglik_verisini_sil(self) -> None:
        """Geçmiş muayene verisi ve ona bağlı ilaç/şikâyet bağlamı bellekten bırakılır."""
        self.gecmis = None
        self.son_ilac = None
        self.bekleyen_konu = None
        self.son_sikayet = None
        self.tavsiye_teklifi = self.konu_soruldu = False
        self.tavsiye_poliklinik = None


@dataclass
class Sonuc:
    durum_kodu: int
    govde: dict[str, Any]
    zamanlama: dict[str, float] = field(default_factory=dict)  # Server-Timing için (ms)
    anlama_kaynagi: str | None = None


class GirdiHatasi(ValueError):
    pass


# --------------------------------------------------------------------------
# Oturum deposu (bellek içi, TTL'li). Kullanıcı metni saklanmaz.
# --------------------------------------------------------------------------

class OturumDeposu:
    def __init__(self, omur_sn: float, azami: int):
        self._omur = omur_sn
        self._azami = azami
        self._oturumlar: OrderedDict[str, Oturum] = OrderedDict()
        # END ile kapatılan oturum kimlikleri (yalnız kimlik + zaman; veri yok). Sıfırlamadan sonra gelen
        # gecikmeli istek oturumu API'den yeniden kurup sağlık verisini yeniden belleğe almasın.
        self._bitenler: OrderedDict[str, float] = OrderedDict()

    @staticmethod
    def _kullanimda(o: Oturum) -> bool:
        return o.kullanim > 0 or o.kilit.locked()

    @staticmethod
    def _bosalt(o: Oturum) -> None:
        o.saglik_verisini_sil()  # sağlık verisi referansı hemen bırakılır

    def _temizle(self) -> None:
        simdi = _time.monotonic()
        for anahtar in [k for k, o in self._oturumlar.items()
                        if simdi - o.son_erisim > self._omur and not self._kullanimda(o)]:
            self._bosalt(self._oturumlar.pop(anahtar))
        # Sınır aşıldıysa en eski KULLANILMAYAN oturumlar atılır; işlenmekte olan oturum
        # atılırsa aynı kimlikle gelen istek yeni bir kilit alır ve kilit koruması bozulurdu.
        fazla = len(self._oturumlar) - self._azami
        if fazla > 0:
            for anahtar in [k for k, o in self._oturumlar.items() if not self._kullanimda(o)][:fazla]:
                self._bosalt(self._oturumlar.pop(anahtar))

    def getir(self, oturum_id: str) -> Oturum | None:
        self._temizle()
        o = self._oturumlar.get(oturum_id)
        if o is not None:
            o.son_erisim = _time.monotonic()
            self._oturumlar.move_to_end(oturum_id)
        return o

    def olustur(self, oturum_id: str) -> Oturum:
        self._temizle()
        o = Oturum(oturum_id)
        self._oturumlar[oturum_id] = o
        return o

    def sil(self, oturum_id: str) -> bool:
        o = self._oturumlar.pop(oturum_id, None)
        if o is not None:
            self._bosalt(o)
        simdi = _time.monotonic()
        self._bitenler[oturum_id] = simdi
        self._bitenler.move_to_end(oturum_id)
        while self._bitenler and (len(self._bitenler) > max(self._azami, 1) * 4
                                  or simdi - next(iter(self._bitenler.values())) > self._omur):
            self._bitenler.popitem(last=False)
        return o is not None

    def bitti_mi(self, oturum_id: str) -> bool:
        zaman = self._bitenler.get(oturum_id)
        return zaman is not None and _time.monotonic() - zaman <= self._omur

    def __len__(self) -> int:
        return len(self._oturumlar)


# --------------------------------------------------------------------------
# Orkestratör
# --------------------------------------------------------------------------

class Orkestrator:
    def __init__(self, *, alan: ModuleType, api: HastaKabulApi, llm: LLMSaglayici | None,
                 saat: SaatKaynagi = simdi_utc, oturum_omru_sn: float = 1200, azami_oturum: int = 500,
                 arama_gun_sayisi: int = 3, sunulan_slot_sayisi: int = 6, slot_min_once_dk: int = 5,
                 azami_metin_uzunlugu: int = 500, anlama_zaman_asimi_sn: float | None = None,
                 teshis_logu: bool = False, bosta_kalma_sn: float = 300, alternatif_gun_sayisi: int = 7,
                 gecmis_sesli: bool = True, gecmis_ikinci_faktor: bool = False, gecmis_limit: int = 3,
                 randevu_ufuk_gun: int = 8, ilac_konu_llm: bool = False, saglik_bilgi: bool = True,
                 saglik_bilgi_zaman_asimi_sn: float = 10.0, saglik_llm: LLMSaglayici | None = None):
        self.alan = alan
        self.api = api
        self.llm = llm
        self.saat = saat
        self.depo = OturumDeposu(oturum_omru_sn, azami_oturum)
        # Backend slotları bugün dahil ``randevu_ufuk_gun`` gün için açar; aramalar bu ufku aşmaz.
        self.randevu_ufuk_gun = max(1, randevu_ufuk_gun)
        self.arama_gun_sayisi = min(max(1, arama_gun_sayisi), self.randevu_ufuk_gun)
        self.alternatif_gun_sayisi = min(max(self.arama_gun_sayisi, alternatif_gun_sayisi), self.randevu_ufuk_gun)
        self.sunulan_slot_sayisi = max(1, sunulan_slot_sayisi)
        self.slot_min_once = timedelta(minutes=slot_min_once_dk)
        self.azami_metin_uzunlugu = azami_metin_uzunlugu
        self.anlama_zaman_asimi_sn = anlama_zaman_asimi_sn
        self.teshis_logu = teshis_logu
        self.bosta_kalma = timedelta(seconds=bosta_kalma_sn)
        self.gecmis_sesli = gecmis_sesli
        self.gecmis_ikinci_faktor = gecmis_ikinci_faktor
        self.gecmis_limit = max(1, min(10, gecmis_limit))
        self.ilac_konu_llm = ilac_konu_llm
        # Genel sağlık/ilaç bilgi yolu: ayar açık ve bir LLM varsa (ayrı model verilmediyse anlama LLM'i).
        self.saglik_llm = (saglik_llm or llm) if saglik_bilgi else None
        self.saglik_bilgi_zaman_asimi_sn = max(1.0, saglik_bilgi_zaman_asimi_sn)

    # ---- Giriş noktası --------------------------------------------------------
    async def isle(self, istek: dict[str, Any], istek_kimligi: str) -> Sonuc:
        baslangic = _time.perf_counter()
        olcum = ApiOlcum()
        try:
            oturum_id, olay, metin, veri = self._dogrula(istek)
        except GirdiHatasi as hata:
            # Eylem verilmez: arayüz bulunduğu adımda kalır, yalnız mesajı gösterir.
            return self._hata(400, "VALIDATION_FAILED", str(hata), None, self.alan.GECERSIZ_GIRDI,
                              {}, istek_kimligi)
        if olay == "END":
            # Kiosk sıfırlandı: bellekteki oturum (geçmiş muayene verisi dahil) silinir. API'ye gidilmez.
            self.depo.sil(oturum_id)
            return self._sonla(Sonuc(200, yanit_govdesi(None, "", {"ended": True}, istek_kimligi)), olcum,
                               baslangic)
        if self.depo.bitti_mi(oturum_id):
            return self._sonla(self._bitmis_oturum(istek_kimligi), olcum, baslangic)
        if olay == "KEEPALIVE":
            return self._sonla(await self._canli_tut(oturum_id, istek_kimligi), olcum, baslangic)
        oturum = self.depo.getir(oturum_id)
        yeni = oturum is None
        on_api: dict[str, Any] | None = None
        if oturum is None:
            # Oturum, API onu tanımadan depoya girmez (rastgele UUID'lerle depo doldurulamaz).
            gecici = _Baglam(self, Oturum(oturum_id), istek_kimligi, olcum)
            api = await gecici._api_oturumu()
            if isinstance(api, ApiHatasi):
                sonuc = gecici._oturum_hatasi(api)
                if sonuc is None:  # sistem hatası: oturum durumu bilinmiyor
                    if olay == "START":
                        sonuc = gecici._yanit("ASK_TC", self.alan.SELAMLAMA)
                    else:
                        sonuc = self._hata(503, "API_UNAVAILABLE", "Oturum durumu API'den okunamadı.", None,
                                           self.alan.SISTEM_HATASI, {"retry": True}, istek_kimligi)
                return self._sonla(sonuc, olcum, baslangic)
            on_api = api
            if self.depo.bitti_mi(oturum_id):  # API beklenirken END geldi: oturum yeniden kurulmaz
                return self._sonla(self._bitmis_oturum(istek_kimligi), olcum, baslangic)
            # Bekleme sırasında aynı kimlikle başka istek oturumu oluşturmuş olabilir.
            oturum = self.depo.getir(oturum_id) or self.depo.olustur(oturum_id)
        oturum.kullanim += 1
        try:
            async with oturum.kilit:
                baglam = _Baglam(self, oturum, istek_kimligi, olcum, on_api=on_api)
                if baglam.bosta_kaldi:
                    oturum.saglik_verisini_sil()  # boşta kalan oturumun sağlık verisi bellekte tutulmaz
                    # Ekrandaki kişi değişmiş olabilir: geçmiş muayene bu oturumda bir daha verilmez.
                    oturum.gecmis_kilitli = True
                sonuc = await self._olay_isle(baglam, olay, metin, veri, yeni)
                oturum.son_islem = baglam.simdi
                if oturum.durum == DEVIR:
                    oturum.saglik_verisini_sil()
        finally:
            oturum.kullanim -= 1
            oturum.son_erisim = _time.monotonic()
        return self._sonla(sonuc, olcum, baslangic)

    async def _canli_tut(self, oturum_id: str, istek_kimligi: str) -> Sonuc:
        """KEEPALIVE: bellekte olan oturumun boşta kalma saatini ileri alır. Oturum yoksa (asistan hiç
        açılmadı, servis yeniden başladı) oluşturulmaz, API'ye gidilmez. Süre zaten aşıldıysa normal istekteki
        gibi sağlık verisi silinir ve geçmiş kilitlenir (KEEPALIVE kilidi geri açamaz)."""
        oturum = self.depo.getir(oturum_id)
        if oturum is None:
            return Sonuc(200, yanit_govdesi(None, "", {"alive": False, "listen": False}, istek_kimligi))
        oturum.kullanim += 1
        try:
            async with oturum.kilit:
                simdi = self.saat()
                bosta = oturum.son_islem is not None and simdi - oturum.son_islem > self.bosta_kalma
                if bosta:
                    oturum.saglik_verisini_sil()
                    oturum.gecmis_kilitli = True
                oturum.son_islem = simdi
        finally:
            oturum.kullanim -= 1
            oturum.son_erisim = _time.monotonic()
        return Sonuc(200, yanit_govdesi(None, "", {"alive": not bosta, "listen": False}, istek_kimligi))

    async def _olay_isle(self, baglam: _Baglam, olay: str | None, metin: str | None, veri: dict[str, Any],
                         yeni: bool) -> Sonuc:
        if olay is not None:
            baglam.o.onay_soruldu = False  # yalnız _onay_iste yeniden kurar (SLOT_SELECTED)
            baglam.o.oneri = None  # poliklinik önerisi yalnız bir sonraki sesli/yazılı yanıta geçerlidir
        if olay == "START":
            return await baglam.basla()
        if olay == "TC_VERIFIED":
            return await baglam.tc_dogrulandi()
        if yeni:
            kurtarma = await baglam.kurtar()
            if kurtarma is not None:
                return kurtarma
        if olay == "SLOT_SELECTED":
            return await baglam.slot_secildi(str(veri.get("slotId") or ""))
        if olay == "CONFIRM":
            return await baglam.onay_olayi(str(veri.get("slotId") or ""))
        return await baglam.metin(metin or "")

    def _sonla(self, sonuc: Sonuc, olcum: ApiOlcum, baslangic: float) -> Sonuc:
        if olcum.cagri_sayisi:
            sonuc.zamanlama["api"] = olcum.toplam_ms
        sonuc.zamanlama["toplam"] = (_time.perf_counter() - baslangic) * 1000
        return sonuc

    def _dogrula(self, istek: dict[str, Any]) -> tuple[str, str | None, str | None, dict[str, Any]]:
        if not isinstance(istek, dict):
            raise GirdiHatasi("Gövde JSON nesnesi olmalı.")
        ham_id = istek.get("sessionId")
        try:
            oturum_id = str(uuid.UUID(str(ham_id)))
        except (ValueError, TypeError) as exc:
            raise GirdiHatasi("sessionId geçerli bir UUID olmalı.") from exc
        olay = istek.get("event")
        metin = istek.get("userText")
        veri = istek.get("data") or {}
        if olay is not None and olay not in OLAYLAR:
            raise GirdiHatasi(f"event şunlardan biri olmalı: {', '.join(OLAYLAR)}.")
        if not isinstance(veri, dict):
            raise GirdiHatasi("data bir nesne olmalı.")
        if metin is not None and not isinstance(metin, str):
            raise GirdiHatasi("userText metin olmalı.")
        if olay is None:
            if not metin or not metin.strip():
                raise GirdiHatasi("event ya da userText gerekli.")
            if len(metin) > self.azami_metin_uzunlugu:
                raise GirdiHatasi("userText çok uzun.")
        if olay in ("SLOT_SELECTED", "CONFIRM") and not isinstance(veri.get("slotId"), str):
            raise GirdiHatasi(f"{olay} için data.slotId gerekli.")
        return oturum_id, olay, metin, veri

    def _bitmis_oturum(self, istek_kimligi: str) -> Sonuc:
        """END ile kapatılmış oturuma gelen (gecikmeli) istek: veri verilmez, kiosk yeni oturum açar."""
        return self._hata(409, "VOICE_SESSION_ENDED", "Oturum uygulama tarafından kapatıldı.", "HANDOFF",
                          self.alan.OTURUM_GECERSIZ,
                          {"reason": "SESSION_ENDED", "restart": True, "listen": False}, istek_kimligi)

    def _hata(self, durum: int, kod: str, mesaj: str, eylem: str | None, metin: str, veri: dict[str, Any],
              istek_kimligi: str) -> Sonuc:
        govde = yanit_govdesi(eylem, metin, veri, istek_kimligi)
        govde["error"] = {"code": kod, "message": mesaj, "requestId": istek_kimligi}
        return Sonuc(durum, govde)


def yanit_govdesi(eylem: str | None, metin: str, veri: dict[str, Any], istek_kimligi: str) -> dict[str, Any]:
    # replyText: sözleşmedeki alan; message: eski istemciler için aynı metin.
    return {"action": eylem, "replyText": metin, "message": metin, "data": veri, "requestId": istek_kimligi}


class _Baglam:
    """Tek bir isteğin işlenmesi (oturum kilidi altında)."""

    def __init__(self, ork: Orkestrator, oturum: Oturum, istek_kimligi: str, olcum: ApiOlcum,
                 on_api: dict[str, Any] | None = None):
        self.ork = ork
        self.alan = ork.alan
        self.o = oturum
        self.rid = istek_kimligi
        self.olcum = olcum
        self.simdi = ork.saat()
        self.bugun = yerel_gun(self.simdi)
        self._on_api = on_api  # bu istekte API'den az önce okunmuş oturum görünümü (bir kez kullanılır)
        self._ek_veri: dict[str, Any] = {}  # bu yanıta eklenecek veri (ör. geçmiş muayene kartı)
        self._onay_soruldu = oturum.onay_soruldu  # metin(): bu turdan önceki asistan yanıtı onay sorusu muydu
        self._bekleyen_konu: str | None = None    # metin(): önceki turda "hangi ilacınız?" diye sorulan konu
        # Önceki istekten bu yana boşta kalma süresi aşıldı mı (onay reddedilir).
        self.bosta_kaldi = (oturum.son_islem is not None
                            and self.simdi - oturum.son_islem > ork.bosta_kalma)

    # ---- Yanıt kurucular ------------------------------------------------------
    def _beklenen_disa(self, eylem: str | None) -> str | None:
        if eylem == "ASK_TC":
            return "tc"
        if eylem == "CONFIRM":
            return "onay"
        if eylem == "OFFER_SLOTS":
            return self.o.beklenen or "poliklinik"
        return None

    def _secenekler(self, beklenen: str | None) -> list[str]:
        if beklenen == "poliklinik":
            # Önerilen poliklinik varsa ilk sırada (yalnız sıra değişir; liste aynı).
            oneri = self.alan.poliklinik_bul(self.o.oneri)
            return sorted((p.ad for p in self.alan.POLIKLINIKLER),
                          key=lambda ad: 0 if oneri is not None and oneri.ad == ad else 1)
        if beklenen == "hekim":
            return [ad for _, ad in self.o.hekim_adaylari] + [self.alan.HEKIM_FARK_ETMEZ_SECENEGI]
        if beklenen == "gun":
            return list(self.alan.GUN_SECENEKLERI)
        if beklenen == "saat":
            return list(self.alan.SAAT_SECENEKLERI)
        return []

    def _yanit(self, eylem: str | None, metin: str, veri: dict[str, Any] | None = None, durum: int = 200,
               hata_kodu: str | None = None, hata_mesaji: str = "") -> Sonuc:
        veri = dict(veri or {})
        beklenen = self._beklenen_disa(eylem)
        # İpuçları: arayüz sesli döngüyü (listen) ve yedek düğmeleri (choices) buna göre kurar.
        veri.setdefault("expect", beklenen)
        veri.setdefault("listen", eylem in ("OFFER_SLOTS", "CONFIRM") and self.o.anlasilmama < 2)
        secenekler = self._secenekler(beklenen)
        if secenekler:
            veri.setdefault("choices", secenekler)
        veri.update(self._ek_veri)
        govde = yanit_govdesi(eylem, metin, veri, self.rid)
        if hata_kodu:
            govde["error"] = {"code": hata_kodu, "message": hata_mesaji or hata_kodu, "requestId": self.rid}
            govde["data"].setdefault("errorCode", hata_kodu)
        return Sonuc(durum, govde)

    def _slot_verisi(self) -> dict[str, Any]:
        return {"slots": [s.disa(self.alan) for s in self.o.sunulan.values()]}

    def _onay_verisi(self) -> dict[str, Any]:
        s = self.o.sunulan.get(self.o.secili or "")
        if s is None:
            return {}
        return {**s.disa(self.alan), "slots": [x.disa(self.alan) for x in self.o.sunulan.values()]}

    def _randevu_verisi(self) -> dict[str, Any]:
        return dict(self.o.randevu or {})

    def _mevcut_adim(self, metin: str, durum: int = 200, hata_kodu: str | None = None) -> Sonuc:
        """Durumu değiştirmeden, bulunulan adımın eylemiyle yanıt verir."""
        if self.o.durum == TC_BEKLENIYOR:
            return self._yanit("ASK_TC", metin, durum=durum, hata_kodu=hata_kodu)
        if self.o.durum == ONAY_BEKLENIYOR and self.o.secili in self.o.sunulan:
            return self._yanit("CONFIRM", metin, self._onay_verisi(), durum, hata_kodu)
        if self.o.durum == TAMAMLANDI:
            return self._yanit("DONE", metin, self._randevu_verisi(), durum, hata_kodu)
        if self.o.durum == DEVIR:
            return self._yanit("HANDOFF", metin, {"reason": self.o.devir_nedeni or "HANDOFF"}, durum, hata_kodu)
        if not self.o.sunulan and self.o.son_sistem_hatasi and hata_kodu is None:
            # Boş liste "saat yok" değil, "saatler alınamadı" demektir (D4-b): arayüz ayırt edebilsin.
            return self._yanit("OFFER_SLOTS", metin, self._slot_verisi(), 503, "API_UNAVAILABLE",
                               "Uygun saatler API'den alınamadı.")
        return self._yanit("OFFER_SLOTS", metin, self._slot_verisi(), durum, hata_kodu)

    def _devret(self, neden: str, metin: str, durum: int = 200, hata_kodu: str | None = None) -> Sonuc:
        self.o.durum = DEVIR
        self.o.devir_nedeni = neden
        self.o.saglik_verisini_sil()
        return self._yanit("HANDOFF", metin, {"reason": neden}, durum, hata_kodu)

    def _suresi_doldu(self, durum: int = 409, hata_kodu: str | None = "VOICE_SESSION_NOT_VERIFIED") -> Sonuc:
        """Doğrulanmış sandığımız oturum artık geçerli değil (süre doldu): sağlık verisi ve seçimler
        silinir; kiosk ``restart`` görünce yeni oturum açıp TC adımına döner."""
        self.o.durum = DEVIR
        self.o.devir_nedeni = "SESSION_EXPIRED"
        self.o.saglik_verisini_sil()
        self.o.sunulan, self.o.secili, self.o.sesli = {}, None, []
        return self._yanit("HANDOFF", self.alan.OTURUM_SURESI_DOLDU, {"reason": "SESSION_EXPIRED", "restart": True},
                           durum, hata_kodu)

    # ---- API: oturum ------------------------------------------------------------
    async def _api_oturumu(self) -> dict[str, Any] | ApiHatasi:
        if self._on_api is not None:
            veri, self._on_api = self._on_api, None
            return veri
        try:
            return await self.ork.api.oturum_getir(self.o.oturum_id, istek_kimligi=self.rid, olcum=self.olcum)
        except ApiHatasi as hata:
            return hata

    def _oturum_hatasi(self, hata: ApiHatasi) -> Sonuc | None:
        if hata.kod == "VOICE_SESSION_NOT_FOUND" or hata.durum == 404:
            return self._devret("SESSION_NOT_FOUND", self.alan.OTURUM_GECERSIZ, 404, "VOICE_SESSION_NOT_FOUND")
        return None

    def _durum_yonlendir(self, api_durumu: str) -> Sonuc | None:
        if api_durumu == "HANDED_OFF":
            return self._devret("TC_ATTEMPTS_EXHAUSTED", self.alan.DEVIR_TC)
        if api_durumu == "EXPIRED" and self.o.durum != TAMAMLANDI:
            return self._suresi_doldu(200, None)
        if api_durumu == "COMPLETED" and self.o.durum != TAMAMLANDI:
            return self._devret(f"SESSION_{api_durumu}", self.alan.OTURUM_GECERSIZ)
        return None

    async def kurtar(self) -> Sonuc | None:
        """Orkestratör yeniden başladıysa (bellek boş) oturum durumunu API'den kurar."""
        api = await self._api_oturumu()
        if isinstance(api, ApiHatasi):
            return self._oturum_hatasi(api)  # sistem hatasında TC adımında kalınır
        yonlendirme = self._durum_yonlendir(str(api.get("status")))
        if yonlendirme is not None:
            return yonlendirme
        if api.get("verified") and api.get("status") == "VERIFIED":
            self.o.durum = SLOT_SECIMI
        return None

    # ---- Olaylar ------------------------------------------------------------------
    async def basla(self) -> Sonuc:
        # Aynı oturumda yeniden START: durum sıfırlanır (tamamlanan randevu korunur).
        if self.o.durum == TAMAMLANDI:
            return self._mevcut_adim(self.alan.TAMAMLANDI_BILGI)
        self.o.durum, self.o.sunulan, self.o.secili, self.o.sesli = TC_BEKLENIYOR, {}, None, []
        self.o.saglik_verisini_sil()
        api = await self._api_oturumu()
        if isinstance(api, ApiHatasi):
            hata = self._oturum_hatasi(api)
            if hata is not None:
                return hata
            # API'ye ulaşılamadı: akış kesilmesin, TC adımına geçilir (doğrulama da API'ye gider).
            return self._yanit("ASK_TC", self.alan.SELAMLAMA)
        yonlendirme = self._durum_yonlendir(str(api.get("status")))
        if yonlendirme is not None:
            return yonlendirme
        if api.get("verified"):
            return await self._dogrulanmis_basla()
        return self._yanit("ASK_TC", self.alan.SELAMLAMA)

    async def tc_dogrulandi(self) -> Sonuc:
        if self.o.durum == TAMAMLANDI:
            return self._mevcut_adim(self.alan.TAMAMLANDI_BILGI)
        api = await self._api_oturumu()
        if isinstance(api, ApiHatasi):
            hata = self._oturum_hatasi(api)
            if hata is not None:
                return hata
            self.o.son_sistem_hatasi = True
            # ASK_TC DÖNÜLMEZ: API oturumu çoktan doğrulamış olabilir; arayüz TC'yi yeniden sorarsa
            # backend INVALID_STATUS_TRANSITION verir ve hasta takılır (O4). Eylem yok + yeniden dene.
            govde = yanit_govdesi(None, self.alan.DOGRULANDI_SISTEM_HATASI,
                                  {"retry": "TC_VERIFIED", "listen": False}, self.rid)
            govde["error"] = {"code": "API_UNAVAILABLE", "message": "Oturum durumu API'den okunamadı.",
                              "requestId": self.rid}
            return Sonuc(503, govde)
        yonlendirme = self._durum_yonlendir(str(api.get("status")))
        if yonlendirme is not None:
            return yonlendirme
        if not (api.get("verified") and api.get("status") == "VERIFIED"):
            self.o.durum = TC_BEKLENIYOR
            return self._yanit("ASK_TC", self.alan.KIMLIK_DOGRULANMADI, durum=409,
                               hata_kodu="VOICE_SESSION_NOT_VERIFIED",
                               hata_mesaji="Oturum API'de doğrulanmış değil.")
        return await self._dogrulanmis_basla()

    def _tercihleri_sifirla(self) -> None:
        o = self.o
        o.poliklinik = o.gun = o.saat_bas = o.saat_bit = None
        o.gun_serbest = o.saat_serbest = False
        o.secili = None
        o.sesli = []
        o.anlasilmama = 0
        o.hekim, o.hekim_poliklinik, o.hekim_adaylari = None, None, []

    async def _dogrulanmis_basla(self) -> Sonuc:
        self.o.durum = SLOT_SECIMI
        self._tercihleri_sifirla()
        self.o.beklenen = "poliklinik"
        slotlar, sistem_hatasi = await self._ilk_oneriler()
        liste = self.alan.poliklinik_listesi_metni()
        if sistem_hatasi and not slotlar:
            self.o.sunulan = {}
            self.o.son_sistem_hatasi = True
            return self._yanit("OFFER_SLOTS", self.alan.POLIKLINIK_SOR.format(liste=liste) + " "
                               + self.alan.SISTEM_HATASI_SAATLER, self._slot_verisi(), 503, "API_UNAVAILABLE",
                               "Uygun saatler API'den alınamadı.")
        self.o.son_sistem_hatasi = False
        self.o.sunulan = {s.slot_id: s for s in slotlar}
        return self._yanit("OFFER_SLOTS", self.alan.POLIKLINIK_SOR.format(liste=liste), self._slot_verisi())

    async def slot_secildi(self, slot_id: str) -> Sonuc:
        if self.o.durum == TAMAMLANDI:
            return self._mevcut_adim(self.alan.TAMAMLANDI_BILGI)
        if self.o.durum == DEVIR:
            return self._mevcut_adim(self.alan.DEVIR)
        if self.o.durum == TC_BEKLENIYOR:
            return self._yanit("ASK_TC", self.alan.ONCE_KIMLIK, durum=409, hata_kodu="VOICE_SESSION_NOT_VERIFIED",
                               hata_mesaji="Kimlik doğrulanmadan saat seçilemez.")
        slot = self.o.sunulan.get(slot_id)
        if slot is None:
            self.o.durum, self.o.secili = SLOT_SECIMI, None
            if not self.o.sunulan:
                await self._yeniden_ara()
            return self._yanit("OFFER_SLOTS", self.alan.SLOT_LISTEDE_YOK, self._slot_verisi(), 400,
                               "VALIDATION_FAILED", "Seçilen slot bu oturumda sunulmadı.")
        return self._sec(slot)

    def _sec(self, slot: SlotBilgisi) -> Sonuc:
        """Seçimi işaretler ve açık onay ister (randevu YOK; yan etkisiz)."""
        self.o.secili = slot.slot_id
        self.o.durum = ONAY_BEKLENIYOR
        self.o.anlasilmama = 0
        return self._onay_iste()

    def _onay_iste(self) -> Sonuc:
        slot = self.o.sunulan[self.o.secili or ""]
        p = self.alan.poliklinik_bul(slot.poliklinik)
        etiket = f"{p.ad if p else slot.poliklinik}, {slot_uzun_etiket(slot.baslangic)}"
        if slot.hekim:
            etiket += f", {slot.hekim}"
        self.o.onay_soruldu = True
        return self._yanit("CONFIRM", self.alan.ONAY_SOR.format(etiket=etiket.rstrip(".")), self._onay_verisi())

    async def onay_olayi(self, slot_id: str) -> Sonuc:
        """Düğmeden gelen açık onay. Ekrandaki seçim sunucudaki seçimle aynı olmalıdır."""
        if self.o.durum == TAMAMLANDI:
            return self._mevcut_adim(self._tamamlandi_metni())
        if self.o.durum == DEVIR:
            return self._mevcut_adim(self.alan.DEVIR)
        if self.o.durum == TC_BEKLENIYOR:
            return self._yanit("ASK_TC", self.alan.ONCE_KIMLIK, durum=409, hata_kodu="VOICE_SESSION_NOT_VERIFIED",
                               hata_mesaji="Kimlik doğrulanmadan onay verilemez.")
        if self.o.durum != ONAY_BEKLENIYOR or self.o.secili not in self.o.sunulan:
            if not self.o.sunulan:
                await self._yeniden_ara()
            return self._mevcut_adim(self.alan.ONCE_SAAT_SECIN, 409, "NO_SELECTION")
        if slot_id != self.o.secili:
            return self._yanit("CONFIRM", self.alan.SECIM_GUNCEL_DEGIL, self._onay_verisi(), 409,
                               "SELECTION_MISMATCH", "Onaylanan slot sunucudaki seçimle aynı değil.")
        return await self._randevu_al()

    # ---- Serbest metin ---------------------------------------------------------
    def _llm_adimi(self) -> str:
        """LLM'e giden adım KODU (kapalı liste; açıklaması konu modülünde sabittir)."""
        if self.o.durum == ONAY_BEKLENIYOR:
            return "ONAY_BEKLENIYOR"
        return _LLM_ADIMLARI.get(self.o.beklenen or "", "SLOT_SECIMI")

    async def metin(self, metin: str) -> Sonuc:
        a = self.alan
        # Bir önceki yanıt seçimi okuyup onay sorduysa True; bu turda başka bir yanıt verilirse kapanır.
        self._onay_soruldu = self.o.onay_soruldu
        self.o.onay_soruldu = False
        # Önceki yanıtın poliklinik önerisi yalnız bu tur için geçerlidir.
        oneri, self.o.oneri = self.o.oneri, None
        # "Hangi ilacınız için?" sorusunun konusu yalnız bu tur için geçerlidir.
        self._bekleyen_konu, self.o.bekleyen_konu = self.o.bekleyen_konu, None
        # Tavsiye teklifi ve "hangi şikâyetiniz için?" sorusu da yalnız bu tur için geçerlidir.
        self._tavsiye_teklifi, self.o.tavsiye_teklifi = self.o.tavsiye_teklifi, False
        self._konu_soruldu, self.o.konu_soruldu = self.o.konu_soruldu, False
        maske = kisisel_maskele(metin)
        maskeli = maske.metin
        if acil_mi(normalize(maskeli), a):
            # Acil durum her adımda (kimlik öncesi, onay beklerken, devirde bile) önceliklidir; LLM'e gitmez.
            return self._acil_yaniti()
        kurum = kurum_sorusu(normalize(maskeli), a)
        if kurum is not None:
            # Kurum sorusu (çağrı merkezi, adres, çalışma saatleri, ücret, sigorta): her adımda sabit şablon.
            return self._kurum_yaniti(kurum)
        if self.o.durum == DEVIR:
            return self._mevcut_adim(a.DEVIR)
        if self.o.durum == TC_BEKLENIYOR:
            # Kullanıcı arayüzde doğrulamış ama TC_VERIFIED gelmemiş olabilir: API'ye bak.
            if maske.tc_var:
                return self._yanit("ASK_TC", a.TC_TUS_TAKIMINDAN)
            api = await self._api_oturumu()
            if isinstance(api, dict) and api.get("status") == "VERIFIED":
                self.o.durum = SLOT_SECIMI
            else:
                kural = kural_tabanli_anla(maskeli, self.bugun, a)
                if kural.niyet in (*_GECMIS_NIYETLERI, "ilac_tavsiye"):
                    return self._kural_yaniti(self._yanit("ASK_TC", a.GECMIS_ONCE_KIMLIK))
                if kural.niyet in ("selam", "yardim"):
                    return self._kural_yaniti(self._yanit("ASK_TC", a.YARDIM_GIRIS_ONCESI))
                if kural.niyet == "veda":
                    return self._kural_yaniti(self._yanit("ASK_TC", a.VEDA_YANIT, {"listen": False}))
                return self._kural_yaniti(self._yanit("ASK_TC", a.ONCE_KIMLIK))

        if self.o.durum == TAMAMLANDI:
            # Randevu tamam: geçmiş muayene soruları, sohbet ve genel sağlık bilgisi yanıtlanır (niyet LLM'siz).
            if not maske.tc_var:
                tarifli = await self._tarif_yolu(maskeli, metin)
                if tarifli is not None:
                    return tarifli
                tavsiye = await self._tavsiye_yolu(maskeli, None)
                if tavsiye is not None:
                    return tavsiye
            onbellek = self._onbellekli_ilac_niyeti(maskeli)
            if onbellek is None and not maske.tc_var:
                on = await self._saglik_on_yolu(maskeli)
                if isinstance(on, Sonuc):
                    return on
                onbellek = on
            kural = onbellek or kural_tabanli_anla(maskeli, self.bugun, a)
            if kural.niyet == "kimlik":
                return self._mevcut_adim(a.TC_TUS_TAKIMINDAN)
            if kural.niyet == "ilac_tavsiye":
                return self._mevcut_adim(a.ILAC_TAVSIYE_RET)
            if kural.niyet in _GECMIS_NIYETLERI:
                return await self._gecmis_yaniti(kural, maskeli, metin)
            sohbet = self._sohbet_metni(kural)
            if sohbet is not None:
                return self._kural_yaniti(self._sohbet_yaniti(kural.niyet, sohbet))
            if kural.niyet == "mevcut_randevu" and kural.alt == "degisiklik":
                return self._kural_yaniti(self._mevcut_adim(a.MEVCUT_RANDEVU_IPTAL))
            # Oturum başına bir randevu (backend oturumu COMPLETED yapar): ikinci istek için ne yapılacağı söylenir.
            return self._mevcut_adim(f"{self._tamamlandi_metni()} {a.YENI_RANDEVU_ICIN}")

        if self.o.durum == SLOT_SECIMI and not self.o.sunulan and not self.o.poliklinik and not self.o.gun:
            # Doğrulama başka yoldan öğrenildiyse (TC_VERIFIED kaybı, süreç yeniden başlaması) henüz
            # hiç saat sorgulanmamıştır: boş liste "saat yok" diye gösterilmesin (O5).
            slotlar, sistem_hatasi = await self._ilk_oneriler()
            self.o.sunulan = {s.slot_id: s for s in slotlar}
            self.o.son_sistem_hatasi = sistem_hatasi
        elif self.o.durum == SLOT_SECIMI and not self.o.sunulan and self.o.son_sistem_hatasi:
            # Önceki turda API'ye ulaşılamadı. Bayrak bir sonraki başarılı sorguya kadar kalırsa
            # backend düzeldikten sonra da her yanıt eski 503'ü taşır: güncel tercihlerle yeniden sorulur.
            gunler = [self.o.gun] if self.o.gun else self._ufuk()
            slotlar, sistem_hatasi = await self._slotlari_getir([self.o.poliklinik], gunler) \
                if self.o.poliklinik else await self._ilk_oneriler()
            self._ekrana_koy(slotlar)
            self.o.son_sistem_hatasi = sistem_hatasi

        if not maske.tc_var:
            # Öncelik: acil > kurum (yukarıda) > ilaç bağlamı (tarifle anılan kayıtlı ilaç) > tavsiye > şikâyet.
            tarifli = await self._tarif_yolu(maskeli, metin)
            if tarifli is not None:
                return tarifli
            # Şikâyet için tavsiye isteği / randevu reddi (kural; LLM'e yalnız koddan kurulan konu sorusu gider).
            tavsiye = await self._tavsiye_yolu(maskeli, oneri)
            if tavsiye is not None:
                return tavsiye

        if oneri and self.o.durum == SLOT_SECIMI:
            yanit = await self._oneri_yaniti(oneri, maskeli)
            if yanit is not None:
                return self._kural_yaniti(yanit)

        if self.o.durum == SLOT_SECIMI and not self.o.poliklinik and self.o.tavsiye_poliklinik \
                and self._randevu_istegi_mi(normalize(maskeli)):
            # Tavsiyeden sonra "tamam, randevu alayım": teklif edilen poliklinikle akış başlar (randevu yine ayrı
            # açık onayla oluşur).
            self.o.poliklinik = self.o.tavsiye_poliklinik
            self._randevu_niyeti()
            return self._kural_yaniti(await self._ilerle())

        if self.o.durum == SLOT_SECIMI and self.o.beklenen == "hekim" and self.o.poliklinik:
            # Hekim sorusunun yanıtı (ad, unvan, sıra, "fark etmez"): kodda eşlenir, LLM'e gidilmez.
            yanit = await self._hekim_yaniti(maskeli, metin)
            if yanit is not None:
                return self._kural_yaniti(yanit)

        llm_baslangic = _time.perf_counter()
        adim = self._llm_adimi()
        anlam = self._onbellekli_ilac_niyeti(maskeli)
        if anlam is None and oneri and self.o.durum == SLOT_SECIMI:
            # "Evet, yarın sabah olsun": öneriyi kabul + zaman tercihi (kural; LLM'e gidilmez).
            anlam = self._oneri_ve_zaman(oneri, maskeli)
        if anlam is None and not maske.tc_var:
            # Genel sağlık/ilaç bilgi sorusu ("baş ağrısına ne iyi gelir", "aspirin ne işe yarar"): kuralla
            # seçilir; adım (saat/onay beklemesi) değişmez. Kayıtlı ilaç çıkarsa mevcut ilaç akışına gider.
            on = await self._saglik_on_yolu(maskeli)
            if isinstance(on, Sonuc):
                return on
            anlam = on
        llm = self.ork.llm
        if anlam is None and llm is not None and self.o.gecmis is None and ilac_adayi_var_mi(maskeli, a) \
                and self._llme_gidecek_mi(maskeli, adim):
            # KVKK: cümle LLM'e gidebilir ve ilaç adı olabilecek bir kelime taşıyor ("atorvastatin nedir").
            # Hastanın kayıtlı ilaç adları (dağarcıkta olmasalar da) maskelenebilsin diye hekim onaylı geçmiş
            # önce belleğe alınır; kayıtlı bir ilaç geçiyorsa soru LLM'siz ilaç sorusu olarak çözülür.
            anlam, llm = await self._gecmisle_yeniden_dene(maskeli)
            if isinstance(anlam, Sonuc):
                return anlam
        if anlam is None:
            anlam = await anla(
                metin, alan=a, llm=llm, bugun=self.bugun, simdi_metni=f"{yerel(self.simdi):%H:%M}",
                # LLM'in ufuk dışı günü atılmasın: ufku orkestratör denetler ve dürüst şablon verir.
                adim=adim, ufuk_gun=max(31, self.ork.randevu_ufuk_gun),
                zaman_asimi_sn=self.ork.anlama_zaman_asimi_sn, toplam_sure_sn=self.ork.anlama_zaman_asimi_sn,
                ek_ilac_adlari=tuple(i.ad for z in (self.o.gecmis or []) for i in z.ilaclar),
            )
        llm_ms = (_time.perf_counter() - llm_baslangic) * 1000
        if self.ork.teshis_logu:  # varsayılan kapalı; yalnız niyet ve alanlar, metin değil
            log.info("teşhis anlam niyet=%s poliklinik=%s gun=%s saat=%s-%s sira=%s kaynak=%s",
                     anlam.niyet, anlam.poliklinik, anlam.gun, anlam.saat_baslangic, anlam.saat_bitis,
                     anlam.sira, anlam.kaynak)
        sonuc = await self._anlami_uygula(anlam, maskeli, metin)
        if anlam.kaynak == "llm" or anlam.llm_hatasi:
            sonuc.zamanlama["llm"] = llm_ms
        sonuc.anlama_kaynagi = anlam.kaynak
        if anlam.llm_hatasi:
            sonuc.govde["data"]["degraded"] = True
        return sonuc

    def _llme_gidecek_mi(self, maskeli: str, adim: str) -> bool:
        """``anla`` bu cümleyi LLM'e gönderecek mi? (Kural çözemedi: belirsiz; acil/kimlik/açık onay değil.)
        Kuralın çözdüğü cümlede geçmiş boşuna belleğe alınmaz ("Metformin kullanıyorum" → tıbbi yorum yok)."""
        kural = kural_tabanli_anla(maskeli, self.bugun, self.alan, adim=adim)
        return kural.niyet == "belirsiz" and not kural.acik_onay

    async def _gecmisle_yeniden_dene(self, maskeli: str) -> tuple[Anlam | Sonuc | None, LLMSaglayici | None]:
        """Geçmişi belleğe alıp cümleyi kayıtlı ilaçlarla yeniden dener. Dönüş: (anlam ya da oturum hatası
        yanıtı, kullanılacak LLM). Geçmiş okunamazsa ya da bu oturumda kilitliyse LLM KULLANILMAZ: hastanın
        ilaç adları maskede bilinmeden cümle dışarı gitmez (yanıt kural tabanlıdır)."""
        if self.o.gecmis_kilitli:
            return None, None
        gecmis = await self._gecmisi_getir()
        if isinstance(gecmis, Sonuc):
            if gecmis.durum_kodu in (404, 409):  # oturum bulunamadı / süresi doldu: o yanıt verilir
                return gecmis, None
            return None, None
        return self._onbellekli_ilac_niyeti(maskeli), self.ork.llm

    # ---- Genel sağlık / ilaç bilgisi (saglik_bilgisi) -----------------------------------------
    def _kayitli_ilac_adlari(self) -> tuple[str, ...]:
        return tuple(i.ad for z in (self.o.gecmis or []) for i in z.ilaclar)

    async def _saglik_on_yolu(self, maskeli: str) -> Sonuc | Anlam | None:
        """Genel sağlık/ilaç bilgi sorusu mu? Dönüş: yanıt; kayıtlı ilaç çıktıysa mevcut ilaç akışına giden
        anlam; yolun dışındaysa ``None`` (mevcut davranış). Acil denetimi çağıranda (her şeyden önce) yapılır.
        Yol kapalıysa ya da LLM yoksa hiç devreye girmez."""
        a = self.alan
        if self.ork.saglik_llm is None or self.o.durum in (TC_BEKLENIYOR, DEVIR):
            return None
        karar = saglik_karari(maskeli, a, self._kayitli_ilac_adlari())
        if karar is None:
            return None
        if (karar.ilac_var or ilac_adayi_var_mi(maskeli, a)) and self.o.gecmis is None:
            # Adı geçen ilaç hastanın kayıtlı ilacı olabilir: kayıtlı ilaç sorusu hekim onaylı özetle yanıtlanır
            # (mevcut akış). Geçmiş kilitliyse ya da okunamıyorsa soru genel bilgi sorusu olarak kalır.
            if self.o.gecmis_kilitli:
                return None
            gecmis = await self._gecmisi_getir()
            if isinstance(gecmis, Sonuc) and gecmis.durum_kodu in (404, 409):
                return gecmis
            if not isinstance(gecmis, Sonuc):
                karar = saglik_karari(maskeli, a, self._kayitli_ilac_adlari()) or karar
        if self.o.gecmis and ilac_bul(maskeli, self.o.gecmis):
            return self._onbellekli_ilac_niyeti(maskeli)  # kayıtlı ilaç: mevcut akış (_gecmis_yaniti)
        if karar.tur == "spesifik":
            return self._saglik_sonucu(spesifik_metni(a, karar.neden), ai=False)
        n = normalize(maskeli)
        kayit = dagarcik_kaydi_bul(n) or self._marka_dagarcik_kaydi(n)
        konu, _ = konu_bul(n, a)
        bilgi = dagarcik_bilgisi(kayit, konu, a)
        if kayit and bilgi:
            # Dağarcıkta hazır, onaylı genel bilgi: LLM'e gidilmez.
            metin = f"{a.ILAC_KONU_GIRIS_KISA.format(ad=kayit.ad)} {bilgi} {a.SAGLIK_BILGI_KAPANIS}"
            return self._saglik_sonucu(metin, ai=False)
        return await self._saglik_llm_yaniti(maskeli)

    def _marka_dagarcik_kaydi(self, n: str):
        """"Parol ne işe yarar": markanın etken maddesi dağarcıktaysa o kayıt."""
        for kelime in n.split():
            for marka, etken in self.alan.ILAC_MARKA_ETKEN.items():
                if kelime.startswith(marka) and len(kelime) - len(marka) <= 4:
                    kayit = dagarcik_kaydi_bul(normalize(etken))
                    if kayit is not None:
                        return kayit
        return None

    async def _saglik_llm_metni(self, maskeli: str, ara: str = "") -> tuple[str | None, float]:
        """Yerel LLM'den kısa genel bilgi (yalnız maskeli soru gider) + koddan ek + SABİT kapanış.
        LLM hata verir, zaman aşımına uğrar ya da çıktı denetimden geçmezse ``None``. Asla istisna fırlatmaz."""
        sonuc = await genel_bilgi_uret(maskeli, alan=self.alan, llm=self.ork.saglik_llm,
                                       zaman_asimi_sn=self.ork.saglik_bilgi_zaman_asimi_sn)
        if sonuc.metin is None:
            return None, sonuc.sure_ms
        return yanit_birlestir(self.alan, sonuc.metin, ara), sonuc.sure_ms

    async def _saglik_llm_yaniti(self, maskeli: str) -> Sonuc:
        metin, sure_ms = await self._saglik_llm_metni(maskeli)
        yanit = self._saglik_sonucu(metin or self.alan.SAGLIK_BILGI_VEREMIYORUM, ai=metin is not None)
        if sure_ms:
            yanit.zamanlama["llm"] = sure_ms
        return yanit

    def _saglik_sonucu(self, metin: str, *, ai: bool) -> Sonuc:
        """Bilgi yanıtı: adım değişmez (saat/onay beklemesi sürer). Onay beklenirken hatırlatılır; buradaki bir
        "evet" doğrudan randevu almaz (onay sorusu yeniden okunur)."""
        self.o.anlasilmama = 0
        if ai:
            self._ek_veri["aiGeneralInfo"] = True
        if self.o.durum == ONAY_BEKLENIYOR and self.o.secili in self.o.sunulan:
            metin = f"{metin} {self.alan.SAGLIK_ONAY_HATIRLAT}"
        return self._kural_yaniti(self._mevcut_adim(metin))

    async def _kayitli_ilac_bilgisi(self, ilac_konu: str, eslesme: list[tuple[Ziyaret, Ilac]],
                                    ziyaretler: list[Ziyaret], maskeli: str, tarif: bool = False) -> Sonuc | None:
        """Kullanıcının ADIYLA (ya da tarifiyle: ``tarif``) sorduğu kayıtlı ilaç için dağarcıkta hazır bilgi yoksa
        ("Metforminle araba kullanabilir miyim"): LLM'e yalnız kullanıcının maskeli sorusu gider (ilacın bu hastaya
        yazıldığı, adı ya da talimatı GİTMEZ); hekim talimatı koddan eklenir. Hazır bilgi varsa ya da yol uygun
        değilse ``None`` (mevcut akış)."""
        a = self.alan
        if self.ork.saglik_llm is None or not self.ork.gecmis_sesli or not eslesme \
                or len({normalize(i.ad) for _, i in eslesme}) != 1:
            return None  # tek bir ilaç adıyla sorulmadıysa mevcut akış
        if ilac_konu in OZET_KONULARI or ilac_konu in ("doz", "etkilesim"):
            return None
        z, i = eslesme[0]
        kayit = dagarcik_kaydi_bul(normalize(i.ad))
        if ilac_konu != "tanimsiz" and dagarcik_bilgisi(kayit, ilac_konu, a):
            return None
        karar = saglik_karari(maskeli, a, self._kayitli_ilac_adlari())
        if karar is None and tarif and soru_mu(normalize(maskeli), maskeli):
            # Tarifle sorulan ilaç ("tansiyon ilacımla araba kullanabilir miyim"): cümlede ilaç adı olmadığı için
            # genel karar yolu soruyu "adı söylenmeyen ilaç" sayar; kişiye özel karar denetimi burada yapılır.
            neden = _spesifik_neden(normalize(maskeli), [], True, a)
            karar = SaglikKarari("spesifik", False, neden) if neden else SaglikKarari("bilgi", False)
        if karar is None:
            return None
        sure_ms = 0.0
        if karar.tur == "spesifik":
            metin: str | None = spesifik_metni(a, karar.neden)
            ai = False
        else:
            # Hekim talimatı LLM'e GİTMEZ; koddan, sığarsa LLM metninden sonra eklenir.
            talimat = a.ILAC_KONU_TALIMAT.format(kullanim=i.kullanim) if i.kullanim else ""
            talimat = talimat if not talimat or talimat[-1] in ".!?" else talimat + "."
            metin, sure_ms = await self._saglik_llm_metni(maskeli, ara=talimat)
            if metin is None:
                return None  # LLM yanıt veremedi: mevcut dürüst şablon ("elimde onaylı bilgi yok")
            ai = True
        self._son_ilaci_kaydet(z, i)
        self._ek_veri["history"] = {"visits": [x.disa() for x in ziyaretler], "focus": [i.ad],
                                    "spoken": True, "topic": veri_konusu(ilac_konu, a)}
        yanit = self._saglik_sonucu(metin, ai=ai)
        if sure_ms:
            yanit.zamanlama["llm"] = sure_ms
        return yanit

    # ---- Tarifle sorulan kayıtlı ilaç ("tansiyon ilacım", "kalp doktorunun verdiği ilaç") ----------------
    async def _tarif_yolu(self, maskeli: str, ham: str) -> Sonuc | None:
        """Kullanıcı kayıtlı ilacını adıyla değil tarifiyle anıyor ("Bu tansiyonumu düşüren ilacı kullandığım zaman
        yememem gereken şeyler var mı?"). Tarif hekim onaylı kayıttaki ilaçlara KODDA uygulanır (``ilac_tarifi``);
        tek ilaca uyarsa o ilacın konu yanıtı verilir ve ilacın adı söylenir, birden çoğuna ya da hiçbirine
        uymuyorsa hangi ilaç olduğu sorulur (kayıtlı ilaçlar sayılarak). ``None``: tarif yok ya da ilaç adıyla
        soruldu (mevcut akış). Bu yol şikâyet → poliklinik önerisinden ve tavsiye yolundan ÖNCE gelir: ilaç
        bağlamındaki "tansiyon/şeker" şikâyet değildir. Acil ve kurum soruları çağıranda daha önce yakalanır."""
        a, o = self.alan, self.o
        if o.durum not in (SLOT_SECIMI, ONAY_BEKLENIYOR, TAMAMLANDI):
            return None
        n = normalize(maskeli)
        tarif = tarif_bul(n, a)
        if tarif is None or ilac_adlari(n, self._kayitli_ilac_adlari()):
            return None  # tarif yok ya da ilaç adı söylendi ("demir ilacım", "aspirin"): mevcut akış
        konu, _ = konu_bul(n, a)
        if konu is None and self._tarif_ek_soru_var(n, tarif):
            konu = "tanimsiz"  # "tansiyon ilacımla araba kullanabilir miyim": dağarcıkta konusu olmayan soru
        if self.ork.gecmis_ikinci_faktor or o.gecmis_kilitli:
            # Kapalı başarısız / yeniden doğrulama yanıtları geçmiş yolunda.
            return await self._gecmis_yaniti(Anlam("ilac_sorusu", gecmis_konu="liste", ilac_konu=konu), maskeli, ham)
        ziyaretler = await self._gecmisi_getir()
        if isinstance(ziyaretler, Sonuc):
            return ziyaretler
        if ilac_bul(maskeli, ziyaretler):
            return None  # kayıtlı ilacın adı geçiyor: mevcut ad yolu
        o.anlasilmama = 0
        secilen = tarif_coz(tarif, ziyaretler, a) if ziyaretler else []
        if len(secilen) != 1:
            return self._kural_yaniti(self._tarif_sor(tarif, secilen, ziyaretler, konu))
        z, i = secilen[0]
        if konu is None or konu in OZET_KONULARI:
            # "Tansiyon ilacım ne işe yarıyor" / yalnız tarif: hekim onaylı özet (ad + amaç + talimat).
            self._son_ilaci_kaydet(z, i)
            metin = gecmis_metni(a, [z], "ilac", [(z, i)])
            if not self.ork.gecmis_sesli:
                metin = a.GECMIS_EKRANDA
            self._ek_veri["history"] = {"visits": [x.disa() for x in ziyaretler], "focus": [i.ad],
                                        "spoken": bool(self.ork.gecmis_sesli)}
            if konu:
                self._ek_veri["history"]["topic"] = konu
            sonuc = self._mevcut_adim(metin)
        else:
            sonuc = await self._kayitli_ilac_bilgisi(konu, [(z, i)], ziyaretler, maskeli, tarif=True)
            if sonuc is None:
                sonuc = self._ilac_konu_yaniti(konu, [(z, i)], ziyaretler, maskeli)
        if self.ork.gecmis_sesli and normalize(i.ad) not in normalize(sonuc.govde.get("replyText") or ""):
            sonuc = self._on_ekle(sonuc, a.ILAC_TARIF_ON.format(ad=i.ad))
        return self._kural_yaniti(sonuc)

    def _tarif_ek_soru_var(self, n: str, tarif: Tarif) -> bool:
        """Tarif ve ilaç kelimeleri dışında soru içeriği var mı ("araba", "güneş")? Yoksa ("tansiyon ilacım ne")
        hekim onaylı özet okunur."""
        a = self.alan
        kalan = []
        for k in n.split():
            if k in tarif.kelimeler or re.match(a.ILAC_TARIF_ILAC_KELIMESI, k) or re.match(a.ILAC_TARIF_KULLANIM, k) \
                    or re.match(a.ILAC_TARIF_AMAC_FIILI, k) or re.match(a.ILAC_TARIF_STT_HAP, k):
                continue
            kalan.append(k)
        metin = " ".join(kalan)
        return soru_mu(n) and ilac_adayi_var_mi(metin, a)

    def _tarif_sor(self, tarif: Tarif, secilen: list[tuple[Ziyaret, Ilac]], ziyaretler: list[Ziyaret],
                   konu: str | None) -> Sonuc:
        """Tarif birden çok kayıtlı ilaca uyuyor ya da hiçbirine uymuyor: tahmin edilmez, sorulur. Konu bir sonraki
        turda söylenen ilaç adıyla yanıtlanır (``bekleyen_konu``)."""
        a = self.alan
        adlar: list[str] = []
        for z in ziyaretler:
            for i in z.ilaclar:
                if all(normalize(i.ad) != normalize(x) for x in adlar):
                    adlar.append(i.ad)

        def liste(x: list[str]) -> str:
            return x[0] if len(x) == 1 else ", ".join(x[:-1]) + " ve " + x[-1]

        if not adlar:
            metin = a.ILAC_KAYITTA_YOK
        elif not self.ork.gecmis_sesli:
            metin = a.ILAC_TARIF_EKRANDA
        elif secilen:
            metin = a.ILAC_TARIF_HANGISI.format(tarif=tarif_metni(tarif, a), ilaclar=liste([i.ad for _, i in secilen]))
        else:
            metin = a.ILAC_TARIF_YOK.format(tarif=tarif_metni(tarif, a), ilaclar=liste(adlar))
        if adlar and konu and konu != "tanimsiz":
            self.o.bekleyen_konu = konu
        self._ek_veri["history"] = {"visits": [z.disa() for z in ziyaretler], "focus": [],
                                    "spoken": bool(self.ork.gecmis_sesli)}
        if konu:
            self._ek_veri["history"]["topic"] = veri_konusu(konu, a)
        return self._mevcut_adim(metin)

    # ---- Acil / sohbet / şikâyet önerisi (hepsi kural + şablon; LLM yok) ------------------------
    @staticmethod
    def _kural_yaniti(sonuc: Sonuc) -> Sonuc:
        sonuc.anlama_kaynagi = sonuc.anlama_kaynagi or "kural"
        return sonuc

    def _kurum_yaniti(self, konu: str) -> Sonuc:
        """Kurum sorusu: yapılandırmadan gelen sabit metin (ücret/sigorta için bilgi uydurulmaz, çağrı merkezine
        yönlendirilir). Durum ve seçim değişmez; ``data.institutionInfo`` konu kodu (geriye uyumlu ek alan)."""
        a = self.alan
        metin = a.KURUM_YANITLARI[konu]
        self._ek_veri["institutionInfo"] = konu
        self.o.anlasilmama = 0
        if self.o.durum == TC_BEKLENIYOR:
            return self._kural_yaniti(self._yanit("ASK_TC", metin))
        if self.o.durum == ONAY_BEKLENIYOR and self.o.secili in self.o.sunulan:
            metin = f"{metin} {a.KURUM_ONAY_HATIRLAT}"
        elif self.o.durum == SLOT_SECIMI:
            # Randevu akışının ortasında: adımın sorusu yeniden sorulur (kullanıcı nerede kaldığını kaçırmasın).
            beklenen = self.o.beklenen or ""
            soru = {"hekim": a.HEKIM_SORU_TEKRAR, "gun": a.GUN_SORU_TEKRAR, "saat": a.SAAT_SORU_TEKRAR}.get(beklenen)
            if beklenen == "hekim" and not self.o.hekim_adaylari:
                soru = None
            if beklenen == "secim" and self.o.sunulan:
                soru = a.SECIM_TEKRAR
            if soru:
                metin = f"{a.KURUM_YANITLARI_AKISTA.get(konu, metin)} {a.KURUM_ADIM_HATIRLAT}{soru}"
        elif self.o.durum == TAMAMLANDI:
            metin = a.KURUM_YANITLARI_AKISTA.get(konu, metin)
        return self._kural_yaniti(self._mevcut_adim(metin))

    @staticmethod
    def _on_ekle(sonuc: Sonuc, on: str) -> Sonuc:
        """Yanıt metninin başına kısa bir teyit cümlesi ekler (replyText ve message birlikte)."""
        metin = f"{on} {sonuc.govde.get('replyText') or ''}".strip()
        sonuc.govde["replyText"] = sonuc.govde["message"] = metin
        return sonuc

    def _hekim_eslestir(self, n: str) -> tuple[str, str] | None:
        """Hekim sorusunun yanıtındaki ad ("Selin Hanım", "Doktor Nermin") ya da tek bir adaya uyan unvan
        ("profesör olsun"). Birden çok aday tutarsa seçim yapılmaz."""
        adaylar = self.o.hekim_adaylari
        eslesen = []
        for anahtar, ad in adaylar:
            kelimeler = [k for k in normalize(ad).split() if k not in _UNVAN_KELIMELERI and len(k) > 1]
            if kelimeler and ifade_gecer_mi(n, kelimeler[0]):
                eslesen.append((anahtar, ad))
        if len(eslesen) == 1:
            return eslesen[0]
        if eslesen:
            return None
        for unvan, ifadeler in self.alan.HEKIM_UNVAN_IFADELERI.items():
            if any(ifade_gecer_mi(n, i) for i in ifadeler):
                uyan = [(k, ad) for k, ad in adaylar if normalize(ad).split()[:1] == [unvan]]
                if len(uyan) == 1:
                    return uyan[0]
        return None

    async def _hekim_yaniti(self, maskeli: str, ham: str = "") -> Sonuc | None:
        """Hekim sorusunun yanıtı. Ad/unvan/sıra → tercih; "fark etmez/tercihim yok" → tercih yok; kısa "evet"
        → soru yeniden. Diğer cümleler (gün, başka poliklinik, sohbet…) genel akışa bırakılır (None); hekim
        sorusu bu poliklinik için bir kez sorulduğundan yeniden sorulmaz (tercih yok sayılır).

        Hekim adı eşlemesi ham metinde yapılır: maskeleme "Selin Hanım"ı "[ad] hanım" yapar (LLM için doğru),
        oysa aday adları backend'den gelir ve eşleme kodda kalır; ham metin LLM'e de yanıta da gitmez."""
        o, a = self.o, self.alan
        n = normalize(maskeli)
        if not n or not o.hekim_adaylari:
            return None
        secilen = self._hekim_eslestir(normalize(ham)) if ham else None
        if secilen is None:
            secilen = self._hekim_eslestir(n)
        if secilen is None:
            kural = kural_tabanli_anla(maskeli, self.bugun, a, adim="SECIM_BEKLENIYOR")
            if kural.niyet == "secim" and kural.sira is not None and kural.goreli is None:
                idx = -1 if kural.sira == -1 else kural.sira - 1
                if -len(o.hekim_adaylari) <= idx < len(o.hekim_adaylari):
                    secilen = o.hekim_adaylari[idx]
        if secilen is not None:
            o.hekim, o.beklenen = secilen, None
            return self._on_ekle(await self._ilerle(), a.HEKIM_NOT_ALINDI.format(hekim=secilen[1].rstrip(".")))
        if any(ifade_gecer_mi(n, i) for i in ("en erken", "en yakin", "ilk bos")):
            return None  # genel akış: gün/saat de serbest, en erken saatler (hekim fark etmez)
        if n in {normalize(i) for i in a.HEKIM_TERCIH_YOK_TAM} \
                or any(ifade_gecer_mi(n, i) for i in a.HEKIM_FARK_ETMEZ_IFADELERI):
            o.hekim, o.beklenen = None, None
            return await self._ilerle()
        if n in {normalize(i) for i in a.ONERI_KABUL_IFADELERI}:
            return self._mevcut_adim(a.HEKIM_SORU_TEKRAR)
        if AD_MASKESI in maskeli:
            # Adaylara uymayan bir kişi adı ("Zümrüt Hanım"): LLM'e gönderilmez (2026-11-16 denetim: model
            # "[ad] hanım"ı kimlik bildirimi sanıp "kimlik numaranızı sesli söylemeyin" diyordu); hekimler sayılır.
            adlar = [ad for _, ad in o.hekim_adaylari]
            liste = ", ".join(adlar[:-1]) + " ve " + adlar[-1] if len(adlar) > 1 else adlar[0]
            return self._mevcut_adim(a.HEKIM_BULUNAMADI.format(hekimler=liste))
        return None

    async def _hekim_sor(self, p_ad: str) -> Sonuc | None:
        """Poliklinik için hekim sorusu (bir kez). Seçilen gün(ler)de boş saati olan birden çok hekim varsa
        sorulur; tek hekimde ya da "en erken/fark etmez" denmişse sorulmaz (None)."""
        o, a = self.o, self.alan
        o.hekim_poliklinik, o.hekim, o.hekim_adaylari = o.poliklinik, None, []
        if o.gun_serbest:
            return None
        slotlar, _ = await self._slotlari_getir([o.poliklinik or ""], [o.gun] if o.gun else self._ufuk())
        adaylar: list[tuple[str, str]] = []
        for s in slotlar:
            if s.hekim and all(s.hekim_anahtari != k for k, _ in adaylar):
                adaylar.append((s.hekim_anahtari, s.hekim))
        if len(adaylar) < 2:
            return None
        adaylar.sort(key=lambda h: normalize(h[1]).split()[-2:])
        o.hekim_adaylari = adaylar
        o.beklenen = "hekim"
        o.sunulan, o.sesli = {}, []
        o.son_sistem_hatasi = False
        adlar = [ad for _, ad in adaylar]
        liste = ", ".join(adlar[:-1]) + " ve " + adlar[-1]
        return self._yanit("OFFER_SLOTS", a.HEKIM_SORU.format(poliklinik=p_ad, hekimler=liste),
                           self._slot_verisi())

    def _acil_yaniti(self) -> Sonuc:
        """Acil durum: sabit 112 şablonu. Durum ve seçim değişmez (randevu akışına sokulmaz); ``data.emergency``
        ön yüzün 112 düğmesi gösterebilmesi için (geriye uyumlu ek alan)."""
        self._ek_veri["emergency"] = True
        if self.o.durum == TC_BEKLENIYOR:
            sonuc = self._yanit("ASK_TC", self.alan.ACIL_YANIT)
        else:
            sonuc = self._mevcut_adim(self.alan.ACIL_YANIT)
        return self._kural_yaniti(sonuc)

    def _sohbet_metni(self, anlam: Anlam) -> str | None:
        a = self.alan
        if anlam.niyet == "selam":
            return a.SELAM_YANIT
        if anlam.niyet == "yardim":
            return a.YARDIM_TAMAMLANDI if self.o.durum == TAMAMLANDI else a.YARDIM
        if anlam.niyet == "tesekkur":
            return a.TESEKKUR_YANIT
        if anlam.niyet == "veda":
            return a.VEDA_YANIT
        return None

    def _sohbet_yaniti(self, niyet: str, metin: str) -> Sonuc:
        if niyet == "veda":
            self.o.saglik_verisini_sil()  # kullanıcı ayrılıyor: sağlık verisi bellekte tutulmaz
            self._ek_veri["listen"] = False
            return self._mevcut_adim(metin)
        if self.o.durum == ONAY_BEKLENIYOR and self.o.secili in self.o.sunulan:
            # Onay sorusu geçerliliğini korur; randevu yine yalnız açık onayla.
            self.o.onay_soruldu = self._onay_soruldu
            return self._mevcut_adim(f"{metin} {self.alan.ONAY_ICIN_ACIK}")
        return self._mevcut_adim(metin)

    async def _oneri_yaniti(self, oneri: str, maskeli: str) -> Sonuc | None:
        """Önceki yanıt bir poliklinik önerdiyse kısa "evet/olur/bakalım" polikliniği SEÇER (randevu onayı
        DEĞİLDİR: seçili saat yok, durum SLOT_SECIMI), "hayır/başka" poliklinik sorar. Diğer cümleler
        normal anlama akışına bırakılır (None)."""
        a = self.alan
        n = normalize(maskeli)
        if n in {normalize(i) for i in a.ONERI_KABUL_IFADELERI} or self._randevu_istegi_mi(n):
            # "evet" ya da "tamam, randevu alayım" (poliklinik adı söylenmeden): önerilen poliklinik seçilir.
            self.o.poliklinik = oneri
            self.o.sunulan, self.o.sesli, self.o.secili = {}, [], None
            self._randevu_niyeti()
            return await self._ilerle()
        if any(ifade_gecer_mi(n, i) for i in a.ONERI_RET_IFADELERI) and metindeki_poliklinik(n, a) is None \
                and kural_tabanli_anla(maskeli, self.bugun, a).niyet not in ("sikayet", "tibbi_soru", "acil"):
            self.o.poliklinik = None
            self.o.beklenen = "poliklinik"
            return self._mevcut_adim(a.POLIKLINIK_SORU.format(liste=a.poliklinik_listesi_metni()))
        return None

    def _oneri_ve_zaman(self, oneri: str, maskeli: str) -> Anlam | None:
        n = normalize(maskeli)
        ilk = n.split()[0] if n else ""
        if ilk not in {normalize(i) for i in self.alan.ONERI_KABUL_IFADELERI}:
            return None
        kural = kural_tabanli_anla(maskeli, self.bugun, self.alan, adim=self._llm_adimi())
        if kural.niyet == "poliklinik_ve_zaman" and kural.poliklinik is None and kural.zaman_var:
            return replace(kural, poliklinik=oneri)
        return None

    # ---- Tavsiye (şikâyet için "evde ne yapabilirim", "randevu değil, fikir ver") ----------------------
    def _randevu_akisinda(self) -> bool:
        """Randevu akışının ortasında mı (poliklinik seçilmiş, hekim/gün/saat/seçim/onay bekleniyor ya da kullanıcı
        randevu istedi)? Akış dışındayken anlaşılmayan cümlede poliklinik sorulmaz."""
        o = self.o
        if o.durum == ONAY_BEKLENIYOR:
            return True
        if o.durum != SLOT_SECIMI:
            return False
        return bool(o.poliklinik) or o.beklenen in ("hekim", "gun", "saat", "secim") or o.randevu_istendi

    def _randevu_niyeti(self) -> None:
        self.o.randevu_istendi = True
        self.o.randevu_reddedildi = False

    def _randevu_istegi_mi(self, n: str) -> bool:
        """"Tamam randevu alayım", "evet randevu bak": poliklinik adı ve ret olmadan açık randevu isteği."""
        a = self.alan
        if not n or randevu_reddi(n, a) or metindeki_poliklinik(n, a) is not None:
            return False
        if any(ifade_gecer_mi(n, i) for i in ("istemiyorum", "istemem", "değil", "başka", "hayır")):
            return False
        return any(ifade_gecer_mi(n, i) for i in a.RANDEVU_ISTEGI_IFADELERI) and any(
            ifade_gecer_mi(n, i) for i in (*a.RANDEVU_ISTEK_FIILLERI, "bak", "alayım", "alalım", "olsun"))

    async def _tavsiye_yolu(self, maskeli: str, oneri: str | None) -> Sonuc | None:
        """Şikâyet için tavsiye isteği ya da randevu reddi. ``None``: bu yolun dışı (mevcut akış).

        Sıra (acil ve kurum soruları çağıranda önce yakalanır):
        1. Önceki tur "…evde neler yapılabileceğini anlatabilirim" dediyse "evet/anlat" → tavsiye.
        2. Ciddi belirti, ilaç/doz/teşhis/tahlil, çocuk/gebelik, poliklinik adı → None (mevcut akış).
        3. Tavsiye işareti + konu (cümleden ya da oturumdaki son şikâyetten) → yerel LLM'le genel öz bakım bilgisi
           (soru koddan kurulur) + koddan "sürerse doktora başvurun" + sabit kapanış + ısrarsız randevu teklifi.
           Konu bilinmiyorsa "Hangi şikâyetiniz için?" sorulur (poliklinik sorulmaz).
        4. Randevu reddi ("randevu değil", öneriye "istemiyorum") → "Peki, randevu bakmıyorum." + tavsiye teklifi."""
        a, o = self.alan, self.o
        if o.durum not in (SLOT_SECIMI, ONAY_BEKLENIYOR, TAMAMLANDI):
            return None
        n = normalize(maskeli)
        if not n:
            return None
        llm_var = self.ork.saglik_llm is not None
        if self._tavsiye_teklifi and o.son_sikayet and llm_var \
                and n in {normalize(i) for i in a.TAVSIYE_KABUL_IFADELERI}:
            return await self._tavsiye_yaniti(o.son_sikayet, ())
        engel = tavsiye_engeli(n, a, bool(ilac_adlari(n, self._kayitli_ilac_adlari())))
        if engel:
            return None
        onceki = o.son_sikayet
        konu = sikayet_konusu(n, a)
        if konu:
            o.son_sikayet = konu
        hedef = konu or onceki
        istek = tavsiye_istegi(n, a)
        akista = self._randevu_akisinda()
        ret = (randevu_reddi(n, a) or (oneri is not None and n in {normalize(i) for i in a.ONERI_RANDEVU_RET_TAM})) \
            and (not akista or oneri is not None)
        kisisel = kisisel_sikayet_var(n, a)
        randevu_dendi = ifade_gecer_mi(n, "randevu")
        if istek == "zayif" and not hedef:
            istek = None
        if istek and akista and not konu and istek != "guclu":
            istek = None  # akış ortasında ("yarın evde olmam") yalnız açık tavsiye isteği yanıtlanır
        if istek and konu and not kisisel and onceki is None and not ret and not self._konu_soruldu:
            # Genel bilgi sorusu ("baş ağrısına ne iyi gelir"): mevcut genel sağlık bilgisi yolu yanıtlar.
            karar = saglik_karari(maskeli, a, self._kayitli_ilac_adlari())
            if karar is not None and karar.tur == "bilgi":
                return None
        if ret:
            o.randevu_reddedildi, o.randevu_istendi = True, False
            # Fikrini değiştirip "tamam, randevu alayım" derse konunun polikliniği kullanılır (teklif edilmez).
            o.tavsiye_poliklinik = konu_poliklinigi(hedef, a) or o.tavsiye_poliklinik
        if (istek or (self._konu_soruldu and konu)) and llm_var:
            if hedef is None:
                if saglik_karari(maskeli, a, self._kayitli_ilac_adlari()) is not None and not ret:
                    return None  # genel sağlık bilgi sorusu ("D vitamini eksikliğine ne iyi gelir")
                o.konu_soruldu = True
                o.anlasilmama = 0
                metin = f"{a.RANDEVU_RET_YANIT} {a.TAVSIYE_KONU_SOR}" if ret and randevu_dendi else a.TAVSIYE_KONU_SOR
                return self._kural_yaniti(self._mevcut_adim(metin))
            return await self._tavsiye_yaniti(hedef, alt_sorular(n, a),
                                              on=a.RANDEVU_RET_YANIT if ret and not istek and randevu_dendi else "")
        if ret:
            o.anlasilmama = 0
            parca = [a.RANDEVU_RET_YANIT]
            if oneri and a.ONERI_ACIL_NOTU.get(oneri):
                parca.append(a.ONERI_ACIL_NOTU[oneri])  # "kalbim ağrıyor" → "istemiyorum": 112 notu korunur
            if hedef and llm_var:
                o.tavsiye_teklifi = True
                parca.append(a.RANDEVU_RET_TAVSIYE_TEKLIFI.format(konu=konu_adi(hedef, a)))
            else:
                parca.append(a.RANDEVU_RET_GENEL)
            return self._kural_yaniti(self._mevcut_adim(" ".join(parca)))
        return None

    async def _tavsiye_yaniti(self, konu: str, alt: tuple[str, ...], on: str = "") -> Sonuc:
        """Konu için genel öz bakım bilgisi. LLM'e yalnız koddan kurulan konu + soru gider; metin denetlenir.
        Randevu akışı dışında ve kullanıcı randevuyu reddetmediyse konu polikliniği ısrarsızca teklif edilir
        (``data.suggestion``; sonraki "evet" yalnız polikliniği seçer, randevu ayrı açık onayla oluşur)."""
        a, o = self.alan, self.o
        o.son_sikayet = konu
        teklif = ""
        p = a.poliklinik_bul(konu_poliklinigi(konu, a))
        if p is not None and o.durum == SLOT_SECIMI and not self._randevu_akisinda() and not o.randevu_reddedildi \
                and p.kod not in o.teklif_edilen:
            # Israrsız: aynı poliklinik konuşmada bir kez teklif edilir ("tamam randevu alayım" yine çalışır).
            teklif = a.TAVSIYE_RANDEVU_TEKLIFI.format(poliklinik=p.ad)
        uyari = a.TAVSIYE_UYARI_NOTLARI.get(konu, "")
        sabit = [x for x in (uyari, a.TAVSIYE_SURERSE, a.SAGLIK_BILGI_KAPANIS) if x]
        butce = a.SAGLIK_BILGI_AZAMI_SESLI - len(" ".join([on, *sabit, teklif]).strip()) - 1
        sonuc = await genel_bilgi_uret("", alan=a, llm=self.ork.saglik_llm,
                                       zaman_asimi_sn=self.ork.saglik_bilgi_zaman_asimi_sn,
                                       yuk=saglik_tavsiye_yuku_kur(konu, alt, alan=a),
                                       azami_karakter=butce, tavsiye=True)
        if sonuc.metin is not None:
            parcalar = [on, sonuc.metin, *sabit]
        else:
            parcalar = [on, a.SAGLIK_BILGI_VEREMIYORUM, uyari]
        if p is not None:
            o.tavsiye_poliklinik = p.kod
        if teklif:
            parcalar.append(teklif)
            o.teklif_edilen.add(p.kod)
            o.oneri = p.kod
            self._ek_veri["suggestion"] = {"clinic": p.kod, "clinicName": p.ad}
        yanit = self._saglik_sonucu(" ".join(x for x in parcalar if x), ai=sonuc.metin is not None)
        if sonuc.sure_ms:
            yanit.zamanlama["llm"] = sonuc.sure_ms
        return yanit

    def _sikayet_yaniti(self, anlam: Anlam) -> Sonuc:
        """Şikâyet: tek polikliniğe işaret ediyorsa öneri + teyit sorusu; değilse poliklinik listesi.
        Teşhis yoktur. Onay beklenirken öneri SORULMAZ (oradaki "evet" randevu onayıyla karışmasın)."""
        a, o = self.alan, self.o
        p = a.poliklinik_bul(anlam.oneri)
        tibbi = anlam.niyet == "tibbi_soru"
        if o.durum == ONAY_BEKLENIYOR and o.secili in o.sunulan:
            bilgi = a.ONERI_BILGI.format(poliklinik=p.ad) + " " if p else ""
            if tibbi:
                bilgi = a.TIBBI_YORUM_YOK + " " + bilgi
            return self._mevcut_adim(bilgi + a.ONAY_BEKLIYOR_HATIRLAT)
        if o.durum == ONAY_BEKLENIYOR:
            o.durum, o.secili = SLOT_SECIMI, None
        # Şikâyetle birlikte söylenen gün/saat saklanır ("kalbim için cuma öğleden sonra").
        on_ek = ""
        if anlam.gun and self._ufuk_disi(anlam.gun):
            on_ek = self._ufuk_metni() + " "
            anlam = replace(anlam, gun=None)
        if anlam.gun:
            o.gun, o.gun_serbest = anlam.gun, False
        if anlam.saat_baslangic is not None:
            o.saat_bas, o.saat_bit, o.saat_serbest = anlam.saat_baslangic, anlam.saat_bitis or anlam.saat_baslangic, False
        if anlam.fark_etmez:
            o.gun_serbest = o.gun_serbest or o.gun is None
            o.saat_serbest = o.saat_serbest or o.saat_bas is None
        if anlam.zaman_var:
            on_ek += a.TERCIH_NOT_ALINDI.format(tercih=self._aralik_metni(o.gun, o.saat_bas, o.saat_bit)) + " "
        o.beklenen = "poliklinik"
        liste = a.poliklinik_listesi_metni()
        if p is None and o.randevu_reddedildi and not tibbi:
            # Randevu istemediğini söyledi: poliklinik listesiyle "hangisinden randevu?" diye sorulmaz.
            metin = a.TAVSIYE_SURERSE
            if o.son_sikayet and self.ork.saglik_llm is not None:
                o.tavsiye_teklifi = True
                metin += " " + a.RANDEVU_RET_TAVSIYE_TEKLIFI.format(konu=konu_adi(o.son_sikayet, a))
            return self._mevcut_adim(on_ek + metin)
        if p is None:
            if tibbi:
                return self._mevcut_adim(a.TIBBI_YANIT)
            sablon = a.YONLENDIRME_SOR if anlam.alt == "yonlendirme" else a.SIKAYET_BELIRSIZ
            return self._mevcut_adim(on_ek + sablon.format(liste=liste))
        if o.randevu_reddedildi and not tibbi:
            # Kullanıcı bu konuşmada randevu istemediğini söyledi: poliklinik bilgisi verilir, randevu sorulmaz.
            metin = a.ONERI_BILGI.format(poliklinik=p.ad)
            notu = a.ONERI_ACIL_NOTU.get(p.kod)
            if notu:
                metin += " " + notu
            if o.son_sikayet and self.ork.saglik_llm is not None:
                o.tavsiye_teklifi = True
                metin += " " + a.RANDEVU_RET_TAVSIYE_TEKLIFI.format(konu=konu_adi(o.son_sikayet, a))
            return self._mevcut_adim(on_ek + metin)
        o.oneri = p.kod
        self._ek_veri["suggestion"] = {"clinic": p.kod, "clinicName": p.ad}
        metin = a.ONERI_SOR.format(poliklinik=p.ad)
        notu = a.ONERI_ACIL_NOTU.get(p.kod)
        if notu:
            metin += " " + notu
        if tibbi:
            metin = a.TIBBI_YORUM_YOK + " " + metin
        return self._mevcut_adim(on_ek + metin)

    def _onbellekli_ilac_niyeti(self, maskeli: str) -> Anlam | None:
        """Geçmiş zaten alındıysa ve cümlede kayıtlı bir ilacın adı geçiyorsa (kodda bulanık
        eşleşme), LLM'e gitmeden ilaç sorusu sayılır; konu kuralla bulunur. Ad geçmiyor ama bir ilaç
        konusu soruluyorsa ve az önce bir ilaç konuşulduysa ("peki alkol?") takip sorusudur."""
        if not self.o.gecmis:
            return None
        kural = kural_tabanli_anla(maskeli, self.bugun, self.alan, adim=self._llm_adimi())
        if kural.acik_onay:
            return None
        n = normalize(maskeli)
        konu, guclu = konu_bul(n, self.alan)
        if kural.niyet in ("belirsiz", "tibbi_soru", "sikayet", "baska_saat", "ret", "onay") \
                and ilac_bul(maskeli, self.o.gecmis):
            if konu or self._bekleyen_konu:
                return Anlam("ilac_sorusu", gecmis_konu="liste", ilac_konu=konu or self._bekleyen_konu)
            if any(ifade_gecer_mi(n, i) for i in self.alan.ILAC_TAVSIYE_IFADELERI):
                return Anlam("ilac_tavsiye")
            return Anlam("ilac_sorusu", gecmis_konu="liste")
        # Takip sorusu yalnız ilaca dönük olduğu açıksa: güçlü ifade, ilaca özgü konu (alkol, yemek, alerji,
        # hamilelik), zamir ("bunu", "peki") ya da ilaç fiili. "Eşimle birlikte gelebilir miyim" takip değildir.
        takip = guclu or konu in ("alkol", "yemek", "alerji", "hamilelik", *OZET_KONULARI) or any(
            ifade_gecer_mi(n, i) for i in _TAKIP_ISARETLERI)
        if konu and takip and self.o.son_ilac is not None and kural.niyet in (
                "belirsiz", "tibbi_soru", "sikayet", "ilac_tavsiye", "ret", "onay"):
            return Anlam("ilac_sorusu", gecmis_konu="liste", ilac_konu=konu)
        return None

    async def _anlami_uygula(self, anlam: Anlam, maskeli: str, ham_metin: str) -> Sonuc:
        a = self.alan
        n = anlam.niyet
        if n != "belirsiz":
            self.o.anlasilmama = 0
        if n in ("randevu_istegi", "poliklinik_degistir", "gun_degistir", "fark_etmez", "secim", "baska_saat") \
                or (n == "poliklinik_ve_zaman" and (anlam.poliklinik or anlam.zaman_var or anlam.fark_etmez)):
            self._randevu_niyeti()
        if n == "kimlik":
            return self._mevcut_adim(a.TC_TUS_TAKIMINDAN)
        if n == "kurum_bilgisi" and anlam.alt in a.KURUM_YANITLARI:
            return self._kurum_yaniti(anlam.alt)
        if n == "ilac_tavsiye":
            return self._mevcut_adim(a.ILAC_TAVSIYE_RET)
        if n == "acil":
            return self._acil_yaniti()
        if n in _GECMIS_NIYETLERI:
            return await self._gecmis_yaniti(anlam, maskeli, ham_metin)
        if n == "sikayet" or (n == "tibbi_soru" and anlam.oneri and anlam.kaynak != "llm"):
            return self._sikayet_yaniti(anlam)
        if n == "tibbi_soru":
            return self._mevcut_adim(a.TIBBI_YANIT)
        sohbet = self._sohbet_metni(anlam) if anlam.kaynak != "llm" else None
        if sohbet is not None:
            return self._sohbet_yaniti(n, sohbet)
        if n == "mevcut_randevu":
            if self.o.durum == ONAY_BEKLENIYOR and anlam.alt == "degisiklik":
                # Onay beklenirken "randevuyu iptal et": seçili (henüz oluşmamış) randevudan vazgeçmektir.
                self.o.durum, self.o.secili = SLOT_SECIMI, None
                self.o.beklenen = "secim"
                return self._mevcut_adim(a.SECIM_IPTAL)
            return self._mevcut_adim(a.MEVCUT_RANDEVU_IPTAL if anlam.alt == "degisiklik" else a.MEVCUT_RANDEVU_YOK)
        if n == "onay":
            if self.o.durum == ONAY_BEKLENIYOR and self.o.secili in self.o.sunulan:
                if anlam.acik_onay and anlam.kaynak != "llm":
                    if not self._onay_soruldu and not self.bosta_kaldi:
                        # Son yanıt onay sorusu değildi (arada geçmiş/tıbbi/anlaşılmadı yanıtı): "evet"
                        # başka bir şeye verilmiş olabilir. Seçim yeniden okunur, randevu YOK.
                        return self._onay_iste()
                    return await self._randevu_al()  # boşta kaldıysa _randevu_al reddeder
                # LLM'in "onay" kararı ya da kapalı listede olmayan onaya benzer cümle ASLA
                # rezervasyon yapmaz (K1/Y1): yalnız açık onay istenir. Onay sorusu hâlâ geçerlidir.
                self.o.onay_soruldu = self._onay_soruldu
                return self._mevcut_adim(a.ONAY_ICIN_ACIK)
            return self._mevcut_adim(a.ONCE_SAAT_SECIN)
        if n == "ret":
            if self.o.durum == ONAY_BEKLENIYOR:
                self.o.durum, self.o.secili = SLOT_SECIMI, None
                self.o.beklenen = "secim"
                return self._mevcut_adim(a.SECIM_IPTAL)
            if self.o.durum == SLOT_SECIMI and not self._randevu_akisinda():
                # Randevu akışı dışında "hayır / değil": poliklinik sorusuyla üstüne gidilmez.
                return self._mevcut_adim(a.RET_NOTR)
            return await self._baska_saat()
        if n == "secim":
            return await self._secim(anlam)
        if n == "poliklinik_degistir":
            self.o.poliklinik = None
            self.o.sunulan, self.o.sesli = {}, []
            return await self._ilerle()
        if n == "gun_degistir":
            self.o.gun, self.o.gun_serbest = None, False
            if not self.o.poliklinik:
                return await self._ilerle()
            return await self._ilerle(gun_sor=True)
        if n == "baska_saat":
            return await self._baska_saat()
        if n == "fark_etmez":
            return await self._fark_etmez()
        if n == "poliklinik_ve_zaman" and (anlam.poliklinik or anlam.zaman_var or anlam.fark_etmez):
            return await self._tercih_uygula(anlam)
        if n == "gecmis_tarih":
            # "13 Kasım" bugünden önce: gelecek yıla atanmaz, gün yeniden sorulur (seçim değişmez).
            if self.o.durum == ONAY_BEKLENIYOR:
                self.o.onay_soruldu = self._onay_soruldu
                return self._mevcut_adim(a.GECMIS_TARIH)
            if anlam.poliklinik:
                self.o.poliklinik = anlam.poliklinik
            self.o.gun, self.o.gun_serbest = None, False
            self.o.beklenen = "gun" if self.o.poliklinik else (self.o.beklenen or "poliklinik")
            return self._mevcut_adim(a.GECMIS_TARIH)
        if n == "randevu_istegi":
            # "Ayşe Yılmaz için randevu istiyorum": kural niyeti çıkardı, LLM'e gidilmez; eksik bilgi sorulur.
            if self.o.durum == ONAY_BEKLENIYOR:
                self.o.onay_soruldu = self._onay_soruldu
                return self._mevcut_adim(a.ONAY_ICIN_ACIK)
            return await self._ilerle()
        if n == "konu_disi":
            return self._mevcut_adim(a.KONU_DISI_YANIT)
        return self._anlasilmadi(anlam)

    def _anlasilmadi(self, anlam: Anlam) -> Sonuc:
        """Bir kez farklı sözle yeniden sorar; ikinci kez ekran seçeneklerini ve örnek gösterir.
        Sesli döngü bu noktada durur (listen=false): sonsuz döngü yok."""
        a = self.alan
        if anlam.kaynak == "kural_yedek":
            return self._mevcut_adim(a.ANLAMA_KULLANILAMIYOR, 503, "LLM_UNAVAILABLE")
        self.o.anlasilmama += 1
        beklenen = "onay" if self.o.durum == ONAY_BEKLENIYOR else (self.o.beklenen or "poliklinik")
        if beklenen == "poliklinik" and not self._randevu_akisinda():
            # Kullanıcı randevu akışında değil: "hangi poliklinik?" diye zorlanmaz; ne yapılabileceği söylenir.
            return self._mevcut_adim(a.ANLASILAMADI_NOTR_EKRAN if self.o.anlasilmama >= 2 else a.ANLASILAMADI_NOTR)
        if self.o.anlasilmama >= 2:
            return self._mevcut_adim(a.ANLASILAMADI_EKRAN.format(ornek=a.ANLASILAMADI_ORNEKLERI[beklenen]))
        tekrar = {"poliklinik": a.POLIKLINIK_SORU_TEKRAR, "gun": a.GUN_SORU_TEKRAR, "saat": a.SAAT_SORU_TEKRAR,
                  "secim": a.SECIM_TEKRAR, "onay": a.ONAY_ICIN_ACIK, "hekim": a.HEKIM_SORU_TEKRAR}[beklenen]
        return self._mevcut_adim(a.ANLASILAMADI_ONEKI + tekrar)

    def _tamamlandi_metni(self) -> str:
        r = self.o.randevu or {}
        if not r.get("accessCode"):
            return self.alan.TAMAMLANDI_BILGI
        return self.alan.RANDEVU_ZATEN_VAR.format(etiket=r.get("longLabel", ""), poliklinik=r.get("clinicName", ""),
                                                  kod=r["accessCode"])

    # ---- Yuva doldurma diyaloğu --------------------------------------------------
    async def _tercih_uygula(self, anlam: Anlam) -> Sonuc:
        o = self.o
        bas, bit = anlam.saat_baslangic, anlam.saat_bitis
        # Sunulan saatlerden biri söylendiyse ("dokuz buçuk", "yarın 10:15") doğrudan seçilir.
        # Poliklinik henüz seçilmediyse ``sunulan`` her poliklinikten karışık ekran yedeğidir: "yarın saat on"
        # poliklinik sorusunu atlayıp rastgele bir polikliniğin saatini seçmesin.
        gorunur = o.poliklinik or o.beklenen == "secim" or o.durum == ONAY_BEKLENIYOR
        if bas is not None and bas == bit and not anlam.poliklinik and o.sunulan and gorunur:
            eslesen = [s for s in o.sunulan.values() if yerel_saat(s.baslangic) == bas
                       and (anlam.gun is None or yerel(s.baslangic).date() == anlam.gun)]
            if eslesen:
                return self._sec(eslesen[0])
        if anlam.gun and self._ufuk_disi(anlam.gun):
            # "20 Aralık", "iki hafta sonra salı": slotlar açık değil; uydurma "boş saat yok" yerine dürüst şablon.
            if anlam.poliklinik:
                o.poliklinik = anlam.poliklinik
            o.gun, o.gun_serbest = None, False
            if o.durum == ONAY_BEKLENIYOR:
                o.onay_soruldu = self._onay_soruldu
                return self._mevcut_adim(self._ufuk_metni())
            if o.poliklinik:
                o.beklenen = "gun"
                return self._mevcut_adim(f"{self._ufuk_metni()} {self.alan.UFUK_GUN_SORU}")
            o.beklenen = "poliklinik"
            return self._mevcut_adim(f"{self._ufuk_metni()} "
                                     + self.alan.POLIKLINIK_SORU.format(liste=self.alan.poliklinik_listesi_metni()))
        if anlam.poliklinik:
            o.poliklinik = anlam.poliklinik
        elif not o.poliklinik and (anlam.gun or bas is not None):
            # Poliklinikten önce yalnız gün/saat söylendi ("haftaya salı"): yanıt bunu teyit eder.
            self._tercih_teyit = True
        if anlam.gun:
            o.gun, o.gun_serbest = anlam.gun, False
        if bas is not None:
            o.saat_bas, o.saat_bit, o.saat_serbest = bas, bit or bas, False
        if anlam.fark_etmez:
            if o.gun is None:
                o.gun_serbest = True
            if bas is None:
                o.saat_serbest = True
        return await self._ilerle()

    async def _fark_etmez(self) -> Sonuc:
        o, a = self.o, self.alan
        if o.durum == ONAY_BEKLENIYOR:
            return self._mevcut_adim(a.ONAY_ICIN_ACIK)
        if o.beklenen == "secim" and o.sunulan:
            ilk = o.sunulan.get(o.sesli[0]) if o.sesli else next(iter(o.sunulan.values()))
            if ilk is not None:
                return self._sec(ilk)
        if not o.poliklinik:
            o.beklenen = "poliklinik"
            return self._mevcut_adim(a.POLIKLINIK_GEREKLI.format(liste=a.poliklinik_listesi_metni()))
        if o.gun is None:
            o.gun_serbest = True
        if o.saat_bas is None:
            o.saat_serbest = True
        return await self._ilerle()

    def _son_gun(self) -> date:
        return self.bugun + timedelta(days=self.ork.randevu_ufuk_gun - 1)

    def _ufuk_disi(self, gun: date) -> bool:
        return gun > self._son_gun()

    def _ufuk_metni(self) -> str:
        return self.alan.UFUK_DISI.format(gun_sayisi=self.ork.randevu_ufuk_gun, son_tarih=tarih_metni(self._son_gun()))

    def _ufuk(self, gun_sayisi: int | None = None) -> list[date]:
        return [self.bugun + timedelta(days=i) for i in range(gun_sayisi or self.ork.arama_gun_sayisi)]

    def _gun_basligi(self, g: date) -> str:
        fark = (g - self.bugun).days
        return "Bugün" if fark == 0 else "Yarın" if fark == 1 else tarih_metni(g)

    def _gun_ifadesi(self, g: date) -> str:
        """Cümle içinde: "yarın", "bugün", "20 Kasım Cuma günü"."""
        fark = (g - self.bugun).days
        return "bugün" if fark == 0 else "yarın" if fark == 1 else f"{tarih_metni(g)} günü"

    def _secenek_metni(self, slotlar: list[SlotBilgisi]) -> str:
        """Sesli okunacak seçenekler: "Yarın 09:00, 09:30 ve 09:45" (saatleri TTS doğal okur)."""
        gruplar: list[tuple[date, list[str]]] = []
        for s in slotlar:
            g = yerel(s.baslangic).date()
            if not gruplar or gruplar[-1][0] != g:
                gruplar.append((g, []))
            gruplar[-1][1].append(f"{yerel(s.baslangic):%H:%M}")
        parcalar = []
        for g, saatler in gruplar:
            liste = saatler[0] if len(saatler) == 1 else ", ".join(saatler[:-1]) + " ve " + saatler[-1]
            parcalar.append(f"{self._gun_basligi(g)} {liste}")
        return "; ".join(parcalar)

    def _sun(self, slotlar: list[SlotBilgisi], sablon: str | None = None, **alanlar: str) -> Sonuc:
        """Saatleri ekranda listeler, ilk üçünü sesli okur ve hangisinin istendiğini sorar."""
        o, a = self.o, self.alan
        ekran = _tekil_saatler(slotlar)[: self.ork.sunulan_slot_sayisi]
        o.sunulan = {s.slot_id: s for s in ekran}
        o.sesli = [s.slot_id for s in ekran[:SESLI_SECENEK]]
        o.durum, o.secili, o.beklenen = SLOT_SECIMI, None, "secim"
        secenekler = self._secenek_metni(ekran[:SESLI_SECENEK])
        if sablon is None:
            metin = a.SECENEK_SUN.format(secenekler=secenekler)
        else:
            metin = sablon.format(secenekler=secenekler, **alanlar)
        if len(ekran) > SESLI_SECENEK:
            metin += a.SECENEK_EKRANDA_DAHA
        if o.poliklinik and len(ekran) == 1 and o.saat_bas is not None and o.saat_bas == o.saat_bit \
                and yerel_saat(ekran[0].baslangic) == o.saat_bas:
            return self._sec(ekran[0])  # tek ve tam eşleşme: doğrudan onaya (randevu yine yalnız açık onayla)
        return self._yanit("OFFER_SLOTS", metin, self._slot_verisi())

    def _ekrana_koy(self, slotlar: list[SlotBilgisi]) -> None:
        """Soru sorulurken yedek olarak ekranda gösterilecek saatler (sesli okunmaz)."""
        ekran = _tekil_saatler(slotlar)[: self.ork.sunulan_slot_sayisi]
        self.o.sunulan = {s.slot_id: s for s in ekran}
        self.o.sesli = []

    def _saat_sistem_hatasi(self) -> Sonuc:
        self.o.sunulan, self.o.sesli = {}, []
        self.o.son_sistem_hatasi = True
        return self._yanit("OFFER_SLOTS", self.alan.SISTEM_HATASI_SAATLER, self._slot_verisi(), 503,
                           "API_UNAVAILABLE", "Uygun saatler API'den alınamadı.")

    def _poliklinik_bos(self, p_ad: str) -> Sonuc:
        o = self.o
        if o.hekim is not None and o.hekim_poliklinik == o.poliklinik and len(o.hekim_adaylari) > 1:
            # Seçilen hekimin boş saati yok (poliklinikte başka hekim var): tercih kaldırılır, hekim yeniden sorulur.
            ad = o.hekim[1]
            o.hekim, o.gun, o.gun_serbest = None, None, False
            o.sunulan, o.sesli, o.beklenen = {}, [], "hekim"
            o.son_sistem_hatasi = False
            return self._yanit("OFFER_SLOTS", self.alan.HEKIM_BOS.format(hekim=ad), self._slot_verisi())
        o.poliklinik = None
        o.sunulan, o.sesli, o.beklenen = {}, [], "poliklinik"
        o.son_sistem_hatasi = False
        return self._yanit("OFFER_SLOTS", self.alan.POLIKLINIK_HIC_YOK.format(poliklinik=p_ad), self._slot_verisi())

    async def _ilerle(self, gun_sor: bool = False) -> Sonuc:
        """Eksik bilgi neyse onu sorar; bilgiler tamamsa uygun saatleri sesli önerir."""
        o, a = self.o, self.alan
        o.durum, o.secili = SLOT_SECIMI, None
        if not o.poliklinik:
            o.beklenen = "poliklinik"
            if not o.sunulan:
                slotlar, hata = await self._ilk_oneriler()
                self._ekrana_koy(slotlar)
                o.son_sistem_hatasi = hata
            soru = a.POLIKLINIK_SORU.format(liste=a.poliklinik_listesi_metni())
            if getattr(self, "_tercih_teyit", False) and (o.gun is not None or o.saat_bas is not None):
                # "haftaya salı" tek başına: gün saklanır ve yanıtta açıkça teyit edilir.
                soru = a.TERCIH_NOT_ALINDI.format(tercih=self._aralik_metni(o.gun, o.saat_bas, o.saat_bit)) + " " + soru
            return self._mevcut_adim(soru)
        p = a.poliklinik_bul(o.poliklinik)
        p_ad = p.ad if p else o.poliklinik
        if o.hekim_poliklinik != o.poliklinik:
            soru = await self._hekim_sor(p_ad)
            if soru is not None:
                return soru
        gun_serbest = o.gun is None and not gun_sor and (o.gun_serbest or o.saat_bas is not None)
        if o.gun is None and not gun_serbest:
            slotlar, hata = await self._slotlari_getir([o.poliklinik], self._ufuk())
            if not slotlar:
                return self._saat_sistem_hatasi() if hata else self._poliklinik_bos(p_ad)
            self._ekrana_koy(slotlar)
            o.son_sistem_hatasi = hata
            o.beklenen = "gun"
            return self._yanit("OFFER_SLOTS", a.GUN_SORU.format(poliklinik=p_ad), self._slot_verisi())
        gunler = [o.gun] if o.gun else self._ufuk()
        slotlar, hata = await self._slotlari_getir([o.poliklinik], gunler)
        o.son_sistem_hatasi = hata
        if not slotlar:
            if hata:
                return self._saat_sistem_hatasi()
            if o.gun:
                return await self._yakin_gun_oner(p_ad)
            return self._poliklinik_bos(p_ad)
        if o.saat_bas is None and not o.saat_serbest and o.gun and len(slotlar) > SESLI_SECENEK:
            self._ekrana_koy(slotlar)
            o.beklenen = "saat"
            return self._yanit("OFFER_SLOTS", a.SAAT_SORU.format(gun=self._gun_basligi(o.gun)), self._slot_verisi())
        return self._saate_gore_sun(slotlar)

    @staticmethod
    def _aralikta(s: SlotBilgisi, bas: time | None, bit: time | None) -> bool:
        if bas is None:
            return True
        t = yerel_saat(s.baslangic)
        return bas <= t <= (bit or bas)

    def _saate_gore_sun(self, slotlar: list[SlotBilgisi]) -> Sonuc:
        o, a = self.o, self.alan
        bas, bit = o.saat_bas, o.saat_bit
        if bas is None:
            return self._sun(slotlar)
        uygun = [s for s in slotlar if self._aralikta(s, bas, bit)]
        if uygun:
            return self._sun(uygun)
        # İstenen saatte/aralıkta yer yok: en yakın seçenekler (aynı gün(ler)de).
        alt, ust = bas.hour * 60 + bas.minute, (bit or bas).hour * 60 + (bit or bas).minute

        def uzaklik(s: SlotBilgisi) -> int:
            t = yerel_saat(s.baslangic)
            dk = t.hour * 60 + t.minute
            return 0 if alt <= dk <= ust else min(abs(dk - alt), abs(dk - ust))

        yakin = sorted(slotlar, key=lambda s: (uzaklik(s), s.baslangic))[:SESLI_SECENEK]
        saat_ifadesi = f"saat {bas:%H:%M}" if bas == bit else f"{bas:%H:%M} ile {(bit or bas):%H:%M} arası"
        gun = self._gun_basligi(o.gun) if o.gun else "Önümüzdeki günlerde"
        return self._sun(yakin, a.ISTENEN_YOK_YAKIN, istek=f"{gun} {saat_ifadesi} için")

    async def _yakin_gun_oner(self, p_ad: str) -> Sonuc:
        """İstenen günde hiç yer yoksa en yakın uygun günün saatlerini önerir."""
        o, a = self.o, self.alan
        istenen = o.gun
        assert istenen is not None
        gunler = [g for g in self._ufuk(self.ork.alternatif_gun_sayisi) if g != istenen]
        alt, hata = await self._slotlari_getir([o.poliklinik or ""], gunler)
        if not alt:
            return self._saat_sistem_hatasi() if hata else self._poliklinik_bos(p_ad)
        en_yakin = min({yerel(s.baslangic).date() for s in alt}, key=lambda g: (abs((g - istenen).days), g))
        gunun = [s for s in alt if yerel(s.baslangic).date() == en_yakin]
        uygun = [s for s in gunun if self._aralikta(s, o.saat_bas, o.saat_bit)] or gunun
        o.gun = en_yakin
        return self._sun(uygun, a.GUN_YOK_YAKIN, poliklinik=p_ad, gun=self._gun_ifadesi(istenen))

    async def _secim(self, anlam: Anlam) -> Sonuc:
        """Sıra sayısı ("ilki", "sonuncusu") ya da göreli istek ("daha geç") ile seçim."""
        o, a = self.o, self.alan
        if o.durum == ONAY_BEKLENIYOR:
            o.durum, o.secili = SLOT_SECIMI, None
        liste = list(o.sunulan.values())
        sesli = [o.sunulan[i] for i in o.sesli if i in o.sunulan] or liste
        if not liste:
            return await self._ilerle()
        if anlam.sira is not None and not o.sesli and o.beklenen in ("poliklinik", "gun", "saat"):
            # Asistan henüz poliklinik/gün/saat soruyor: ``sunulan`` yalnız ekran yedeğidir (arayüz bu adımda
            # kartı göstermez, sesli de okunmadı). "İkincisi" duyulmamış/görülmemiş bir saati seçmesin.
            tekrar = {"poliklinik": a.POLIKLINIK_SORU_TEKRAR, "gun": a.GUN_SORU_TEKRAR, "saat": a.SAAT_SORU_TEKRAR}
            return self._mevcut_adim(a.SIRA_YOK.format(soru=tekrar[o.beklenen]))
        if anlam.sira is not None:
            hedef = sesli if (anlam.sira == -1 or anlam.sira <= len(sesli)) else liste
            idx = -1 if anlam.sira == -1 else anlam.sira - 1
            if not -len(hedef) <= idx < len(hedef):
                o.beklenen = "secim"
                return self._mevcut_adim(a.SIRA_YOK.format(soru=a.SECIM_TEKRAR))
            return self._sec(hedef[idx])
        if anlam.goreli and o.poliklinik:
            gunler = [o.gun] if o.gun else self._ufuk()
            slotlar, hata = await self._slotlari_getir([o.poliklinik], gunler)
            if hata and not slotlar:
                return self._saat_sistem_hatasi()
            if anlam.goreli == "daha_gec":
                sinir = max(s.baslangic for s in sesli)
                aday = [s for s in slotlar if s.baslangic > sinir]
                bos_metin = a.DAHA_GEC_YOK
            else:
                sinir = min(s.baslangic for s in sesli)
                aday = sorted((s for s in slotlar if s.baslangic < sinir), key=lambda s: s.baslangic,
                              reverse=True)[:SESLI_SECENEK]
                bos_metin = a.DAHA_ERKEN_YOK
            if not aday:
                o.beklenen = "secim"
                return self._mevcut_adim(bos_metin)
            return self._sun(aday)
        return self._anlasilmadi(anlam)

    # ---- Geçmiş muayene / ilaç -----------------------------------------------------
    async def _gecmisi_getir(self) -> list[Ziyaret] | Sonuc:
        if self.o.gecmis is not None:
            return self.o.gecmis
        try:
            veri = await self.ork.api.gecmis_getir(self.o.oturum_id, limit=self.ork.gecmis_limit,
                                                   istek_kimligi=self.rid, olcum=self.olcum)
            ziyaretler = ziyaretleri_ayristir(veri)
        except ApiHatasi as hata:
            if hata.kod == "VOICE_SESSION_NOT_FOUND" and not hata.sistem_hatasi:
                return self._devret("SESSION_NOT_FOUND", self.alan.OTURUM_GECERSIZ, 404, "VOICE_SESSION_NOT_FOUND")
            if hata.kod == "VOICE_SESSION_NOT_VERIFIED" and not hata.sistem_hatasi:
                # Bu noktaya yalnız doğrulanmış oturumla gelinir; 409 burada süre dolması demektir.
                return self._suresi_doldu()
            log.warning("geçmiş muayene alınamadı: %s", hata)
            return self._mevcut_adim(self.alan.GECMIS_ALINAMADI, 503, "API_UNAVAILABLE")
        except ValueError:
            log.warning("geçmiş muayene yanıtı beklenen biçimde değil")
            return self._mevcut_adim(self.alan.GECMIS_ALINAMADI, 503, "API_UNAVAILABLE")
        self.o.gecmis = ziyaretler
        return ziyaretler

    async def _gecmis_yaniti(self, anlam: Anlam, maskeli: str, ham_metin: str | None = None) -> Sonuc:
        a = self.alan
        if self.ork.gecmis_ikinci_faktor:
            # İkinci faktör açık ama backend'de erişim kodu doğrulama ucu yok: kapalı başarısız olunur.
            return self._mevcut_adim(a.GECMIS_EK_DOGRULAMA)
        if self.o.gecmis_kilitli:
            # Oturum bir kez boşta kaldı (kiosk sıfırlanmamış, başka biri gelmiş olabilir): sağlık verisi
            # verilmez; kiosk yeni oturumla kimlik adımına döner.
            self.o.durum = DEVIR
            self.o.devir_nedeni = "SESSION_IDLE"
            self.o.saglik_verisini_sil()
            self.o.sunulan, self.o.secili, self.o.sesli = {}, None, []
            return self._yanit("HANDOFF", a.GECMIS_YENIDEN_DOGRULAMA, {"reason": "SESSION_IDLE", "restart": True},
                               409, "SESSION_IDLE", "Oturum boşta kaldığı için geçmiş muayene verilmedi.")
        ziyaretler = await self._gecmisi_getir()
        if isinstance(ziyaretler, Sonuc):
            return ziyaretler
        konu = anlam.gecmis_konu or "liste"
        eslesme = None
        bulunamadi = False
        poliklinik_yok = None
        # "ortopedi muayenemde ne yazıldı": yalnız o polikliniğin onaylı kaydı okunur; yoksa açıkça söylenir
        # (başka polikliniğin kaydı "son muayene" diye okunmaz).
        p_kod = anlam.poliklinik or metindeki_poliklinik(normalize(maskeli), a)
        if p_kod and ziyaretler:
            ziyaretler = poliklinik_ziyaretleri(ziyaretler, p_kod, a)
            if not ziyaretler:
                p = a.poliklinik_bul(p_kod)
                poliklinik_yok = p.ad if p else p_kod
        elif p_kod:
            p = a.poliklinik_bul(p_kod)
            poliklinik_yok = p.ad if p else p_kod
        # "Elif Hanım ne yazmıştı": yalnız o hekimin onaylı muayeneleri. Ad eşlemesi ham metinde, yalnız kodda
        # yapılır (LLM'e, loga, yanıta gitmez); ad kelimeleri ilaç adayı sayılmaz.
        hekim_yok = False
        hekim_adlari: set[str] = set()
        if ziyaretler and not poliklinik_yok:
            suzulen, hekim_adlari = hekim_suz(ziyaretler, ham_metin or maskeli)
            if suzulen is not None:
                ziyaretler = suzulen
                hekim_yok = not suzulen
                if hekim_yok:
                    ziyaretler = await self._gecmisi_getir()  # kart tüm kaydı gösterir
                    if isinstance(ziyaretler, Sonuc):
                        return ziyaretler
        if anlam.niyet in ("ilac_sorusu", "gecmis_muayene") and ziyaretler:
            eslesme = ilac_bul(maskeli, ziyaretler)
            if not eslesme and anlam.ilac_adi:
                eslesme = ilac_bul(anlam.ilac_adi, ziyaretler)
            # KVKK: ilaç adını çıkarmak için cümle LLM'e GÖNDERİLMEZ. Cümle kullanıcının kullandığı ilacın
            # adını taşır (sağlık verisi) ve LLM'in döndüreceği ad zaten metinde geçmek zorundadır
            # (_temellendir); ilac_bul metindeki her kelimeyi/kelime çiftini kayıtlarla kodda dener.
            n_soru = normalize(maskeli)
            liste_istegi = any(ifade_gecer_mi(n_soru, i) for i in a.ILAC_LISTESI_IFADELERI)
            aday_metni = self._konu_kelimeleri_cikar(maskeli) if anlam.ilac_konu else maskeli
            if hekim_adlari:
                aday_metni = " ".join(k for k in normalize(aday_metni).split() if k not in hekim_adlari)
            if not eslesme and not liste_istegi and not hekim_yok                     and (anlam.ilac_adi or ilac_adayi_var_mi(aday_metni, a)):
                # Kullanıcı bir ilaç adı söyledi ("aspirin ne için yazılmıştı") ama onaylı kayıtlarda yok:
                # başka ilacın bilgisi okunmaz, kayıt listesi de okunmaz.
                bulunamadi = True
            if eslesme:
                konu = "ilac"
        if anlam.niyet == "ilac_sorusu" and ziyaretler and not poliklinik_yok and not bulunamadi and not hekim_yok:
            ilac_konu = anlam.ilac_konu
            if ilac_konu is None and eslesme and self._ek_soru_var(maskeli, eslesme):
                # Kural konuyu çıkaramadı ("Metforminle araba kullanabilir miyim"): yanıt "elimde onaylı bilgi
                # yok". ILAC_KONU_LLM açıksa önce LLM'e YALNIZ maskeli cümleden konu etiketi sorulur (varsayılan
                # kapalı: yerel modelin etiketi sık yanlış, yanlış konunun bilgisi okunurdu).
                llm_konu = await self._llm_ilac_konusu(maskeli) if self.ork.ilac_konu_llm else None
                ilac_konu = llm_konu or "tanimsiz"
            if ilac_konu is not None:
                if eslesme:
                    sonuc = await self._kayitli_ilac_bilgisi(ilac_konu, eslesme, ziyaretler, maskeli)
                    if sonuc is not None:
                        return sonuc
                sonuc = self._ilac_konu_yaniti(ilac_konu, eslesme or [], ziyaretler, maskeli)
                if sonuc is not None:
                    return sonuc
        if poliklinik_yok:
            metin = a.GECMIS_POLIKLINIK_YOK.format(poliklinik=poliklinik_yok)
        elif hekim_yok and not eslesme:
            metin = a.GECMIS_HEKIM_YOK
        elif bulunamadi:
            metin = a.ILAC_BULUNAMADI
        else:
            metin = gecmis_metni(a, ziyaretler, konu, eslesme, poliklinik_suzuldu=bool(p_kod))
            # Takip sorusu ("peki yan etkisi?") için son konuşulan ilaç: adıyla sorulan ilaç ya da tek ilaçlı
            # son muayene.
            if eslesme:
                self._son_ilaci_kaydet(*eslesme[0])
            elif konu == "liste" and ziyaretler and len(ziyaretler[0].ilaclar) == 1:
                self._son_ilaci_kaydet(ziyaretler[0], ziyaretler[0].ilaclar[0])
        sesli = self.ork.gecmis_sesli
        if ziyaretler and not sesli and not bulunamadi:
            metin = a.GECMIS_EKRANDA
        self._ek_veri["history"] = {
            "visits": [z.disa() for z in ziyaretler],
            "focus": [i.ad for _, i in (eslesme or [])],
            "spoken": bool(sesli),
        }
        return self._mevcut_adim(metin)

    # ---- İlaç soru konuları (yan etki, alerji, aç/tok, alkol…) ----------------------------
    def _son_ilaci_kaydet(self, z: Ziyaret, i: Ilac) -> None:
        for zi, kayitli in enumerate(self.o.gecmis or []):
            if kayitli is z:
                for ii, x in enumerate(kayitli.ilaclar):
                    if x is i:
                        self.o.son_ilac = (zi, ii)
                        return

    def _son_ilac(self, ziyaretler: list[Ziyaret]) -> tuple[Ziyaret, Ilac] | None:
        """Son konuşulan ilaç (yalnız bu yanıtta geçerli ziyaretlerden biriyse)."""
        if self.o.son_ilac is None or not self.o.gecmis:
            return None
        zi, ii = self.o.son_ilac
        if zi >= len(self.o.gecmis) or ii >= len(self.o.gecmis[zi].ilaclar):
            return None
        z = self.o.gecmis[zi]
        if not any(z is x for x in ziyaretler):
            return None
        return z, z.ilaclar[ii]

    def _konu_kelimeleri_cikar(self, maskeli: str) -> str:
        """Konu sorusundaki konu kelimeleri ("bırakabilir", "alerjimin") ilaç adı adayı sayılmasın diye atılır."""
        kokler = {k for _, ifadeler, _ in self.alan.ILAC_KONU_KALIPLARI for i in ifadeler
                  for k in normalize(i).split() if len(k) >= 4}
        return " ".join(k for k in normalize(maskeli).split() if not k.startswith(tuple(kokler)))

    def _ek_soru_var(self, maskeli: str, eslesme: list[tuple[Ziyaret, Ilac]]) -> bool:
        """Cümlede ilaç adı ve soru kalıpları dışında anlam taşıyan kelime var mı ("araba", "güneş")?
        Yoksa ("Amlodipin", "amlodipin hakkında") hekim onaylı özet okunur."""
        adlar = [i.ad for _, i in eslesme]
        kalan = [k for k in normalize(maskeli).split()
                 if not any(_ad_eslesir_mi(k, ad) for ad in adlar) and not k.startswith("hakkind")]
        return ilac_adayi_var_mi(" ".join(kalan), self.alan)

    async def _llm_ilac_konusu(self, maskeli: str) -> str | None:
        """Kuralın çözemediği ilaç sorusunun konusu: LLM'e yalnız ilaç adları ``[ilaç]`` yapılmış cümle gider;
        maskeden sonra hâlâ sağlık ifadesi taşıyan cümle hiç gönderilmez."""
        a = self.alan
        adlar = tuple(i.ad for z in (self.o.gecmis or []) for i in z.ilaclar)
        metin = ilac_adlarini_maskele(maskeli, adlar)
        metin = re.sub(r"(?i)(?<![\w\[])(ilaç|ilac|hap|tablet|şurup|kapsül)\w*", ILAC_MASKESI, metin)
        kontrol = normalize(metin.replace(ILAC_MASKESI, " "))
        if any(ifade_gecer_mi(kontrol, i) for i in getattr(a, "SAGLIK_VERISI_IFADELERI", ())):
            return None
        return await ilac_konusu_sor(metin, alan=a, llm=self.ork.llm, bugun=self.bugun,
                                     simdi_metni=f"{yerel(self.simdi):%H:%M}",
                                     zaman_asimi_sn=self.ork.anlama_zaman_asimi_sn)

    def _ilac_konu_yaniti(self, ilac_konu: str, eslesme: list[tuple[Ziyaret, Ilac]], ziyaretler: list[Ziyaret],
                          maskeli: str) -> Sonuc | None:
        """Konu sorusunun yanıtı (şablon + hekim onaylı özet + dağarcık). ``None``: mevcut geçmiş yanıtına
        bırakılır (ör. ilaç adı söylenmeden "ne işe yarar" → son muayenenin özeti)."""
        a = self.alan
        secilen = list(eslesme)
        if not secilen:
            son = self._son_ilac(ziyaretler)
            if son is not None:
                secilen = [son]
        if not secilen:
            # Hangi ilaç? "son muayenemde…" → en yeni ziyaret; değilse (gerekirse poliklinikle süzülmüş) tüm kayıt.
            n = normalize(maskeli)
            adaylar_z = ziyaretler[:1] if any(ifade_gecer_mi(n, i) for i in ("son muayene", "en son")) else ziyaretler
            adaylar: list[tuple[Ziyaret, Ilac]] = []
            for z in adaylar_z:
                for i in z.ilaclar:
                    if all(normalize(i.ad) != normalize(x.ad) for _, x in adaylar):
                        adaylar.append((z, i))
            if len(adaylar) == 1:
                secilen = adaylar
            elif ilac_konu in OZET_KONULARI or ilac_konu == "tanimsiz":
                return None
            elif not adaylar:
                return self._ilac_konu_sonucu(a.ILAC_KAYITTA_YOK, ziyaretler, [], ilac_konu)
            else:
                self.o.bekleyen_konu = ilac_konu
                adlar = [i.ad for _, i in adaylar]
                liste = adlar[0] if len(adlar) == 1 else ", ".join(adlar[:-1]) + " ve " + adlar[-1]
                metin = a.ILAC_HANGISI.format(ilaclar=liste) if self.ork.gecmis_sesli else a.ILAC_HANGISI_EKRANDA
                return self._ilac_konu_sonucu(metin, ziyaretler, [], ilac_konu, ekran_metni=True)
        if ilac_konu == "tanimsiz":
            metin = a.ILAC_KONU_TANIMSIZ.format(ad=secilen[0][1].ad)
        else:
            metin = konu_metni(a, secilen, ilac_konu)
        self._son_ilaci_kaydet(*secilen[0])
        return self._ilac_konu_sonucu(metin, ziyaretler, secilen, ilac_konu)

    def _ilac_konu_sonucu(self, metin: str, ziyaretler: list[Ziyaret], secilen: list[tuple[Ziyaret, Ilac]],
                          ilac_konu: str, ekran_metni: bool = False) -> Sonuc:
        sesli = self.ork.gecmis_sesli
        if not sesli and not ekran_metni and secilen:
            metin = self.alan.GECMIS_EKRANDA
        self._ek_veri["history"] = {
            "visits": [z.disa() for z in ziyaretler],
            "focus": [i.ad for _, i in secilen],
            "spoken": bool(sesli),
            "topic": veri_konusu(ilac_konu, self.alan),
        }
        return self._mevcut_adim(metin)

    # ---- Slot arama ----------------------------------------------------------------
    def _gunler(self, gun: date | None) -> list[date]:
        if gun is not None:
            return [gun]
        return self._ufuk()

    async def _slotlari_getir(self, poliklinikler: list[str], gunler: list[date],
                              haric: set[str] = frozenset()) -> tuple[list[SlotBilgisi], bool]:
        """Yan etkisiz müsaitlik sorgusu. Dönüş: (gelecekteki slotlar, sistem hatası oldu mu)."""
        isler = [(p, g) for p in poliklinikler for g in gunler]
        sonuclar = await asyncio.gather(
            *(self.ork.api.slotlar(p, g.isoformat(), istek_kimligi=self.rid, olcum=self.olcum) for p, g in isler),
            return_exceptions=True,
        )
        slotlar: list[SlotBilgisi] = []
        sistem_hatasi = False
        sinir = self.simdi + self.ork.slot_min_once
        o = self.o
        # Hekim tercihi yalnız seçili poliklinikte ve o poliklinik için verildiyse uygulanır.
        tercih = o.hekim[0] if (o.hekim is not None and o.poliklinik and o.hekim_poliklinik == o.poliklinik) else None
        for (p, _), sonuc in zip(isler, sonuclar, strict=True):
            if isinstance(sonuc, ApiHatasi):
                # /slots için "boş saat yok" YALNIZ 200 + boş liste demektir (backend SlotService:
                # bilinmeyen poliklinik → 404 NOT_FOUND, boş clinic → 400 VALIDATION_FAILED). Kodlar
                # sabit listeden gittiğinden her hata yapılandırma/sistem hatasıdır; "saat yok" denmez.
                sistem_hatasi = True
                log.warning("slot sorgusu başarısız: %s", sonuc)
                continue
            if isinstance(sonuc, BaseException):
                sistem_hatasi = True
                log.error("slot sorgusunda beklenmeyen hata: %s", type(sonuc).__name__)
                continue
            for ham in sonuc:
                try:
                    bas = utc_coz(str(ham["startTime"]))
                except (ValueError, KeyError):
                    continue
                if bas <= sinir or str(ham["slotId"]) in haric or self._cakisan_mi(p, bas):
                    continue
                slot = SlotBilgisi(str(ham["slotId"]), bas, p, str(ham.get("doctorDisplayName") or ""),
                                   str(ham.get("doctorId") or ""))
                if tercih is not None and p == o.poliklinik and slot.hekim_anahtari != tercih:
                    continue
                slotlar.append(slot)
        slotlar.sort(key=lambda s: (s.baslangic, s.poliklinik))
        return slotlar, sistem_hatasi

    def _cakisan_mi(self, poliklinik: str, bas: datetime) -> bool:
        """Backend'in bu oturumda bildirdiği çakışmalara göre baştan reddedilecek slot mu?"""
        if (poliklinik, yerel(bas).date()) in self.o.haric_gunler:
            return True
        return any(abs(bas - h) < CAKISMA_PENCERESI for h in self.o.haric_saatler)

    async def _ilk_oneriler(self) -> tuple[list[SlotBilgisi], bool]:
        """Poliklinik seçilmeden önce: her poliklinikten en yakın iki saat (ekran yedeği)."""
        kodlar = list(self.alan.POLIKLINIK_KODLARI)
        slotlar, hata = await self._slotlari_getir(kodlar, self._gunler(None))
        secilen: list[SlotBilgisi] = []
        for kod in kodlar:
            secilen += [s for s in slotlar if s.poliklinik == kod][:2]
        secilen.sort(key=lambda s: (s.baslangic, s.poliklinik))
        return secilen[: max(self.ork.sunulan_slot_sayisi, len(kodlar))], hata

    def _aralik_metni(self, gun: date | None, bas: time | None, bit: time | None) -> str:
        parca = goreli_gun_metni(gun, self.bugun) if gun else "önümüzdeki günlerde"
        if bas and bit and bas != bit:
            parca += f" {bas:%H:%M} ile {bit:%H:%M} arasında"
        elif bas:
            parca += f" saat {bas:%H:%M} civarında"
        return parca

    async def _yeniden_ara(self, haric: set[str] = frozenset(), baska: bool = False) -> Sonuc:
        """Mevcut tercihlerle listeyi tazeler (saat alındı/geçti/boşta kaldı sonrası)."""
        a = self.alan
        poliklinikler = [self.o.poliklinik] if self.o.poliklinik else list(a.POLIKLINIK_KODLARI)
        slotlar, sistem_hatasi = await self._slotlari_getir(poliklinikler, self._gunler(self.o.gun), haric)
        self.o.secili = None
        self.o.durum = SLOT_SECIMI
        self.o.son_sistem_hatasi = sistem_hatasi
        bas, bit = self.o.saat_bas, self.o.saat_bit
        uygun = [s for s in slotlar if self._aralikta(s, bas, bit)] or (slotlar if bas is not None else [])
        uygun = uygun[: self.ork.sunulan_slot_sayisi]
        p = a.poliklinik_bul(self.o.poliklinik)
        p_ad = p.ad if p else "Tüm poliklinikler"
        aralik = self._aralik_metni(self.o.gun, bas, bit)
        if not uygun:
            if sistem_hatasi:
                return self._saat_sistem_hatasi()
            if baska and self.o.sunulan:
                self.o.beklenen = "secim"
                return self._yanit("OFFER_SLOTS", f"{p_ad} için {aralik} başka boş saat görünmüyor. "
                                   "Başka bir gün ya da poliklinik söyleyebilirsiniz.", self._slot_verisi())
            self.o.sunulan, self.o.sesli = {}, []
            return self._yanit("OFFER_SLOTS", a.SAAT_BULUNAMADI.format(poliklinik=p_ad, aralik=aralik),
                               self._slot_verisi())
        return self._sun(uygun)

    async def _baska_saat(self) -> Sonuc:
        if not self.o.poliklinik:
            if not self.o.sunulan:
                return await self._dogrulanmis_basla()
            return await self._ilerle()
        return await self._yeniden_ara(haric=set(self.o.sunulan), baska=True)

    # ---- Randevu ------------------------------------------------------------------------
    async def _secimi_birak_ve_yenile(self, slot: SlotBilgisi, metin: str, durum: int, kod: str,
                                      mesaj: str = "") -> Sonuc:
        """Seçimi kaldırır, güncel saatleri getirir ve slot seçimine döner (randevu yok)."""
        self.o.sunulan.pop(slot.slot_id, None)
        yeni = await self._yeniden_ara(haric={slot.slot_id})
        if yeni.durum_kodu == 503:
            return yeni
        if yeni.govde["action"] == "CONFIRM":  # tek eşleşme otomatik onaya geçtiyse listeye dön
            self.o.durum, self.o.secili = SLOT_SECIMI, None
        self.o.beklenen = "secim"
        return self._yanit("OFFER_SLOTS", metin, self._slot_verisi(), durum, kod, mesaj or kod)

    async def _randevu_al(self) -> Sonuc:
        a = self.alan
        slot = self.o.sunulan[self.o.secili or ""]
        if self.bosta_kaldi:
            # Uzun süre işlem yoksa ekrandaki kişi değişmiş olabilir: onay reddedilir (Y2).
            self.o.secili, self.o.durum = None, SLOT_SECIMI
            yeni = await self._yeniden_ara()
            if yeni.durum_kodu == 503:
                return yeni
            if yeni.govde["action"] == "CONFIRM":
                self.o.durum, self.o.secili = SLOT_SECIMI, None
            self.o.beklenen = "secim"
            return self._yanit("OFFER_SLOTS", a.BOSTA_KALDI, self._slot_verisi(), 409, "SESSION_IDLE",
                               "Oturum boşta kalma süresini aştı; onay reddedildi.")
        if slot.baslangic <= self.simdi + self.ork.slot_min_once:
            # Liste sunulduktan sonra saat geçti ya da çok yaklaştı (O3).
            return await self._secimi_birak_ve_yenile(slot, a.SLOT_GECTI, 409, "SLOT_EXPIRED")
        try:
            sonuc = await self.ork.api.randevu_olustur(self.o.oturum_id, slot.slot_id, istek_kimligi=self.rid,
                                                       olcum=self.olcum)
        except ApiHatasi as hata:
            return await self._randevu_hatasi(hata, slot)
        p = a.poliklinik_bul(slot.poliklinik)
        p_ad = p.ad if p else str(sonuc.get("clinicName") or "")
        try:
            zaman = utc_coz(str(sonuc.get("scheduledAt") or "")) if sonuc.get("scheduledAt") else slot.baslangic
        except ValueError:
            zaman = slot.baslangic
        hekim = str(sonuc.get("doctorDisplayName") or slot.hekim or "")
        self.o.randevu = {
            "appointmentId": sonuc.get("appointmentId"),
            "accessCode": str(sonuc.get("accessCode")),
            "slotId": slot.slot_id,
            "scheduledAt": sonuc.get("scheduledAt"),  # API'nin UTC değeri olduğu gibi
            "label": slot_etiketi(zaman),
            "longLabel": slot_uzun_etiket(zaman),
            "clinicName": p_ad,
            "doctorDisplayName": hekim,
            "status": sonuc.get("status"),
        }
        self.o.durum = TAMAMLANDI
        self.o.son_sistem_hatasi = False
        metin = a.RANDEVU_OLUSTU.format(etiket=slot_uzun_etiket(zaman), poliklinik=p_ad,
                                        hekim=(hekim or "hekiminiz").rstrip("."), kod=self.o.randevu["accessCode"])
        return self._yanit("DONE", metin, self._randevu_verisi())

    async def _slot_artik_yok(self, slot: SlotBilgisi) -> bool:
        """Slot API'nin güncel boş listesinde yok mu? Sorgu başarısızsa False (bilinmiyor)."""
        try:
            liste = await self.ork.api.slotlar(slot.poliklinik, yerel(slot.baslangic).date().isoformat(),
                                               istek_kimligi=self.rid, olcum=self.olcum)
        except ApiHatasi:
            return False
        return all(str(s.get("slotId")) != slot.slot_id for s in liste)

    def _cakisma_turu(self, hata: ApiHatasi) -> str | None:
        """Backend aynı kodu iki durum için döner; mesaj yalnız ipucudur (tutmazsa None → genel şablon)."""
        n = normalize(hata.mesaj or "")
        return next((tur for ipucu, tur in self.alan.CAKISMA_IPUCLARI if ipucu in n), None)

    async def _randevu_cakismasi(self, slot: SlotBilgisi, hata: ApiHatasi) -> Sonuc:
        """409 APPOINTMENT_CONFLICT: randevu oluşmadı. Seçim kaldırılır (onay döngüsü yok), çakışan gün/saat
        bu oturumda yeniden önerilmez ve gün ya da saat yeniden sorulur."""
        o, a = self.o, self.alan
        tur = self._cakisma_turu(hata)
        gun = yerel(slot.baslangic).date()
        p = a.poliklinik_bul(slot.poliklinik)
        p_ad = p.ad if p else slot.poliklinik
        o.durum, o.secili, o.onay_soruldu = SLOT_SECIMI, None, False
        o.poliklinik = slot.poliklinik
        o.saat_bas = o.saat_bit = None
        o.saat_serbest = False
        if tur == "saat":
            o.haric_saatler.append(slot.baslangic)
            o.gun, o.gun_serbest = gun, False
            slotlar, sistem = await self._slotlari_getir([slot.poliklinik], [gun])
            o.beklenen = "saat"
            metin = a.RANDEVU_CAKISMA_SAAT + " " + a.SAAT_SORU.format(gun=self._gun_basligi(gun))
        else:
            if tur == "gun":
                o.haric_gunler.add((slot.poliklinik, gun))
                ifade = self._gun_ifadesi(gun)
                metin = a.RANDEVU_CAKISMA_GUN.format(gun=ifade[:1].upper() + ifade[1:], poliklinik=p_ad)
            else:
                # Tür bilinmiyor: en temkinli dışlama (hem o saat hem o gün bu poliklinik).
                o.haric_saatler.append(slot.baslangic)
                o.haric_gunler.add((slot.poliklinik, gun))
                metin = a.RANDEVU_CAKISMA
            o.gun, o.gun_serbest = None, False
            slotlar, sistem = await self._slotlari_getir([slot.poliklinik], self._ufuk())
            o.beklenen = "gun"
            # GUN_SORU örnek olarak "yarın" verir; çakışan gün yarınsa çelişirdi.
            metin += " " + a.CAKISMA_GUN_SORU
        self._ekrana_koy(slotlar)
        o.son_sistem_hatasi = sistem and not slotlar
        return self._yanit("OFFER_SLOTS", metin, self._slot_verisi(), 409, "APPOINTMENT_CONFLICT",
                           "Hastanın başka bir randevusuyla çakışıyor; randevu oluşturulmadı.")

    async def _randevu_hatasi(self, hata: ApiHatasi, slot: SlotBilgisi) -> Sonuc:
        a = self.alan
        if hata.kod in ("SLOT_TAKEN", "NOT_FOUND") and not hata.sistem_hatasi:
            # Slot gitti: listeden çıkar, güncel saatleri getir, slot seçimine dön.
            durum = 409 if hata.kod == "SLOT_TAKEN" else 404
            return await self._secimi_birak_ve_yenile(slot, a.SLOT_ALINDI_YENI, durum, hata.kod, hata.mesaj)
        if hata.kod == "SLOT_IN_PAST" and not hata.sistem_hatasi:
            # Backend'e göre saat geçti (saat kayması): kendi SLOT_EXPIRED davranışımızla aynı.
            return await self._secimi_birak_ve_yenile(slot, a.SLOT_GECTI, 409, "SLOT_EXPIRED")
        if hata.kod == "APPOINTMENT_CONFLICT" and not hata.sistem_hatasi:
            return await self._randevu_cakismasi(slot, hata)
        if hata.kod == "VOICE_SESSION_NOT_VERIFIED" and not hata.sistem_hatasi:
            return self._suresi_doldu()
        if hata.kod in ("VOICE_SESSION_NOT_FOUND", "VOICE_SESSION_EXHAUSTED") and not hata.sistem_hatasi:
            durum = 404 if hata.kod == "VOICE_SESSION_NOT_FOUND" else 409
            return self._devret("SESSION_NOT_VERIFIED", a.OTURUM_GECERSIZ, durum, hata.kod)
        if not hata.sistem_hatasi:
            # İş kuralı reddi ama özel dalı olmayan kod (INVALID_STATUS_TRANSITION, backend'e sonradan eklenen
            # bir 409...): randevu oluşmadı ve aynı onay yeniden denenirse aynı ret gelir. "Sisteme
            # ulaşamıyorum" + yapışkan onay döngüsü yerine önce oturumun gerçek durumu okunur, sonra seçim
            # bırakılıp güncel saatler sunulur (kök neden: 2026-11-16 API_UNAVAILABLE döngüsü).
            log.warning("randevu iş kuralıyla reddedildi (özel şablon yok): %s", hata)
            api = await self._api_oturumu()
            if isinstance(api, dict):
                yonlendirme = self._durum_yonlendir(str(api.get("status")))
                if yonlendirme is not None:
                    return yonlendirme
            return await self._secimi_birak_ve_yenile(slot, a.RANDEVU_OLUSTURULAMADI, 409, hata.kod, hata.mesaj)
        # Sistem hatası. Zaman aşımı ya da 5xx'te randevu oluşmuş olabilir: oturumdan kontrol et.
        if hata.zaman_asimi or hata.durum >= 500 or hata.kod == "INVALID_BODY":
            api = await self._api_oturumu()
            if isinstance(api, dict) and api.get("status") == "COMPLETED":
                return self._devret("BOOKING_UNCERTAIN", a.SONUC_BELIRSIZ, 503, "API_UNAVAILABLE")
            if isinstance(api, dict) and hata.durum >= 500 and await self._slot_artik_yok(slot):
                # Gerçek backend eşzamanlı çakışmada (Appointments.SlotId benzersiz indeksi)
                # SLOT_TAKEN değil 500 INTERNAL_ERROR döner; slot listeden düştüyse bu odur (D1).
                return await self._secimi_birak_ve_yenile(slot, a.SLOT_ALINDI_YENI, 409, "SLOT_TAKEN",
                                                          "Slot eşzamanlı olarak başka hastaya verildi.")
        log.warning("randevu oluşturulamadı (sistem hatası): %s", hata)
        self.o.son_sistem_hatasi = True
        # Yanıt doğrudan bu seçimin onayına verildi: aynı seçim için "onaylıyorum" yeniden denemedir.
        self.o.onay_soruldu = True
        return self._yanit("CONFIRM", a.SISTEM_HATASI, self._onay_verisi(), 503, "API_UNAVAILABLE",
                           "Randevu API'si yanıt vermedi.")


def _tekil_saatler(slotlar: list[SlotBilgisi]) -> list[SlotBilgisi]:
    """Zamana göre sıralı; aynı poliklinikte aynı başlangıçlı ikinci slot (başka hekim) atılır. Hekim tercihi
    yokken çok hekimli poliklinikte "09:00, 09:00 ve 09:15" okunmasın; seçilen slotun hekimi onayda söylenir."""
    goruldu: set[tuple[datetime, str]] = set()
    sonuc: list[SlotBilgisi] = []
    for s in sorted(slotlar, key=lambda s: (s.baslangic, s.poliklinik)):
        anahtar = (s.baslangic, s.poliklinik)
        if anahtar not in goruldu:
            goruldu.add(anahtar)
            sonuc.append(s)
    return sonuc


__all__ = ["OLAYLAR", "Orkestrator", "Oturum", "OturumDeposu", "Sonuc", "yanit_govdesi"]
