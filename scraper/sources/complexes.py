"""bina.az residential complexes (yaşayış kompleksləri): developers' own prices for new builds.

The list page (/yasayis-kompleksleri) names every complex with its "from" price per m²
(the cheapest layout), total price range, developer and completion date. A complex's
own page adds the map pin, district, building facts and the price of each layout.

Two layouts are handled for both pages: the current site (JSON in __NEXT_DATA__) and the
older server-rendered HTML that archive.org copies of 2024–2025 contain. Complexes are
keyed by their URL slug, which both layouts share.
"""
import json
import re

from bs4 import BeautifulSoup

BASE = "https://bina.az"
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_SLUG = re.compile(r"/yasayis-kompleksleri/([^/?#\"]+)")
_PER_METER = re.compile(r'class="[^"]*meter[^"]*"[^>]*>\s*<span class="price-val">([\d\s.,]+)</span>')
_LATLNG = re.compile(r'data-lat(?:itude)?="(4[01]\.\d+)"[^>]*?data-l(?:ng|on|ongitude)="(4[89]\.\d+|50\.\d+)"')


def _num(s) -> float | None:
    if s is None:
        return None
    s = re.sub(r"[^\d.,]", "", str(s)).replace(",", ".")
    try:
        return float(s.replace(" ", "")) if s else None
    except ValueError:
        return None


def _next(html: str) -> dict | None:
    m = _NEXT.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))["props"]["pageProps"]
    except (ValueError, KeyError):
        return None


def _slug(path: str | None) -> str | None:
    m = _SLUG.search(path or "")
    return m.group(1) if m else None


def _price(p: dict | None) -> dict:
    p = p or {}
    total = p.get("total") or {}
    return {"per_sqm": p.get("perSqm"), "min_total": total.get("minimum"), "max_total": total.get("maximum"),
            "currency": p.get("currency") or "AZN"}


def parse_list(html: str) -> list[dict]:
    """Every complex on the list page: slug, name, developer, prices, deadline, area info."""
    pp = _next(html)
    if pp and isinstance(pp.get("residences"), list):
        out = []
        for r in pp["residences"]:
            slug = _slug(r.get("path"))
            if not slug:
                continue
            out.append({"slug": slug, "bina_id": r.get("id"), "name": (r.get("name") or "").strip(),
                        "developer": r.get("companyName"), "city": (r.get("city") or {}).get("name"),
                        "address": r.get("address"), "metro": r.get("metroName"), "deadline": r.get("deadline"),
                        **_price(r.get("price"))})
        return out
    # older server-rendered layout (archive.org copies)
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a.residences-i[href]"):
        slug = _slug(a["href"])
        price = a.select_one("[data-sqm]")
        if not slug or price is None:
            continue
        text = lambda sel: (a.select_one(sel).get_text(" ", strip=True) if a.select_one(sel) else None)  # noqa: E731
        out.append({"slug": slug, "name": text(".title"), "developer": text(".developer"),
                    "address": text(".location"), "metro": text(".metro"), "deadline": text(".deadline"),
                    "per_sqm": _num(price.get("data-sqm")), "min_total": _num(price.get("data-full-price")),
                    "max_total": None, "currency": "AZN", "city": "Bakı"})
    return out


def parse_detail(html: str, slug: str) -> dict | None:
    """One complex page: location, building facts, layouts and prices."""
    pp = _next(html)
    r = (pp or {}).get("residence")
    if r:
        locs = [x.get("fullName") or "" for x in r.get("locations") or []]
        layouts = [{"rooms": a.get("rooms"), "area": a.get("area"), "per_sqm": a.get("priceFrom"),
                    "finish": (a.get("repair") or {}).get("name")}
                   for a in ((r.get("apartments") or {}).get("nodes") or [])]
        floors = [int(f) for f in (r.get("floorsPerBuildingSplitted") or []) if str(f).isdigit()]
        return {"slug": slug, "bina_id": r.get("id"), "name": (r.get("name") or "").strip(),
                "developer": r.get("companyName"), "city": (r.get("city") or {}).get("name"),
                "address": r.get("address"), "metro": r.get("metroName"), "deadline": r.get("deadline"),
                "district": next((x[:-3] for x in locs if x.endswith(" r.")), None),
                "lat": r.get("lat"), "lng": r.get("lng"), "apartments": r.get("apartmentsCount"),
                "buildings": r.get("buildingsCount"), "floors": max(floors) if floors else None,
                "hits": r.get("hits"), "layouts": layouts, **_price(r.get("price"))}
    # older layout: per-m² price of each layout block; the complex's "from" price is the lowest
    # e.g. <div class="residence-layout__price-per-meter"><span class="price-val">2 700</span>…/m²-dən
    vals = [_num(v) for v in _PER_METER.findall(html)]
    vals = [v for v in vals if v]
    if not vals:
        return None
    out = {"slug": slug, "per_sqm": min(vals), "min_total": None, "max_total": None, "currency": "AZN"}
    if m := _LATLNG.search(html):
        out["lat"], out["lng"] = float(m.group(1)), float(m.group(2))
    return out
