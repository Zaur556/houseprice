"""Runtime settings. Everything can be overridden with environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        (ROOT / "data").mkdir(exist_ok=True)
        url = f"sqlite:///{ROOT / 'data' / 'baku.db'}"
    # Neon gives postgres:// or postgresql:// URLs; SQLAlchemy needs the psycopg driver named.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = _db_url()
PROXY_URL = os.getenv("PROXY_URL") or None          # e.g. http://user:pass@host:port
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "3"))  # seconds between pages (yeniemlak allows ~25 req/min)
MAX_PAGES = int(os.getenv("MAX_PAGES", "40"))           # pages per section per run (~25 ads per page)
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 BakuPriceTracker/1.0",
)
USD_AZN = 1.70  # the manat is pegged at 1.70 per US dollar
DASHBOARD_DATA = ROOT / "dashboard" / "data.json"

# One entry per listing section. Check these URLs with `python -m scraper.run --check`
# after any site redesign; only the URL needs to change, parsing is text-based.
SECTIONS = {
    "bina": [
        {"deal": "sale", "building": "new", "url": "https://bina.az/baki/alqi-satqi/menziller/yeni-tikili"},
        {"deal": "sale", "building": "old", "url": "https://bina.az/baki/alqi-satqi/menziller/kohne-tikili"},
        {"deal": "rent", "building": None, "url": "https://bina.az/baki/kiraye/menziller"},
    ],
    "yeniemlak": [
        # elan_nov: 1 = sale, 2 = rent; emlak=1 apartments; seher[]=7 Baku
        {"deal": "sale", "building": None, "url": "https://yeniemlak.az/elan/axtar?elan_nov=1&emlak=1&seher%5B%5D=7"},
        {"deal": "rent", "building": None, "url": "https://yeniemlak.az/elan/axtar?elan_nov=2&emlak=1&seher%5B%5D=7"},
    ],
}
