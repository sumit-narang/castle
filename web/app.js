/* Castles of Ireland — Mapbox GL JS frontend.
 * Reads the pipeline output (data/castles.geojson), renders clustered sticker markers,
 * and provides type/ruin/article/search filters.
 */

const DATA_URL = "../data/castles.geojson";

const CATEGORIES = [
  { id: "tower_house", label: "Tower house", color: "#8c6d46" },
  { id: "manor", label: "Manor", color: "#9b6ab0" },
  { id: "fortress", label: "Fortress", color: "#c1440e" },
  { id: "castle", label: "Castle", color: "#3b82f6" },
  { id: "motte", label: "Motte", color: "#2a9d8f" },
];
const COLOR_BY_CAT = Object.fromEntries(CATEGORIES.map((c) => [c.id, c.color]));

// ---- State ----
let allFeatures = [];
let map;

const filters = {
  categories: new Set(CATEGORIES.map((c) => c.id)),
  ruinsOnly: false,
  namedOnly: true,
  articleOnly: false,
  search: "",
};

// ---- Filtering (single source of truth feeds both map + list) ----
function matches(props) {
  if (!filters.categories.has(props.category)) return false;
  if (filters.ruinsOnly && !props.ruin) return false;
  if (filters.namedOnly && !props.named) return false;
  if (filters.articleOnly && !props.has_article) return false;
  if (filters.search) {
    const name = (props.name || "").toLowerCase();
    if (!name.includes(filters.search)) return false;
  }
  return true;
}

function applyFilters() {
  const filtered = allFeatures.filter((f) => matches(f.properties));
  updateMapData(filtered);
  renderList(filtered);
  document.getElementById("stats").textContent =
    `${filtered.length.toLocaleString()} shown · ${allFeatures.length.toLocaleString()} total`;
}

// ---- Sidebar list ----
// Rows are paged in rather than all at once: a full 1,000-row rebuild on every
// keystroke is slow, so we render PAGE_SIZE at a time behind a "Show more".
const PAGE_SIZE = 400;
let listFeatures = [];  // current filtered + sorted named features
let listRendered = 0;   // how many of them are currently in the DOM

function resultRow(f) {
  const p = f.properties;
  const li = document.createElement("li");
  li.className = "result";
  const built = p.built_year ? ` · ${p.built_year}` : "";
  li.innerHTML = `
    ${p.thumbnail ? `<img class="thumb" loading="lazy" src="${p.thumbnail}" alt="">` : `<div class="thumb"></div>`}
    <div class="meta">
      <div class="name">${escapeHtml(p.name)}${p.has_article ? ` <span class="wiki-badge" title="Has a Wikipedia article">W</span>` : ""}</div>
      <div class="sub">${p.category_label}${p.ruin ? " · ruin" : ""}${built}</div>
    </div>`;
  li.addEventListener("click", () => {
    map.flyTo({ center: f.geometry.coordinates, zoom: 14 });
    openPopup(f);
  });
  return li;
}

function appendPage(count = PAGE_SIZE) {
  const list = document.getElementById("results");
  list.querySelector(".list-more")?.remove();

  const end = Math.min(listRendered + count, listFeatures.length);
  const batch = document.createDocumentFragment();
  for (let i = listRendered; i < end; i++) batch.appendChild(resultRow(listFeatures[i]));
  list.appendChild(batch);
  listRendered = end;

  const remaining = listFeatures.length - listRendered;
  if (remaining > 0) {
    const li = document.createElement("li");
    li.className = "list-more";
    li.innerHTML = `
      <button type="button">Show ${Math.min(PAGE_SIZE, remaining).toLocaleString()} more</button>
      <span class="sub">${remaining.toLocaleString()} not shown</span>`;
    li.querySelector("button").addEventListener("click", () => appendPage());
    list.appendChild(li);
  }
}

function renderList(features) {
  const named = features.filter((f) => f.properties.named);
  named.sort((a, b) => a.properties.name.localeCompare(b.properties.name));

  listFeatures = named;
  listRendered = 0;
  document.getElementById("results").innerHTML = "";
  appendPage();
}

