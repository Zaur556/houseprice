from datetime import timedelta

from scraper import db
from scraper.parse import build_record
from scraper.sources.yeniemlak import _cards

SEC = {"deal": "sale", "building": None}
HTML = """<html><body><div class="list">
<div class="ad"><a href="/elan/satilir-2-otaqli-bina-evi-menzil-yasamal-rayonu-111111"><img></a>
  <div>125 000 AZN</div><div>65 m²</div><div>Mərtəbə: 5/12</div><a href="/elan/satilir-2-otaqli-bina-evi-menzil-yasamal-rayonu-111111">Ətraflı</a></div>
<div class="ad"><a href="/elan/satilir-3-otaqli-bina-evi-menzil-nesimi-rayonu-222222">x</a>
  <div>210 000 AZN</div><div>95 m²</div><div>Yeni tikili</div></div>
</div></body></html>"""


def test_yeniemlak_cards_are_separated():
    cards = {c["id"]: c for c in _cards(HTML)}
    assert set(cards) == {"111111", "222222"}
    assert "125 000" in cards["111111"]["text"] and "210 000" not in cards["111111"]["text"]
    r = build_record("yeniemlak", "222222", cards["222222"]["url"], cards["222222"]["text"], SEC)
    assert r["district"] == "Nəsimi" and r["rooms"] == 3 and r["building"] == "new"


def test_snapshots_and_delisting(tmp_path):
    engine = db.get_engine(f"sqlite:///{tmp_path / 't.db'}")
    t0 = db.utcnow()
    a = build_record("bina", "1", "u1", "100 000 AZN 50 m² Nəsimi r.", SEC)
    b = build_record("bina", "2", "u2", "200 000 AZN 80 m² Yasamal r.", SEC)
    assert db.save_listings(engine, [a, b], now=t0) == {"found": 2, "new": 2, "price_changes": 0}

    a2 = build_record("bina", "1", "u1", "95 000 AZN 50 m² Nəsimi r.", SEC)
    t1 = t0 + timedelta(hours=6)
    assert db.save_listings(engine, [a2], now=t1)["price_changes"] == 1
    assert db.mark_delisted(engine, a["section"], t1, found=1) == 0       # 1 of 2 found: guard holds
    a3 = build_record("bina", "1", "u1", "95 000 AZN 50 m² Nəsimi r.", SEC)
    c = build_record("bina", "3", "u3", "120 000 AZN 60 m² Xətai r.", SEC)
    t2 = t1 + timedelta(hours=6)
    db.save_listings(engine, [a3, c], now=t2)
    assert db.mark_delisted(engine, a["section"], t2, found=2) == 1        # ad 2 gone
    with engine.connect() as conn:
        snaps = conn.execute(db.snapshots.select()).fetchall()
        gone = conn.execute(db.listings.select().where(db.listings.c.id == "bina:2")).one()
    assert len(snaps) == 4 and not gone.is_active
