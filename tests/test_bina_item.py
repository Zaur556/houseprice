import json

from scraper.sources.bina_item import parse_item, usable

# Shapes of the two page layouts seen on 2026-10-18, trimmed to the parts the parser reads.
ARCHIVED = """<html><head><title>1 otaqlı yeni tikili — 43 m² — İnşaatçılar m., Bakı, Azərbaycan (4663134) - bina.az</title></head>
<body><nav><a>Ağdam r.</a><a>Novxanı q.</a><label>Mərtəbə</label></nav>
<h1>Satılır 1 otaqlı yeni tikili 43 m², İnşaatçılar m.</h1>
<div>149 000</div><div>AZN</div><div>Baxışların sayı: 212</div><div>Müddəti başa çatıb: 01.11.2024, 15:33</div>
<div>Kateqoriya</div><div>Yeni tikili</div><div>Mərtəbə</div><div>5 / 17</div><div>Sahə</div><div>43 m²</div>
<div>Otaq sayı</div><div>1</div><p>Yasamalda təmirli mənzil.</p>
<div class="map" data-lat="40.38015814813899" data-lng="49.80487512695312"></div>
<a>İnşaatçılar m.</a><a>Yasamal r.</a><div>Elanın nömrəsi: 4663134</div>
<footer><a>Xəzər r.</a></footer></body></html>"""

ITEM = {"__typename": "Item", "id": "6554081", "isLeased": False, "isPaidDaily": None, "state": "PUBLISHED",
        "rooms": 3, "floor": 8, "floors": 9, "area": {"units": "m²", "value": 80},
        "price": {"currency": "AZN", "total": 225000},
        "category": {"slug": "menziller/kohne-tikili"}, "city": {"slug": "baki"},
        "location": {"fullName": "Xalqlar Dostluğu m."},
        "nearestLocations": [{"fullName": "Nizami r."}, {"fullName": "Xalqlar Dostluğu m."}]}
LIVE = ('<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": {"currentItemData": ITEM}}}) + "</script></html>")


def test_archived_layout():
    p = parse_item(ARCHIVED)
    assert usable(p)
    assert (p["id"], p["deal"], p["building"], p["rooms"], p["area_m2"]) == ("4663134", "sale", "new", 1, 43)
    assert (p["floor"], p["floors_total"], p["price_azn"]) == (5, 17, 149000)
    assert p["district"] == "Yasamal" and p["metro"] == "İnşaatçılar" and "settlement" not in p
    assert round(p["lat"], 3) == 40.380 and p["expired_at"].year == 2024


def test_live_layout():
    p = parse_item(LIVE)
    assert usable(p)
    assert (p["district"], p["metro"], p["building"], p["deal"]) == ("Nizami", "Xalqlar Dostluğu", "old", "sale")
    assert (p["floor"], p["floors_total"], p["area_m2"], p["price_azn"]) == (8, 9, 80, 225000)


def test_not_usable():
    house = {**ITEM, "category": {"slug": "heyet-evleri"}}
    daily = {**ITEM, "isLeased": True, "isPaidDaily": True}
    elsewhere = {**ITEM, "city": {"slug": "sumqayit"}}
    for item in (house, daily, elsewhere):
        html = '<script id="__NEXT_DATA__">' + json.dumps({"props": {"pageProps": {"currentItemData": item}}}) + "</script>"
        assert not usable(parse_item(html))
