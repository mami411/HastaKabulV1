"""Sağlayıcı katmanı: OpenAI uyumlu (sahte HTTP), Anthropic (sahte istemci), fabrika, hatalar."""

import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from ortak.llm import (
    AnthropicSaglayici,
    AracCagrisi,
    AracTanimi,
    LLMBaglantiHatasi,
    LLMLimitHatasi,
    LLMYanitHatasi,
    LLMYapilandirmaHatasi,
    LLMZamanAsimi,
    Mesaj,
    OpenAIUyumluSaglayici,
    SahteSaglayici,
    saglayici_olustur,
)
from ortak.llm.temel import json_ayikla, sema_dogrula


def calistir(coro):
    return asyncio.run(coro)


def openai_yaniti(icerik="", arac_cagrilari=None, durum=200):
    mesaj = {"role": "assistant", "content": icerik}
    if arac_cagrilari:
        mesaj["tool_calls"] = arac_cagrilari
    return httpx.Response(durum, json={"model": "m", "choices": [{"message": mesaj, "finish_reason": "stop"}],
                                       "usage": {"prompt_tokens": 3, "completion_tokens": 2}})


def saglayici(isleyici, **kw):
    kw.setdefault("yeniden_deneme", 0)
    s = OpenAIUyumluSaglayici("m", taban_url="http://llm/v1", transport=httpx.MockTransport(isleyici), **kw)
    s.bekleme_sn = 0
    return s


def test_openai_metin_ve_istek_bicimi():
    gelen = {}

    def isle(istek):
        gelen.update(json.loads(istek.content))
        gelen["_yol"] = istek.url.path
        gelen["_yetki"] = istek.headers.get("Authorization")
        return openai_yaniti("<think>gizli düşünce</think>Merhaba")

    s = OpenAIUyumluSaglayici("qwen", taban_url="http://llm/v1/", api_anahtari="gizli",
                              ek_govde={"reasoning_effort": "none"},
                              transport=httpx.MockTransport(isle))
    metin = calistir(s.metin_uret("Selam", sistem="Kısa yanıt ver."))
    assert metin == "Merhaba"  # <think> bloğu temizlenir
    assert gelen["_yol"] == "/v1/chat/completions"
    assert gelen["_yetki"] == "Bearer gizli"
    assert gelen["model"] == "qwen" and gelen["reasoning_effort"] == "none"
    assert gelen["messages"][0] == {"role": "system", "content": "Kısa yanıt ver."}
    assert gelen["messages"][1] == {"role": "user", "content": "Selam"}


def test_openai_json_semasi_ve_dogrulama():
    gelen = {}
    sema = {"type": "object", "properties": {"niyet": {"type": "string", "enum": ["onay", "ret"]}},
            "required": ["niyet"]}

    def isle(istek):
        gelen.update(json.loads(istek.content))
        return openai_yaniti('```json\n{"niyet": "onay"}\n```')

    s = saglayici(isle)
    assert calistir(s.json_uret("evet", sema=sema, sema_adi="anlam")) == {"niyet": "onay"}
    assert gelen["response_format"]["type"] == "json_schema"
    assert gelen["response_format"]["json_schema"]["name"] == "anlam"

    s2 = saglayici(lambda istek: openai_yaniti('{"niyet": "belki"}'))
    with pytest.raises(LLMYanitHatasi):
        calistir(s2.json_uret("evet", sema=sema))
    s3 = saglayici(lambda istek: openai_yaniti("JSON değil"))
    with pytest.raises(LLMYanitHatasi):
        calistir(s3.json_uret("evet", sema=sema))


def test_openai_json_modu_nesne():
    gelen = {}

    def isle(istek):
        gelen.update(json.loads(istek.content))
        return openai_yaniti('{"a": 1}')

    s = saglayici(isle, json_modu="nesne")
    assert calistir(s.json_uret("x")) == {"a": 1}
    assert gelen["response_format"] == {"type": "json_object"}


