"""Build the dashboard payload from the database.

    python -m analytics.export               # write dashboard/data.json and publish to the DB
    python -m analytics.export --no-file     # publish only (what CI does)

The payload is a compact, column-oriented copy of the cleaned listings and their price
histories (no titles, descriptions, phone numbers or exact coordinates). The dashboard
computes every statistic from it in the browser, so any combination of filters works
instantly. It is stored in dashboard_exports and served by api/data.js.

Cleaning done here, once:
- areas outside 15–600 m² and prices per m² outside PPM2_RANGE are dropped;
- the same flat posted on both sites is kept once (cross_source_duplicates);
- districts missing from an ad are inferred from its metro station or settlement;
- each listing is pinned to a map place: its metro station, else its settlement, else its district.
"""
import argparse
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from scraper import db
from scraper.config import DASHBOARD_DATA, ROOT
from scraper.parse import METRO_DISTRICT, SETTLEMENT_DISTRICT, norm

PPM2_RANGE = {"sale": (250, 25000), "rent": (1.5, 150)}   # AZN per m² (rent: per month)
EPOCH = datetime(2024, 1, 1)                              # day numbers in the payload count from here
PLACES = ROOT / "dashboard" / "geo" / "places.json"
SOURCES = ["bina", "yeniemlak"]
CURRENT_DAYS = 3        # a listing seen in the last 3 days counts as online now


def load(engine):
    L = pd.read_sql_table("listings", engine)
    S = pd.read_sql_table("price_snapshots", engine)
    R = pd.read_sql_table("scrape_runs", engine)
    for col in ("first_seen", "last_seen", "delisted_at"):
        L[col] = pd.to_datetime(L[col])
    S["observed_at"] = pd.to_datetime(S["observed_at"])
    S["origin"] = S["origin"].fillna("live") if "origin" in S else "live"
    return L, S, R


def cross_source_duplicates(L):
    """Same flat posted on two sites: same deal, rooms, area, floor, building height and location."""
    k = L.dropna(subset=["rooms", "area_m2", "floor", "floors_total"]).copy()
    k["loc"] = k.district.fillna(k.metro)
    k = k.dropna(subset=["loc"])
    if k.empty:
        return []
    k["key"] = (k.deal + "|" + k.rooms.astype(int).astype(str) + "|" + k.area_m2.round().astype(int).astype(str)
                + "|" + k.floor.astype(int).astype(str) + "|" + k.floors_total.astype(int).astype(str) + "|" + k["loc"])
    cnt = k.groupby(["key", "source"]).size().reset_index(name="n")
    multi = cnt.groupby("key").source.nunique()
    multi = multi[multi > 1].index
    if not len(multi):
        return []
    keep = (cnt[cnt.key.isin(multi)].sort_values(["key", "n", "source"], ascending=[True, False, True])
            .drop_duplicates("key").set_index("key").source)
    dup = k[k.key.isin(multi)]
    return dup[dup.source != dup.key.map(keep)].id.tolist()


def prepare(L, S):
    L = L[(L.area_m2 >= 15) & (L.area_m2 <= 600) & L.deal.isin(PPM2_RANGE) & L.source.isin(SOURCES)].copy()
    L["building"] = L["building"].where(L["building"].isin(["new", "old"]), None)
    L = L[~L.id.isin(cross_source_duplicates(L))]

    # Snapshots: drop impossible prices per m², keep the last one per listing per day.
    S = S.merge(L[["id", "area_m2", "deal"]].rename(columns={"id": "listing_id"}), on="listing_id")
    ppm2 = S.price_azn / S.area_m2
    lo, hi = S.deal.map(lambda d: PPM2_RANGE[d][0]), S.deal.map(lambda d: PPM2_RANGE[d][1])
    S = S[(ppm2 >= lo) & (ppm2 <= hi)].copy()
    S["day"] = (S.observed_at - EPOCH).dt.days
    S = S.sort_values(["listing_id", "observed_at"]).drop_duplicates(["listing_id", "day"], keep="last")
    L = L[L.id.isin(S.listing_id)]

    # Infer missing districts from metro station or settlement (marked as inferred).
    need = L.district.isna()
    guess = L.settlement.map(lambda s: SETTLEMENT_DISTRICT.get(norm(s)) if isinstance(s, str) else None)
    guess = guess.fillna(L.metro.map(METRO_DISTRICT))
    L.loc[need & guess.notna(), "district_inferred"] = True
    L.loc[need, "district"] = guess[need]
    return L, S


def place_index(L, places):
    """Pin each listing to a map place: metro station, else settlement, else district centre."""
    by = {(p["kind"], p.get("key") or p["name"]): i for i, p in enumerate(places)}

    def pick(row):
        if isinstance(row.metro, str) and ("metro", row.metro) in by:
            return by[("metro", row.metro)]
        if isinstance(row.settlement, str) and ("settlement", norm(row.settlement)) in by:
            return by[("settlement", norm(row.settlement))]
        if isinstance(row.district, str) and ("district", row.district) in by:
            return by[("district", row.district)]
        return -1
    return L.apply(pick, axis=1) if len(L) else pd.Series(dtype=int)


