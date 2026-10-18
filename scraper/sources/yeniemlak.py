"""yeniemlak.az: server-rendered pages, ~25 ads per page. Plain HTTP is enough."""
import re
import time

import requests
from bs4 import BeautifulSoup

from ..config import PROXY_URL, USER_AGENT

ID_RE = re.compile(r"/elan/[^?#]*?-(\d{4,})(?:[/?#]|$)")


def _cards(html: str, base: str = "https://yeniemlak.az"):
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for a in soup.select('a[href*="/elan/"]'):
        href = a.get("href", "")
        m = ID_RE.search(href)
        if not m:
            continue
        lid = m.group(1)
        node = a
        for _ in range(8):
            parent = node.parent
            if parent is None or parent.name in ("body", "html"):
                break
            ids = {mm.group(1) for x in parent.select('a[href*="/elan/"]') if (mm := ID_RE.search(x.get("href", "")))}
            if len(ids) > 1:
                break
            node = parent
        text = node.get_text("\n", strip=True)
        url = href if href.startswith("http") else base + href
        if lid not in out or len(text) > len(out[lid]["text"]):
            out[lid] = {"id": lid, "url": url.split("?")[0], "text": text}
    return list(out.values())


def scrape(section: dict, max_pages: int, delay: float, log=print):
    """Return (cards, reached_end)."""
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "az,en;q=0.8"})
    if PROXY_URL:
        s.proxies.update({"http": PROXY_URL, "https": PROXY_URL})
    seen: dict[str, dict] = {}
    reached_end = False
    for page in range(1, max_pages + 1):
        url = section["url"] + (f"&page={page}" if page > 1 else "")
        for attempt in range(3):
            r = s.get(url, timeout=30)
            if r.status_code != 429:
                break
            time.sleep(60 * (attempt + 1))
        r.raise_for_status()
        cards = [c for c in _cards(r.text) if c["id"] not in seen]
        if not cards:
            reached_end = True
            break
        seen.update({c["id"]: c for c in cards})
        time.sleep(delay)
    log(f"  yeniemlak {section['deal']}: {len(seen)} cards from {page} pages")
    return list(seen.values()), reached_end
