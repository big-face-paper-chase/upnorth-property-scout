# Up North Property Scout — Project Handoff

**For:** whoever's taking this over next
**From:** Chase, via Moose (his AI assistant)
**Date:** September 25, 2026
**Status:** Working end-to-end. Public beta behind a (weak) invite gate. Real authentication is the big unfinished job.

---

## 1. What this is

An autonomous watcher for Michigan tax-foreclosure property auctions. It scrapes
[tax-sale.info](https://www.tax-sale.info/) (Title Check, LLC — the platform behind
74+ Michigan counties' online tax auctions), tracks every parcel in 39 northern-Michigan
and Upper-Peninsula counties, and publishes a filterable dashboard website:

- **Live site:** https://big-face-paper-chase.github.io/upnorth-property-scout/
- **Repo:** https://github.com/big-face-paper-chase/upnorth-property-scout

Features as of today: smart search templates, Market Intel analytics tab
(sell-through by county, sales trends, deal benchmarks), auction calendar,
recently-sold tracker, CSV export, waterfront/cheap-house/cheap-acreage alert rules,
per-lot price/status history, and a 30-minute new-listing watcher that also polls
live auction days for real-time sales.

---

## 2. Where it's at (honest status)

| Piece | State |
|---|---|
| Scraper (tax-sale.info) | ✅ Working. Discovery + CSV + listing-page parsers, retry logic |
| SQLite store + diffing | ✅ Working. 858 parcels tracked (as of 2026-09-25) |
| 30-min new-listing watcher | ✅ Working (cron `upnorth-scout-watcher`) |
| Daily full re-scan | ✅ Working (cron `upnorth-scout-daily`, ~7:37 AM ET) |
| Dashboard (filters, templates, Market Intel, calendar, CSV) | ✅ Working, mobile + desktop |
| Alert rules | ✅ Working, tuned in `config.yaml` |
| Per-lot history table | ✅ Recording; **not yet surfaced in UI** |
| Self-running copy (template + Actions) | ✅ Working — friend clicks "Use this template", sets his counties/code, Pages + workflows do the rest |
| Invite-code gate | ⚠️ **NOT real security** — see §6 job #1 |
| Repo visibility | ⚠️ Public (should go private when real auth lands) |
| Per-user settings / saved searches | ❌ Needs a backend with user accounts |
| Manual "scan now" button | ❌ Needs a backend endpoint |
| Auto-bid | ❌ **Deliberately not built.** Real money, legally binding bids. Do not build without a direct conversation with Chase about max-bid rules and hard limits. |

---

## 3. How it works

```
tax-sale.info ──► scout.py / watch.py ──► SQLite (data/scout.db)
                                              │
                        ┌─────────────────────┼─────────────────────┐
                        ▼                     ▼                     ▼
                  report.py ──► docs/data.json ──► GitHub Pages dashboard
                                              (publish.py pushes via GitHub API)
```

**Two runners:**

- **`scout.py`** — the full scan. Discovers all catalogs on the auctions index,
  matches them to the 39 target counties, downloads each catalog's official CSV
  export + listing page, upserts parcels, diffs against last run, fires alert rules,
  rebuilds `docs/data.json`. Takes ~4 minutes for ~90 catalogs.
- **`watch.py`** — the event watcher (runs every 30 min). Fetches only the auctions
  index (1 request), diffs catalog IDs against the `catalogs` table. New target-region
  catalog → full ingest of just that catalog. On live auction days (a catalog's
  `auction_date == today`) it polls those listing pages for lots flipping
  available → sold. Rebuilds + pushes the feed itself when anything changed.

**Key files:**

| File | Role |
|---|---|
| `config.yaml` | Counties, source URLs, alert thresholds, watchlist. No secrets. |
| `sources/taxsale.py` | `TaxSaleSource`: catalog discovery, CSV + HTML parsers, retry-on-proxy-failure `_get` |
| `store.py` | SQLite schema + upserts. Tables: `parcels`, `catalogs`, `parcel_history`, `alerts`, `runs` |
| `scout.py` | Full-scan runner; `ingest_catalog()` is shared with `watch.py` |
| `watch.py` | 30-min watcher (new catalogs + live-auction-day sale polling) |
| `report.py` | Builds `docs/data.json` incl. `analytics` aggregates |
| `docs/` | Static dashboard: `index.html`, `app.js`, `styles.css`, `data.json` |
| `publish.py` | Pushes `docs/` to GitHub via API (uses local credential helper — see §5) |
| `run_daily.sh` | Cron wrapper for the daily scan |

**Scheduled jobs** (currently on Moose's VM, need re-homing — see §6 job #4):

- `upnorth-scout-watcher` — every 30 min → `python3 watch.py`. Silent unless: new catalog posted, live sale detected, or errors.
- `upnorth-scout-daily` — ~7:37 AM ET daily → `run_daily.sh`. Full scan + feed push. Silent unless new alerts or failures.

---

## 4. Run it locally

```bash
git clone https://github.com/big-face-paper-chase/upnorth-property-scout.git
cd upnorth-property-scout
python3 -m pip install requests pyyaml

python3 scout.py            # full scan (~4 min), rebuilds docs/data.json
python3 watch.py            # one watcher pass (~15–60 sec)
# then open docs/index.html in a browser (data.json loads locally)
```

The SQLite DB builds itself on first run (`data/` is gitignored). A full scan
starts from zero and catches up — no seed data required.

**Tune it:** alert thresholds and counties live in `config.yaml`. Smart-search
templates live in `docs/app.js` (`TEMPLATES`). Analytics queries live in
`report.py` (`_analytics`).

---

## 5. Things that will bite you (learned the hard way)

1. **The sandbox egress proxy drops connections intermittently** (~40% of requests
   in the worst window). `sources/taxsale.py::_get` retries 3× with backoff — don't
   remove that. If you re-home the runners somewhere with clean egress, you can
   relax it.
2. **`html` module vs `html` variable shadowing** in `taxsale.py` — the fix was
   `from html import unescape as html_unescape`. Don't reintroduce `import html`.
3. **CSV vs listing page:** the CSV export has the structured parcel data; the HTML
   listing page has photos, categories, and live sold prices. You need both.
4. **Stale marking:** `scout.py` marks `available` parcels not seen in a run as
   `stale` (kept for history, hidden from feed). The watcher intentionally does
   *not* do this — only the full scan has complete visibility.
5. **Alert de-dup:** the same parcel+rule never alerts twice (checked in `alerts`
   table). New alert *types* on old parcels will still fire once.
6. **DNR surplus** parcels ride inside county catalogs (`is_dnr` flag) — they're
   not a separate feed.
7. **Be polite to tax-sale.info.** It's a small LLC's site. The 30-min watcher is
   1 request on quiet days; only auction days are heavier. Don't crank the cadence
   without reason.
8. **`publish.py` auth** is wired to the original VM's GitHub credential helper
   (`custom.github` surrogate). You'll need to swap in your own token — easiest is
   rewriting `put_file()` to use `gh` CLI or a plain `GITHUB_TOKEN` env var.

---

## 6. What needs finishing (in order)

### #1 — Real authentication (the big one)
The current "invite code" is a client-side SHA-256 check in `app.js` with the hash
**public in the repo**. `data.json` is fetchable directly. It keeps honest people
honest and nothing more. **Do not describe it as secure.**

The planned replacement: **Cloudflare Pages + Cloudflare Access** (free tier,
50 users, email-OTP login, Chase approves each invitee, TLS in transit).
Blocker: Chase needs to create a free Cloudflare account first, then hand over an
API token so the site + Access policy can be wired up. Steps when unblocked:
1. Deploy `docs/` to Cloudflare Pages (or keep GitHub Pages behind Access —
   Pages is cleaner).
2. Put Cloudflare Access in front: allowlist by email, Chase approves adds/removes.
3. Make the GitHub repo **private** and disable public Pages.
4. Rotate/discard the old invite code (shared privately with Chase — do not reuse it) — it's burned.
5. Verify unauthenticated requests get a login wall, not data.

### #2 — Per-user settings + manual scan trigger
Chase wants: personal alert thresholds, saved searches, and a "scan now" button.
All three need a real backend (user accounts + a job endpoint) — they can't be
done on the static site. Natural home: a tiny Cloudflare Worker + D1/KV alongside
the Pages deploy from #1. The per-device `localStorage` filter memory in `app.js`
is the stopgap; replace it with server-side prefs.

### #3 — Surface per-lot history
`parcel_history` already records every price/status change. Next: a timeline view
per lot (price drops, re-listings, sale). Data's there; it's a UI job in `app.js`.

### #4 — Re-home the runners (mostly solved)
The repo is now a GitHub **template** with self-running Actions workflows
(`.github/workflows/scout.yml`): daily full scan + 30-min watcher + manual
"Run workflow" trigger, committing `docs/data.json` + `data/scout.db` back to the
repo, with GitHub Pages serving the site. The friend's path: "Use this template"
→ set his counties + invite code → enable Pages → done, no server.
Once his copy is live and healthy, the VM crons (`upnorth-scout-watcher`,
`upnorth-scout-daily`) can be retired — or kept as a backup.
Note the VM's flaky egress proxy (§5.1): anywhere else will be *more* reliable.

### #5 — More sources (nice-to-have)
MiBid (state surplus), PropertyRoom, and county sheriff foreclosure notices are
currently **watchlisted, not scraped** (login walls / no clean feed). Sheriff
notices are per-county legal-newspaper publications — there's no statewide feed,
so this one is genuinely hard.

### ⛔ Auto-bid — do NOT build unprompted
Chase floated "maybe auto-bid." Bidding on tax-sale.info is legally binding and
moves real money. This needs his bidding account, explicit max-bid rules per lot,
and hard spending caps — a serious conversation first, not a feature ticket.

---

## 7. Key numbers (2026-09-25, will drift)

- 858 parcels tracked: 113 available, 625 sold, 120 unsold
- 181 catalogs known, 90 in target region
- 530 listings with photos
- Benchmarks: avg asking bid ≈ 19% of SEV; lots sell for ≈ 6× their minimum bid
- Next big auction: no-reserve auction **October 30, 2026**

## 8. Verify-before-bid disclaimer (keep this on the site)

Parcel data comes from tax-sale.info catalog exports, for research only. Always
verify with the county treasurer before bidding. Properties are sold as-is;
interiors are usually unknown; many are occupied — be respectful.
