"""Backfill developer price history for bina.az residential complexes from archive.org.

    python -m tools.backfill_complexes            # pages listed in tools/complex_archive.json
    python -m tools.backfill_complexes --cdx      # also look for newer copies in the CDX index

archive.org has copies of the complexes list page (every complex's "from" price per m²
on that day) and of single complex pages, from late 2024 on. Each copy becomes a
complex_prices row with origin="archive". Copies already read are recorded in
archive_fetches, so reruns only fetch what's new.
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from sqlalchemy import insert, select

from scraper import db
from scraper.sources.complexes import _slug, parse_detail, parse_list
from tools.backfill_archive import CDX, UA

SEED = Path(__file__).with_name("complex_archive.json")


def cdx_copies(s) -> list[tuple[str, str]]:
    try:
        r = s.get(CDX, params={"url": "bina.az/yasayis-kompleksleri*", "output": "json", "fl": "timestamp,original",
                               "filter": ["statuscode:200", "mimetype:text/html"]}, timeout=90)
        rows = r.json()[1:] if r.ok else []
    except (requests.RequestException, ValueError):
        rows = []
    return [(t, u) for t, u in rows if "residences_view=map" not in u]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdx", action="store_true", help="also ask archive.org's index for copies (often down)")
    args = ap.parse_args(argv)
    engine = db.get_engine()
    s = requests.Session()
    s.headers.update(UA)
    copies = {tuple(x) for x in json.loads(SEED.read_text(encoding="utf-8"))}
    if args.cdx:
        copies |= set(cdx_copies(s))
    with engine.connect() as c:
        done = {r.key for r in c.execute(select(db.archive_fetches.c.key).where(db.archive_fetches.c.key.like("rc %")))}
    todo = sorted((ts, url) for ts, url in copies if f"rc {ts} {url}"[:120] not in done)
    print(f"complex backfill: {len(todo)} archived pages to read")
    added = 0
    for ts, url in todo:
        time.sleep(4.5)                        # archive.org allows ~15 page requests a minute
        try:
            r = s.get(f"https://web.archive.org/web/{ts}id_/{url}", timeout=60)
        except requests.RequestException as e:
            print(f"  {url} @ {ts}: {e}")
            continue
        if r.status_code == 429 or r.status_code >= 500:
            print(f"  {url} @ {ts}: HTTP {r.status_code}, will retry next run")
            time.sleep(30)
            continue
        seen = datetime.strptime(ts, "%Y%m%d%H%M%S")
        slug = _slug(url)
        rows = [parse_detail(r.text, slug)] if slug else parse_list(r.text)
        rows = [x for x in rows if x]
        added += sum(db.save_complex(engine, x, seen, origin="archive") for x in rows)
        with engine.begin() as c:
            c.execute(insert(db.archive_fetches).values(key=f"rc {ts} {url}"[:120], status="ok" if rows else "skipped",
                                                        fetched_at=db.utcnow()))
    print(f"complex backfill: {added} archived prices added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
