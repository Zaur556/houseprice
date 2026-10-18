"""bina.az: client-rendered with infinite scroll, so a headless browser is used.

Cards are located by their /items/<id> links, then the smallest surrounding
element that holds only that ad is read as plain text.
"""
import re

from ..config import PROXY_URL, USER_AGENT

EXTRACT_JS = r"""
() => {
  const out = {};
  const idOf = (href) => { const m = (href || '').match(/\/items\/(\d+)/); return m ? m[1] : null; };
  document.querySelectorAll('a[href*="/items/"]').forEach(a => {
    const id = idOf(a.getAttribute('href'));
    if (!id) return;
    let node = a.closest('[data-cy="item-card"]');
    if (!node) {
      node = a;
      for (let i = 0; i < 8 && node.parentElement; i++) {
        const p = node.parentElement;
        const ids = new Set([...p.querySelectorAll('a[href*="/items/"]')].map(x => idOf(x.getAttribute('href'))));
        if (ids.size > 1) break;
        node = p;
      }
    }
    // The currency is an SVG icon (₼), so innerText has a bare number. Spell it out.
    let priceLine = '';
    const pf = node.querySelector('[data-cy="item-card-price-full"]');
    if (pf) {
      const box = pf.closest('.price-container') || pf.parentElement;
      const cur = /\$|usd/i.test(box.innerText || '') ? 'USD' : 'AZN';
      priceLine = pf.innerText.trim() + ' ' + cur + '\n';
    }
    const text = priceLine + (node.innerText || '') + '\n' + (a.getAttribute('aria-label') || '');
    const url = new URL(a.getAttribute('href'), location.origin).href.split('?')[0];
    if (!out[id] || text.length > out[id].text.length) out[id] = { id, url, text };
  });
  return Object.values(out);
}
"""

MORE_BUTTON = re.compile(r"daha çox|ardını|göstər|yüklə", re.I)


def scrape(section: dict, max_items: int, delay: float, log=print):
    """Return (cards, reached_end). Each card: {id, url, text}."""
    from playwright.sync_api import sync_playwright

    seen: dict[str, dict] = {}
    reached_end = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy={"server": PROXY_URL} if PROXY_URL else None)
        ctx = browser.new_context(user_agent=USER_AGENT, locale="az-AZ", viewport={"width": 1280, "height": 2000})
        page = ctx.new_page()
        page.route("**/*", lambda r: r.abort() if r.request.resource_type in ("image", "media", "font") else r.continue_())
        page.goto(section["url"], wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_selector('a[href*="/items/"]', timeout=30_000)

        stale = 0
        while len(seen) < max_items:
            before = len(seen)
            for card in page.evaluate(EXTRACT_JS):
                if card["id"] not in seen or len(card["text"]) > len(seen[card["id"]]["text"]):
                    seen[card["id"]] = card
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            btn = page.get_by_role("button", name=MORE_BUTTON)
            if btn.count() and btn.first.is_visible():
                btn.first.click()
            page.wait_for_timeout(int(delay * 1000))
            stale = stale + 1 if len(seen) == before else 0
            if stale >= 4:
                reached_end = True
                break
        browser.close()
    log(f"  bina {section['deal']}/{section.get('building') or 'any'}: {len(seen)} cards")
    return list(seen.values())[:max_items], reached_end
