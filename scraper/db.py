"""Storage: listings (one row per ad), price_snapshots (one row per price change), scrape_runs."""
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, Float, Index, Integer, MetaData, String, Table, Text,
                        bindparam, case, create_engine, func, insert, inspect, select, text, update)

from .config import DATABASE_URL

metadata = MetaData()

listings = Table(
    "listings", metadata,
    Column("id", String(64), primary_key=True),          # "<source>:<source_id>"
    Column("source", String(20), nullable=False, index=True),
    Column("source_id", String(32), nullable=False),
    Column("section", String(40), index=True),
    Column("url", Text),
    Column("deal", String(8), index=True),                # sale | rent
    Column("building", String(8)),                        # new | old | NULL
    Column("rooms", Integer),
    Column("area_m2", Float),
    Column("floor", Integer),
    Column("floors_total", Integer),
    Column("district", String(40)),
    Column("district_inferred", Boolean, default=False),
    Column("metro", String(40)),
    Column("settlement", String(60)),
    Column("title", Text),
    Column("price_azn", Float),                           # current price
    Column("first_seen", DateTime, nullable=False),
    Column("last_seen", DateTime, nullable=False),
    Column("is_active", Boolean, default=True, index=True),
    Column("delisted_at", DateTime),
    Column("lat", Float),                                 # only when the ad page gives coordinates
    Column("lng", Float),
    Column("enriched_at", DateTime),                      # ad page read (district etc. from the source's own data)
)

snapshots = Table(
    "price_snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("listing_id", String(64), nullable=False, index=True),
    Column("observed_at", DateTime, nullable=False, index=True),
    Column("price", Float),
    Column("currency", String(4)),
    Column("price_azn", Float),
    Column("origin", String(8), default="live"),          # live (our scrape) | archive (archive.org copy)
    Index("ix_snap_listing_time", "listing_id", "observed_at"),
)

# Archived ad pages known to exist (from archive.org's CDX index), saved so the backfill
# can keep working when that index is down.
archive_queue = Table(
    "archive_queue", metadata,
    Column("key", String(120), primary_key=True),         # "<timestamp> <url>", same as archive_fetches
    Column("ts", String(14), nullable=False),
    Column("url", Text, nullable=False),
)

# Archived pages already processed by tools.backfill_archive, so reruns resume.
archive_fetches = Table(
    "archive_fetches", metadata,
    Column("key", String(120), primary_key=True),         # "<timestamp> <url>"
    Column("status", String(12)),                         # ok | skipped | error
    Column("fetched_at", DateTime),
)

runs = Table(
    "scrape_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(20)),
    Column("section", String(40)),
    Column("started_at", DateTime),
    Column("finished_at", DateTime),
    Column("status", String(10)),        # running | ok | error
    Column("found", Integer),
    Column("new", Integer),
    Column("price_changes", Integer),
    Column("delisted", Integer),
    Column("error", Text),
)

# Finished dashboard payloads. The Vercel function /api/data serves the newest row,
# so the live site updates as soon as an export lands, without a redeploy.
exports = Table(
    "dashboard_exports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("generated_at", DateTime, nullable=False, index=True),
    Column("payload", Text, nullable=False),              # data.json as text, served verbatim
)
KEEP_EXPORTS = 28    # one week of 6-hourly exports

ATTRS = ("source", "source_id", "section", "url", "deal", "building", "rooms", "area_m2", "floor",
         "floors_total", "district", "district_inferred", "metro", "settlement", "title")
LOCATION = ("district", "metro", "settlement")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def get_engine(url: str = DATABASE_URL):
    engine = create_engine(url, pool_pre_ping=True)
    metadata.create_all(engine)
    migrate(engine)
    return engine


def migrate(engine):
    """Add columns and indexes introduced after a table was first created (create_all skips those)."""
    insp = inspect(engine)
    with engine.begin() as c:
        for table in (listings, snapshots):
            have = {col["name"] for col in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    ddl = col.type.compile(engine.dialect)
                    c.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}'))
        if "ix_snap_listing_time" not in {i["name"] for i in insp.get_indexes("price_snapshots")}:
            c.execute(text("CREATE INDEX IF NOT EXISTS ix_snap_listing_time ON price_snapshots (listing_id, observed_at)"))


def start_run(engine, source: str, section: str) -> int:
    with engine.begin() as c:
        res = c.execute(insert(runs).values(source=source, section=section, started_at=utcnow(), status="running"))
        return res.inserted_primary_key[0]


def finish_run(engine, run_id: int, status: str, error: str | None = None, **stats):
    with engine.begin() as c:
        c.execute(update(runs).where(runs.c.id == run_id).values(
            finished_at=utcnow(), status=status, error=error, **stats))


