# Baku apartment price tracker

Scrapes apartment listings for Baku from bina.az and yeniemlak.az every 6 hours,
stores every price change in Postgres (Neon), and serves a live analytics
dashboard on Vercel.

```
GitHub Actions (cron, every 6 h)
  └─ scraper.run        bina.az (headless Chromium) + yeniemlak.az (HTTP)
       └─ Neon Postgres  listings · price_snapshots · scrape_runs
  └─ scraper.enrich     reads bina.az ad pages for listings whose card named no district
  └─ analytics.export   cleaned listings + price histories → Neon table dashboard_exports

GitHub Actions (manual, once)
  └─ tools.backfill_archive   archive.org copies of bina.az ads, last 24 months → price_snapshots (origin=archive)

Vercel
  ├─ dashboard/index.html   static page, polls for new data every 5 minutes
  ├─ api/data.js            newest export from Neon (edge-cached 5 min)
  └─ api/status.js          newest export id + last scrape run per section
```

Scraping stays on GitHub Actions because it needs headless Chromium and up to an
hour per run, which doesn't fit in a Vercel function. A new export reaches the site
within 5 minutes with no redeploy. The browser only receives the aggregated export
and never queries the listings tables. The database URL stays in Vercel's server-side environment.

## What the dashboard shows

Filters: sale or rent, new or old building, rooms (several at once), period (3 months to 2 years), and
under *More filters* district (several), price range, area range and source. Every chart, table and the
map follow the filters, and the address bar keeps them, so a view can be shared as a link.

The payload is the cleaned listings and their price histories in a compact column format (no titles,
descriptions, phone numbers or exact coordinates). The browser computes all statistics from it.

