"""Scrape all configured sections and store results.

    python -m scraper.run                 # all sources
    python -m scraper.run --source bina   # one source
    python -m scraper.run --check         # 1 page per section, print parsed rows, no DB writes
"""
import argparse
import sys
import traceback

from . import db
from .config import MAX_PAGES, REQUEST_DELAY, SECTIONS
from .parse import build_record
from .sources import bina, yeniemlak

SCRAPERS = {
    "bina": lambda sec, pages: bina.scrape(sec, max_items=pages * 24, delay=REQUEST_DELAY),
    "yeniemlak": lambda sec, pages: yeniemlak.scrape(sec, max_pages=pages, delay=REQUEST_DELAY),
}
FIELDS = ("price_azn", "area_m2", "rooms", "floor", "district", "metro", "building")


def check_report(records, cards):
    n = len(records)
    print(f"  usable {n}/{len(cards)} cards")
    if not n:
        if cards:
            print("  first raw card text:\n   ", cards[0]["text"][:300].replace("\n", " | "))
        return
    print("  field coverage: " + ", ".join(f"{f} {sum(r[f] is not None for r in records) / n:.0%}" for f in FIELDS))
    for r in records[:5]:
        print("   ", {k: r[k] for k in FIELDS}, r["url"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=list(SECTIONS), action="append")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    args = ap.parse_args(argv)
    pages = 1 if args.check else args.max_pages
    engine = None if args.check else db.get_engine()
    failures = 0

    for source in args.source or list(SECTIONS):
        for sec in SECTIONS[source]:
            key = f"{source}:{sec['deal']}:{sec.get('building') or 'any'}"
            print(f"[{key}] {sec['url']}")
            run_id = None if args.check else db.start_run(engine, source, key)
            started = db.utcnow()
            try:
                cards, reached_end = SCRAPERS[source](sec, pages)
                records = [r for c in cards if (r := build_record(source, c["id"], c["url"], c["text"], sec))]
                if args.check:
                    check_report(records, cards)
                    continue
                stats = db.save_listings(engine, records, now=started)
                stats["delisted"] = db.mark_delisted(engine, key, started, len(records)) if reached_end else 0
                db.finish_run(engine, run_id, "ok", **stats)
                print(f"  saved: {stats}")
            except Exception as e:  # keep going with the other sections
                failures += 1
                traceback.print_exc()
                if run_id:
                    db.finish_run(engine, run_id, "error", error=f"{type(e).__name__}: {e}"[:500])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
