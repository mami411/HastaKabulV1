"""KVKK sınırı: LLM'e giden yük ve LLM çıktısının kullanıcıya hiç ulaşmaması.

Sahte sağlayıcı LLM'e giden HER mesajı kaydeder; testler bu kayıtların tamamında kişisel/sağlık
verisi olmadığını doğrular. Sahte LLM yanıtına konan işaret metni hiçbir yanıtta görünmemelidir.
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import BUGUN
from ortak.llm import SahteSaglayici
from ses_servisi.alanlar import hasta_kabul as alan
from ses_servisi.llm_siniri import kisisel_maskele, llm_yuku_kur

ISARET = "LLM-SIZINTI-ISARETI"

# Kural katmanının çözemediği, LLM'e düşen cümleler (kişisel veri gömülü).
BELIRSIZ = [
    # (Sağlık verisi taşıyan "kalbim için …" gibi cümleler artık LLM'e hiç gitmez; ayrı testte.)
    "bir bakılmasını rica ediyorum, ben Ayşe Yılmaz",
    "adım Ayşe Yılmaz, telefonum 123 45 67, bir bakılmasını rica ediyorum",
    "e-postam ayse.yilmaz@ornek.com, şuraya bir bakılmasını rica ediyorum",
    "Ayşe Hanım olarak bir bakılmasını rica ediyorum",
]


def _isaretli_yanit(_mesajlar):
    return json.dumps({"niyet": "belirsiz", "poliklinik": None, "gun": None, "saat_baslangic": None,
                       "saat_bitis": None, "yanit": ISARET, "aciklama": ISARET})


def _tum_yuk(llm: SahteSaglayici) -> str:
    return "\n".join(m.icerik for c in llm.cagrilar for m in c["mesajlar"])


def test_llm_yukunde_kisisel_ve_saglik_verisi_yok(kur):
    llm = SahteSaglayici(varsayilan=_isaretli_yanit)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    yanitlar = [k.orkestre(oid, event="TC_VERIFIED").json()]
    yanitlar.append(k.orkestre(oid, userText="son muayenemde hangi ilaçlar yazıldı").json())  # geçmiş bellekte
    for cumle in BELIRSIZ:
        yanitlar.append(k.orkestre(oid, userText=cumle).json())
    yanitlar.append(k.orkestre(oid, userText="kardiyoloji yarın sabah").json())
    yanitlar.append(k.orkestre(oid, userText="ilki").json())
    yanitlar.append(k.orkestre(oid, userText="evet onaylıyorum").json())
    erisim = yanitlar[-1]["data"]["accessCode"]
    assert erisim
    for cumle in BELIRSIZ:  # tamamlanan oturumda LLM'e hiç gidilmez
        yanitlar.append(k.orkestre(oid, userText=cumle).json())

    assert llm.cagrilar, "senaryo LLM'e en az bir kez gitmeli"
    for cagri in llm.cagrilar:
        # Her çağrıda tam iki mesaj: sabit sistem istemi + maskeli kullanıcı cümlesi; geçmiş tur yok
        assert [m.rol for m in cagri["mesajlar"]] == ["system", "user"]
        assert not cagri["araclar"]
    yuk = _tum_yuk(llm)
    gizliler = ["Ayşe", "Yılmaz", "123 45 67", "1234567", "ornek.com", oid, erisim,
                "Metformin", "Demir sülfat", "Amlodipin", "Kan şekeriniz", "Tansiyonunuz", "Mehmet D.", "Elif K.",
                "Parasetamol", "ONAYSIZ", "10000000146"]
    for y in yanitlar:  # önceki asistan yanıtları da gitmez
        if len(y.get("replyText") or "") > 40:
            gizliler.append(y["replyText"])
    for gizli in gizliler:
        assert gizli not in yuk, gizli
    assert "[ad]" in yuk and "[gizli numara]" in yuk and "[gizli e-posta]" in yuk
    assert len(llm.cagrilar) == len(BELIRSIZ)


def test_llm_ciktisi_kullaniciya_hic_ulasmaz(kur):
    llm = SahteSaglayici(varsayilan=_isaretli_yanit)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    govdeler = [k.orkestre(oid, event="TC_VERIFIED").text]
    for cumle in BELIRSIZ:
        govdeler.append(k.orkestre(oid, userText=cumle).text)
    assert llm.cagrilar
    for g in govdeler:
        assert ISARET not in g


@pytest.mark.parametrize("niyet", ["konu_disi", "tibbi_soru", "belirsiz", "selam"])
def test_her_yanit_metni_sablondan(kur, niyet):
    """LLM hangi niyeti söylerse söylesin replyText alan modülündeki şablonlardan kurulur."""
    llm = SahteSaglayici(varsayilan=json.dumps({
        "niyet": niyet, "poliklinik": None, "gun": None, "saat_baslangic": None, "saat_bitis": None,
        "yanit": ISARET}))
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    r = k.orkestre(oid, userText=BELIRSIZ[0])
    assert ISARET not in r.text
    metin = r.json()["replyText"]
    sablonlar = [v for v in vars(alan).values() if isinstance(v, str) and len(v) > 20]
    sabit_parcalar = [re.split(r"\{[^}]*\}", s)[0] for s in sablonlar]
    assert any(p and metin.startswith(p) or metin == s for p, s in zip(sabit_parcalar, sablonlar)), metin


def test_llm_onay_niyeti_randevu_olusturmaz(kur):
    llm = SahteSaglayici(varsayilan=json.dumps({"niyet": "onay", "poliklinik": None, "gun": None,
                                                "saat_baslangic": None, "saat_bitis": None}))
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    k.orkestre(oid, userText="kardiyoloji yarın sabah")
    k.orkestre(oid, userText="ilki")
    r = k.orkestre(oid, userText="şuraya bir bakılmasını rica ediyorum").json()
    assert llm.cagrilar  # cümle gerçekten LLM'e gitti ve LLM "onay" dedi
    assert r["action"] != "DONE"
    assert not [c for c in k.api.cagrilar if c[0] == "POST" and c[1].endswith("/appointments")]


def test_tc_iceren_cumle_llme_gitmez(kur):
    llm = SahteSaglayici(varsayilan=_isaretli_yanit)
    k = kur(llm)
    oid = k.api.oturum_ac("VERIFIED")
    k.orkestre(oid, event="TC_VERIFIED")
    k.orkestre(oid, userText="kimlik numaram 1000 0000 146, bir bakılmasını rica ediyorum")
    assert "10000000146" not in _tum_yuk(llm) and "1000 0000 146" not in _tum_yuk(llm)


# ------------------------------------------------------------------ birim: maskeleme ve yük kurucu

@pytest.mark.parametrize("girdi,olmamali,olmali", [
    ("ben Ayşe Yılmaz kardiyoloji istiyorum", ["Ayşe", "Yılmaz"], ["[ad]", "kardiyoloji"]),
    ("benim adım Mehmet, yarın sabah", ["Mehmet"], ["[ad]", "yarın sabah"]),
    ("Ayşe Hanım için dahiliye", ["Ayşe"], ["dahiliye"]),
    ("telefonum 0 532 123 45 67", ["532", "45 67"], ["[gizli numara]"]),
    ("a.b@c.com.tr adresim", ["a.b@c", "com.tr"], ["[gizli e-posta]"]),
    ("tc 10000000146", ["10000000146"], []),
    ("yarın 10:30 kardiyoloji", [], ["10:30", "kardiyoloji"]),
    ("ben yarın sabah gelirim", [], ["yarın sabah"]),
])
def test_maskeleme(girdi, olmamali, olmali):
    m = kisisel_maskele(girdi).metin
    for x in olmamali:
        assert x not in m, m
    for x in olmali:
        assert x in m, m


def test_yuk_kurucu_tek_kaynak():
    yuk = llm_yuku_kur("ben Ayşe, kalbim için", alan=alan, bugun=BUGUN, simdi_metni="19:36",
                       adim="BILINMEYEN_ADIM; hasta adı Ayşe")
    assert [m.rol for m in yuk.mesajlar] == ["user"] and yuk.sistem  # sistem istemi ayrı, sabit
    assert "Ayşe" not in yuk.tum_metin() and "BILINMEYEN_ADIM" not in yuk.tum_metin()
    # Geçersiz saat metni de gönderilmez
    yuk2 = llm_yuku_kur("kalbim", alan=alan, bugun=BUGUN, simdi_metni="Ayşe", adim=None)
    assert "Ayşe" not in yuk2.tum_metin()
