let DATA = null;

const $ = (id) => document.getElementById(id);
const money = (x) => (x === null || x === undefined) ? "—" : "$" + Number(x).toLocaleString("en-US", {maximumFractionDigits: 0});
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function badges(p) {
  const b = [];
  if (p.category) b.push(`<span class="badge">${esc(p.category)}</span>`);
  if (p.county) b.push(`<span class="badge">${esc(p.county)} Co.</span>`);
  if (p.is_dnr) b.push(`<span class="badge dnr">DNR surplus</span>`);
  if (p.category === "Waterfront") b.push(`<span class="badge water">Waterfront</span>`);
  if (p.min_bid !== null && p.min_bid <= 1000) b.push(`<span class="badge hot">Under $1k</span>`);
  return b.join("");
}

function card(p, soldMode) {
  const price = soldMode
    ? `<div class="bid">${money(p.sold_price)}<small>sold</small></div>`
    : `<div class="bid">${money(p.min_bid)}<small>minimum bid</small></div>`;
  const meta = `
    <div class="meta">
      <div>Lot <b>${esc(p.lot)}</b></div>
      <div>Acres <b>${p.acres ?? "—"}</b></div>
      <div>SEV <b>${money(p.sev)}</b></div>
      <div>Back taxes <b>${money(p.current_taxes)}</b></div>
    </div>`;
  const addr = [p.address, p.local_unit].filter(Boolean).join(" · ");
  const actions = `
    <div class="actions">
      ${p.detail_url ? `<a class="a-lot" href="${esc(p.detail_url)}" target="_blank" rel="noopener">View lot</a>` : ""}
      ${p.map_url ? `<a class="a-map" href="${esc(p.map_url)}" target="_blank" rel="noopener">Map</a>` : ""}
    </div>`;
  return `<article class="card">
    ${p.photo_url ? `<img class="thumb" src="${esc(p.photo_url)}" alt="" loading="lazy">` : ""}
    <div class="badges">${badges(p)}</div>
    <h3>${esc(p.title)}</h3>
    ${price}${meta}
    ${addr ? `<div class="auction-line">${esc(addr)}</div>` : ""}
    ${p.comment ? `<p class="desc">${esc(p.comment)}</p>` : ""}
    <div class="auction-line">${esc(p.auction || "")}${p.auction_date ? " · " + esc(p.auction_date) : ""}</div>
    ${actions}
  </article>`;
}

