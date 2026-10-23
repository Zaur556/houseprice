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


# Card texts as the sources rendered them on 2026-10-18 (currency drawn as an icon,
# so the scrapers prepend "<price> AZN").
def test_bina_card_with_icon_currency():
    t = "165 500 AZN\nAgentlik\n165 500\nNardaran q.\n1 otaqlı\n48 m²\n3/7 mərtəbə\nBakı, bugün 01:15"
    r = build_record("bina", "9", "https://bina.az/items/9", t, {"deal": "sale", "building": "new"})
    assert r["price_azn"] == 165500 and r["rooms"] == 1 and r["area_m2"] == 48
    assert (r["floor"], r["floors_total"]) == (3, 7)


def test_bina_daily_rent_is_dropped():
    t = "50 AZN\n50\n/gün\n28 May m.\n2 otaqlı\n60 m²\n15/16 mərtəbə"
    assert build_record("bina", "8", "https://bina.az/items/8", t, {"deal": "rent", "building": None}) is None
    monthly = t.replace("/gün", "/ay").replace("50", "900")
    assert build_record("bina", "8", "https://bina.az/items/8", monthly, {"deal": "rent", "building": None})["price_azn"] == 900


def test_yeniemlak_card_height_first_floor():
    url = "https://yeniemlak.az/elan/satilir-2-otaqli-bina-evi-menzil-nerimanov-rayonu-4sayli-dost-merkezin-yani-ziy-219335"
    t = ("120000 AZN\nSatılır\n120000\nBaxış sayı:\n26\nBina evi menzil\n/ Köhnə tikili\n2\notaq\n40\nm2\n5\n/\n3\n"
         "Mərtəbə\nÜnvan:\nBakı\nNərimanov rayonu\nmetro.\nNəriman Nərimanov")
    r = build_record("yeniemlak", "219335", url, t, {"deal": "sale", "building": None})
    assert r["price_azn"] == 120000 and r["rooms"] == 2 and r["area_m2"] == 40
    assert (r["floor"], r["floors_total"]) == (3, 5)
    assert r["building"] == "old" and r["district"] == "Nərimanov" and r["metro"] == "Nəriman Nərimanov"


def test_renovation_from_ad_text():
    assert parse_card("120000 AZN 60 m2 Əla təmirli, mebelli")["renovated"] is True
    assert parse_card("120000 AZN 60 m2 Avro təmir olunub")["renovated"] is True
    assert parse_card("90000 AZN 40 m2 təmirsiz, öz zövqünüzə təmir edə bilərsiniz")["renovated"] is False
    assert "renovated" not in parse_card("120000 AZN 60 m2 Nəsimi r.")
