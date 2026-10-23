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
and rerun. The list of archived pages from archive.org's CDX index is saved in
archive_queue, so a rerun still has work when that index is down (it often returns 503).
Pages are read newest first, so recent months fill in before older ones.

    python -m tools.backfill_archive --index skip         # use only the saved list
    python -m tools.backfill_archive --seed cdx.json      # add a saved CDX JSON export to the list Archived copies are a sample of what was online (a few hundred a month),
not a full sweep, and the dashboard says so.
"""
import argparse
import json
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


class IndexDown(Exception):
    pass


def cdx_month(s: requests.Session, start: datetime, end: datetime, log=print, split=True) -> list[tuple[str, str]]:
    """(timestamp, url) for archived ad pages between two dates; splits the range once if CDX times out."""
    params = {"url": "bina.az/items/*", "output": "json", "fl": "timestamp,original",
              "filter": ["statuscode:200", "mimetype:text/html"],
              "from": start.strftime("%Y%m%d"), "to": (end - timedelta(days=1)).strftime("%Y%m%d")}
    for attempt in range(2):
        try:
            r = s.get(CDX, params=params, timeout=90)
            if r.ok:
                rows = r.json()
                return [(t, u) for t, u in rows[1:]] if rows else []
        except (requests.RequestException, ValueError):
            pass
        time.sleep(10 * (attempt + 1))
    if split and (end - start).days > 10:
        mid = start + (end - start) / 2
        mid = datetime(mid.year, mid.month, mid.day)
        return cdx_month(s, start, mid, log, False) + cdx_month(s, mid, end, log, False)
    raise IndexDown(f"{start:%Y-%m-%d}..{end:%Y-%m-%d}")


def pick(rows, picked: dict) -> int:
    """Keep one copy per ad per calendar month. Returns how many were added."""
    n = 0
    for ts, url in rows:
        m = ID_RE.search(url.split("?")[0])
        if m and (m.group(1), ts[:6]) not in picked:
            picked[(m.group(1), ts[:6])] = (f"{ts} {url}"[:120], ts, url)
            n += 1
    return n


def save_queue(engine, items) -> int:
    """Add (key, ts, url) items to archive_queue; returns how many were new."""
    with engine.begin() as c:
        have = {r.key for r in c.execute(select(db.archive_queue.c.key))}
        new = [{"key": k, "ts": ts, "url": u} for k, ts, u in items if k not in have]
        for i in range(0, len(new), 1000):
            c.execute(insert(db.archive_queue), new[i:i + 1000])
    return len(new)


def seed(engine, path: str) -> int:
    """Load a CDX JSON export (rows of [timestamp, original, ...]) into the queue."""
    rows = json.loads(open(path, encoding="utf-8").read())
    if rows and rows[0] and rows[0][0] == "timestamp":
        rows = rows[1:]
    picked = {}
    pick([(r[0], r[1]) for r in rows if "/items/" in r[1]], picked)
    return save_queue(engine, picked.values())


def candidates(s, months: int, log=print) -> list[tuple[str, str, str]]:
    """One copy per ad per calendar month from the CDX index: (key, timestamp, url).
    Stops early when the index looks down, returning what it got."""
    today = db.utcnow()
    first = datetime(today.year, today.month, 1)
    starts = []
    for i in range(months, -1, -1):
        y, m = first.year, first.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        starts.append(datetime(y, m, 1))
    picked, failures = {}, 0
    for a in reversed(starts):                      # newest months first
        b = datetime(a.year + (a.month == 12), a.month % 12 + 1, 1)
        try:
            rows = cdx_month(s, a, min(b, today + timedelta(days=1)), log)
        except IndexDown as e:
            failures += 1
            log(f"  {a:%Y-%m}: CDX index unavailable ({e})")
            if failures >= 3:
                log("  CDX index looks down; carrying on with the saved list of archived pages")
                break
            continue
        failures = 0
        log(f"  {a:%Y-%m}: {len(rows)} archived ad pages, {pick(rows, picked)} distinct ads")
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
    ap.add_argument("--index", choices=["auto", "skip"], default="auto",
                    help="auto: refresh the page list from archive.org's CDX index first; skip: saved list only")
    ap.add_argument("--seed", help="CDX JSON export to add to the saved page list")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    engine = db.get_engine()
    s = requests.Session()
    s.headers.update(UA)
    if args.seed:
        print(f"backfill: {seed(engine, args.seed)} pages added to the saved list from {args.seed}")
    if args.index == "auto":
        print(f"backfill: {save_queue(engine, candidates(s, args.months))} new pages found in the CDX index")
    oldest = (db.utcnow() - timedelta(days=31 * args.months)).strftime("%Y%m")
    with engine.connect() as c:
        done = {r.key for r in c.execute(select(db.archive_fetches.c.key))}
        queue = [(r.key, r.ts, r.url) for r in c.execute(select(db.archive_queue))]
    todo = [q for q in queue if q[0] not in done and q[1][:6] >= oldest]
    todo.sort(key=lambda q: q[1], reverse=True)     # newest first: recent months matter most
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