function filtered() {
  const q = $("q").value.trim().toLowerCase();
  const county = $("f-county").value;
  const cat = $("f-cat").value;
  const dnr = $("f-dnr").value;
  const maxBid = parseFloat($("f-maxbid").value);
  const minAcres = parseFloat($("f-minacres").value);
  const water = $("f-water").checked;
  const sort = $("f-sort").value;
  let list = DATA.available.filter((p) => {
    if (county && p.county !== county) return false;
    if (cat && p.category !== cat) return false;
    if (dnr === "dnr" && !p.is_dnr) return false;
    if (dnr === "county" && p.is_dnr) return false;
    if (!isNaN(maxBid) && (p.min_bid === null || p.min_bid > maxBid)) return false;
    if (!isNaN(minAcres) && (p.acres === null || p.acres < minAcres)) return false;
    if (water && p.category !== "Waterfront") return false;
    if (q) {
      const hay = `${p.title} ${p.address} ${p.comment} ${p.county}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const disc = (p) => (p.min_bid != null && p.sev > 0) ? p.min_bid / p.sev : 1e12;
  const by = {
    "bid-asc": (a, b) => (a.min_bid ?? 1e12) - (b.min_bid ?? 1e12),
    "bid-desc": (a, b) => (b.min_bid ?? -1) - (a.min_bid ?? -1),
    "acres-desc": (a, b) => (b.acres ?? -1) - (a.acres ?? -1),
    "sev-desc": (a, b) => (b.sev ?? -1) - (a.sev ?? -1),
    "discount-desc": (a, b) => disc(a) - disc(b),
  }[sort];
  list.sort(by);
  return list;
}

// --- smart search templates -------------------------------------------
const TEMPLATES = [
  {name: "Waterfront deals", f: {water: true}, },
  {name: "Houses under $20k", f: {cat: "Homes/Cottages", maxBid: 20000}},
  {name: "5+ acres under $5k", f: {minAcres: 5, maxBid: 5000}},
  {name: "DNR land", f: {dnr: "dnr"}},
  {name: "Everything under $1k", f: {maxBid: 1000, sort: "bid-asc"}},
  {name: "Biggest SEV discounts", f: {sort: "discount-desc"}},
  {name: "Clear all", f: {}},
];

function applyTemplate(t, btn) {
  $("q").value = "";
  $("f-county").value = "";
  $("f-cat").value = t.f.cat || "";
  $("f-dnr").value = t.f.dnr || "";
  $("f-maxbid").value = t.f.maxBid ?? "";
  $("f-minacres").value = t.f.minAcres ?? "";
  $("f-water").checked = !!t.f.water;
  $("f-sort").value = t.f.sort || "bid-asc";
  saveFilters();
  render();
  document.querySelectorAll(".tpl").forEach((b) => b.classList.remove("on"));
  if (btn) btn.classList.add("on");
}

function renderTemplates() {
  $("templates").innerHTML = `<span class="tpl-label">Smart searches:</span>` + TEMPLATES.map(
    (t, i) => `<button class="tpl" data-i="${i}">${esc(t.name)}</button>`).join("");
  $("templates").addEventListener("click", (e) => {
    const b = e.target.closest(".tpl");
    if (b) applyTemplate(TEMPLATES[+b.dataset.i], b);
  });
}

// --- per-device filter memory ------------------------------------------
const FILTER_IDS = ["q", "f-county", "f-cat", "f-dnr", "f-maxbid", "f-minacres", "f-water", "f-sort"];
function saveFilters() {
  try {
    const v = {};
    FILTER_IDS.forEach((id) => { v[id] = $(id).type === "checkbox" ? $(id).checked : $(id).value; });
    localStorage.setItem("scout_filters", JSON.stringify(v));
  } catch (e) {}
}
function loadFilters() {
  try {
    const v = JSON.parse(localStorage.getItem("scout_filters") || "{}");
    FILTER_IDS.forEach((id) => {
      if (v[id] === undefined) return;
      if ($(id).type === "checkbox") $(id).checked = !!v[id]; else $(id).value = v[id];
    });
  } catch (e) {}
}

// --- market intel --------------------------------------------------------
function bar(pct, label) {
  return `<div class="ibar"><div class="ifill" style="width:${Math.min(100, pct)}%"></div>
    <span class="ilabel">${label}</span></div>`;
}
function money0(n) { return n == null ? "—" : "$" + Math.round(n).toLocaleString(); }

function renderIntel() {
  const a = DATA.analytics;
  if (!a) { $("intel").innerHTML = `<p class="note">Analytics not available yet.</p>`; return; }
  const st = a.sell_through.slice(0, 12);
  const maxRate = Math.max(...st.map((r) => r.rate), 0.01);
  const months = a.sales_by_month.slice(-12);
  const maxN = Math.max(...months.map((m) => m.n), 1);
  const mixMax = Math.max(...a.inventory_mix.map((m) => m.n), 1);

  $("intel").innerHTML = `
  <div class="intel-grid">
    <div class="icard">
      <h3>Sell-through rate by county</h3>
      <p class="inote">Share of listed lots that actually sold — high means competitive bidding.</p>
      ${st.map((r) => bar(r.rate / maxRate * 100,
        `${esc(r.county)} — ${Math.round(r.rate * 100)}% (${r.sold}/${r.closed})`)).join("")}
    </div>
    <div class="icard">
      <h3>Sales per auction month</h3>
      <p class="inote">Volume and average sold price over time.</p>
      ${months.map((m) => bar(m.n / maxN * 100, `${m.month} — ${m.n} sold · avg ${money0(m.avg_price)}`)).join("")}
    </div>
    <div class="icard">
      <h3>Available inventory mix</h3>
      <p class="inote">What's sitting in the current catalogs right now.</p>
      ${a.inventory_mix.map((m) => bar(m.n / mixMax * 100,
        `${esc(m.category)} — ${m.n} lots · avg bid ${money0(m.avg_bid)}`)).join("")}
    </div>
    <div class="icard">
      <h3>Deal benchmarks</h3>
      <p class="inote">Read these before you bid.</p>
      <ul class="ifacts">
        <li>Average asking bid is <strong>${a.bid_vs_sev.avg_ratio != null ? Math.round(a.bid_vs_sev.avg_ratio * 100) + "%" : "—"}</strong> of SEV
          <span>(SEV ≈ half of market value, so lower = deeper discount; ${a.bid_vs_sev.n} lots)</span></li>
        <li>Lots sell for <strong>${a.sold_vs_min.avg_ratio != null ? a.sold_vs_min.avg_ratio.toFixed(2) + "×" : "—"}</strong> their minimum bid on average
          <span>(${a.sold_vs_min.n} sales — bid above minimum on anything contested)</span></li>
        ${a.cheapest_waterfront.map((w) => `<li>Waterfront: <a href="${esc(w.detail_url || "#")}" target="_blank" rel="noopener">${esc(w.title || "lot")}</a>
          <strong>${money0(w.min_bid)}</strong> <span>(${esc(w.county || "")})</span></li>`).join("")}
      </ul>
    </div>
  </div>`;
}

function render() {
  const list = filtered();
  $("count").textContent = `${list.length} lot${list.length === 1 ? "" : "s"} shown`;
  $("grid").innerHTML = list.map((p) => card(p, false)).join("") || `<p class="note">No lots match those filters.</p>`;
}

function toCSV(rows) {
  const cols = ["county", "lot", "title", "category", "address", "local_unit", "min_bid", "current_taxes", "sev", "acres", "auction", "auction_date", "catalog", "is_dnr", "detail_url", "photo_url", "map_url"];
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  return [cols.join(","), ...rows.map((p) => cols.map((c) => q(p[c])).join(","))].join("\n");
}

async function sha256(str) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(str.trim()));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// To rotate the invite code: replace INVITE_HASH with the SHA-256 of the new code.
const INVITE_HASH = "75cb94edf36cde6b44bd7587f996247b99e3184852c5be218078edb577320788";

async function gate() {
  const gateEl = $("gate"), appEl = $("app");
  if (sessionStorage.getItem("scout_invite") === "1") {
    gateEl.classList.add("done");
    appEl.classList.remove("app-locked");
    return true;
  }
  gateEl.classList.add("open");
  return new Promise((resolve) => {
    const tryCode = async () => {
      const h = await sha256($("gate-code").value);
      if (h === INVITE_HASH) {
        sessionStorage.setItem("scout_invite", "1");
        gateEl.classList.remove("open");
        gateEl.classList.add("done");
        appEl.classList.remove("app-locked");
        resolve(true);
      } else {
        $("gate-err").textContent = "That code didn't work — try again.";
        resolve(false);
      }
    };
    $("gate-go").addEventListener("click", tryCode);
    $("gate-code").addEventListener("keydown", (e) => { if (e.key === "Enter") tryCode(); });
  });
}

async function boot() {
  if (await gate()) init().catch(bootError);
  // wrong code: gate stays open, listeners remain for another try
}

function bootError(e) {
  document.querySelector("main").innerHTML = `<p class="note">Couldn't load the latest scan data (${esc(e.message)}). Try refreshing.</p>`;
}

async function fetchData(retries = 2) {
  for (let i = 0; i <= retries; i++) {
    try {
      const res = await fetch("data.json", {cache: "no-store"});
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      if (i === retries) throw e;
      await new Promise((r) => setTimeout(r, 1200));
    }
  }
}

async function init() {
  DATA = await fetchData();

  $("stats").innerHTML = `
    <div class="stat"><b>${DATA.stats.available.toLocaleString()}</b><span>available lots</span></div>
    <div class="stat"><b>${DATA.stats.counties_with_inventory}</b><span>counties</span></div>
    <div class="stat"><b>${DATA.stats.sold_tracked.toLocaleString()}</b><span>recent sales tracked</span></div>`;
  $("generated").textContent = new Date(DATA.generated_at).toLocaleString();

  const co = $("f-county");
  DATA.counties.forEach((c) => { const o = document.createElement("option"); o.value = c; o.textContent = c; co.appendChild(o); });
  const ct = $("f-cat");
  DATA.categories.forEach((c) => { const o = document.createElement("option"); o.value = c; o.textContent = c; ct.appendChild(o); });

  ["q", "f-county", "f-cat", "f-dnr", "f-maxbid", "f-minacres", "f-water", "f-sort"].forEach((id) => {
    $(id).addEventListener("input", () => { saveFilters(); render(); });
    $(id).addEventListener("change", () => { saveFilters(); render(); });
  });
  loadFilters();
  renderTemplates();
  $("dl-csv").addEventListener("click", () => {
    const blob = new Blob([toCSV(filtered())], {type: "text/csv"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "upnorth-scout-lots.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  });

  document.querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
      t.classList.add("active");
      document.querySelectorAll(".panel").forEach((p) => p.classList.add("hidden"));
      $("tab-" + t.dataset.tab).classList.remove("hidden");
    });
  });

  render();
  renderIntel();
  $("sold-grid").innerHTML = DATA.recently_sold.map((p) => card(p, true)).join("");

  const today = new Date().toISOString().slice(0, 10);
  $("calendar").innerHTML = DATA.calendar.map((c) => {
    const past = c.auction_date < today;
    return `<div class="cal-row"><span class="d">${esc(c.auction_date || "TBA")}</span>
      <span><strong>${esc(c.auction_name || "")}</strong>
      ${past ? '<span class="past-tag">past</span>' : '<span class="past-tag" style="color:#3f6b4f;border-color:#3f6b4f">upcoming</span>'}</span>
      <span class="n">${c.avail} available tracked · ${c.n} lots total</span></div>`;
  }).join("");

  $("watchlist").innerHTML = DATA.watchlist.map((w) => `
    <div class="watch"><h3><a href="${esc(w.url)}" target="_blank" rel="noopener">${esc(w.name)}</a></h3>
    <p>${esc(w.note)}</p></div>`).join("");
}

boot();
