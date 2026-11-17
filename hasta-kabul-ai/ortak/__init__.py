"""HastaKabul yapay zekâ servislerinin ortak paketi.

İçerik:
- ``ortak.llm``     : sağlayıcıdan bağımsız LLM katmanı (OpenAI uyumlu, Anthropic, sahte)
- ``ortak.ayarlar`` : ortam değişkeni okuma yardımcıları (.env desteği)
- ``ortak.turkce``  : Türkçe küçük harf, saat okuma, sayı okuma, TC maskeleme
- ``ortak.guvenlik``: yasak ifade filtresi (konudan bağımsız çekirdek)

Bu paket hiçbir servise (ses, sadeleştirme) bağlı değildir; servisler bunu kullanır.
"""