def test_openai_arac_cagrisi_gidis_donus():
    istekler = []

    def isle(istek):
        istekler.append(json.loads(istek.content))
        return openai_yaniti("", [{"id": "c1", "type": "function", "function": {
            "name": "uygun_saatleri_ara", "arguments": '{"poliklinik": "KARDIYOLOJI", "gun": "2026-11-16"}'}}])

    arac = AracTanimi("uygun_saatleri_ara", "Saat arar.", {"type": "object", "properties": {}})
    s = saglayici(isle)
    yanit = calistir(s.sohbet("kalp yarın", araclar=[arac], arac_secimi="uygun_saatleri_ara"))
    assert yanit.arac_cagrilari == (AracCagrisi("c1", "uygun_saatleri_ara",
                                                {"poliklinik": "KARDIYOLOJI", "gun": "2026-11-16"}),)
    assert istekler[0]["tools"][0]["function"]["name"] == "uygun_saatleri_ara"
    assert istekler[0]["tool_choice"] == {"type": "function", "function": {"name": "uygun_saatleri_ara"}}
    # Araç sonucu geçmişe eklenip ikinci tur gönderilebilir
    gecmis = [Mesaj.kullanici("kalp yarın"), Mesaj.asistan("", yanit.arac_cagrilari),
              Mesaj.arac_sonucu("c1", '{"slotlar": []}')]
    calistir(s.sohbet(gecmis, araclar=[arac]))
    ikinci = istekler[1]["messages"]
    assert ikinci[1]["tool_calls"][0]["function"]["name"] == "uygun_saatleri_ara"
    assert ikinci[2] == {"role": "tool", "tool_call_id": "c1", "content": '{"slotlar": []}'}


@pytest.mark.parametrize("durum,sinif", [(429, LLMLimitHatasi), (401, LLMYapilandirmaHatasi),
                                         (404, LLMYapilandirmaHatasi), (500, LLMBaglantiHatasi),
                                         (400, LLMYanitHatasi), (504, LLMZamanAsimi)])
def test_openai_hata_siniflari(durum, sinif):
    s = saglayici(lambda istek: httpx.Response(durum, json={"error": {"message": "x"}}))
    with pytest.raises(sinif) as bilgi:
        calistir(s.metin_uret("x"))
    assert bilgi.value.durum_kodu == durum


def test_openai_zaman_asimi_ve_baglanti():
    def zaman(istek):
        raise httpx.ReadTimeout("t", request=istek)

    def baglanti(istek):
        raise httpx.ConnectError("c", request=istek)

    with pytest.raises(LLMZamanAsimi):
        calistir(saglayici(zaman).metin_uret("x"))
    with pytest.raises(LLMBaglantiHatasi):
        calistir(saglayici(baglanti).metin_uret("x"))


def test_gecici_hatada_yeniden_dener_kalicida_denemez():
    sayac = {"n": 0}

    def isle(istek):
        sayac["n"] += 1
        return httpx.Response(429) if sayac["n"] == 1 else openai_yaniti("tamam")

    assert calistir(saglayici(isle, yeniden_deneme=1).metin_uret("x")) == "tamam"
    assert sayac["n"] == 2

    sayac["n"] = 0

    def kalici(istek):
        sayac["n"] += 1
        return httpx.Response(401)

    with pytest.raises(LLMYapilandirmaHatasi):
        calistir(saglayici(kalici, yeniden_deneme=3).metin_uret("x"))
    assert sayac["n"] == 1


def test_hata_mesajinda_istem_icerigi_yok():
    s = saglayici(lambda istek: httpx.Response(500))
    with pytest.raises(LLMBaglantiHatasi) as bilgi:
        calistir(s.metin_uret("Ayşe Hanımeli kalp ağrısı"))
    assert "Ayşe" not in str(bilgi.value)


def test_fabrika_onek_ve_ortak_varsayilan():
    ortam = {"LLM_MODEL": "ortak-model", "LLM_TABAN_URL": "http://ollama/v1", "SES_LLM_MODEL": "ses-model",
             "LLM_ZAMAN_ASIMI_SN": "7", "SES_LLM_EK_GOVDE": '{"think": false}'}
    ses = saglayici_olustur("SES_LLM", ortam)
    sade = saglayici_olustur("SADE_LLM", ortam)
    assert isinstance(ses, OpenAIUyumluSaglayici)
    assert ses.model == "ses-model" and sade.model == "ortak-model"
    assert ses.taban_url == "http://ollama/v1" and ses.zaman_asimi_sn == 7.0
    assert ses.ek_govde == {"think": False} and sade.ek_govde == {}


