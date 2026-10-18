"""Build the dashboard payload from the database.

    python -m analytics.export               # write dashboard/data.json and publish to the DB
    python -m analytics.export --no-file     # publish only (what CI does)

All aggregation happens here. The payload is stored in the dashboard_exports
table, which the Vercel function api/data.js serves to the dashboard, so the
browser never queries listings directly.
"""
import argparse
import json
import math
from datetime import timedelta

import numpy as np
import pandas as pd

from scraper import db
from scraper.config import DASHBOARD_DATA

DEALS, BUILDINGS, ROOMS = ["sale", "rent"], ["all", "new", "old"], ["all", "1", "2", "3", "4"]
PPM2_RANGE = {"sale": (250, 25000), "rent": (1.5, 150)}   # AZN per m² (rent: per month)
WEEKS = 52
MIN_N = 8          # minimum listings for a median to be shown


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if math.isnan(o) or math.isinf(o) else round(float(o), 2)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if o is pd.NaT:
        return None
    return o


def _med(s, n=MIN_N):
    s = s.dropna()
    return float(s.median()) if len(s) >= n else None


def _pct(a, b):
    return (a / b - 1) * 100 if a is not None and b else None


def load(engine):
    L = pd.read_sql_table("listings", engine)
    S = pd.read_sql_table("price_snapshots", engine)
    R = pd.read_sql_table("scrape_runs", engine)
    for col in ("first_seen", "last_seen", "delisted_at"):
        L[col] = pd.to_datetime(L[col])
    S["observed_at"] = pd.to_datetime(S["observed_at"])
    return L, S, R


def prepare(L):
    L = L[(L.area_m2 >= 15) & (L.area_m2 <= 600)].copy()
    L["rooms_bucket"] = np.where(L.rooms >= 4, "4", L.rooms.astype("Int64").astype(str))
    L.loc[L.rooms.isna(), "rooms_bucket"] = None
    L["building"] = L["building"].where(L["building"].isin(["new", "old"]), None)
    return L[~L.id.isin(cross_source_duplicates(L))]


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


def weekly_panel(L, S, now):
    """One row per (week, listing seen that week) with the price valid at week end."""
    weeks = pd.date_range(end=now.normalize(), periods=WEEKS, freq="7D")
    parts = []
    for w in weeks:
        m = (L.first_seen <= w + timedelta(days=1)) & (L.last_seen >= w - timedelta(days=6))
        if m.any():
            parts.append(pd.DataFrame({"week": w + timedelta(days=1), "listing_id": L.id[m].values}))
    if not parts:
        return pd.DataFrame(columns=["week", "listing_id", "price_azn"]), weeks
    P = pd.concat(parts).sort_values("week")
    snap = S[["listing_id", "observed_at", "price_azn"]].sort_values("observed_at")
    P = pd.merge_asof(P, snap, left_on="week", right_on="observed_at", by="listing_id", direction="backward")
    P = P.dropna(subset=["price_azn"]).drop(columns="observed_at")
    P["week"] = P["week"] - timedelta(days=1)
    attrs = L[["id", "deal", "building", "rooms_bucket", "area_m2", "district"]].rename(columns={"id": "listing_id"})
    P = P.merge(attrs, on="listing_id")
    return add_ppm2(P), weeks


def add_ppm2(df):
    df = df.copy()
    df["ppm2"] = df.price_azn / df.area_m2
    lo = df.deal.map(lambda d: PPM2_RANGE[d][0])
    hi = df.deal.map(lambda d: PPM2_RANGE[d][1])
    return df[(df.ppm2 >= lo) & (df.ppm2 <= hi)]


def seg(df, deal, b, r):
    m = df.deal == deal
    if b != "all":
        m &= df.building == b
    if r != "all":
        m &= df.rooms_bucket == r
    return df[m]


def price_changes(S, L):
    S = S.sort_values(["listing_id", "observed_at"]).copy()
    S["prev"] = S.groupby("listing_id").price_azn.shift()
    C = S[S.prev.notna() & ((S.price_azn - S.prev).abs() > 0.5)].copy()
    C["pct"] = (C.price_azn / C.prev - 1) * 100
    cols = ["id", "deal", "building", "rooms_bucket", "rooms", "area_m2", "district", "metro", "url", "source"]
    return C.merge(L[cols].rename(columns={"id": "listing_id"}), on="listing_id")


