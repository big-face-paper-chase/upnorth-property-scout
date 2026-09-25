"""SQLite store for the Up North Property Scout."""
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS parcels (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    catalog_id INTEGER NOT NULL,
    catalog_slug TEXT,
    catalog_label TEXT,
    county TEXT,
    auction_name TEXT,
    auction_date TEXT,
    is_dnr INTEGER DEFAULT 0,
    lot_number TEXT,
    title TEXT,
    category TEXT,
    address TEXT,
    local_unit TEXT,
    parcel_id TEXT,
    min_bid REAL,
    current_taxes REAL,
    sev REAL,
    acres REAL,
    legal_description TEXT,
    comment TEXT,
    latitude REAL,
    longitude REAL,
    detail_url TEXT,
    status TEXT,
    sold_price REAL,
    photo_url TEXT,
    first_seen TEXT,
    last_seen TEXT,
    UNIQUE(source, catalog_id, lot_number)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    catalogs_scanned INTEGER,
    parcels_seen INTEGER,
    new_count INTEGER,
    changed_count INTEGER,
    errors TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    run_id INTEGER,
    parcel_id INTEGER,
    rule TEXT,
    message TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_parcels_county ON parcels(county);
CREATE INDEX IF NOT EXISTS idx_parcels_status ON parcels(status);
CREATE TABLE IF NOT EXISTS catalogs (
    catalog_id INTEGER PRIMARY KEY,
    label TEXT,
    county TEXT,
    auction_name TEXT,
    auction_date TEXT,
    is_dnr INTEGER DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS parcel_history (
    id INTEGER PRIMARY KEY,
    parcel_id INTEGER NOT NULL,
    changed_at TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_parcel ON parcel_history(parcel_id);
"""

PARCEL_COLS = [
    "source", "catalog_id", "catalog_slug", "catalog_label", "county",
    "auction_name", "auction_date", "is_dnr", "lot_number", "title",
    "category", "address", "local_unit", "parcel_id", "min_bid",
    "current_taxes", "sev", "acres", "legal_description", "comment",
    "latitude", "longitude", "detail_url", "status", "sold_price", "photo_url",
]


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # lightweight migrations for existing DBs
    cols = {r[1] for r in conn.execute("PRAGMA table_info(parcels)").fetchall()}
    if "photo_url" not in cols:
        conn.execute("ALTER TABLE parcels ADD COLUMN photo_url TEXT")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


COMPARE_FIELDS = ["min_bid", "status", "sold_price", "title", "category"]


def upsert_parcel(conn: sqlite3.Connection, p: dict) -> tuple[str, dict, int]:
    """Insert or update a parcel. Returns (outcome, changes, parcel_id)."""
    now = _now()
    cur = conn.execute(
        "SELECT * FROM parcels WHERE source=? AND catalog_id=? AND lot_number=?",
        (p["source"], p["catalog_id"], p["lot_number"]),
    )
    row = cur.fetchone()
    vals = [p.get(c) for c in PARCEL_COLS]
    if row is None:
        cols = ",".join(PARCEL_COLS)
        ph = ",".join("?" * len(PARCEL_COLS))
        cur = conn.execute(
            f"INSERT INTO parcels ({cols}, first_seen, last_seen) VALUES ({ph}, ?, ?)",
            (*vals, now, now),
        )
        return "new", {}, cur.lastrowid
    changes = {}
    for f in COMPARE_FIELDS:
        old, new = row[f], p.get(f)
        if (old or None) != (new or None):
            # treat numeric wobble (e.g. 11088.77 vs 11088.770) as equal
            try:
                if old is not None and new is not None and abs(float(old) - float(new)) < 0.005:
                    continue
            except (TypeError, ValueError):
                pass
            changes[f] = {"old": old, "new": new}
    sets = ",".join(f"{c}=?" for c in PARCEL_COLS)
    conn.execute(
        f"UPDATE parcels SET {sets}, last_seen=? WHERE id=?",
        (*vals, now, row["id"]),
    )
    if changes:
        record_history(conn, row["id"], changes)
    return ("changed" if changes else "same"), changes, row["id"]


def record_history(conn: sqlite3.Connection, parcel_id: int, changes: dict):
    """Append per-field change records (price/status timeline for the lot)."""
    now = _now()
    for field, ch in changes.items():
        conn.execute(
            """INSERT INTO parcel_history (parcel_id, changed_at, field, old_value, new_value)
               VALUES (?,?,?,?,?)""",
            (parcel_id, now, field,
             None if ch["old"] is None else str(ch["old"]),
             None if ch["new"] is None else str(ch["new"])),
        )


def start_run(conn: sqlite3.Connection) -> int:
    cur = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (_now(),))
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, stats: dict, errors: list[str]):
    conn.execute(
        """UPDATE runs SET finished_at=?, catalogs_scanned=?, parcels_seen=?,
           new_count=?, changed_count=?, errors=? WHERE id=?""",
        (_now(), stats.get("catalogs_scanned", 0), stats.get("parcels_seen", 0),
         stats.get("new_count", 0), stats.get("changed_count", 0),
         "\n".join(errors[:20]), run_id),
    )


def add_alert(conn: sqlite3.Connection, run_id: int, parcel_id: int, rule: str, message: str):
    conn.execute(
        "INSERT INTO alerts (run_id, parcel_id, rule, message, created_at) VALUES (?,?,?,?,?)",
        (run_id, parcel_id, rule, message, _now()),
    )


def get_parcel(conn: sqlite3.Connection, parcel_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM parcels WHERE id=?", (parcel_id,)).fetchone()


def upsert_catalog(conn: sqlite3.Connection, c: dict, county: str | None):
    """Track a discovered catalog; returns True if it is newly seen."""
    row = conn.execute("SELECT catalog_id FROM catalogs WHERE catalog_id=?",
                       (c["catalog_id"],)).fetchone()
    now = _now()
    if row:
        conn.execute(
            """UPDATE catalogs SET label=?, county=?, auction_name=?, auction_date=?,
               is_dnr=?, last_seen=? WHERE catalog_id=?""",
            (c.get("label"), county, c.get("auction_name"), c.get("auction_date"),
             1 if c.get("is_dnr") else 0, now, c["catalog_id"]),
        )
        return False
    conn.execute(
        """INSERT INTO catalogs
           (catalog_id, label, county, auction_name, auction_date, is_dnr, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?)""",
        (c["catalog_id"], c.get("label"), county, c.get("auction_name"),
         c.get("auction_date"), 1 if c.get("is_dnr") else 0, now, now),
    )
    return True


def hot_catalogs(conn: sqlite3.Connection, today_iso: str) -> list[dict]:
    """Target-region catalogs whose auction date is today (live bidding)."""
    rows = conn.execute(
        "SELECT * FROM catalogs WHERE auction_date=? AND county IS NOT NULL",
        (today_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_lot_sold(conn: sqlite3.Connection, catalog_id: int, lot_number: str,
                  sold_price: float | None) -> dict | None:
    """Flip an available parcel to sold; returns the parcel row if it changed."""
    row = conn.execute(
        """SELECT * FROM parcels WHERE source='taxsale' AND catalog_id=?
           AND lot_number=? AND status='available'""",
        (catalog_id, lot_number),
    ).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE parcels SET status='sold', sold_price=?, last_seen=? WHERE id=?",
        (sold_price, _now(), row["id"]),
    )
    record_history(conn, row["id"], {
        "status": {"old": "available", "new": "sold"},
        "sold_price": {"old": row["sold_price"], "new": sold_price},
    })
    return dict(row)


def report_parcels(conn: sqlite3.Connection) -> list[dict]:
    """All currently-available parcels in target region, newest auctions first."""
    rows = conn.execute(
        """SELECT * FROM parcels
           WHERE status='available'
           ORDER BY auction_date DESC, county, min_bid"""
    ).fetchall()
    return [dict(r) for r in rows]


def sold_parcels(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM parcels WHERE status='sold' AND sold_price IS NOT NULL
           ORDER BY auction_date DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def run_alerts(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT a.*, p.county, p.title, p.lot_number, p.min_bid, p.category,
                  p.acres, p.detail_url, p.auction_date, p.catalog_label
           FROM alerts a JOIN parcels p ON p.id = a.parcel_id
           WHERE a.run_id=? ORDER BY p.min_bid""",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]
