# Up North Property Scout

An autonomous watcher for Michigan tax-foreclosure and surplus property auctions
across **Northern Michigan and the Upper Peninsula** — scrapes, monitors,
organizes, and publishes them as a filterable dashboard.

- **Scrapes:** every county auction catalog on [tax-sale.info](https://www.tax-sale.info/)
  (Title Check, LLC — the platform behind 74+ Michigan counties' tax-foreclosure
  auctions), including DNR surplus parcels bundled into county catalogs.
- **Watches:** a 30-minute watcher checks for newly posted catalogs and, on live
  auction days, polls for lots selling in real time. A daily full re-scan diffs
  every parcel — new listings, minimum-bid changes, price drops, and sales.
- **Organizes:** everything lands in SQLite (parcels, catalogs, per-lot price/status
  history, alerts), then publishes as a dashboard with smart search templates,
  Market Intel analytics (sell-through by county, sales trends, deal benchmarks),
  auction calendar, recently-sold tracker, and CSV export.
- **Watchlists** sources that can't be scraped (MiBid state surplus, PropertyRoom
  police auctions, county sheriff sales) so people know where else to look.

**Live site:** https://big-face-paper-chase.github.io/upnorth-property-scout/

## Coverage

39 counties — all 15 Upper Peninsula counties plus 24 northern Lower Peninsula
counties. See `config.yaml`.

## Layout

```
config.yaml        counties, sources, alert rules, watchlist (no secrets)
scout.py           full scan: discover → ingest → diff → alert → rebuild feed
watch.py           30-min watcher: new-catalog detection + live-auction-day polling
sources/taxsale.py tax-sale.info discovery + CSV/listing parsers (with retries)
store.py           SQLite schema + upserts (parcels, catalogs, parcel_history, alerts, runs)
report.py          builds data.json incl. analytics aggregates
repo root          dashboard site (GitHub Pages): index.html, app.js, styles.css, data.json
publish.py         pushes the site files to GitHub via API
run_daily.sh       cron wrapper: scan → rebuild feed → publish
data/              scout.db + run logs (local only, gitignored)
HANDOFF.md         full project handoff notes — start here if you're taking this over
```

## Run it

```bash
python3 -m pip install requests pyyaml
python3 scout.py            # full scan (~4 min), rebuilds data.json
python3 watch.py            # one watcher pass (new catalogs + live-auction polling)
```

Then open `index.html` in a browser. The DB builds itself on first run.

## Make your own copy

This repo is a GitHub **template** — your own scout, your own counties, your own
site, no server needed:

1. Click **Use this template** → create your own repo.
2. Edit `config.yaml` (your counties, your alert thresholds) and set your own
   invite code in `app.js` (search `INVITE_HASH` — replace with the SHA-256
   of your code; generate with `echo -n "your-code" | sha256sum`).
3. Repo Settings → Pages → deploy from the `main` branch. Your site goes live.
4. The workflows in `.github/workflows/scout.yml` start automatically: a daily
   full scan plus the 30-minute watcher, committing fresh data back to the repo.
   The Actions tab's **Run workflow** button triggers a scan on demand.

GitHub runs the scans and hosts the site — nothing to install or keep awake.
One caveat: GitHub pauses scheduled workflows after 60 days of repo inactivity;
one manual run wakes them back up.

## Alert rules

Tune in `config.yaml` under `alerts`:

| Rule | Default |
|---|---|
| Any waterfront lot | on |
| House/cottage under | $20,000 |
| Any structure under | $10,000 |
| 5+ acres under | $5,000 |
| Any lot under | $1,000 |
| Minimum-bid drop | 20%+ |

Same parcel+rule never alerts twice. Smart-search templates live in `app.js`
(`TEMPLATES`); analytics queries in `report.py` (`_analytics`).

## Note on the invite gate

The invite-code check in `app.js` is client-side only — it is not real
authentication (see `HANDOFF.md` §6 job #1 for the replacement plan). Do not
treat it as a security boundary.

## Disclaimer

Parcel data comes from tax-sale.info catalog exports, for research only.
Always verify with the county treasurer before bidding. Properties are sold
as-is; interiors are usually unknown.