def build_segment(cur, pan, weeks, chg, L, now, deal, b, r, rent_cur):
    c, p = seg(cur, deal, b, r), seg(pan, deal, b, r)
    by_week = p.groupby("week")
    # Mix-adjusted series: district medians averaged with fixed weights, so a week
    # with more ads in cheap districts does not read as a price fall.
    dw = p.dropna(subset=["district"]).groupby("district").size()
    dw = dw / dw.sum() if dw.sum() else dw
    trend = []
    for w in weeks:
        g = by_week.get_group(w) if w in by_week.groups else p.iloc[0:0]
        adj = None
        if len(g):
            dm = g.dropna(subset=["district"]).groupby("district").ppm2.agg(["median", "size"])
            dm = dm[dm["size"] >= 5]
            cover = dw.reindex(dm.index).fillna(0)
            if cover.sum() >= 0.7:
                adj = float((dm["median"] * cover).sum() / cover.sum())
        row = {"week": w.date().isoformat(), "n": len(g), "median": _med(g.ppm2), "adjusted": adj,
               "p25": float(g.ppm2.quantile(.25)) if len(g) >= MIN_N else None,
               "p75": float(g.ppm2.quantile(.75)) if len(g) >= MIN_N else None}
        if b == "all":
            row["new"] = _med(g.ppm2[g.building == "new"])
            row["old"] = _med(g.ppm2[g.building == "old"])
        trend.append(row)

    def change(back):
        if len(trend) <= back:
            return None
        a, z = trend[-1], trend[-1 - back]
        if a["adjusted"] and z["adjusted"]:
            return _pct(a["adjusted"], z["adjusted"])
        return _pct(a["median"], z["median"])

    sc = seg(chg, deal, b, r)
    recent_cuts = sc[(sc.observed_at >= now - timedelta(days=30)) & (sc.pct < 0) & sc.listing_id.isin(c.id)]
    seg_all = seg(L, deal, b, r)
    gone = seg_all[(~seg_all.is_active) & (seg_all.delisted_at >= now - timedelta(days=90))]
    days_listed = (gone.last_seen - gone.first_seen).dt.days

    # Price changes per week (same weeks as the trend: week w covers the 7 days ending on w).
    sw = sc[sc.observed_at.dt.normalize() > weeks[0] - timedelta(days=7)]
    wk = np.searchsorted(weeks.values, sw.observed_at.dt.normalize().values, side="left")
    change_trend = []
    for i, w in enumerate(weeks):
        g = sw.pct[wk == i]
        n_cut, n_rise = int((g < 0).sum()), int((g > 0).sum())
        online = trend[i]["n"]
        change_trend.append({"week": w.date().isoformat(), "cuts": n_cut, "rises": n_rise,
                             "median_cut_pct": _med(g[g < 0], 3),
                             "cut_rate": n_cut / online * 100 if online >= MIN_N else None})

    med_now = _med(c.ppm2)
    kpi = {
        "active": len(c), "median_price": _med(c.price_azn), "median_ppm2": med_now,
        "p25_ppm2": float(c.ppm2.quantile(.25)) if len(c) >= MIN_N else None,
        "p75_ppm2": float(c.ppm2.quantile(.75)) if len(c) >= MIN_N else None,
        "median_area": _med(c.area_m2),
        "change_4w": change(4), "change_12w": change(12),
        "new_7d": int((seg_all.first_seen >= now - timedelta(days=7)).sum()),
        "cuts_30d": int(recent_cuts.listing_id.nunique()),
        "cut_share_30d": recent_cuts.listing_id.nunique() / len(c) * 100 if len(c) else None,
        "median_cut_pct": _med(recent_cuts.pct, 3),
        "median_days_listed": _med(days_listed, 10),
    }

    # districts
    last_w, prev_w = weeks[-1], weeks[-5] if len(weeks) >= 5 else weeks[0]
    p_last = p[p.week == last_w].groupby("district").ppm2
    p_prev = p[p.week == prev_w].groupby("district").ppm2
    rent_by_d = seg(rent_cur, "rent", "all", r).groupby("district").ppm2 if deal == "sale" else None
    districts = []
    for d, g in c.dropna(subset=["district"]).groupby("district"):
        if len(g) < 3:
            continue
        row = {"district": d, "n": len(g), "median_price": _med(g.price_azn, 3), "median_ppm2": _med(g.ppm2, 3),
               "inferred_share": float(g.district_inferred.fillna(False).astype(bool).mean() * 100)}
        a = _med(p_last.get_group(d), 5) if d in p_last.groups else None
        z = _med(p_prev.get_group(d), 5) if d in p_prev.groups else None
        row["change_4w"] = _pct(a, z)
        dc = recent_cuts[recent_cuts.district == d]
        row["cut_share_30d"] = dc.listing_id.nunique() / len(g) * 100
        row["median_cut_pct"] = _med(dc.pct, 3)
        if b == "all":
            row["new_ppm2"] = _med(g.ppm2[g.building == "new"], 3)
            row["old_ppm2"] = _med(g.ppm2[g.building == "old"], 3)
        if rent_by_d is not None and d in rent_by_d.groups and row["median_ppm2"]:
            rent = _med(rent_by_d.get_group(d), 3)
            row["gross_yield"] = rent * 12 / row["median_ppm2"] * 100 if rent else None
        districts.append(row)
    districts.sort(key=lambda x: -(x["median_ppm2"] or 0))

    top = [x["district"] for x in sorted(districts, key=lambda x: -x["n"])[:8]]
    dtrend = {d: [_med(g.ppm2, 5) for w in weeks
                  for g in [p[(p.week == w) & (p.district == d)]]] for d in top}

    metro = [{"metro": m, "n": len(g), "median_ppm2": _med(g.ppm2, 3), "median_price": _med(g.price_azn, 3)}
             for m, g in c.dropna(subset=["metro"]).groupby("metro") if len(g) >= 3]
    metro = sorted(metro, key=lambda x: -x["n"])[:20]

    rooms = []
    if r == "all":
        for rb, g in c.dropna(subset=["rooms_bucket"]).groupby("rooms_bucket"):
            rooms.append({"rooms": rb, "n": len(g), "median_price": _med(g.price_azn, 3),
                          "median_ppm2": _med(g.ppm2, 3), "median_area": _med(g.area_m2, 3)})
        rooms.sort(key=lambda x: x["rooms"])

    hist = None
    if len(c) >= 20:
        lo, hi = c.ppm2.quantile(.02), c.ppm2.quantile(.98)
        counts, edges = np.histogram(c.ppm2, bins=20, range=(lo, hi))
        hist = {"edges": edges.tolist(), "counts": counts.tolist()}

    f = c.dropna(subset=["floor", "floors_total"])
    f = f[f.floors_total > 1]
    pos = np.select([f.floor <= 1, f.floor >= f.floors_total], ["first", "top"], "middle")
    floors = [{"position": k, "n": int((pos == k).sum()), "median_ppm2": _med(f.ppm2[pos == k], 5)}
              for k in ("first", "middle", "top")]

    return {"kpi": kpi, "trend": trend, "change_trend": change_trend, "districts": districts, "district_trend": dtrend,
            "metro": metro, "rooms": rooms, "hist": hist, "floors": floors}


