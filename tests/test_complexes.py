import json
from datetime import datetime, timedelta

from scraper import db
from scraper.sources.complexes import parse_detail, parse_list

# Trimmed shapes of the pages as seen on 2026-10-23 (live) and in 2025 archive.org copies.
RES = {"__typename": "Residence", "id": "70", "name": "28 Residence", "city": {"name": "Bakı"},
       "price": {"perSqm": 3800, "total": {"minimum": 348200, "maximum": 1010000}, "currency": "AZN"},
       "address": "Nəsimi r., Azadlıq pr. 16", "deadline": "2023", "path": "/yasayis-kompleksleri/28-residence",
       "metroName": "28 May", "companyName": "28 Residence"}
LIVE_LIST = '<script id="__NEXT_DATA__">' + json.dumps({"props": {"pageProps": {"residences": [RES]}}}) + "</script>"
DETAIL = {**RES, "lat": 40.3837, "lng": 49.8455, "apartmentsCount": 582, "buildingsCount": 8, "hits": 103299,
          "floorsPerBuildingSplitted": ["16", "12"], "locations": [{"fullName": "Nəsimi r."}, {"fullName": "28 May m."}],
          "apartments": {"nodes": [{"rooms": 2, "area": 82.9, "priceFrom": 4200, "repair": {"name": "Tam təmirli"}},
                                   {"rooms": 5, "area": 265.8, "priceFrom": 3800, "repair": {"name": "Qara suvaq"}}]}}
LIVE_DETAIL = '<script id="__NEXT_DATA__">' + json.dumps({"props": {"pageProps": {"residence": DETAIL}}}) + "</script>"
OLD_LIST = """<div class="residences"><a class="residences-i" href="/yasayis-kompleksleri/melissa-park">
<div class="price" data-full-price="133 600" data-sqm="2 650"><span class="price-val">133 600</span>AZN-dən</div>
<div class="title">Melissa Park</div><div class="card_params"><div class="developer">Melissa Group</div>
<div class="location">Yasamal r.</div><div class="metro">20 Yanvar</div><div class="deadline">Təhvil verilib</div></div></a></div>"""
OLD_DETAIL = """<div data-lat="40.38369257273174" data-lng="49.84548139533987"></div>
<div class="residence-layout__price-per-meter"><span class="price-val">2 900</span><span class="price-cur">AZN</span>/m²-dən</div>
<div class="residence-layout__price-per-meter"><span class="price-val">2 700</span><span class="price-cur">AZN</span>/m²-dən</div>"""


def test_parse_all_layouts():
    (a,) = parse_list(LIVE_LIST)
    assert (a["slug"], a["per_sqm"], a["min_total"], a["metro"]) == ("28-residence", 3800, 348200, "28 May")
    d = parse_detail(LIVE_DETAIL, "28-residence")
    assert (d["district"], d["floors"], d["apartments"], round(d["lat"], 2)) == ("Nəsimi", 16, 582, 40.38)
    assert d["layouts"][0] == {"rooms": 2, "area": 82.9, "per_sqm": 4200, "finish": "Tam təmirli"}
    (o,) = parse_list(OLD_LIST)
    assert (o["slug"], o["name"], o["per_sqm"], o["min_total"]) == ("melissa-park", "Melissa Park", 2650, 133600)
    old = parse_detail(OLD_DETAIL, "28-residence")
    assert old["per_sqm"] == 2700 and round(old["lng"], 2) == 49.85      # the lowest layout price


def test_price_history(tmp_path):
    engine = db.get_engine(f"sqlite:///{tmp_path / 'c.db'}")
    t0 = datetime(2026, 10, 23, 12)
    (live,) = parse_list(LIVE_LIST)
    assert db.save_complex(engine, live, t0)
    assert not db.save_complex(engine, live, t0 + timedelta(hours=6))         # unchanged price: no new row
    assert db.save_complex(engine, {**live, "per_sqm": 3900}, t0 + timedelta(hours=12))
    old = parse_detail(OLD_DETAIL, "28-residence")
    seen = datetime(2025, 1, 15, 3)
    assert db.save_complex(engine, old, seen, origin="archive")
    assert not db.save_complex(engine, old, seen + timedelta(hours=2), origin="archive")   # same day
    with engine.connect() as c:
        rows = c.execute(db.complex_prices.select().order_by(db.complex_prices.c.observed_at)).fetchall()
        cx = c.execute(db.complexes.select()).one()
    assert [(r.per_sqm, r.origin) for r in rows] == [(2700, "archive"), (3800, "live"), (3900, "live")]
    assert cx.first_seen == seen and cx.name == "28 Residence"              # an old copy doesn't rename it
