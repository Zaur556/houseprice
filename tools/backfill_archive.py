"""Backfill price history from archive.org copies of bina.az ad pages.

    python -m tools.backfill_archive                    # last 24 months
    python -m tools.backfill_archive --months 6 --limit 200
    python -m tools.backfill_archive --dry-run          # list what would be fetched

archive.org keeps copies of individual bina.az ads (/items/<id>), each showing the
asking price on the day it was archived. Every usable copy (a Baku apartment for sale
or monthly rent) becomes a snapshot with origin="archive", dated when it was archived
(or when the ad expired, if that was earlier). The same ad archived in several months
gives a real price history, and ads still online today join up with the live scrape.

Copies already processed are recorded in archive_fetches, so the job can be stopped
and rerun. Archived copies are a sample of what was online (a few hundred a month),
not a full sweep, and the dashboard says so.
"""
import argparse
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests
from sqlalchemy import insert, select

from scraper import db
from scraper.config import USER_AGENT
from scraper.sources.bina_item import parse_item, usable

CDX = "https://web.archive.org/cdx/search/cdx"
ID_RE = re.compile(r"bina\.az/items/(\d+)(?:\.html)?/?$")
UA = {"User-Agent": USER_AGENT + " (price history backfill)"}


class Pace:
    """At most one request per `interval` seconds across threads; back off on 429."""

    def __init__(self, interval: float):
        self.interval, self.next, self.lock = interval, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.interval
        time.sleep(max(0.0, t - now))

    def slow_down(self, seconds: float):
        with self.lock:
            self.next = max(self.next, time.monotonic() + seconds)


def cdx_month(s: requests.Session, start: datetime, end: datetime, log=print) -> list[tuple[str, str]]:
    """(timestamp, url) for archived ad pages between two dates; splits the range if CDX times out."""
    params = {"url": "bina.az/items/*", "output": "json", "fl": "timestamp,original",
              "filter": ["statuscode:200", "mimetype:text/html"],
              "from": start.strftime("%Y%m%d"), "to": (end - timedelta(days=1)).strftime("%Y%m%d")}
    for attempt in range(3):
        try:
            r = s.get(CDX, params=params, timeout=120)
            if r.ok:
                rows = r.json()
                return [(t, u) for t, u in rows[1:]] if rows else []
        except (requests.RequestException, ValueError):
            pass
        time.sleep(10 * (attempt + 1))
    if (end - start).days > 3:
        mid = start + (end - start) / 2
        mid = datetime(mid.year, mid.month, mid.day)
        return cdx_month(s, start, mid, log) + cdx_month(s, mid, end, log)
    log(f"  CDX failed for {start:%Y-%m-%d}..{end:%Y-%m-%d}, skipping")
    return []


def candidates(s, months: int, log=print) -> list[tuple[str, str, str]]:
    """One copy per ad per calendar month: (key, timestamp, url)."""
    today = db.utcnow()
    first = datetime(today.year, today.month, 1)
    starts = []
    for i in range(months, -1, -1):
        y, m = first.year, first.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        starts.append(datetime(y, m, 1))
    picked = {}
    for a in starts:
        b = datetime(a.year + (a.month == 12), a.month % 12 + 1, 1)
        rows = cdx_month(s, a, min(b, today + timedelta(days=1)), log)
        n = 0
        for ts, url in rows:
            m = ID_RE.search(url.split("?")[0])
            if m and (m.group(1), ts[:6]) not in picked:
                picked[(m.group(1), ts[:6])] = (f"{ts} {url}"[:120], ts, url)
                n += 1
        log(f"  {a:%Y-%m}: {len(rows)} archived ad pages, {n} distinct ads")
    return list(picked.values())


def fetch(s, pace: Pace, ts: str, url: str):
    for attempt in range(4):
        pace.wait()
        try:
            r = s.get(f"https://web.archive.org/web/{ts}id_/{url}", timeout=60)
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            pace.slow_down(60 * (attempt + 1))
            continue
        return r.text if r.ok else None
    raise RuntimeError("archive.org kept refusing")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0, help="stop after this many pages (0 = all)")
    # archive.org throttles at roughly 15 page requests a minute per address; going faster triggers
    # long back-offs and ends up slower (3 workers at 0.7 s managed ~2 pages a minute).
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--interval", type=float, default=4.5, help="seconds between requests, all workers")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    engine = db.get_engine()
    s = requests.Session()
    s.headers.update(UA)
    todo = candidates(s, args.months)
    with engine.connect() as c:
        done = {r.key for r in c.execute(select(db.archive_fetches.c.key))}
    todo = [t for t in todo if t[0] not in done]
    todo.sort(key=lambda t: t[1])
    if args.limit:
        todo = todo[:args.limit]
    print(f"backfill: {len(todo)} archived pages to read ({len(done)} done in earlier runs)")
    if args.dry_run:
        return 0

    pace = Pace(args.interval)
    stats = {"new": 0, "merged": 0, "duplicate": 0, "skipped": 0, "error": 0}
    started = time.monotonic()
    with ThreadPoolExecutor(args.workers) as pool:
        futs = {pool.submit(fetch, s, pace, ts, url): (key, ts, url) for key, ts, url in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            key, ts, url = futs[fut]
            status = "error"
            try:
                html = fut.result()
                p = parse_item(html) if html else None
                if usable(p):
                    seen = datetime.strptime(ts, "%Y%m%d%H%M%S")
                    if p.get("expired_at") and p["expired_at"] < seen:
                        seen = p["expired_at"]
                    status = db.save_archived(engine, "bina", p, seen)
                else:
                    status = "skipped"      # not a Baku apartment, daily rent, or unreadable
            except Exception as e:  # keep going; the page is retried on the next run
                print(f"  {url} @ {ts}: {type(e).__name__}: {e}")
            stats[status] = stats.get(status, 0) + 1
            if status != "error":
                with engine.begin() as c:
                    c.execute(insert(db.archive_fetches).values(key=key, status=status, fetched_at=db.utcnow()))
            if i % 100 == 0:
                rate = i / (time.monotonic() - started)
                print(f"  {i}/{len(todo)} pages, {rate * 60:.0f}/min, {stats}")
    print(f"backfill done: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