- Median asking price per m², 4- and 12-week change, middle 50% range
- Weekly trend with a mix-adjusted line (district medians at fixed weights, so a week with more ads in cheap districts doesn't look like a price fall)
- District table: listings, median price, per m², 4-week change, new vs old build, gross rental yield
- District trend lines, rooms breakdown, price distribution, floor premium
- Share of ads that cut their price in the last 30 days and the typical cut, overall and per district
- Weekly count of price cuts and rises, and cuts as a share of listings online
- Days listed before an ad disappears
- Feed of individual price changes with links to the ads
- Metro station table
- Price map: a bubble per metro station, settlement or district, coloured by median price per m², change
  over the period, share of price cuts, gross yield or listing count, with the biggest risers and fallers
- Check a listing: paste an ad link to see its price history and how it compares with similar flats
- CSV download of the district table

## Two years of history

The scraper only knows prices from the day it starts. Earlier history comes from archive.org, which keeps
copies of individual bina.az ads. Run it once after setup: GitHub → Actions → *Backfill history from
archive.org* → *Run workflow*. It reads about 7,500 archived pages from the last 24 months (roughly 2–3
hours, rate-limited), keeps the Baku apartments, and stores each as a snapshot dated when it was archived.
It can be stopped and rerun; finished pages are skipped. Locally: `python -m tools.backfill_archive --limit 50`.

Archived ads are a sample of a few hundred ads a month, not a sweep, and archive.org saved almost nothing
for some months (November 2025 to April 2026). The trend chart draws those stretches dashed and leaves gaps
where there are too few ads.

## Setup (about 15 minutes)

1. **Database.** Create a Neon project and copy the pooled connection string. Tables are created on the first run.
2. **GitHub secrets.** In the repo, Settings → Secrets and variables → Actions → add `DATABASE_URL`. Add `PROXY_URL` only if you need it (see below).
3. **Vercel.** On vercel.com, *Add New → Project → Import* this repository. Leave the framework preset at *Other*; `vercel.json` sets everything else. Under *Environment Variables*, add `DATABASE_URL` with the same Neon string, then deploy. (Alternatively, connect Neon through Vercel's *Storage* tab, which sets `DATABASE_URL` for you.)
4. **First run.** GitHub → Actions → *Scrape and publish* → *Run workflow*. When it finishes, the Vercel site switches from the demo numbers to live data and the header shows **Live**.
5. *Optional:* add a repository variable `SITE_URL` with your Vercel URL so each run summary links to the dashboard.

Every push to `main` redeploys the site, but the data doesn't depend on deploys.

Health checks: `/api/status` shows the newest export and the last run for each section. If `/api/data` returns 503, `DATABASE_URL` is missing on Vercel. If it returns 404, no export has been published yet.

## Check the scrapers first

Run this locally before the first scheduled run, and again whenever a site changes its layout:

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m scraper.run --check
```

It reads one page per section, writes nothing, and prints how many cards parsed and the coverage of each field (price, area, rooms, floor, district, metro). If a section shows 0 usable cards, open the URL in `scraper/config.py` in a browser and update it. Parsing is text-based, so field extraction usually survives redesigns; only the section URL or the listing link pattern (`/items/<id>` on bina.az, `/elan/...-<id>` on yeniemlak.az) tends to break.

The yeniemlak.az rent URL (`elan_nov=2`) and page parameter (`&page=N`) are the two settings most worth confirming on the first check.

## If a site blocks the runner

yeniemlak.az is known to block cloud provider IP ranges, and GitHub's runners are cloud IPs. Two fixes:

- **Self-hosted runner** (free): any always-on machine (a home PC, a small office server in Baku). Settings → Actions → Runners → *New self-hosted runner*, then change `runs-on: ubuntu-latest` to `runs-on: self-hosted` in the `scrape` job.
- **Proxy**: set the `PROXY_URL` secret to a residential or local proxy.

Failed sections show as a red dot in the dashboard header, with the error on hover.

## Run locally

```bash
cp .env.example .env            # optional; without DATABASE_URL a local SQLite file is used
python -m scraper.run           # scrape
python -m analytics.export      # rebuild dashboard/data.json (and publish to the DB)
python -m http.server -d dashboard 8000   # open http://localhost:8000
```

Served as plain files, the page finds no `/api/data` and falls back to `data.json`; the header then shows **Snapshot** instead of **Live**. To run the API locally against Neon, use `npm i -g vercel && vercel dev` with `DATABASE_URL` set.

Preview with invented data:

```bash
DATABASE_URL=sqlite:///data/demo.db python -m tools.seed_demo
DATABASE_URL=sqlite:///data/demo.db python -m analytics.export
```

The `data.json` shipped in this repo is that demo output. The live site shows it only until the first export reaches Neon.

## How the numbers work

- **Asking prices, not transactions.** Listing sites show what sellers ask.
- **Price history.** A snapshot is stored when an ad first appears and whenever its price changes, so history costs almost no storage.
- **Delisting.** When a run reaches the end of a section, ads not seen in it are marked delisted. A guard skips this if the run found under 60% of the active ads (a sign of blocking, not a market event). With the default `MAX_PAGES=40` the runs cover the newest ~1,000 ads per section; raise it to sweep whole sections.
- **Duplicates.** The same flat on both sites (same deal, rooms, rounded area, floor, building height and district or metro) is counted once.
- **Districts.** Taken from the ad. For bina.az ads whose card names only a metro station or settlement, `scraper.enrich` reads the ad page, where the site gives the district (up to `ENRICH_MAX`=600 pages per run). Anything still missing is inferred from a small map in `scraper/parse.py` and marked with * in the dashboard.
- **Price changes.** Cuts and rises in the feed and the 30-day statistics only count changes our own scraper saw within two weeks, so the date is known. Changes between an archived copy and today count towards long-run change only.
- **Currency.** USD prices are converted at the 1.70 peg.

## Be a good citizen

Keep `REQUEST_DELAY` at 3 seconds or more, check each site's terms of use and robots.txt, and don't republish the raw ads. Aggregated statistics are the intended output.

## Layout

```
scraper/      config.py · parse.py · db.py · run.py · enrich.py · sources/bina.py · sources/bina_item.py · sources/yeniemlak.py
analytics/    export.py  → dashboard_exports table (+ dashboard/data.json locally)
dashboard/    index.html (static, Chart.js + Leaflet) · data.json (demo fallback) · geo/places.json (map points)
api/          data.js · status.js  (Vercel functions, Neon serverless driver)
tools/        seed_demo.py · backfill_archive.py
tests/        pytest
vercel.json   output directory, headers   ·   .vercelignore keeps the Python code out of the deploy
```
