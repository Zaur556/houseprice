"""Read bina.az ad pages for what the listing card doesn't show: district and renovation.

    python -m scraper.enrich               # up to ENRICH_MAX (1500) ad pages, newest first
    python -m scraper.enrich --max 50

bina.az cards often show only a metro station or settlement, and never whether the flat
is renovated. The ad page has the site's own data for both ("nearestLocations",
"hasRepair"), so one plain HTTP request per ad settles them for good. Each listing is
fetched once; listings without a district go first.
"""
import argparse
import os
import sys
import time
from datetime import datetime

import requests
from sqlalchemy import select

from . import db
from .config import PROXY_URL, REQUEST_DELAY, USER_AGENT
from .sources.bina_item import parse_item

# Pages read before renovation was collected are read once more for it.
RENOVATION_SINCE = datetime(2026, 10, 23, 14)
ENRICH_MAX = int(os.getenv("ENRICH_MAX", "2500"))   # ~1,400 new bina ads per run, plus the backlog


def pending(engine, limit: int) -> list[tuple[str, str]]:
    L = db.listings
    no_district = (L.c.district.is_(None)) | (L.c.district_inferred.is_(True))
    q = (select(L.c.id, L.c.url)
         .where(L.c.source == "bina", L.c.is_active,
                L.c.enriched_at.is_(None) | (L.c.renovated.is_(None) & (L.c.enriched_at < RENOVATION_SINCE)))
         .order_by(no_district.desc(), L.c.first_seen.desc()).limit(limit))
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
    print(f"enrich: {len(todo)} bina ad pages to read")
    done = fixed = renov = errors = 0
    delay = max(0.5, REQUEST_DELAY / 6)    # ad pages answer in ~0.3 s
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
            renov += bool(item and item.get("renovated") is not None)
        except requests.RequestException as e:
            errors += 1
            print(f"  {url}: {e}")
            if errors >= 10:
                break
        time.sleep(delay)
    print(f"enrich: read {done} ad pages, district found for {fixed}, renovation for {renov}, {errors} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
