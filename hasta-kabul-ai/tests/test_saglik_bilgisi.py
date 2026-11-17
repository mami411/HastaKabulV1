"""Genel sağlık / ilaç bilgi yolu (``saglik_bilgisi``, ``SAGLIK_BILGI_LLM``).

- Yol seçimi: bilgi sorusu → yerel LLM; kişiye özel (doz, bırakma, tahlil, teşhis, iki ilaç, kişisel durum) →
  sabit yönlendirme; acil → 112 (her şeyden önce); şikâyet/randevu/kayıt/sağlık dışı → mevcut akış.
- Çıktı denetimi: doz/sayı, teşhis, kişisel karar, dil, liste, uzunluk; ret → dürüst sabit metin.
- LLM hata/zaman aşımı → sabit metin, çökme yok. Kapanış her zaman koddan.
- KVKK: LLM'e yalnız kimliği maskelenmiş soru gider; TC, ad, kayıt (muayene, reçete, talimat, randevu) gitmez.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from ortak.llm import LLMBaglantiHatasi, LLMZamanAsimi, SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.ilac_bilgisi import dagarcik_kaydi
from ses_servisi.llm_siniri import saglik_bilgi_yuku_kur
from ses_servisi.saglik_bilgisi import saglik_karari, spesifik_metni, yanit_birlestir, yanit_denetle

BELIRSIZ = json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
                       "saat_bitis": None})
IYI_YANIT = ("Aspirin ağrı, ateş ve iltihabı azaltmak için kullanılan bir ilaçtır. Bazı kişilerde kalp damar "
             "hastalıklarında pıhtı oluşumunu azaltmak amacıyla da kullanılır.")


def saglik_cagrisi_mi(mesajlar) -> bool:
    return mesajlar[0].icerik == alan.SAGLIK_BILGI_ISTEMI


def akilli_llm(saglik_yaniti=IYI_YANIT, niyet=BELIRSIZ) -> SahteSaglayici:
    """Sağlık bilgi istemine ``saglik_yaniti``, anlama istemine ``niyet`` döner."""
    def cevap(mesajlar):
        if saglik_cagrisi_mi(mesajlar):
            return saglik_yaniti(mesajlar) if callable(saglik_yaniti) else saglik_yaniti
        return niyet
    return SahteSaglayici(varsayilan=cevap)


def saglik_yukleri(llm: SahteSaglayici) -> list[str]:
    return ["\n".join(m.icerik for m in c["mesajlar"][1:]) for c in llm.cagrilar if saglik_cagrisi_mi(c["mesajlar"])]


def hazirla(kur, llm=None, **ayar):
    k = kur(llm if llm is not None else akilli_llm(), **ayar)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    return k, oid


# ------------------------------------------------------------------ yol seçimi (kural)

@pytest.mark.parametrize("cumle", [
    "aspirin ne işe yarar", "baş ağrısına ne iyi gelir", "tansiyon neden yükselir", "Atorvastatin ne için kullanılır",
    "grip ile soğuk algınlığının farkı ne", "Kafein zararlı mı", "bas agrisina ne iyi gelir",
    "Kolesterolüm yüksek nelere dikkat etmeliyim", "Parol ile alkol alınır mı", "asprin ne işe yarar",
    "Losartan ne işe yarar", "Zatürre bulaşıcı mı",
])
def test_bilgi_sorulari_bilgi_yoluna_gider(cumle):
    karar = saglik_karari(cumle, alan)
    assert karar is not None and karar.tur == "bilgi", cumle


@pytest.mark.parametrize("cumle,neden", [
    ("aspirin dozunu artırabilir miyim", "doz"),
    ("parolü kaç tane içmeliyim", "doz"),
    ("ramipril bırakabilir miyim", "birakma"),
    ("aspirin ile klopidogrel birlikte alınır mı", "iki_ilac"),
    ("kolesterol tahlilim 240 çıktı ne demek", "tahlil"),
    ("Metformin kullanıyorum alkol içebilir miyim", "kisisel_ilac"),
    ("hamileyim parol kullanabilir miyim", "kisisel_ilac"),
])
def test_kisiye_ozel_sorular_sabit_yonlendirme(cumle, neden):
    karar = saglik_karari(cumle, alan)
    assert karar is not None and karar.tur == "spesifik" and karar.neden == neden, (cumle, karar)


@pytest.mark.parametrize("cumle", [
    "başım ağrıyor", "kalbim çarpıyor", "kardiyolojiden randevu istiyorum", "yarın sabah olur",
    "ilaçlarım neler", "son muayenemde ne yazıldı", "hava nasıl olacak", "teşekkürler",
    "yan etkisi var mı", "peki alkol?", "ilacın yan etkisi var mı", "Lustral içiyorum bir şey sorabilir miyim",
    "katarakt olabilir mi diye baktırmak istiyorum", "tansiyon için hangi bölüme gitmeliyim",
])
def test_yol_disi_cumleler_mevcut_akista_kalir(cumle):
    assert saglik_karari(cumle, alan) is None, cumle


# ------------------------------------------------------------------ orkestratör

def test_bilgi_sorusu_llm_ile_yanitlanir_ve_kapanis_koddan(kur):
    k, oid = hazirla(kur)
    r = k.orkestre(oid, userText="aspirin ne işe yarar").json()
    assert r["replyText"] == f"{IYI_YANIT} {alan.SAGLIK_BILGI_KAPANIS}"
    assert r["data"]["aiGeneralInfo"] is True
    assert r["action"] == "OFFER_SLOTS"  # adım değişmedi


def test_modelin_kendi_kapanisi_atilir_sabit_kapanis_bir_kez(kur):
    llm = akilli_llm(IYI_YANIT + " Kendi durumunuz için doktorunuza danışın.")
    k, oid = hazirla(kur, llm)
    r = k.orkestre(oid, userText="aspirin ne işe yarar").json()
    assert r["replyText"].count("danışın") == 1 and r["replyText"].endswith(alan.SAGLIK_BILGI_KAPANIS)


def test_dagarcikta_hazir_bilgi_varsa_llme_gidilmez(kur):
    llm = akilli_llm()
    k, oid = hazirla(kur, llm)
    r = k.orkestre(oid, userText="ibuprofen aç karnına içilir mi").json()
    assert dagarcik_kaydi("İbuprofen").konular["yemek"] in r["replyText"]
    assert r["replyText"].endswith(alan.SAGLIK_BILGI_KAPANIS) and "aiGeneralInfo" not in r["data"]
    assert saglik_yukleri(llm) == []


def test_spesifik_soru_sabit_metin_llm_yok(kur):
    llm = akilli_llm()
    k, oid = hazirla(kur, llm)
    r = k.orkestre(oid, userText="aspirin dozunu artırabilir miyim").json()
    assert r["replyText"] == spesifik_metni(alan, "doz") and "aiGeneralInfo" not in r["data"]
    assert saglik_yukleri(llm) == []


@pytest.mark.parametrize("cumle", ["göğsüm çok ağrıyor aspirin içeyim mi", "nefes alamıyorum ne iyi gelir",
                                   "annem bütün ilaçları içti", "aspirin içtim yüzüm şişti"])
def test_acil_her_seyden_once(kur, cumle):
    llm = akilli_llm()
    k, oid = hazirla(kur, llm)
    r = k.orkestre(oid, userText=cumle).json()
    assert r["replyText"] == alan.ACIL_YANIT and r["data"]["emergency"] is True
    assert llm.cagrilar == []


@pytest.mark.parametrize("cumle", ["başım ağrıyor", "kalbim çarpıyor", "gözlerim bulanık görüyor"])
def test_sikayet_yaniti_yol_kapaliykenle_ayni(kur, cumle):
    llm = akilli_llm()
    k, oid = hazirla(kur, llm)
    acik = k.orkestre(oid, userText=cumle).json()
    k2, oid2 = hazirla(kur, SAGLIK_BILGI_LLM="0")
    kapali = k2.orkestre(oid2, userText=cumle).json()
    assert acik["replyText"] == kapali["replyText"] and acik["data"].get("suggestion") == kapali["data"].get("suggestion")
    assert "aiGeneralInfo" not in acik["data"] and saglik_yukleri(llm) == []


GENEL_YANIT = ("Baş ağrısı çoğu zaman yorgunluk, uykusuzluk ya da sıvı eksikliğiyle ilişkilidir. Dinlenmek ve "
               "yeterince su içmek bazı kişilerde rahatlama sağlayabilir.")


def test_saat_beklerken_bilgi_sorusu_adimi_bozmaz(kur):
    k, oid = hazirla(kur, akilli_llm(GENEL_YANIT))
    once = k.orkestre(oid, userText="kardiyoloji yarın").json()
    r = k.orkestre(oid, userText="baş ağrısına ne iyi gelir").json()
    assert r["data"]["aiGeneralInfo"] is True and r["action"] == once["action"]
    assert r["data"]["expect"] == once["data"]["expect"]


def test_onay_beklerken_bilgi_sorusu_randevu_almaz_hatirlatir(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    assert k.orkestre(oid, userText="ilki").json()["action"] == "CONFIRM"
    r = k.orkestre(oid, userText="aspirin ne işe yarar").json()
    assert r["action"] == "CONFIRM" and r["replyText"].endswith(alan.SAGLIK_ONAY_HATIRLAT)
    assert not [c for c in k.api.cagrilar if c[0] == "POST" and c[1].endswith("/appointments")]
    # Bilgi yanıtından sonra "evet" doğrudan randevu almaz: onay sorusu yeniden okunur.
    r = k.orkestre(oid, userText="evet").json()
    assert r["action"] == "CONFIRM"


def test_randevu_sonrasi_da_bilgi_sorusu(kur):
    k, oid = hazirla(kur)
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    k.orkestre(oid, userText="ilki")
    assert k.orkestre(oid, userText="evet").json()["action"] == "DONE"
    r = k.orkestre(oid, userText="aspirin ne işe yarar").json()
    assert r["action"] == "DONE" and r["data"]["aiGeneralInfo"] is True


def test_saglik_disi_soru_davranisi_degismez(kur):
    llm = akilli_llm()
    k, oid = hazirla(kur, llm)
    r = k.orkestre(oid, userText="hava nasıl olacak").json()
    assert "aiGeneralInfo" not in r["data"] and saglik_yukleri(llm) == []


def test_ayar_kapaliyken_eski_davranis(kur):
    llm = akilli_llm()
    k, oid = hazirla(kur, llm, SAGLIK_BILGI_LLM="0")
    r = k.orkestre(oid, userText="baş ağrısına ne iyi gelir").json()
    assert "aiGeneralInfo" not in r["data"] and saglik_yukleri(llm) == []


def test_llm_yokken_eski_davranis(kur):
    k = kur(None)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText="baş ağrısına ne iyi gelir").json()
    assert r.get("replyText") and "aiGeneralInfo" not in r["data"]


# ------------------------------------------------------------------ hata / zaman aşımı / denetim

@pytest.mark.parametrize("hata", [LLMZamanAsimi("zaman"), LLMBaglantiHatasi("yok"), RuntimeError("beklenmeyen")])
def test_llm_hatasinda_durust_sabit_metin(kur, hata):
    def at(_m):
        raise hata
    k, oid = hazirla(kur, akilli_llm(at))
    r = k.orkestre(oid, userText="aspirin ne işe yarar")
    assert r.status_code == 200
    assert r.json()["replyText"] == alan.SAGLIK_BILGI_VEREMIYORUM and "aiGeneralInfo" not in r.json()["data"]


class YavasSaglayici(SahteSaglayici):
    async def _sohbet(self, mesajlar, **kw):
        if saglik_cagrisi_mi(mesajlar):
            await asyncio.sleep(5)
        return await super()._sohbet(mesajlar, **kw)


def test_zaman_asiminda_sessiz_kalmaz(kur):
    llm = YavasSaglayici(varsayilan=lambda m: IYI_YANIT if saglik_cagrisi_mi(m) else BELIRSIZ)
    k, oid = hazirla(kur, llm, SAGLIK_BILGI_ZAMAN_ASIMI_SN="1")
    r = k.orkestre(oid, userText="aspirin ne işe yarar")
    assert r.status_code == 200 and r.json()["replyText"] == alan.SAGLIK_BILGI_VEREMIYORUM


@pytest.mark.parametrize("ham,neden", [
    ("Aspirin genellikle günde 100 mg alınır.", "doz"),
    ("Parasetamol günde üç kez kullanılır.", "doz"),
    ("Bu ilaç 4 saatte bir alınır.", "doz"),
    ("Sizde muhtemelen migren var.", "teshis"),
    ("Bu ilacı bırakabilirsiniz, sorun olmaz.", "kisisel_karar"),
    ("Aspirin is used for pain and fever relief in adults.", "dil"),
    ("- Aspirin ağrı kesicidir.\n- Ateşi düşürür.", "liste"),
    ("", "bos"),
    ("Aspirin ağrıyı azaltır ve", "kesik"),
    ("Aspirin ağrı kesicidir. Ancak Coumadin ile birlikte dikkat edilmelidir.", "baska_ilac"),
    ("Not: bu soru aslında parola hakkında olabilir. Aspirin ağrı kesicidir.", "meta"),
    ("Metforminle araba kullanabilirsiniz.", "izin"),
    ("Zatürre bulaşıcı değildir, yani bir kişiden diğerine geçmez.", "riskli_iddia"),
    ("Reflüde yemeklerden sonra yatmayı tercih edin.", "riskli_iddia"),
])
def test_cikti_denetimi_reddeder(ham, neden):
    soru = "Soru: aspirin ne işe yarar" if "etformin" not in ham else "Soru: metforminle araba kullanabilir miyim"
    metin, sonuc = yanit_denetle(ham, soru, alan)
    assert metin is None and sonuc == neden, (ham, sonuc)


def test_cikti_denetimi_uzunlugu_ve_bicimi_duzeltir():
    ham = ("**Aspirin** ağrıyı azaltır (ağrı kesici). " + "Bazı kişilerde mide rahatsızlığı yapabilir. " * 6)
    metin, neden = yanit_denetle(ham, "Soru: aspirin ne işe yarar", alan)
    assert neden == "" and metin is not None
    assert "*" not in metin and "(" not in metin
    assert len(metin) <= alan.SAGLIK_BILGI_AZAMI_KARAKTER
    assert len(re.findall(r"[.!?]", metin)) <= alan.SAGLIK_BILGI_AZAMI_CUMLE


def test_dogru_uyari_riskli_iddia_sayilmaz():
    metin, neden = yanit_denetle("Reflüde yemeklerden sonra hemen yatmayın; akşam öğününü hafif tutmak önerilir.",
                                 "Soru: reflü için nelere dikkat etmeli", alan)
    assert neden == "" and metin


def test_112_cumlesi_kirpilmaz():
    ham = ("Göğüs ağrısı kalp, kas ya da mide kaynaklı olabilir. Bazı kişilerde stresle ilgili olabilir. "
           "Nedenleri çeşitlidir ve değerlendirme gerekir. Bazen soğuk algınlığından da olabilir. "
           "Ani ve şiddetli göğüs ağrısında gecikmeden 112 aranmalıdır.")
    metin, neden = yanit_denetle(ham, "Soru: göğüs ağrısı neden olur", alan)
    assert neden == "" and "112" in metin


def test_kapanis_her_zaman_eklenir_ara_sigmazsa_atlanir():
    assert yanit_birlestir(alan, "Kısa bilgi.").endswith(alan.SAGLIK_BILGI_KAPANIS)
    uzun = "x" * 600
    sonuc = yanit_birlestir(alan, "Kısa bilgi.", uzun)
    assert uzun not in sonuc and sonuc.endswith(alan.SAGLIK_BILGI_KAPANIS)


# ------------------------------------------------------------------ KVKK

def test_llme_tc_ve_ad_gitmez(kur):
    llm = akilli_llm()
    k, oid = hazirla(kur, llm)
    k.orkestre(oid, userText="Ben Ayşe Yılmaz, aspirin ne işe yarar")
    k.orkestre(oid, userText="Benim adım Zeynep Kara baş ağrısına ne iyi gelir")
    k.orkestre(oid, userText="10000000146 aspirin ne işe yarar")
    yuk = "\n".join(saglik_yukleri(llm))
    assert yuk, "bilgi yoluna en az bir soru gitmeli"
    for gizli in ("Ayşe", "Yılmaz", "Zeynep", "Kara", "10000000146"):
        assert gizli not in yuk, (gizli, yuk)
    assert all("10000000146" not in m.icerik for c in llm.cagrilar for m in c["mesajlar"])


def test_tc_iceren_cumle_saglik_yoluna_hic_gitmez():
    assert saglik_bilgi_yuku_kur("10000000146 aspirin ne işe yarar", alan=alan) is None


def test_llme_hasta_kaydi_gitmez_talimat_koddan_eklenir(kur):
    llm = akilli_llm("Metformin bazı kişilerde baş dönmesi ya da halsizlik yapabilir. Bu belirtiler araç "
                     "kullanırken dikkat gerektirebilir.")
    k, oid = hazirla(kur, llm)
    k.orkestre(oid, userText="ilaçlarım neler")
    r = k.orkestre(oid, userText="Metforminle araba kullanabilir miyim").json()
    # Kayıtlı ilaç: LLM metni + hekim talimatı (koddan) + sabit kapanış; geçmiş kartı da gelir.
    assert r["data"]["aiGeneralInfo"] is True and r["data"]["history"]["focus"] == ["Metformin"]
    assert "Hekiminizin talimatı: Günde iki kez" in r["replyText"]
    assert r["replyText"].endswith(alan.SAGLIK_BILGI_KAPANIS)
    yuk = "\n".join(saglik_yukleri(llm))
    for gizli in ("Günde iki kez", "Kan şekerinizi dengeler", "Dahiliye", "Kardiyoloji", "Mehmet", "Elif",
                  "tip 2 diyabet", "Amlodipin", "Demir", "yazıl", "reçete", "hasta"):
        assert gizli.lower() not in yuk.lower(), (gizli, yuk)
    assert "metformin" in yuk.lower()  # soruda kullanıcının söylediği ilaç adı açık kalır (kasıtlı)


def test_randevu_bilgisi_llme_gitmez(kur):
    llm = akilli_llm()
    k, oid = hazirla(kur, llm)
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    k.orkestre(oid, userText="ilki")
    kod = k.orkestre(oid, userText="evet").json()["data"].get("accessCode")
    k.orkestre(oid, userText="aspirin ne işe yarar")
    yuk = "\n".join(saglik_yukleri(llm))
    assert yuk and "Kardiyoloji" not in yuk and (not kod or kod not in yuk)


def test_loglara_soru_ve_yanit_yazilmaz(kur, caplog):
    caplog.set_level("DEBUG")
    k, oid = hazirla(kur, akilli_llm("Sizde muhtemelen migren var."))
    k.orkestre(oid, userText="baş ağrısına ne iyi gelir")
    k2, oid2 = hazirla(kur)
    k2.orkestre(oid2, userText="aspirin ne işe yarar")
    loglar = caplog.text.lower()
    for parca in ("baş ağrısı", "aspirin", "migren", "iltihab"):
        assert parca not in loglar, parca
