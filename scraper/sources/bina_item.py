"""Read one bina.az ad page (/items/<id>).

Two layouts are handled:
- live pages carry the ad as JSON in __NEXT_DATA__ (props.pageProps.currentItemData);
- archive.org copies are plain HTML with labelled fields and the map pin in data-lat/data-lng.

parse_item() returns a dict in the same shape as parse.parse_card plus id, deal, building,
lat/lng, is_apartment, in_baku and expired_at. Fields that can't be read are left out.
"""
import json
import re
from datetime import datetime

from bs4 import BeautifulSoup

from ..config import USD_AZN
from ..parse import METRO, _metro_re, norm

_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_LATLNG = re.compile(r'data-lat="(4[01]\.\d+)"[^>]*?data-lng="(4[89]\.\d+|50\.\d+)"')
_NUM = re.compile(r"^\d{1,3}(?: \d{3})+$|^\d+$")
_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_STATIONS = [(st, _metro_re(st)) for st in METRO]


def _price_azn(total, currency):
    if total is None:
        return None
    return round(float(total) * USD_AZN, 2) if (currency or "").upper() == "USD" else float(total)


def _location(names: list[str], out: dict):
    """bina names places "Yasamal r.", "İnşaatçılar m.", "Badamdar q."."""
    for n in names:
        n = (n or "").strip()
        if n.endswith(" r.") and "district" not in out:
            out["district"] = n[:-3].strip()
        elif n.endswith(" m.") and "metro" not in out:
            t = norm(n)
            out["metro"] = next((st for st, rx in _STATIONS if rx.search(t)), n[:-3].strip())
        elif n.endswith(" q.") and "settlement" not in out:
            out["settlement"] = n[:-3].strip()


def _from_json(item: dict) -> dict:
    out = {"id": str(item.get("id"))}
    price = item.get("price") or {}
    out["price"], out["currency"] = price.get("total"), (price.get("currency") or "AZN").upper()
    out["price_azn"] = _price_azn(out["price"], out["currency"])
    out["area_m2"] = (item.get("area") or {}).get("value")
    out["rooms"], out["floor"], out["floors_total"] = item.get("rooms"), item.get("floor"), item.get("floors")
    slug = (item.get("category") or {}).get("slug") or ""
    out["is_apartment"] = slug.startswith("menziller")
    out["building"] = "new" if "yeni" in slug else "old" if "kohne" in slug else None
    out["in_baku"] = (item.get("city") or {}).get("slug") == "baki"
    out["deal"] = "rent" if item.get("isLeased") else "sale"
    out["daily"] = bool(item.get("isPaidDaily"))
    if item.get("hasRepair") is not None:
        out["renovated"] = bool(item["hasRepair"])
    names = [x.get("fullName") for x in item.get("nearestLocations") or []]
    names.append((item.get("location") or {}).get("fullName"))
    _location(names, out)
    if item.get("state") == "EXPIRED" and item.get("expiresAt"):
        out["expired_at"] = datetime.fromisoformat(item["expiresAt"]).replace(tzinfo=None)
    return out


def _from_html(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for t in soup(["script", "style", "svg", "noscript"]):
        t.decompose()
    lines = [x for x in soup.get_text("\n", strip=True).split("\n") if x.strip()]
    # Keep only the ad itself: from its headline ("Satılır ..." / "Kirayə verilir ...") to "Elanın nömrəsi",
    # so menus, search forms and "similar ads" don't leak in.
    start = next((i for i, x in enumerate(lines) if norm(x).startswith(("satilir ", "kiraye verilir "))), 0)
    end = next((i for i, x in enumerate(lines) if i > start and x.startswith("Elanın nömrəsi")), len(lines))
    lines = lines[start:end + 1]
    out = {}
    if m := re.search(r"\((\d+)\) - bina\.az", title):
        out["id"] = m.group(1)

    def after(label):
        try:
            return lines[lines.index(label) + 1]
        except (ValueError, IndexError):
            return None

    cat = after("Kateqoriya") or ""
    out["is_apartment"] = norm(cat) in ("yeni tikili", "kohne tikili")
    out["building"] = "new" if norm(cat) == "yeni tikili" else "old" if norm(cat) == "kohne tikili" else None
    if (f := after("Mərtəbə")) and (m := re.match(r"(\d+)\s*/\s*(\d+)", f)):
        out["floor"], out["floors_total"] = int(m.group(1)), int(m.group(2))
    if (a := after("Sahə")) and (m := re.match(r"([\d.,]+)\s*m", a)):
        out["area_m2"] = float(m.group(1).replace(",", "."))
    if (rep := after("Təmir")) in ("var", "yoxdur"):
        out["renovated"] = rep == "var"
    if (r := after("Otaq sayı")) and r.isdigit():
        out["rooms"] = int(r)
    for i, x in enumerate(lines[:-1]):
        if _NUM.match(x) and lines[i + 1] in ("AZN", "USD", "$"):
            out["price"] = float(x.replace(" ", ""))
            out["currency"] = "AZN" if lines[i + 1] == "AZN" else "USD"
            out["price_azn"] = _price_azn(out["price"], out["currency"])
            out["daily"] = i + 2 < len(lines) and lines[i + 2].startswith("/gün")
            break
    head = next((norm(x) for x in lines if norm(x).startswith(("satilir ", "kiraye verilir "))), "")
    out["deal"] = "rent" if head.startswith("kiraye") or out.get("daily") else "sale" if head else None
    out["in_baku"] = "Bakı" in title
    _location([x for x in lines if x.endswith((" r.", " m.", " q."))], out)
    if m := _LATLNG.search(html):
        out["lat"], out["lng"] = float(m.group(1)), float(m.group(2))
    if (e := next((x for x in lines if x.startswith("Müddəti başa çatıb")), None)) and (m := _DATE.search(e)):
        out["expired_at"] = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return out


def parse_item(html: str) -> dict | None:
    m = _NEXT.search(html)
    if m:
        try:
            item = json.loads(m.group(1))["props"]["pageProps"].get("currentItemData")
        except (ValueError, KeyError):
            item = None
        if item:
            out = _from_json(item)
            if ll := _LATLNG.search(html):
                out["lat"], out["lng"] = float(ll.group(1)), float(ll.group(2))
            return out
    out = _from_html(html)
    return out if out.get("id") else None


def usable(p: dict | None) -> bool:
    """A Baku apartment with a monthly or sale price and an area."""
    return bool(p and p.get("is_apartment") and p.get("in_baku") and p.get("deal") and p.get("price_azn")
                and p.get("area_m2") and not p.get("daily"))