def test_fabrika_hatalari():
    with pytest.raises(LLMYapilandirmaHatasi):
        saglayici_olustur("LLM", {})  # model yok
    with pytest.raises(LLMYapilandirmaHatasi):
        saglayici_olustur("LLM", {"LLM_MODEL": "m", "LLM_SAGLAYICI": "bilinmeyen"})
    with pytest.raises(LLMYapilandirmaHatasi):
        saglayici_olustur("LLM", {"LLM_MODEL": "m", "LLM_EK_GOVDE": "{bozuk"})


def test_anthropic_paket_yoksa_import_patlamaz(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # paket yokmuş gibi
    import ortak.llm.anthropic_saglayici  # noqa: F401 — modül içe aktarımı patlamaz

    with pytest.raises(LLMYapilandirmaHatasi, match="anthropic paketi"):
        AnthropicSaglayici("claude-x", api_anahtari="k")
    with pytest.raises(LLMYapilandirmaHatasi):
        saglayici_olustur("LLM", {"LLM_MODEL": "m", "LLM_SAGLAYICI": "anthropic", "LLM_API_ANAHTARI": "k"})


class _SahteAnthropicMesajlari:
    def __init__(self, yanit):
        self.yanit = yanit
        self.istekler = []

    async def create(self, **istek):
        self.istekler.append(istek)
        return self.yanit


def test_anthropic_cevirileri_ve_json():
    blok = SimpleNamespace(type="tool_use", id="t1", name="anlam", input={"niyet": "onay"})
    yanit = SimpleNamespace(content=[blok], stop_reason="tool_use", model="claude-x",
                            usage=SimpleNamespace(input_tokens=5, output_tokens=3))
    mesajlar = _SahteAnthropicMesajlari(yanit)
    s = AnthropicSaglayici("claude-x", istemci=SimpleNamespace(messages=mesajlar), yeniden_deneme=0)
    sonuc = calistir(s.json_uret("evet", sistem="Sistem.", sema={"type": "object"}, sema_adi="anlam"))
    assert sonuc == {"niyet": "onay"}
    istek = mesajlar.istekler[0]
    assert istek["system"] == "Sistem."
    assert istek["tool_choice"] == {"type": "tool", "name": "anlam"}
    assert istek["messages"] == [{"role": "user", "content": "evet"}]

    # araç geçmişi Anthropic bloklarına çevrilir
    sistem, cevrilmis = AnthropicSaglayici._mesajlari_cevir([
        Mesaj.sistem("S"), Mesaj.kullanici("a"),
        Mesaj.asistan("", [AracCagrisi("t1", "ara", {"x": 1}), AracCagrisi("t2", "ara", {"x": 2})]),
        Mesaj.arac_sonucu("t1", "r1"), Mesaj.arac_sonucu("t2", "r2"),
    ])
    assert sistem == "S"
    assert cevrilmis[1]["content"][0] == {"type": "tool_use", "id": "t1", "name": "ara", "input": {"x": 1}}
    assert [b["tool_use_id"] for b in cevrilmis[2]["content"]] == ["t1", "t2"]


def test_sahte_saglayici_sirali_yanit_ve_hata():
    s = SahteSaglayici(['{"a": 1}', LLMZamanAsimi("z")])
    assert calistir(s.json_uret("x")) == {"a": 1}
    with pytest.raises(LLMZamanAsimi):
        calistir(s.metin_uret("x"))
    assert len(s.cagrilar) == 2


def test_json_ayikla_ve_sema():
    assert json_ayikla('Yanıt: {"a": [1, 2]} bitti') == {"a": [1, 2]}
    sema_dogrula({"a": None}, {"type": "object", "properties": {"a": {"type": ["string", "null"]}}})
    with pytest.raises(LLMYanitHatasi):
        sema_dogrula({"a": 3}, {"type": "object", "properties": {"a": {"type": "string"}}})
    with pytest.raises(LLMYanitHatasi):
        sema_dogrula({}, {"type": "object", "required": ["a"]})
