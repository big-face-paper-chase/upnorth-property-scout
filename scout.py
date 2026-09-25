"""Up North Property Scout — main runner.

Discovers Michigan tax-foreclosure auction catalogs for the target
(northern MI + UP) counties, ingests parcel data, diffs against the last
run, raises alerts, and rebuilds the public data feed (docs/data.json).

Usage:
    python3 scout.py [--db data/scout.db] [--site docs] [--quiet]
"""
import argparse
import json
import sys
import traceback
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

import store
from report import build_data_json
from sources.taxsale import TaxSaleSource

HERE = Path(__file__).parent


def load_config() -> dict:
    with open(HERE / "config.yaml") as f:
        return yaml.safe_load(f)


def slugify_county(name: str) -> str:
    return name.lower().replace(" ", "-")


def county_of(catalog: dict, counties: list[str]) -> str | None:
    """Match a catalog label/slug to one of the target counties."""
    label = (catalog.get("label") or "").lower()
    slug = (catalog.get("catalog_slug") or "").lower()
    for c in counties:
        s = slugify_county(c)
        if slug == s or slug.startswith(s + "-") or slug.startswith(s + " ") or f" {s} " in f" {label} " or label.startswith(s + " ") or label == s:
            return c
    return None


def alert_rules(cfg: dict, parcel: dict, changes: dict, is_new: bool) -> list[tuple[str, str]]:
    """Return [(rule, message)] for every alert rule the parcel triggers."""
    a = cfg.get("alerts", {})
    hits = []
    cat = (parcel.get("category") or "").lower()
    title = parcel.get("title") or ""
    min_bid = parcel.get("min_bid")
    acres = parcel.get("acres")

    def money(x):
        return f"${x:,.0f}" if x is not None else "n/a"

    where = f"{parcel.get('county')} {parcel.get('catalog_label')} lot {parcel.get('lot_number')}"
    if a.get("waterfront_any") and "waterfront" in cat:
        hits.append(("waterfront", f"Waterfront lot: {title} — {where} — min bid {money(min_bid)}"))
    if min_bid is not None:
        if "home" in cat or "cottage" in cat:
            if min_bid <= a.get("home_under", 20000):
                hits.append(("cheap_home", f"House/cottage under ${a['home_under']:,}: {title} — {where} — min bid {money(min_bid)}"))
        if any(k in cat for k in ("home", "cottage", "commercial building")):
            if min_bid <= a.get("structure_under", 10000):
                hits.append(("cheap_structure", f"Structure under ${a['structure_under']:,}: {title} — {where} — min bid {money(min_bid)}"))
        if acres and acres >= a.get("acreage_min_acres", 5) and min_bid <= a.get("acreage_under", 5000):
            hits.append(("cheap_acreage", f"{acres:g}-acre lot under ${a['acreage_under']:,}: {title} — {where} — min bid {money(min_bid)}"))
        if min_bid <= a.get("cheap_land_under", 1000):
            hits.append(("cheap_land", f"Lot under ${a['cheap_land_under']:,}: {title} — {where} — min bid {money(min_bid)}"))
    if not is_new and "min_bid" in changes:
        old, new = changes["min_bid"]["old"], changes["min_bid"]["new"]
        if old and new and old > 0 and (old - new) / old >= a.get("price_drop_pct", 20) / 100:
            hits.append(("price_drop", f"Price dropped {money(old)} → {money(new)}: {title} — {where}"))
    if not is_new and changes.get("status", {}).get("new") == "sold":
        sp = changes["status"]
        hits.append(("sold", f"SOLD: {title} — {where}"))
    return hits


def ingest_catalog(conn, src, cfg, c, run_id, stats, errors, seen_parcel_ids=None):
    """Full ingest of one catalog: CSV + listing page -> upserts + alerts."""
    try:
        rows = src.fetch_csv_rows(c["catalog_id"])
        extras = src.fetch_listing_extras(c["catalog_id"])
    except Exception as e:
        errors.append(f"{c['label']}: {e}")
        traceback.print_exc()
        return
    stats["catalogs_scanned"] += 1
    for row in rows:
        try:
            p = src.normalize(c, row, extras)
            p["county"] = c["county"]
            p["catalog_slug"] = (row.get("County") or "").strip().lower()
            outcome, changes, pid = store.upsert_parcel(conn, p)
            stats["parcels_seen"] += 1
            if seen_parcel_ids is not None:
                seen_parcel_ids.add(pid)
            if outcome == "new":
                stats["new_count"] += 1
            elif outcome == "changed":
                stats["changed_count"] += 1
            for rule, msg in alert_rules(cfg, p, changes, outcome == "new"):
                # don't re-alert the same parcel+rule twice
                dup = conn.execute(
                    "SELECT 1 FROM alerts WHERE parcel_id=? AND rule=?",
                    (pid, rule),
                ).fetchone()
                if not dup:
                    store.add_alert(conn, run_id, pid, rule, msg)
        except Exception as e:
            errors.append(f"{c['label']} lot {row.get('Lot Number')}: {e}")
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/scout.db")
    ap.add_argument("--site", default="docs")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    db_path = HERE / args.db
    db_path.parent.mkdir(parents=True, exist_ok=True)
    site_dir = HERE / args.site
    site_dir.mkdir(parents=True, exist_ok=True)

    conn = store.connect(str(db_path))
    run_id = store.start_run(conn)
    errors: list[str] = []
    stats = {"catalogs_scanned": 0, "parcels_seen": 0, "new_count": 0, "changed_count": 0}
    seen_parcel_ids: set[int] = set()

    try:
        src = TaxSaleSource(cfg)
        catalogs = src.discover_catalogs()
        if not args.quiet:
            print(f"discovered {len(catalogs)} catalogs on tax-sale.info", flush=True)

        targets = []
        for c in catalogs:
            county = county_of(c, cfg["counties"])
            if county:
                c["county"] = county
                targets.append(c)
        if not args.quiet:
            print(f"{len(targets)} catalogs match target counties", flush=True)

        for c in targets:
            store.upsert_catalog(conn, c, c.get("county"))
            ingest_catalog(conn, src, cfg, c, run_id, stats, errors, seen_parcel_ids)
            if not args.quiet:
                print(f"  {c['label']}: done", flush=True)
            conn.commit()

        # mark parcels not seen this run as stale (kept for history, hidden from feed)
        if seen_parcel_ids:
            conn.execute(
                """UPDATE parcels SET status='stale'
                   WHERE source='taxsale' AND status='available'
                   AND id NOT IN (%s)""" % ",".join("?" * len(seen_parcel_ids)),
                tuple(seen_parcel_ids),
            )
            conn.commit()
    except Exception as e:
        errors.append(f"fatal: {e}")
        traceback.print_exc()
    finally:
        store.finish_run(conn, run_id, stats, errors)
        conn.commit()

    feed_path = build_data_json(conn, cfg, site_dir / "data.json")
    alerts = store.run_alerts(conn, run_id)
    conn.close()

    summary = {
        "run_id": run_id,
        "stats": stats,
        "errors": errors,
        "alert_count": len(alerts),
        "alerts": [{"rule": a["rule"], "message": a["message"]} for a in alerts[:50]],
        "feed": str(feed_path),
    }
    print(json.dumps(summary, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