def _codes(values, vocab):
    pos = {v: i for i, v in enumerate(vocab)}
    return [pos.get(v, -1) if isinstance(v, str) else -1 for v in values]


def _ints(s, missing=-1):
    return [int(v) if pd.notna(v) else missing for v in s]


def build(engine) -> dict:
    L, S, R = load(engine)
    demo = bool((R.source == "demo").any()) if len(R) else False
    places = json.loads(PLACES.read_text(encoding="utf-8"))["places"] if PLACES.exists() else []
    meta = {"generated_at": db.utcnow().isoformat(), "epoch": EPOCH.date().isoformat(), "demo": demo,
            "ppm2_range": PPM2_RANGE, "current_days": CURRENT_DAYS}
    if L.empty:
        return {"meta": {**meta, "empty": True}, "sources": [], "places": places}

    L, S = prepare(L, S)
    now = L.last_seen.max()
    L = L.sort_values("id").reset_index(drop=True)
    order = {lid: i for i, lid in enumerate(L.id)}
    S = S.assign(li=S.listing_id.map(order)).sort_values(["li", "day"])
    so = pd.Series(range(len(S)), index=S.li.values).groupby(level=0).min()
    sn = S.groupby("li").size()

    districts = sorted(L.district.dropna().unique())
    metros = sorted(L.metro.dropna().unique())
    current = L.is_active & (L.last_seen >= now - timedelta(days=CURRENT_DAYS))
    pl = place_index(L, places)
    sid = L.source_id.astype(str)
    multi = L.index.isin(sn[sn > 1].index)
    urls = {int(i): u for i, u in zip(L.index[multi & (L.source != "bina")], L.url[multi & (L.source != "bina")])}

    live = S[S.origin == "live"]
    meta.update({
        "data_through": now.isoformat(), "today": int((now - EPOCH).days),
        "listings_tracked": int(len(L)), "online_now": int(current.sum()),
        "live_since": int(live.day.min()) if len(live) else None,
        "archive_snapshots": int((S.origin == "archive").sum()),
        "archive_listings": int(S[S.origin == "archive"].listing_id.nunique()),
        "first_day": int(S.day.min()),
    })
    data = {
        "meta": meta,
        "dict": {"district": districts, "metro": metros, "source": SOURCES},
        "places": places,
        "L": {
            "src": _codes(L.source, SOURCES),
            "sid": [int(x) if x.isdigit() else -1 for x in sid],
            "deal": [0 if d == "sale" else 1 for d in L.deal],
            "b": [0 if b == "new" else 1 if b == "old" else -1 for b in L.building],
            "r": _ints(L.rooms, 0),
            "a": [round(float(a), 1) for a in L.area_m2],
            "fl": _ints(L.floor, 0), "ft": _ints(L.floors_total, 0),
            "di": _codes(L.district, districts),
            "dx": [1 if x is True else 0 for x in L.district_inferred],
            "mi": _codes(L.metro, metros),
            "pl": [int(x) for x in pl],
            "t0": [int(x) for x in (L.first_seen - EPOCH).dt.days],
            "t1": [int(x) for x in (L.last_seen - EPOCH).dt.days],
            "on": [1 if x else 0 for x in current],
            "rn": [1 if x is True else 0 if x is False else -1 for x in
                   (L.renovated if "renovated" in L else [None] * len(L))],
            "gone": [1 if (not a) and pd.notna(d) else 0 for a, d in zip(L.is_active, L.delisted_at)],
            "so": [int(so.get(i, 0)) for i in L.index], "sn": [int(sn.get(i, 0)) for i in L.index],
        },
        "S": {"t": S.day.astype(int).tolist(), "v": [int(round(v)) for v in S.price_azn],
              "o": [1 if o == "archive" else 0 for o in S.origin]},
        "urls": urls,
    }
    sources = []
    real = R[R.source != "demo"]
    for src, g in real.groupby("source"):
        ok = g[g.status == "ok"]
        last = g.sort_values("started_at").iloc[-1]
        sources.append({"source": src, "last_ok": ok.finished_at.max().isoformat() if len(ok) else None,
                        "last_status": last.status, "last_error": last.error if isinstance(last.error, str) else None,
                        "active": int(((L.source == src) & current).sum())})
    data["sources"] = sources
    return data


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-file", action="store_true", help="skip writing dashboard/data.json")
    ap.add_argument("--no-publish", action="store_true", help="skip storing the export for /api/data")
    args = ap.parse_args(argv)

    engine = db.get_engine()
    data = build(engine)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)
    if not args.no_file:
        DASHBOARD_DATA.parent.mkdir(parents=True, exist_ok=True)
        DASHBOARD_DATA.write_text(payload, encoding="utf-8")
        print(f"wrote {DASHBOARD_DATA} ({DASHBOARD_DATA.stat().st_size / 1024:.0f} KB)")
    if not args.no_publish:
        export_id = db.publish_export(engine, payload)
        print(f"published export #{export_id} ({len(payload.encode()) / 1024:.0f} KB) for /api/data")


if __name__ == "__main__":
    main()