def build(engine) -> dict:
    L, S, R = load(engine)
    demo = bool((R.source == "demo").any()) if len(R) else False
    if L.empty:
        return {"meta": {"generated_at": db.utcnow().isoformat(), "empty": True, "demo": demo}, "segments": {}}
    L = prepare(L)
    now = L.last_seen.max()
    cur = add_ppm2(L[L.is_active & (L.last_seen >= now - timedelta(days=3))])
    pan, weeks = weekly_panel(L, S, now)
    chg = price_changes(S, L)

    segments = {}
    for deal in DEALS:
        for b in BUILDINGS:
            for r in ROOMS:
                segments[f"{deal}|{b}|{r}"] = build_segment(cur, pan, weeks, chg, L, now, deal, b, r, cur)

    feed = chg.sort_values("observed_at", ascending=False).head(400)
    changes = [{"date": row.observed_at.date().isoformat(), "deal": row.deal, "building": row.building,
                "rooms_bucket": row.rooms_bucket, "rooms": row.rooms, "area": row.area_m2,
                "district": row.district, "metro": row.metro, "old": row.prev, "new": row.price_azn,
                "pct": row.pct, "url": row.url, "source": row.source} for row in feed.itertuples()]

    sources = []
    real = R[R.source != "demo"]
    for src, g in real.groupby("source"):
        ok = g[g.status == "ok"]
        last = g.sort_values("started_at").iloc[-1]
        sources.append({"source": src, "last_ok": ok.finished_at.max() if len(ok) else None,
                        "last_status": last.status, "last_error": last.error,
                        "active": int(((L.source == src) & L.is_active).sum())})
    return {
        "meta": {"generated_at": db.utcnow().isoformat(), "data_through": now, "demo": demo,
                 "weeks": [w.date().isoformat() for w in weeks], "listings_tracked": len(L),
                 "price_changes_tracked": len(chg)},
        "sources": sources, "segments": segments, "changes": changes,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-file", action="store_true", help="skip writing dashboard/data.json")
    ap.add_argument("--no-publish", action="store_true", help="skip storing the export for /api/data")
    args = ap.parse_args(argv)

    engine = db.get_engine()
    data = _clean(build(engine))
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if not args.no_file:
        DASHBOARD_DATA.parent.mkdir(parents=True, exist_ok=True)
        DASHBOARD_DATA.write_text(payload, encoding="utf-8")
        print(f"wrote {DASHBOARD_DATA} ({DASHBOARD_DATA.stat().st_size / 1024:.0f} KB)")
    if not args.no_publish:
        export_id = db.publish_export(engine, payload)
        print(f"published export #{export_id} ({len(payload.encode()) / 1024:.0f} KB) for /api/data")


if __name__ == "__main__":
    main()
