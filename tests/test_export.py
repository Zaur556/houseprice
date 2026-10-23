import json
from datetime import datetime, timedelta

from analytics import export
from scraper import db
from scraper.parse import build_record
from tools import seed_demo


def test_publish_keeps_newest(tmp_path):
    engine = db.get_engine(f"sqlite:///{tmp_path / 'e.db'}")
    assert db.latest_export(engine) is None
    for i in range(db.KEEP_EXPORTS + 3):
        db.publish_export(engine, json.dumps({"n": i}))
    assert json.loads(db.latest_export(engine)) == {"n": db.KEEP_EXPORTS + 2}
    with engine.connect() as c:
        assert len(c.execute(db.exports.select()).fetchall()) == db.KEEP_EXPORTS


def test_export_end_to_end(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'demo.db'}"
    get_engine = db.get_engine
    monkeypatch.setattr(db, "get_engine", lambda *a, **k: get_engine(url))
    monkeypatch.setattr(export, "DASHBOARD_DATA", tmp_path / "data.json")
    seed_demo.main(n=1500)
    export.main([])

    data = json.loads(db.latest_export(db.get_engine()))
    assert data == json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))
    L, S = data["L"], data["S"]
    n = len(L["src"])
    assert n > 1000 and all(len(v) == n for v in L.values())
    assert sum(L["sn"]) == len(S["t"]) == len(S["v"]) == len(S["o"])
    assert sum(L["on"]) == data["meta"]["online_now"] > 0
    # snapshots of each listing are contiguous and in time order
    for i in range(0, n, 97):
        ts = S["t"][L["so"][i]:L["so"][i] + L["sn"][i]]
        assert ts == sorted(ts) and len(ts) == L["sn"][i]
    assert any(p >= 0 for p in L["pl"]) and data["places"]


def test_archive_merges_with_live(tmp_path):
    engine = db.get_engine(f"sqlite:///{tmp_path / 'a.db'}")
    t_live = datetime(2026, 10, 18, 12)
    live = build_record("bina", "6554081", "https://bina.az/items/6554081", "225 000 AZN 80 m² 8/9 mərtəbə",
                        {"deal": "sale", "building": "old"})
    db.save_listings(engine, [live], now=t_live)
    archived = {"id": "6554081", "deal": "sale", "building": "old", "price": 210000.0, "currency": "AZN",
                "price_azn": 210000.0, "area_m2": 80, "district": "Nizami", "lat": 40.41, "lng": 49.93}
    seen = t_live - timedelta(days=120)
    assert db.save_archived(engine, "bina", archived, seen) == "merged"
    assert db.save_archived(engine, "bina", archived, seen) == "duplicate"
    assert db.save_archived(engine, "bina", {**archived, "id": "1"}, seen) == "new"
    with engine.connect() as c:
        row = c.execute(db.listings.select().where(db.listings.c.id == "bina:6554081")).one()
        snaps = c.execute(db.snapshots.select().where(db.snapshots.c.listing_id == "bina:6554081")
                          .order_by(db.snapshots.c.observed_at)).fetchall()
        other = c.execute(db.listings.select().where(db.listings.c.id == "bina:1")).one()
    assert row.first_seen == seen and row.is_active and row.price_azn == 225000 and row.lat == 40.41
    assert [(s.origin, s.price_azn) for s in snaps] == [("archive", 210000), ("live", 225000)]
    assert not other.is_active and other.delisted_at is None


def test_enrichment_survives_rescrape(tmp_path):
    engine = db.get_engine(f"sqlite:///{tmp_path / 'n.db'}")
    sec = {"deal": "sale", "building": "new"}
    rec = build_record("bina", "7", "https://bina.az/items/7", "100 000 AZN 50 m² Elmlər Akademiyası m.", sec)
    t0 = datetime(2026, 10, 18)
    db.save_listings(engine, [rec], now=t0)
    db.apply_enrichment(engine, "bina:7", {"district": "Yasamal", "metro": "Elmlər Akademiyası"})
    db.save_listings(engine, [rec], now=t0 + timedelta(hours=6))
    with engine.connect() as c:
        row = c.execute(db.listings.select().where(db.listings.c.id == "bina:7")).one()
    assert row.district == "Yasamal" and row.district_inferred is False and row.enriched_at is not None


def test_backfill_queue_survives_index_outage(tmp_path, monkeypatch):
    from tools import backfill_archive as bf
    engine = db.get_engine(f"sqlite:///{tmp_path / 'q.db'}")
    cdx = tmp_path / "cdx.json"
    cdx.write_text(json.dumps([["timestamp", "original"],
                               ["20250301000000", "https://bina.az/items/1"],
                               ["20250315000000", "https://bina.az/items/1"],     # same ad, same month: one copy
                               ["20250401000000", "https://bina.az/items/1"],
                               ["20260601000000", "https://bina.az/items/2"],
                               ["20260601000000", "https://bina.az/"]]))
    assert bf.seed(engine, str(cdx)) == 3
    assert bf.seed(engine, str(cdx)) == 0                                      # idempotent

    def down(*a, **k):
        raise bf.IndexDown("503")
    monkeypatch.setattr(bf, "cdx_month", down)
    monkeypatch.setattr(bf.time, "sleep", lambda s: None)
    assert bf.candidates(None, 24, log=lambda *a: None) == []                  # gives up after 3 months
