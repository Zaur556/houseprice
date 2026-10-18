"""Turn raw listing-card text (Azerbaijani) into structured fields.

Parsing is deliberately text-based rather than CSS-class-based, so a site
redesign usually only breaks the link pattern, not the field extraction.
"""
import re

from .config import USD_AZN

_TRANS = str.maketrans({
    "ə": "e", "Ə": "e", "ı": "i", "I": "i", "İ": "i", "ö": "o", "Ö": "o",
    "ü": "u", "Ü": "u", "ş": "s", "Ş": "s", "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g",
    "\u00a0": " ", "\u202f": " ",
})


def norm(s: str) -> str:
    """Lowercase ASCII-folded Azerbaijani text for matching."""
    return (s or "").translate(_TRANS).lower()


DISTRICTS = ["Binəqədi", "Qaradağ", "Xətai", "Xəzər", "Nərimanov", "Nəsimi", "Nizami",
             "Pirallahı", "Sabunçu", "Səbail", "Suraxanı", "Yasamal"]

# canonical station -> aliases seen on listing sites
METRO = {
    "İçərişəhər": ["içərişəhər"], "Sahil": ["sahil"], "28 May": ["28 may"],
    "Cəfər Cabbarlı": ["cəfər cabbarlı"], "Gənclik": ["gənclik"],
    "Nəriman Nərimanov": ["nəriman nərimanov", "nərimanov"], "Bakmil": ["bakmil"],
    "Ulduz": ["ulduz"], "Koroğlu": ["koroğlu"], "Qara Qarayev": ["qara qarayev"],
    "Neftçilər": ["neftçilər"], "Xalqlar Dostluğu": ["xalqlar dostluğu", "xalqlar"],
    "Əhmədli": ["əhmədli"], "Həzi Aslanov": ["həzi aslanov"], "Nizami": ["nizami"],
    "Elmlər Akademiyası": ["elmlər akademiyası", "elmlər"], "İnşaatçılar": ["inşaatçılar"],
    "20 Yanvar": ["20 yanvar"], "Memar Əcəmi": ["memar əcəmi"], "Nəsimi": ["nəsimi"],
    "Azadlıq prospekti": ["azadlıq prospekti", "azadlıq"], "Dərnəgül": ["dərnəgül"],
    "Avtovağzal": ["avtovağzal"], "8 Noyabr": ["8 noyabr"], "Xocəsən": ["xocəsən"],
    "Şah İsmayıl Xətai": ["şah ismayıl xətai", "xətai"],
}

# Used only when a card names a station or settlement but no district.
# Kept to unambiguous cases; extend as needed.
METRO_DISTRICT = {
    "İçərişəhər": "Səbail", "Sahil": "Səbail", "28 May": "Nəsimi", "Cəfər Cabbarlı": "Nəsimi",
    "Gənclik": "Nərimanov", "Nəriman Nərimanov": "Nərimanov", "Ulduz": "Nərimanov",
    "Elmlər Akademiyası": "Yasamal", "İnşaatçılar": "Yasamal", "Qara Qarayev": "Nizami",
    "Xalqlar Dostluğu": "Xətai", "Əhmədli": "Xətai", "Həzi Aslanov": "Xətai",
    "Şah İsmayıl Xətai": "Xətai", "Dərnəgül": "Binəqədi", "Avtovağzal": "Binəqədi",
    "Xocəsən": "Binəqədi",
}
SETTLEMENT_DISTRICT = {
    "badamdar": "Səbail", "bayil": "Səbail", "bibiheybet": "Səbail",
    "bakixanov": "Sabunçu", "sabuncu": "Sabunçu", "ramana": "Sabunçu", "zabrat": "Sabunçu",
    "mastaga": "Sabunçu", "bilgeh": "Sabunçu", "yeni yasamal": "Yasamal",
    "ehmedli": "Xətai", "hezi aslanov": "Xətai", "8-ci kilometr": "Nizami",
    "qaracuxur": "Suraxanı", "hovsan": "Suraxanı", "bileceri": "Binəqədi",
    "bineqedi": "Binəqədi", "merdekan": "Xəzər", "buzovna": "Xəzər",
    "sagan": "Xəzər", "lokbatan": "Qaradağ",
}

_DISTRICT_RES = [(d, re.compile(rf"(?<![a-z]){re.escape(norm(d))}[\s-]*(?:r\.|ray)")) for d in DISTRICTS]
def _metro_re(alias: str):
    name = re.escape(norm(alias)).replace(r"\ ", r"[\s-]")
    # "28 May m." / "Gənclik metrosu" (bina) or "metro. Nəriman Nərimanov" (yeniemlak)
    return re.compile(rf"(?<![a-z0-9]){name}[\s-]*(?:m\.|metro|m(?=[\s,]|$))"
                      rf"|metro\.?\s+{name}(?![a-z])")


