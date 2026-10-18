"""Fill in location for bina.az listings whose card named no district.

    python -m scraper.enrich               # up to ENRICH_MAX ad pages, newest first
    python -m scraper.enrich --max 50

bina.az cards often show only a metro station or settlement. The ad page has the
site's own location data, including the district ("nearestLocations"), so one plain
HTTP request per ad fixes the district for good. Each listing is fetched once.
"""
import argparse
import os
import sys
import time

import requests
from sqlalchemy import select

from . import db
from .config import PROXY_URL, REQUEST_DELAY, USER_AGENT
from .sources.bina_item import parse_item

ENRICH_MAX = int(os.getenv("ENRICH_MAX", "600"))


def pending(engine, limit: int) -> list[tuple[str, str]]:
    L = db.listings
    q = (select(L.c.id, L.c.url)
         .where(L.c.source == "bina", L.c.is_active, L.c.enriched_at.is_(None),
                (L.c.district.is_(None)) | (L.c.district_inferred.is_(True)))
         .order_by(L.c.first_seen.desc()).limit(limit))
    with engine.connect() as c:
        return [(r.id, r.url) for r in c.execute(q)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=ENRICH_MAX)
    args = ap.parse_args(argv)
    engine = db.get_engine()
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "az,en;q=0.8"})
    if PROXY_URL:
        s.proxies.update({"http": PROXY_URL, "https": PROXY_URL})
    todo = pending(engine, args.max)
    print(f"enrich: {len(todo)} bina listings without a district")
    done = fixed = errors = 0
    delay = max(1.0, REQUEST_DELAY / 3)
    for lid, url in todo:
        try:
            r = s.get(url, timeout=30)
            if r.status_code == 429:
                print("  rate limited, stopping for this run")
                break
            item = parse_item(r.text) if r.ok else None
            db.apply_enrichment(engine, lid, item)
            done += 1
            fixed += bool(item and item.get("district"))
        except requests.RequestException as e:
            errors += 1
            print(f"  {url}: {e}")
            if errors >= 10:
                break
        time.sleep(delay)
    print(f"enrich: read {done} ad pages, district found for {fixed}, {errors} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
