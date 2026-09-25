"""Up North Property Scout — event-driven watcher.

Runs every ~30 minutes. Cheap by design:
  1. Fetches the tax-sale.info auctions index (1 request) and diffs catalog
     IDs against the local catalogs table.
  2. Any NEW catalog in the target region -> full ingest of just that
     catalog, then rebuild + push the dashboard feed.
  3. On live auction days (a target catalog's auction_date == today), polls
     just those catalogs' listing pages for lots flipping available -> sold.

This is as close to "triggered the moment something posts" as a third-party
site without webhooks allows. The daily full scan remains the backstop.

Usage: python3 watch.py [--db data/scout.db] [--site docs]
"""
import argparse
import json
import subprocess
import sys
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import store
from report import build_data_json
from scout import county_of, ingest_catalog, load_config
from sources.taxsale import TaxSaleSource

HERE = Path(__file__).parent


def push_feed() -> bool:
    r = subprocess.run([sys.executable, str(HERE / "publish.py"), "--data-only"],
                       capture_output=True, text=True)
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/scout.db")
    ap.add_argument("--site", default="docs")
    args = ap.parse_args()

    cfg = load_config()
    conn = store.connect(str(HERE / args.db))
    run_id = store.start_run(conn)
    stats = {"catalogs_scanned": 0, "parcels_seen": 0, "new_count": 0,
             "changed_count": 0, "lots_sold_live": 0}
    errors: list[str] = []
    events: list[str] = []
    changed = False
    today = date.today().isoformat()

    try:
        src = TaxSaleSource(cfg)
        catalogs = src.discover_catalogs()

        # --- 1. new-catalog detection ----------------------------------
        for c in catalogs:
            county = county_of(c, cfg["counties"])
            is_new = store.upsert_catalog(conn, c, county)
            if is_new and county:
                c["county"] = county
                events.append(f"NEW CATALOG: {c['label']} ({c.get('auction_name')})")
                ingest_catalog(conn, src, cfg, c, run_id, stats, errors)
                changed = True
                store.add_alert(conn, run_id, None, "new_catalog",
                                f"New auction catalog posted: {c['label']} — "
                                f"{c.get('auction_name')} ({c.get('auction_date')})")
        conn.commit()

        # --- 2. live-auction-day polling --------------------------------
        hot = store.hot_catalogs(conn, today)
        if hot:
            events.append(f"live auction day: {len(hot)} catalog(s) being watched closely")
        for h in hot:
            try:
                extras = src.fetch_listing_extras(h["catalog_id"])
            except Exception as e:
                errors.append(f"hot poll {h['label']}: {e}")
                continue
            for lot_no, ex in extras.items():
                if ex.get("sold_price"):
                    parcel = store.mark_lot_sold(conn, h["catalog_id"], lot_no,
                                                 ex["sold_price"])
                    if parcel:
                        stats["lots_sold_live"] += 1
                        changed = True
                        msg = (f"SOLD LIVE: {parcel['title']} — {parcel['county']} "
                               f"{h['label']} lot {lot_no} for "
                               f"${ex['sold_price']:,.0f}")
                        events.append(msg)
                        store.add_alert(conn, run_id, parcel["id"], "sold_live", msg)
            conn.commit()

        # --- 3. push if anything moved -----------------------------------
        pushed = False
        if changed:
            build_data_json(conn, cfg, HERE / args.site / "data.json")
            pushed = push_feed()
            events.append(f"dashboard feed rebuilt + pushed: {pushed}")
    except Exception as e:
        errors.append(f"fatal: {e}")
        traceback.print_exc()
    finally:
        store.finish_run(conn, run_id, stats, errors)
        conn.commit()
        conn.close()

    print(json.dumps({"run_id": run_id, "stats": stats, "events": events,
                      "errors": errors}, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
