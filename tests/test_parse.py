from scraper.parse import parse_card, build_record


def test_bina_card():
    t = "125 000 AZN\nBakı, Yasamal r.\n2 otaqlı\n65 m²\n5/12 mərtəbə\nBakı, bugün 14:32"
    p = parse_card(t, "https://bina.az/items/4123456")
    assert p["price_azn"] == 125000 and p["currency"] == "AZN"
    assert p["rooms"] == 2 and p["area_m2"] == 65
    assert (p["floor"], p["floors_total"]) == (5, 12)
    assert p["district"] == "Yasamal" and not p.get("district_inferred")


def test_metro_infers_district():
    p = parse_card("98 500 AZN\nElmlər Akademiyası m.\n3 otaqlı\n92.5 m²\n3/9 mərtəbə")
    assert p["metro"] == "Elmlər Akademiyası"
    assert p["district"] == "Yasamal" and p["district_inferred"]
    assert p["area_m2"] == 92.5


def test_short_metro_alias_and_usd():
    p = parse_card("$ 80 000\nNərimanov m.\n2 otaqlı 70 m²")
    assert p["metro"] == "Nəriman Nərimanov"
    assert p["currency"] == "USD" and p["price_azn"] == 136000


def test_settlement():
    p = parse_card("150 000 AZN\nBadamdar q.\n4 otaqlı\n140 m²")
    assert p["settlement"] == "Badamdar" and p["district"] == "Səbail"


def test_yeniemlak_slug_and_rent():
    url = "https://yeniemlak.az/elan/kiraye-2-otaqli-bina-evi-menzil-nesimi-rayonu-nizami-kucesi-137-214318"
    p = parse_card("800 AZN /ay 55 m² Mərtəbə: 5/5", url)
    assert p["deal"] == "rent" and p["rooms"] == 2 and p["district"] == "Nəsimi"
    assert p["price_azn"] == 800 and (p["floor"], p["floors_total"]) == (5, 5)


def test_build_record_drops_incomplete():
    sec = {"deal": "sale", "building": "new"}
    assert build_record("bina", "1", "u", "no price here 50 m²", sec) is None
    r = build_record("bina", "1", "u", "100 000 AZN 50 m² Nəsimi r.", sec)
    assert r["id"] == "bina:1" and r["building"] == "new" and r["section"] == "bina:sale:new"
