"""Collect bina.az residential complexes (developer prices for new builds).

    python -m scraper.complexes            # list page every run, complex pages every 3 days
    python -m scraper.complexes --all      # read every complex page now

The list page gives every complex's current "from" price per m², so one request per run
keeps prices current. Complex pages (map pin, district, layouts) change rarely and are
read when missing or older than DETAIL_DAYS.
"""
import argparse
import sys
import time
from datetime import timedelta

import requests
from sqlalchemy import select

from . import db
from .config import PROXY_URL, USER_AGENT
from .sources.complexes import BASE, parse_detail, parse_list

DETAIL_DAYS = 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="read every complex page, however recent")
    args = ap.parse_args(argv)
    engine = db.get_engine()
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "az,en;q=0.8"})
    if PROXY_URL:
        s.proxies.update({"http": PROXY_URL, "https": PROXY_URL})

    now = db.utcnow()
    r = s.get(f"{BASE}/yasayis-kompleksleri", timeout=30)
    r.raise_for_status()
    rows = parse_list(r.text)
    if not rows:
        print("complexes: list page parsed to 0 complexes; the layout may have changed")
        return 1
    changed = sum(db.save_complex(engine, c, now) for c in rows)
    print(f"complexes: {len(rows)} on the list page, {changed} price changes")

    with engine.connect() as c:
        fresh = {x.slug for x in c.execute(select(db.complexes.c.slug).where(
            db.complexes.c.detail_at >= now - timedelta(days=DETAIL_DAYS)))}
    todo = [c["slug"] for c in rows if args.all or c["slug"] not in fresh]
    read = 0
    for slug in todo:
        time.sleep(1.0)
        try:
            p = s.get(f"{BASE}/yasayis-kompleksleri/{slug}", timeout=30)
            d = parse_detail(p.text, slug) if p.ok else None
        except requests.RequestException as e:
            print(f"  {slug}: {e}")
            continue
        if d:
            db.save_complex(engine, d, db.utcnow(), detail=True)
            read += 1
    print(f"complexes: read {read} of {len(todo)} complex pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
