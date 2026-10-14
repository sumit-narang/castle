# Implementation notes & audit trail

This document records *what* was built, *why* each decision was made, and the
data/coverage numbers at each step. It complements `README.md` (which is the
user-facing "how to run it" guide). Read this if you need to understand or defend
a choice later.

---

## 1. Original system (baseline)

A pure-standard-library Python pipeline (no `pip install`) that builds a map-ready
dataset of castles, tower houses, manors and ruins across the island of Ireland,
plus a static Mapbox GL JS frontend.

**Pipeline stages** (`run_pipeline.py` orchestrates):

1. `overpass.py` — fetch castle-like features from OpenStreetMap via the Overpass API.
2. `normalize.py` — clean tags into a stable schema; classify architectural type
   (`tower_house` / `manor` / `fortress` / `motte` / `castle`) and a separate
   `ruin` condition flag.
3. `enrich.py` — for records with a Wikidata ID, add `built_year`, `builder`,
   image (P18), short description, Wikipedia URL + lead-paragraph summary.
4. `build.py` — emit `data/castles.json` (flat array) and `data/castles.geojson`.

**Frontend** (`web/`): clustered markers coloured by type, type/ruin/search
filters, click-for-details popups. `serve.py`
reads `.env`, writes `web/config.js` (with the Mapbox token), and serves on
`http://localhost:8765`.

**Baseline coverage** (1386 records, 1064 named):

| field | coverage |
|---|---|
| image (thumbnail) | 391 / 1386 (28%) — 385 of the 1064 *named* (36%) |
| description/summary text | ~33% of named |

---

## 2. Goal

