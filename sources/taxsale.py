"""tax-sale.info source: Michigan county tax-foreclosure auctions (Title Check, LLC).

The site exposes, per county auction catalog:
  - HTML listing page: /listings/catalog/{id}   (lot grid, categories, sold prices)
  - CSV export:        /catalog/getCsv/id/{id}  (structured parcel data, no auth)
  - Auctions index:    /auctions                (all current + past-season catalogs)

All endpoints used here are public and require no login.
"""
import csv
import io
import re
import time
from html import unescape as html_unescape
from datetime import date

import requests

BASE = "https://www.tax-sale.info"
HEADERS = {
    "User-Agent": "UpNorthPropertyScout/1.0 (public-interest research bot; contact via repo)",
    "Accept": "text/html,application/xhtml+xml,text/csv,*/*",
}

CATEGORIES = [
    "Waterfront",
    "Homes/Cottages",
    "Acreage",
    "Commercial Building",
    "Commercial Lot",
    "Vacant Lot",
]

MONTHS = ("January February March April May June July August September October "
          "November December").split()
DATE_RE = re.compile(r"(%s)\s+(\d{1,2}),\s+(\d{4})" % "|".join(MONTHS))
CATALOG_LINK_RE = re.compile(
    r'<a[^>]+href="(/listings/catalog/(\d+))"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
LOT_LINK_RE = re.compile(
    r'<a[^>]+href="(/lot/show/id/(\d+))"[^>]*aria-label="Lot\s+(\d+)\s*-\s*([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
LOT_LINK_RE_NOLABEL = re.compile(
    r'<a[^>]+href="(/lot/show/id/(\d+))"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
LOT_IMG_RE = re.compile(
    r'<img[^>]*title="Lot\s+\d+"[^>]*>', re.IGNORECASE)
LOT_IMG_SRC_RE = re.compile(r'src="([^"]+)"', re.IGNORECASE)


def infer_category(title: str, acres) -> str | None:
    """Keyword fallback when the listing page doesn't tag a lot's category."""
    t = (title or "").lower()
    if "waterfront" in t:
        return "Waterfront"
    if any(k in t for k in ("house", "cottage", "cabin", "mobile home", "residence", "dwelling")):
        return "Homes/Cottages"
    if "commercial building" in t or "commercial structure" in t:
        return "Commercial Building"
    if "commercial" in t:
        return "Commercial Lot"
    if any(k in t for k in ("vacant lot", "vacant parcel", "vacant land")):
        try:
            return "Acreage" if acres and acres >= 2 else "Vacant Lot"
        except TypeError:
            return "Vacant Lot"
    if acres:
        try:
            return "Acreage" if acres >= 2 else "Vacant Lot"
        except TypeError:
            return None
    return None

# site's category vocabulary -> our normalized labels
CATEGORY_MAP = {
    "home or cottage": "Homes/Cottages",
    "waterfront": "Waterfront",
    "acreage": "Acreage",
    "commercial building": "Commercial Building",
    "commercial lot": "Commercial Lot",
    "vacant lot": "Vacant Lot",
}
H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
MONEY_RE = re.compile(r"\$([\d,]+\.\d{2})")
ACRES_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*acres?\b", re.IGNORECASE)


def _clean(html: str) -> str:
    return WS_RE.sub(" ", TAG_RE.sub(" ", html)).strip()


def _money(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        return float(s.replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None


def _parse_date(s: str) -> str | None:
    m = DATE_RE.search(s or "")
    if not m:
        return None
    mon, day, year = m.group(1), m.group(2), m.group(3)
    return f"{year}-{MONTHS.index(mon) + 1:02d}-{int(day):02d}"


def _get(session: requests.Session, url: str, timeout: int, retries: int = 3) -> requests.Response:
    """GET with retries — the sandbox egress proxy drops connections intermittently."""
    last = None
    for attempt in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise last


class TaxSaleSource:
    def __init__(self, cfg: dict):
        scfg = cfg["sources"]["taxsale"]
        self.auctions_url = scfg["auctions_url"]
        self.listing_url = scfg["listing_url"]
        self.csv_url = scfg["csv_url"]
        self.delay = float(scfg.get("request_delay_seconds", 0.6))
        self.timeout = int(scfg.get("timeout_seconds", 30))
        self.session = requests.Session()

    # ---- discovery -----------------------------------------------------
    def discover_catalogs(self) -> list[dict]:
        """Scrape /auctions index -> every catalog with its auction name/date."""
        html = _get(self.session, self.auctions_url, self.timeout).text
        # map each catalog link to the nearest preceding <h2> section + date
        sections = [(m.start(), _clean(m.group(1))) for m in H2_RE.finditer(html)]
        catalogs = []
        for m in CATALOG_LINK_RE.finditer(html):
            href, cid, inner = m.group(1), int(m.group(2)), _clean(m.group(3))
            label = inner
            section, sec_pos = "", 0
            section = ""
            for pos, title in sections:
                if pos < m.start():
                    section, sec_pos = title, pos
                else:
                    break
            # the auction date sits between the <h2> and the catalog links
            sec_date = _parse_date(_clean(html[sec_pos: m.start()]))
            catalogs.append({
                "catalog_id": cid,
                "label": label,
                "auction_name": section,
                "auction_date": sec_date,
                "listing_url": BASE + href,
                "is_dnr": "dnr" in label.lower(),
            })
        # de-dupe (index sometimes repeats links)
        seen, out = set(), []
        for c in catalogs:
            if c["catalog_id"] not in seen:
                seen.add(c["catalog_id"])
                out.append(c)
        return out

    # ---- per-catalog fetch ---------------------------------------------
    def fetch_csv_rows(self, catalog_id: int) -> list[dict]:
        url = self.csv_url.format(id=catalog_id)
        try:
            r = _get(self.session, url, self.timeout)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return []
            raise
        time.sleep(self.delay)
        text = r.text
        # the CSV sometimes starts with a BOM or stray whitespace
        text = text.lstrip("\ufeff \t\r\n")
        reader = csv.DictReader(io.StringIO(text))
        return [row for row in reader if (row.get("Lot Number") or "").strip()]

    def fetch_listing_extras(self, catalog_id: int) -> dict[str, dict]:
        """Parse the HTML listing page -> {lot_number: {category, title, detail_url, sold_price}}."""
        url = self.listing_url.format(id=catalog_id)
        try:
            html = _get(self.session, url, self.timeout).text
        except requests.HTTPError:
            return {}
        time.sleep(self.delay)
        extras: dict[str, dict] = {}

        def handle(href, lot, cat_raw, inner):
            text = _clean(inner)
            cat = CATEGORY_MAP.get((cat_raw or "").strip().lower())
            if not cat:
                for c in ("Commercial Building", "Commercial Lot", "Homes/Cottages",
                          "Waterfront", "Acreage", "Vacant Lot", "Home", "Cottage"):
                    if c.lower() in text.lower():
                        cat = "Homes/Cottages" if c in ("Home", "Cottage") else c
                        break
            sold = None
            if "sold for" in text.lower():
                sm = MONEY_RE.search(text)
                if sm:
                    sold = _money(sm.group(0))
            lots = list(re.finditer(r"\bLot\s+(\d+)\b", text))
            post = text[lots[-1].end():] if lots else text
            title = re.split(r"Minimum Bid:|Sold for", post, flags=re.IGNORECASE)[0]
            title = re.sub(r"^[A-Za-z .'\-]+Re-Offer:\s*", "", title).strip(" -:")
            title = WS_RE.sub(" ", title)[:220]
            title = html_unescape(title)
            photo = None
            im = LOT_IMG_RE.search(inner)
            if im:
                sm = LOT_IMG_SRC_RE.search(im.group(0))
                if sm:
                    photo = sm.group(1)
            entry = extras.setdefault(lot, {})
            if cat and not entry.get("category"):
                entry["category"] = cat
            if sold and not entry.get("sold_price"):
                entry["sold_price"] = sold
            if not entry.get("detail_url"):
                entry["detail_url"] = BASE + href
            if title and not entry.get("title"):
                entry["title"] = title
            if photo and not entry.get("photo_url"):
                entry["photo_url"] = photo

        for m in LOT_LINK_RE.finditer(html):
            href, _lid, lot, cat_raw, inner = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            handle(href, lot, cat_raw, inner)
        for m in LOT_LINK_RE_NOLABEL.finditer(html):
            href, _lid, inner = m.group(1), m.group(2), m.group(3)
            text = _clean(inner)
            lm = re.search(r"\bLot\s+(\d+)\b", text)
            if lm and lm.group(1) not in extras:
                handle(href, lm.group(1), "", inner)
        return extras

    # ---- normalization ---------------------------------------------------
    @staticmethod
    def acres_from(text: str) -> float | None:
        m = ACRES_RE.search(text or "")
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None

    def normalize(self, catalog: dict, row: dict, extras: dict) -> dict:
        lot = (row.get("Lot Number") or "").strip()
        ex = extras.get(lot, {})
        comment = (row.get("Comment 2") or "").strip()
        title = html_unescape(ex.get("title") or (row.get("Address") or "").strip() or f"Lot {lot}")
        acres = self.acres_from(title + " " + comment)
        category = ex.get("category") or infer_category(title, acres)
        slug = (row.get("County") or "").strip().lower()
        sold_price = ex.get("sold_price")
        today = date.today().isoformat()
        auc_date = catalog.get("auction_date")
        if auc_date and auc_date < today:
            status = "sold" if sold_price else "unsold"
        else:
            status = "available"
        return {
            "source": "taxsale",
            "catalog_id": catalog["catalog_id"],
            "catalog_slug": slug,
            "catalog_label": catalog["label"],
            "auction_name": catalog.get("auction_name") or "",
            "auction_date": auc_date,
            "is_dnr": catalog.get("is_dnr", False),
            "lot_number": lot,
            "title": title,
            "category": category,
            "address": (row.get("Address") or "").strip(),
            "local_unit": (row.get("Local Unit") or "").strip(),
            "parcel_id": (row.get("Parcel Id") or "").strip(),
            "min_bid": _money(row.get("Minimum Bid")),
            "current_taxes": _money(row.get("Current Taxes")),
            "sev": _money(row.get("SEV")),
            "acres": acres,
            "legal_description": (row.get("Legal Description") or "").strip(),
            "comment": comment,
            "latitude": _money(row.get("Latitude")),
            "longitude": _money((row.get("Longitude") or "").replace("W", "-")),
            "detail_url": ex.get("detail_url"),
            "photo_url": ex.get("photo_url"),
            "status": status,
            "sold_price": sold_price,
        }
