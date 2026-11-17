"""HastaKabul ai-simplify servisi (port 8001): hekimin tanı ve ilaç listesini sade Türkçeye çevirir.

POST /simplify → 202 (iş kalıcı kuyruğa alınır) → arka planda eşleme + LLM/şablon + kod denetimi →
PUT {API}/api/v1/summaries/{summaryId}. Çıktı hekim onayından sonra hastaya gider.
"""