User observed that all ~1064 named sites *should* be able to show an image and
some description. Asked whether tools like
[crawl4ai](https://github.com/unclecode/crawl4ai) would help.

### Decision: do NOT use crawl4ai (or any HTML scraper)

- crawl4ai drives a headless browser (Playwright + Chromium) to turn rendered
  pages into markdown. It needs `pip install` + a browser binary — this project
  is deliberately **standard-library only, zero dependencies**.
- The data we want is available behind **clean, key-less JSON APIs**
  (Wikidata, Wikimedia Commons, the National Monuments Service ArcGIS service),
  so a browser-based scraper would be **slower and more fragile** for the same
  result. The API path is simply the better tool here.
- (Licensing was raised then explicitly set aside by the user — this is a
  personal side project. Noted, but the decision above stands on engineering
  merit regardless.)

---

## 3. Image backfill (new stage: `pipeline/images.py`, "Stage 3.5")

### Feasibility test (done before building)

- Inspected raw OSM tags: 152 records carry a `wikimedia_commons` tag that
  `normalize.py` was **dropping**; 350 carry a `ref:IE:smr` archaeological number.
- Live-tested **Wikimedia Commons GeoSearch** (`list=geosearch`) on a 40-site
  sample of named, image-less castles: **70%** had a geotagged photo within
  400 m; **60%** had one whose filename matched the castle name or said
  "castle"/"tower". Most come from **Geograph**, which has systematically
  photographed nearly every grid square in Ireland.

### Correction to an early assumption

Initially thought Wikidata P18 was only being used for sites with English
Wikipedia articles. **Wrong** — `enrich.py` already pulls P18 for *all* 442
Wikidata IDs; 391 is simply how many of those have a P18 image. So the real
untapped sources were the `wikimedia_commons` tag and Commons GeoSearch.

### What was built

`images.py` fills the `image` field (a bare Commons filename that `build.py`
already turns into a thumbnail URL — so **no frontend change was needed** for the
images themselves) using two layers:

1. **`wikimedia_commons` OSM tag** — an exact Commons file, no network call.
2. **Commons GeoSearch** fallback — nearest geotagged photo, **filtered** by:
   - name-token overlap with the castle (high confidence), OR
   - a structure keyword ("castle"/"tower"/"keep") in the title (weaker), with
   - a blocklist (placeholder/off-licence/shopping-centre/…) and proximity tiebreak.

Supporting changes:
- `normalize.py` now keeps `wikimedia_commons` and `smr` (previously discarded).
- Records also get `image_source` (`osm_commons_tag` / `commons_geosearch`) and,
  best-effort, `image_credit` + `image_license` (from Commons `extmetadata`).
- On-disk cache `data/image_cache.json` (keyed by record id, **including negative
  results**) so re-runs are instant and the API is not re-hammered.
- Wired into `run_pipeline.py` as stage `images` with a `--no-images` flag.

### Sequencing note (one-off)

The existing `enriched.json` predated the `wikimedia_commons`/`smr` capture in
`normalize.py`. Rather than re-run the slow Wikidata `enrich` stage (hundreds of
API calls we already had answers for), we re-ran `normalize` (instant) and merged
just those two tag fields into `enriched.json` by record id. A clean
`--from normalize` run reproduces the same result for anyone else.

### Result

| | before | after |
|---|---|---|
| Named sites with a photo | 385 (36%) | **810 / 1064 (76%)** |
| All records | 391 (28%) | 821 / 1386 (59%) |

Sources: +425 Commons GeoSearch, +5 OSM tag (most tagged sites already had a
Wikidata image). The 59% "all records" number is dragged down by 322 *unnamed*
OSM nodes (no name to match, and the UI hides them) — **76% of named sites** is
the figure that matters.

**Match quality** of the 425 geo-search images:
- **80% (340) name-matched** — reliable.
- **20% (85) keyword-only fallback** — nearest "castle/tower" photo with no name
  overlap. Geographically correct (within 400 m) but occasionally the wrong
  building (e.g. *Ballygarth Castle* → "Ballygarth church"; *Glanmore Castle* →
  a nearby folly).

**Decision:** keep the 85 fallbacks for now — user will manually review them.
(The alternative was to drop them for ~6% less coverage but 100% precision.)

---

## 4. Photo credits in the UI

User asked to surface the captured `image_credit`/`image_license`.

`web/app.js` now renders a small caption under each popup photo:
`Photo: <photographer> · <licence>` for geo-search images (mostly Geograph,
CC BY-SA), or a generic "Photo via Wikimedia Commons" for Wikidata/tag images.
Styling: `.popup .credit` in `web/style.css`. `build.py` passes
`image_credit` / `image_license` / `image_source` through to the app records.

---

## 5. Descriptions backfill (new stage: `pipeline/descriptions.py`, "Stage 3.6")

### Source

The **Archaeological Survey of Ireland** open dataset (National Monuments
Service), exposed as a public ArcGIS FeatureServer:
`https://services-eu1.arcgis.com/HyjXgkV6KGMSF3jt/arcgis/rest/services/SMROpenData/FeatureServer/0`.
Relevant fields: `SMRS` (SMR number), `MONUMENT_CLASS`, `WEB_NOTES` (a real,
sourced prose description), `WEBSITE_LINK` (the monument's viewer page).

### Key detail: SMR number format

The service pads SMR numbers to **13 chars with trailing dashes**
(`LI022-075----`). OSM values vary (`CO108-051003`, `KK023-071001`,
`MA038-063001-`), so `normalize_smr()` strips trailing dashes then re-pads to 13.
Without this, ~half the lookups miss.

### What was built

- Batched ArcGIS queries (`SMRS IN (...)`, 80 per request) — all 348 unique SMR
  numbers resolved in 5 requests.
- Attaches `smr_description` (cleaned + clamped to 700 chars), `smr_class`,
  `smr_url`. Cache: `data/desc_cache.json` (keyed by SMR, records misses too).
- `build.py` `describe()` picks descriptive text in priority order
  **Wikipedia summary > Wikidata one-liner > SMR notes**, and emits
  `description` (best text) + `description_source` (provenance) + `smr_url`.
- `web/app.js` shows a `Source: …` line under the description and an
  "Archaeology.ie" link in the popup actions when `smr_url` is present.
- Wired into `run_pipeline.py` as stage `descriptions` with `--no-descriptions`.

### Result

All 350 SMR records matched. **205 sites that previously had no text at all now
have a description.** Named-site description coverage: ~33% → **49% (531/1064)**
(Wikipedia 299, Archaeological Survey 130, Wikidata 102 — among named sites).

---

## 6. Final coverage (named sites, 1064)

| field | baseline | now |
|---|---|---|
| image | 36% | **76%** |
| description | ~33% | **49%** |

## 7. New files / artifacts

- `pipeline/images.py`, `pipeline/descriptions.py` — new stages.
- `data/image_cache.json`, `data/desc_cache.json` — network caches (safe to delete; just forces a re-fetch).
- New per-record fields: `image_source`, `image_credit`, `image_license`,
  `smr_description`, `smr_class`, `smr_url`, `description_source`.

## 8. Possible next steps (not done)

- Manually review the 85 keyword-only fallback photos; drop/replace the wrong ones.
- Render `image_credit` in the sidebar list too (currently popup only).
- Pull more SMR fields (period, classification notes) into the popup.
- Hand-curate visiting info (opening hours, fees) for headline sites — sparse in OSM.