def save_listings(engine, records: list[dict], now: datetime | None = None) -> dict:
    """Upsert listings; write a snapshot for new listings and for every price change."""
    now = now or utcnow()
    records = list({r["id"]: r for r in records}.values())   # de-dup within a batch
    stats = {"found": len(records), "new": 0, "price_changes": 0}
    if not records:
        return stats
    with engine.begin() as c:
        existing = {}
        ids = [r["id"] for r in records]
        for i in range(0, len(ids), 500):
            rows = c.execute(select(listings.c.id, listings.c.price_azn).where(listings.c.id.in_(ids[i:i + 500])))
            existing.update({row.id: row.price_azn for row in rows})

        new_rows, upd_rows, snaps = [], [], []
        for r in records:
            snap = {"listing_id": r["id"], "observed_at": now, "price": r["price"],
                    "currency": r["currency"], "price_azn": r["price_azn"], "origin": "live"}
            if r["id"] not in existing:
                new_rows.append({**{k: r.get(k) for k in ATTRS}, "id": r["id"], "price_azn": r["price_azn"],
                                 "first_seen": now, "last_seen": now, "is_active": True})
                snaps.append(snap)
                continue
            if existing[r["id"]] is None or abs(existing[r["id"]] - r["price_azn"]) > 0.5:
                snaps.append(snap)
                stats["price_changes"] += 1
            upd = {f"v_{k}": r.get(k) for k in ATTRS}
            upd.update(b_id=r["id"], v_price_azn=r["price_azn"], v_last_seen=now)
            upd_rows.append(upd)

        if new_rows:
            c.execute(insert(listings), new_rows)
        if upd_rows:
            # Card text can be sparser than the ad page: never blank out a known value, and keep
            # the location read from the ad page (enrich) over one guessed from the card.
            values = {k: func.coalesce(bindparam(f"v_{k}"), listings.c[k]) for k in ATTRS}
            for k in LOCATION:
                values[k] = case((listings.c.enriched_at.is_not(None), listings.c[k]), else_=values[k])
            values["district_inferred"] = case(
                (listings.c.enriched_at.is_not(None), listings.c.district_inferred),
                else_=bindparam("v_district_inferred"))
            stmt = update(listings).where(listings.c.id == bindparam("b_id")).values(
                **values, price_azn=bindparam("v_price_azn"), last_seen=bindparam("v_last_seen"),
                is_active=True, delisted_at=None)
            c.execute(stmt, upd_rows)
        if snaps:
            c.execute(insert(snapshots), snaps)
        stats["new"] = len(new_rows)
    return stats


def apply_enrichment(engine, listing_id: str, item: dict | None, now: datetime | None = None):
    """Store what an ad page says about location. item=None marks the page as tried (gone or unreadable)."""
    vals = {"enriched_at": now or utcnow()}
    if item:
        for k in ("district", "metro", "settlement", "lat", "lng", "floor", "floors_total", "rooms"):
            if item.get(k) is not None:
                vals[k] = item[k]
        if item.get("district"):
            vals["district_inferred"] = False
    with engine.begin() as c:
        c.execute(update(listings).where(listings.c.id == listing_id).values(**vals))


def save_archived(engine, source: str, p: dict, observed_at: datetime) -> str:
    """Merge one archived copy of an ad: a snapshot dated when archive.org saw it, and the listing
    row widened to cover that date. Returns "new", "merged" or "duplicate"."""
    lid = f"{source}:{p['id']}"
    with engine.begin() as c:
        row = c.execute(select(listings.c.first_seen, listings.c.last_seen, listings.c.is_active)
                        .where(listings.c.id == lid)).first()
        dup = c.execute(select(func.count()).select_from(snapshots).where(
            snapshots.c.listing_id == lid, snapshots.c.observed_at == observed_at)).scalar_one()
        if dup:
            return "duplicate"
        if row is None:
            c.execute(insert(listings).values(
                id=lid, source=source, source_id=str(p["id"]),
                section=f"{source}:{p['deal']}:archive", url=f"https://bina.az/items/{p['id']}",
                deal=p["deal"], building=p.get("building"), rooms=p.get("rooms"), area_m2=p.get("area_m2"),
                floor=p.get("floor"), floors_total=p.get("floors_total"), district=p.get("district"),
                district_inferred=False, metro=p.get("metro"), settlement=p.get("settlement"),
                lat=p.get("lat"), lng=p.get("lng"), price_azn=p["price_azn"],
                first_seen=observed_at, last_seen=observed_at, is_active=False, delisted_at=None))
            status = "new"
        else:
            vals = {"first_seen": min(row.first_seen, observed_at)}
            if not row.is_active:
                vals["last_seen"] = max(row.last_seen, observed_at)
                if observed_at >= row.last_seen:
                    vals["price_azn"] = p["price_azn"]
            for k in ("lat", "lng"):
                if p.get(k) is not None:
                    vals[k] = p[k]
            c.execute(update(listings).where(listings.c.id == lid).values(**vals))
            status = "merged"
        c.execute(insert(snapshots).values(listing_id=lid, observed_at=observed_at, price=p["price"],
                                           currency=p["currency"], price_azn=p["price_azn"], origin="archive"))
        return status


def publish_export(engine, payload: str, now: datetime | None = None) -> int:
    """Store a dashboard payload and prune all but the newest KEEP_EXPORTS rows."""
    with engine.begin() as c:
        res = c.execute(insert(exports).values(generated_at=now or utcnow(), payload=payload))
        new_id = res.inserted_primary_key[0]
        keep = select(exports.c.id).order_by(exports.c.id.desc()).limit(KEEP_EXPORTS)
        c.execute(exports.delete().where(exports.c.id.not_in(keep)))
        return new_id


def latest_export(engine) -> str | None:
    with engine.connect() as c:
        return c.execute(select(exports.c.payload).order_by(exports.c.id.desc()).limit(1)).scalar()


def mark_delisted(engine, section: str, run_started: datetime, found: int) -> int:
    """After a full sweep of a section, mark ads that were not seen as delisted.

    Skipped when the sweep found far fewer ads than are active, which usually
    means the scrape was cut short (blocking, layout change), not a market crash.
    """
    with engine.begin() as c:
        active = c.execute(select(func.count()).select_from(listings).where(
            listings.c.section == section, listings.c.is_active)).scalar_one()
        if active and found < 0.6 * active:
            return 0
        res = c.execute(update(listings).where(
            listings.c.section == section, listings.c.is_active, listings.c.last_seen < run_started,
        ).values(is_active=False, delisted_at=listings.c.last_seen))
        return res.rowcount or 0