_METRO_RES = sorted(
    [(st, _metro_re(a)) for st, aliases in METRO.items() for a in aliases],
    key=lambda x: -len(x[1].pattern),
)
_NUM = r"\d{1,3}(?:[ .,]\d{3})+|\d+"
_PRICE_POST = re.compile(rf"({_NUM})(?:[.,]\d{{1,2}})?\s*(azn|₼|manat|usd|\$)")
_PRICE_PRE = re.compile(rf"(\$|usd)\s*({_NUM})")
_AREA = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:m²|m2|kv\.?\s*m)")
_ROOMS = re.compile(r"(\d+)\s*-?\s*otaq")      # "2 otaqlı" (bina), "2 otaq" (yeniemlak), "2-otaqli" (URLs)
_FLOOR = re.compile(r"(\d+)\s*/\s*(\d+)\s*mertebe|mertebe\s*:?\s*(\d+)\s*/\s*(\d+)")
_SETTLEMENT = re.compile(r"([\w\-]+(?: [\w\-]+){0,2}) q\.(?=\s|$|,)")


def _to_int(s: str) -> int:
    return int(re.sub(r"[ .,]", "", s))


def parse_price(text: str):
    t = norm(text)
    m = _PRICE_POST.search(t)
    if m:
        cur = "USD" if m.group(2) in ("usd", "$") else "AZN"
        return float(_to_int(m.group(1))), cur
    m = _PRICE_PRE.search(t)
    if m:
        return float(_to_int(m.group(2))), "USD"
    return None, None


def parse_card(text: str, url: str = "") -> dict:
    t = norm(text)
    u = norm(url)
    out = {}
    price, cur = parse_price(text)
    out["price"], out["currency"] = price, cur
    if price is not None:
        out["price_azn"] = round(price * USD_AZN, 2) if cur == "USD" else price

    if m := _AREA.search(t):
        out["area_m2"] = float(m.group(1).replace(",", "."))
    if m := (_ROOMS.search(t) or _ROOMS.search(u)):
        out["rooms"] = int(m.group(1))
    if m := _FLOOR.search(t):
        a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        # bina writes floor/height ("3/7"), yeniemlak height/floor ("5 / 3"); the floor is never the larger.
        out["floor"], out["floors_total"] = sorted((int(a), int(b)))

    if "yeni tikili" in t or "yeni-tikili" in u:
        out["building"] = "new"
    elif "kohne tikili" in t or "kohne-tikili" in u:
        out["building"] = "old"

    if "/gun" in t or "gunluk" in t or "gunluk" in u:
        out["daily"] = True                  # short-term rental priced per day
    if "kiraye" in u or "/ay" in t or "ayliq" in t:
        out["deal"] = "rent"
    elif "satilir" in u or "alqi-satqi" in u:
        out["deal"] = "sale"

    for name, rx in _DISTRICT_RES:
        if rx.search(t) or rx.search(u):
            out["district"] = name
            break
    for st, rx in _METRO_RES:
        if rx.search(t) or rx.search(u):
            out["metro"] = st
            break
    if m := _SETTLEMENT.search(text.replace("\u00a0", " ")):
        out["settlement"] = m.group(1).strip()

    if "district" not in out:
        inferred = None
        if out.get("settlement"):
            inferred = SETTLEMENT_DISTRICT.get(norm(out["settlement"]))
        if not inferred and out.get("metro"):
            inferred = METRO_DISTRICT.get(out["metro"])
        if inferred:
            out["district"], out["district_inferred"] = inferred, True
    return out


def build_record(source: str, source_id: str, url: str, text: str, section: dict) -> dict | None:
    """Combine section defaults with parsed card fields. Returns None for unusable cards."""
    p = parse_card(text, url)
    if not p.get("price_azn") or not p.get("area_m2") or p.get("daily"):
        return None
    rec = {
        "id": f"{source}:{source_id}",
        "source": source,
        "source_id": str(source_id),
        "section": f"{source}:{section['deal']}:{section.get('building') or 'any'}",
        "url": url,
        "deal": section["deal"],
        "building": section.get("building") or p.get("building"),
        "title": " ".join(text.split())[:200],
        "district_inferred": p.get("district_inferred", False),
    }
    for k in ("rooms", "area_m2", "floor", "floors_total", "district", "metro",
              "settlement", "price", "currency", "price_azn"):
        rec[k] = p.get(k)
    return rec
