// KitchenCheck dashboard — vanilla JS, no build step.

const API = "";  // same-origin

const els = {
  search: document.getElementById("search"),
  filterType: document.getElementById("filter-type"),
  filterBand: document.getElementById("filter-band"),
  sort: document.getElementById("sort"),
  cards: document.getElementById("cards"),
  detail: document.getElementById("detail"),
  statTotal: document.getElementById("stat-total"),
  statRated: document.getElementById("stat-rated"),
  statMean: document.getElementById("stat-mean"),
  statBands: document.getElementById("stat-bands"),
  city: document.getElementById("city"),
};

const BAND_CLASS = {
  "A+": "b-aplus", "A": "b-a", "B": "b-b",
  "C": "b-c", "NC": "b-nc", "UNRATED": "b-unrated"
};
const BAND_LABEL = {
  "A+": "Exemplary", "A": "Satisfactory", "B": "Needs improvement",
  "C": "Significant risk", "NC": "Non-compliant", "UNRATED": "Unrated"
};
const TYPE_LABEL = {
  restaurant: "Restaurant", cloud_kitchen: "Cloud kitchen",
  qsr: "QSR / Café", sweet_shop: "Sweet shop / Bakery"
};

function fmtDate(s) {
  if (!s) return "—";
  const d = new Date(s);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function loadStats() {
  const r = await fetch(`${API}/api/stats`);
  const s = await r.json();
  els.city.textContent = s.city;
  els.statTotal.textContent = s.total_establishments;
  els.statRated.textContent = `${s.rated} / ${s.total_establishments}`;
  els.statMean.textContent = s.mean_kc_score ?? "—";

  els.statBands.innerHTML = `
    <div class="stat-label">Grade distribution</div>
    ${Object.entries(s.band_distribution).map(([band, n]) => `
      <div class="band-row">
        <span class="band-pill ${BAND_CLASS[band] || "b-unrated"}">${band}</span>
        <span>${n}</span>
      </div>
    `).join("")}
  `;
}

async function loadList() {
  const params = new URLSearchParams();
  if (els.search.value.trim()) params.set("q", els.search.value.trim());
  if (els.filterType.value) params.set("type", els.filterType.value);
  if (els.filterBand.value) params.set("band", els.filterBand.value);
  if (els.sort.value) params.set("sort", els.sort.value);

  const r = await fetch(`${API}/api/establishments?${params}`);
  const data = await r.json();
  renderCards(data.results);
}

function renderCards(items) {
  if (!items.length) {
    els.cards.innerHTML = `<div class="empty">No kitchens match these filters.</div>`;
    return;
  }
  els.cards.innerHTML = items.map(it => {
    const score = it.kc_score === null ? "—" : it.kc_score;
    const bandClass = BAND_CLASS[it.band] || "b-unrated";
    const flags = [];
    if (it.is_new) flags.push(`<span class="flag new">New</span>`);
    if (it.stale) flags.push(`<span class="flag warn">Stale</span>`);
    if (it.band === "NC") flags.push(`<span class="flag danger">Critical NC</span>`);
    if (it.risk_prob >= 0.5) flags.push(`<span class="flag warn">High risk</span>`);
    return `
      <article class="card" data-slug="${it.slug}">
        <div class="card-head">
          <div class="grade-badge ${bandClass}">${it.band}</div>
          <div>
            <div class="card-title">${it.name}</div>
            <div class="card-meta">${TYPE_LABEL[it.type] || it.type} · ${it.cuisine}</div>
            <div class="card-meta">${it.area}</div>
          </div>
        </div>
        <div class="card-stats">
          <div><div class="k">KC score</div><div class="v">${score}</div></div>
          <div><div class="k">Risk</div><div class="v">${(it.risk_prob * 100).toFixed(0)}%</div></div>
          <div><div class="k">Inspected</div><div class="v">${fmtDate(it.last_inspection_date)}</div></div>
        </div>
        ${flags.length ? `<div class="flags">${flags.join("")}</div>` : ""}
      </article>
    `;
  }).join("");

  els.cards.querySelectorAll(".card").forEach(c => {
    c.addEventListener("click", () => openDetail(c.dataset.slug));
  });
}

function barClass(pct) {
  if (pct >= 80) return "bar-good";
  if (pct >= 50) return "bar-warn";
  return "bar-bad";
}

async function openDetail(slug) {
  const r = await fetch(`${API}/api/establishments/${slug}`);
  const e = await r.json();
  const s = e.score;
  const bandClass = BAND_CLASS[s.band] || "b-unrated";

  const sections = s.compliance ? s.compliance.section_breakdown.map(sec => `
    <div class="bar-row">
      <span>${sec.title}</span>
      <div class="bar ${barClass(sec.pct)}"><div style="width:${sec.pct}%"></div></div>
      <span>${sec.pct}%</span>
    </div>
  `).join("") : `<div class="empty">No inspections on file.</div>`;

  const violations = s.compliance && s.compliance.top_violations.length
    ? s.compliance.top_violations.map(v => `
      <li class="violation-item ${v.critical ? "crit" : ""}">
        <div class="vlabel">${v.label}</div>
        <div class="vmeta">${v.section} · ${v.status}${v.critical ? " · CRITICAL" : ""} · −${v.marks_lost.toFixed(1)} marks</div>
      </li>
    `).join("")
    : `<li class="violation-item">No violations at last inspection.</li>`;

  const drivers = s.risk.drivers.map(d => `
    <li class="driver-item">
      <div>${d.explanation}</div>
      <div class="driver-impact">${d.impact >= 0 ? "+" : ""}${d.impact}</div>
    </li>
  `).join("");

  const trend = s.trend ? `
    <div class="trend">
      ${s.trend.map(t => `
        <div class="trend-col">
          <div class="trend-bar" style="height:${Math.max(t.pct * 0.5, 4)}px" title="${t.pct}% on ${t.date}"></div>
          <div class="trend-label">${t.date.slice(2, 7)}</div>
        </div>
      `).join("")}
    </div>
  ` : "";

  const badgeUrl = `${window.location.origin}/api/badge/${e.slug}.svg`;

  els.detail.classList.remove("hidden");
  els.detail.innerHTML = `
    <div class="detail-head">
      <div class="grade-badge ${bandClass}">${s.band}</div>
      <div style="flex:1; min-width: 240px;">
        <div class="detail-title">${e.name}</div>
        <div class="detail-sub">${TYPE_LABEL[e.type] || e.type} · ${e.cuisine} · ${e.area}</div>
        <div class="detail-sub">${e.address}</div>
        <div class="detail-sub">FSSAI License ${e.license_id} · Since ${new Date().getFullYear() - (e.years_operation || 0)}</div>
      </div>
      <div style="text-align: right;">
        <div class="detail-sub">KitchenCheck score</div>
        <div style="font-size: 36px; font-weight: 800;">${s.kc_score ?? "—"}</div>
        <div class="detail-sub">${BAND_LABEL[s.band] || ""}</div>
        <button class="close-btn" id="close-detail" style="margin-top:8px;">Close</button>
      </div>
    </div>

    <div class="detail-grid">
      <div class="section">
        <h3>Section breakdown</h3>
        ${sections}
      </div>
      <div class="section">
        <h3>Compliance trend</h3>
        ${trend || `<div class="empty">No history yet.</div>`}
        <div class="detail-sub" style="margin-top:12px;">
          Last inspected ${fmtDate(s.last_inspection_date)}
          ${s.last_inspector ? "· " + s.last_inspector : ""}
          ${s.stale ? "· <span style='color:var(--band-b)'>stale (&gt;180d)</span>" : ""}
        </div>
      </div>
      <div class="section">
        <h3>Top violations</h3>
        <ul class="violation-list">${violations}</ul>
      </div>
      <div class="section">
        <h3>Risk drivers · ${(s.risk.risk_prob * 100).toFixed(0)}% 90-day risk</h3>
        <ul class="driver-list">${drivers}</ul>
      </div>
    </div>

    <div class="badge-embed">
      <img src="${badgeUrl}" alt="KitchenCheck badge" />
      <div style="flex:1; min-width: 0;">
        <div class="detail-sub" style="margin-bottom: 6px;">Embeddable trust badge</div>
        <code>&lt;a href="${window.location.origin}/?slug=${e.slug}"&gt;
  &lt;img src="${badgeUrl}" alt="KitchenCheck grade ${s.band}"/&gt;
&lt;/a&gt;</code>
      </div>
    </div>
  `;

  document.getElementById("close-detail").addEventListener("click", () => {
    els.detail.classList.add("hidden");
    els.detail.innerHTML = "";
    window.history.replaceState({}, "", "/");
  });

  window.history.replaceState({}, "", `/?slug=${e.slug}`);
  els.detail.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Event wiring.
els.search.addEventListener("input", debounce(loadList, 200));
els.filterType.addEventListener("change", loadList);
els.filterBand.addEventListener("change", loadList);
els.sort.addEventListener("change", loadList);

// Bootstrap.
(async () => {
  await Promise.all([loadStats(), loadList()]);
  const params = new URLSearchParams(window.location.search);
  if (params.get("slug")) openDetail(params.get("slug"));
})();
