"""Fill a local SQLite database with synthetic listings so the dashboard can be previewed.

    DATABASE_URL=sqlite:///data/demo.db python -m tools.seed_demo && \
    DATABASE_URL=sqlite:///data/demo.db python -m analytics.export

Numbers are invented. The dashboard shows a "demo data" notice while they are in use.
"""
import random
from datetime import timedelta

from scraper import db
from scraper.parse import METRO_DISTRICT

random.seed(7)
# illustrative sale AZN/m² and monthly rent AZN/m² by district
BASE = {"Səbail": (4100, 19), "Nəsimi": (3300, 16), "Yasamal": (2500, 13), "Nərimanov": (2400, 12.5),
        "Xətai": (2150, 11.5), "Nizami": (1750, 9.5), "Binəqədi": (1550, 9), "Sabunçu": (1250, 7.5),
        "Xəzər": (1300, 8), "Suraxanı": (1050, 6.5), "Qaradağ": (900, 6), "Pirallahı": (850, 5.5)}
WEIGHT = {"Səbail": 6, "Nəsimi": 12, "Yasamal": 14, "Nərimanov": 11, "Xətai": 13, "Nizami": 8,
          "Binəqədi": 9, "Sabunçu": 8, "Xəzər": 4, "Suraxanı": 4, "Qaradağ": 2, "Pirallahı": 1}
STATIONS = {}
for st, d in METRO_DISTRICT.items():
    STATIONS.setdefault(d, []).append(st)


def main(days=730, n=7000):
    engine = db.get_engine()
    now = db.utcnow()
    start = now - timedelta(days=days)
    run_id = db.start_run(engine, "demo", "demo")
    districts = list(WEIGHT)
    listings, snaps = [], []
    for i in range(n):
        deal = "sale" if random.random() < 0.72 else "rent"
        d = random.choices(districts, weights=[WEIGHT[x] for x in districts])[0]
        rooms = random.choices([1, 2, 3, 4, 5], weights=[14, 36, 32, 13, 5])[0]
        area = max(25, random.gauss(32 + rooms * 26, 12))
        building = "new" if random.random() < (0.62 if deal == "sale" else 0.5) else "old"
        floors_total = random.choice([5, 9, 12, 16, 17, 20, 24]) if building == "new" else random.choice([4, 5, 9, 12])
        floor = random.randint(1, floors_total)
        first = start + timedelta(days=random.random() * days)
        life = timedelta(days=random.expovariate(1 / (42 if deal == "sale" else 18)))
        last = min(first + life, now)
        active = last >= now - timedelta(days=1)
        age = (first - start).days / 30
        sale, rent = BASE[d]
        trend = 1 + 0.012 * age                      # ~1.2% a month market drift
        factor = (1.12 if building == "new" else 0.92) * (0.93 if floor == 1 else 1) * random.lognormvariate(0, 0.16)
        ppm2 = (sale if deal == "sale" else rent) * trend * factor
        price = round(ppm2 * area, -2 if deal == "sale" else -1)
        source = random.choice(["bina", "bina", "yeniemlak"])
        metro = random.choice(STATIONS[d]) if d in STATIONS and random.random() < 0.6 else None
        lid = f"{source}:{1_000_000 + i}"
        listings.append({
            "id": lid, "source": source, "source_id": str(1_000_000 + i),
            "section": f"{source}:{deal}:{building if source == 'bina' and deal == 'sale' else 'any'}",
            "url": f"https://example.invalid/{lid}", "deal": deal, "building": building, "rooms": rooms,
            "area_m2": round(area, 1), "floor": floor, "floors_total": floors_total, "district": d,
            "district_inferred": False, "metro": metro, "settlement": None,
            "title": f"{rooms} otaqlı, {area:.0f} m², {d} r.", "price_azn": price,
            "first_seen": first, "last_seen": last, "is_active": active,
            "delisted_at": None if active else last})
        snaps.append({"listing_id": lid, "observed_at": first, "price": price, "currency": "AZN", "price_azn": price})
        if random.random() < 0.28 and (last - first).days > 6:
            when = first + (last - first) * random.uniform(0.3, 0.95)
            cut = round(price * (1 - random.uniform(0.02, 0.09)), -2 if deal == "sale" else -1)
            snaps.append({"listing_id": lid, "observed_at": when, "price": cut, "currency": "AZN", "price_azn": cut})
            listings[-1]["price_azn"] = cut
    with engine.begin() as c:
        c.execute(db.listings.insert(), listings)
        c.execute(db.snapshots.insert(), snaps)
    db.finish_run(engine, run_id, "ok", found=n, new=n, price_changes=0, delisted=0)
    print(f"seeded {n} demo listings")


if __name__ == "__main__":
    main()
