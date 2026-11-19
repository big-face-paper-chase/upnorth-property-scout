"""Build the public data feed (data.json) consumed by the dashboard site."""
import json
from datetime import datetime, timezone
from pathlib import Path

import store


def _parcel_view(p: dict) -> dict:
    lat, lng = p.get("latitude"), p.get("longitude")
    map_url = None
    if lat and lng:
        map_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    return {
        "county": p.get("county"),
        "lot": p.get("lot_number"),
        "title": p.get("title"),
        "category": p.get("category"),
        "address": p.get("address"),
        "local_unit": p.get("local_unit"),
        "min_bid": p.get("min_bid"),
        "current_taxes": p.get("current_taxes"),
        "sev": p.get("sev"),
        "acres": p.get("acres"),
        "comment": (p.get("comment") or "")[:600],
        "auction": p.get("auction_name"),
        "auction_date": p.get("auction_date"),
        "catalog": p.get("catalog_label"),
        "is_dnr": bool(p.get("is_dnr")),
        "detail_url": p.get("detail_url"),
        "photo_url": p.get("photo_url"),
        "map_url": map_url,
        "sold_price": p.get("sold_price"),
        "status": p.get("status"),
    }


def _analytics(conn) -> dict:
    """Market-intel aggregates: sell-through, pricing, trends."""
    out: dict = {}

    # sell-through + median sold price by county
    rows = conn.execute(
        """SELECT county,
                  SUM(CASE WHEN status='sold' THEN 1 ELSE 0 END) AS sold,
                  SUM(CASE WHEN status IN ('sold','unsold') THEN 1 ELSE 0 END) AS closed,
                  AVG(CASE WHEN status='sold' THEN sold_price END) AS avg_sold
           FROM parcels WHERE county IS NOT NULL AND status IN ('sold','unsold','available')
           GROUP BY county HAVING closed >= 3 ORDER BY sold * 1.0 / closed DESC"""
    ).fetchall()
    out["sell_through"] = [
        {"county": r["county"], "sold": r["sold"], "closed": r["closed"],
         "rate": round(r["sold"] / r["closed"], 3) if r["closed"] else 0,
         "avg_sold": round(r["avg_sold"]) if r["avg_sold"] else None}
        for r in rows
    ]

    # how far under SEV are asking bids? (SEV ~= half of market value)
    r = conn.execute(
        """SELECT AVG(min_bid * 1.0 / sev) AS ratio, COUNT(*) AS n
           FROM parcels WHERE status='available' AND min_bid > 0 AND sev > 0"""
    ).fetchone()
    out["bid_vs_sev"] = {"avg_ratio": round(r["ratio"], 3) if r["ratio"] else None,
                         "n": r["n"]}

    # how much over minimum do lots actually sell for?
    r = conn.execute(
        """SELECT AVG(sold_price * 1.0 / min_bid) AS ratio, COUNT(*) AS n
           FROM parcels WHERE status='sold' AND sold_price > 0 AND min_bid > 0"""
    ).fetchone()
    out["sold_vs_min"] = {"avg_ratio": round(r["ratio"], 3) if r["ratio"] else None,
                          "n": r["n"]}

    # sales volume by auction month
    rows = conn.execute(
        """SELECT substr(auction_date, 1, 7) AS month, COUNT(*) AS n,
                  AVG(sold_price) AS avg_price
           FROM parcels WHERE status='sold' AND auction_date IS NOT NULL
           GROUP BY month ORDER BY month"""
    ).fetchall()
    out["sales_by_month"] = [
        {"month": r["month"], "n": r["n"],
         "avg_price": round(r["avg_price"]) if r["avg_price"] else None}
        for r in rows
    ]

    # available inventory mix by category
    rows = conn.execute(
        """SELECT category, COUNT(*) AS n, AVG(min_bid) AS avg_bid
           FROM parcels WHERE status='available'
           GROUP BY category ORDER BY n DESC"""
    ).fetchall()
    out["inventory_mix"] = [
        {"category": r["category"] or "Uncategorized", "n": r["n"],
         "avg_bid": round(r["avg_bid"]) if r["avg_bid"] else None}
        for r in rows
    ]

    # cheapest waterfront / houses right now (headline deals)
    rows = conn.execute(
        """SELECT county, title, min_bid, detail_url FROM parcels
           WHERE status='available' AND category='Waterfront' AND min_bid IS NOT NULL
           ORDER BY min_bid LIMIT 5"""
    ).fetchall()
    out["cheapest_waterfront"] = [dict(r) for r in rows]
    return out


def build_data_json(conn, cfg: dict, out_path: Path) -> Path:
    available = [_parcel_view(p) for p in store.report_parcels(conn)]
    sold = [_parcel_view(p) for p in store.sold_parcels(conn)]
    counties = sorted({p["county"] for p in available if p.get("county")})
    categories = sorted({p["category"] for p in available if p.get("category")})

    # auction calendar: distinct upcoming auctions seen across catalogs
    rows = conn.execute(
        """SELECT auction_name, auction_date, COUNT(*) as n,
                  SUM(CASE WHEN status='available' THEN 1 ELSE 0 END) as avail
           FROM parcels WHERE auction_date IS NOT NULL
           GROUP BY auction_name, auction_date ORDER BY auction_date"""
    ).fetchall()
    calendar = [dict(r) for r in rows]

    latest_run = conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()

    feed = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "region": cfg.get("region_name"),
        "source_note": ("Michigan county tax-foreclosure auctions via tax-sale.info "
                        "(Title Check, LLC). For research only — always verify with the "
                        "county treasurer before bidding."),
        "stats": {
            "available": len(available),
            "counties_with_inventory": len(counties),
            "sold_tracked": len(sold),
        },
        "counties": counties,
        "categories": categories,
        "calendar": calendar,
        "watchlist": cfg.get("watchlist", []),
        "available": available,
        "recently_sold": sold[:300],
        "analytics": _analytics(conn),
        "last_run": dict(latest_run) if latest_run else None,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(feed, indent=1))
    return out_path