// ---- Popups ----
function openPopup(feature) {
  const p = feature.properties;
  const [lon, lat] = feature.geometry.coordinates;
  const dirUrl = `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`;
  const color = COLOR_BY_CAT[p.category] || "#999";

  const html = `
    <div class="popup">
      ${p.thumbnail ? `<img src="${p.thumbnail}" alt="${escapeHtml(p.name)}">${imageCreditHtml(p)}` : ""}
      <div class="body">
        <h3>${escapeHtml(p.name)}</h3>
        <div class="tags">
          <span class="pill" style="background:${color}">${p.category_label}</span>
          ${p.ruin ? `<span class="pill" style="background:#7d6b58">Ruin</span>` : ""}
          ${p.built_year ? `Built ${p.built_year}` : ""}
          ${p.builder ? `<br>Built by ${escapeHtml(p.builder)}` : ""}
        </div>
        ${p.description
          ? `<p class="summary">${escapeHtml(p.description)}</p>
             ${p.description_source ? `<div class="source">Source: ${escapeHtml(p.description_source)}</div>` : ""}`
          : ""}
        <div class="actions">
          <a href="${dirUrl}" target="_blank" rel="noopener">Directions</a>
          ${p.wikipedia_url ? `<a href="${p.wikipedia_url}" target="_blank" rel="noopener">Wikipedia</a>` : ""}
          ${p.smr_url ? `<a href="${p.smr_url}" target="_blank" rel="noopener">Archaeology.ie</a>` : ""}
          <a href="${p.osm_url}" target="_blank" rel="noopener">OSM</a>
        </div>
      </div>
    </div>`;

  new mapboxgl.Popup({ offset: 14, maxWidth: "260px" })
    .setLngLat(feature.geometry.coordinates)
    .setHTML(html)
    .addTo(map);
}

// ---- Map setup ----
// Sources and sticker layers live in map-stickers.js.
function initMap() {
  mapboxgl.accessToken = window.APP_CONFIG.MAPBOX_TOKEN;
  map = new mapboxgl.Map({
    container: "map",
    style: window.APP_CONFIG.MAP_STYLE,
    center: [-8.0, 53.4], // centre of Ireland
    zoom: 6,
  });
  map.addControl(new mapboxgl.NavigationControl(), "top-right");
  map.addControl(new mapboxgl.GeolocateControl({ trackUserLocation: true }), "top-right");
  initMapLayers();
  applyFilters();
}

// ---- Controls wiring ----
function buildControls() {
  const fieldset = document.getElementById("category-filters");
  for (const c of CATEGORIES) {
    const label = document.createElement("label");
    label.className = "chip";
    label.innerHTML = `<input type="checkbox" value="${c.id}" checked>
      <span class="dot" style="background:${c.color}"></span>${c.label}`;
    label.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? filters.categories.add(c.id) : filters.categories.delete(c.id);
      applyFilters();
    });
    fieldset.appendChild(label);
  }

  const bind = (id, key) =>
    document.getElementById(id).addEventListener("change", (e) => {
      filters[key] = e.target.checked;
      applyFilters();
    });
  bind("ruins-only", "ruinsOnly");
  bind("named-only", "namedOnly");
  bind("article-only", "articleOnly");

  let t;
  document.getElementById("search").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => {
      filters.search = e.target.value.trim().toLowerCase();
      applyFilters();
    }, 150);
  });
}

// Small attribution caption shown under a popup photo. Geo-search photos carry a
// photographer + licence; Wikidata/Commons-tag photos fall back to a generic line.
function imageCreditHtml(p) {
  if (!p.thumbnail) return "";
  let text;
  if (p.image_credit) {
    const lic = p.image_license ? ` · ${escapeHtml(p.image_license)}` : "";
    text = `Photo: ${escapeHtml(p.image_credit)}${lic}`;
  } else {
    text = "Photo via Wikimedia Commons";
  }
  return `<div class="credit">${text}</div>`;
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// A real Mapbox public token starts with "pk." and isn't the .env placeholder.
function hasValidToken() {
  const t = window.APP_CONFIG?.MAPBOX_TOKEN || "";
  return t.startsWith("pk.") && t !== "pk.your_public_token_here";
}

// ---- Boot ----
async function boot() {
  if (!hasValidToken()) {
    document.getElementById("token-warning").hidden = false;
  }

  buildControls();

  try {
    const res = await fetch(DATA_URL);
    const geojson = await res.json();
    allFeatures = geojson.features;
    const articleTotal = allFeatures.filter((f) => f.properties.has_article).length;
    document.getElementById("article-count").textContent = articleTotal;
  } catch (err) {
    document.getElementById("stats").textContent =
      "Could not load data — serve this folder over HTTP (see README).";
    console.error(err);
    return;
  }

  if (hasValidToken()) {
    initMap();
  } else {
    // No token: still show the searchable/filterable list so the data is usable.
    applyFilters();
  }
}

boot();
