# Castles of Ireland — data pipeline

A pure-Python (standard library only — no `pip install`) pipeline that builds a
clean, map-ready dataset of every castle, tower house, manor and ruin on the
island of Ireland, sourced from open data.

## Quick start

```bash
python3 run_pipeline.py              # full run (OSM -> Wikidata -> outputs)
python3 run_pipeline.py --no-enrich  # skip the slower Wikidata stage
python3 run_pipeline.py --from normalize   # reuse cached raw_osm.json
```

Outputs land in `data/`:

| file | what it is |
|------|------------|
| `castles.json` | slim flat array — easiest for a JS frontend |
| `castles.geojson` | standard GeoJSON FeatureCollection — for Leaflet/Mapbox/GIS |
| `raw_osm.json` | raw Overpass response (cached, so you can re-run later stages) |
| `normalized.json` | typed/cleaned records before enrichment |
| `enriched.json` | normalized + Wikidata facts |

## The stages

1. **`overpass.py`** — fetches castle-like features from OpenStreetMap via the
   Overpass API, using a bounding box over the whole island (Republic + NI) and
   trying several public mirrors for resilience.
2. **`normalize.py`** — cleans tags into a stable schema and classifies each
   site by **architectural type** (`tower_house`, `manor`, `fortress`, `motte`,
   `castle`) plus a separate **`ruin`** condition flag. Condition is independent
   of type — a tower house can itself be a ruin.
3. **`enrich.py`** — for records carrying a Wikidata ID, uses the Wikidata
   `wbgetentities` API to add `built_year`, `builder`, image, short
   `description`, and the Wikipedia URL, then pulls each article's lead-paragraph
   `summary` from Wikipedia's REST endpoint. Handles rate-limiting (429) with
   backoff. (Uses the entities/REST APIs, not the often-throttled SPARQL service.)
4. **`build.py`** — emits `castles.geojson` and `castles.json`, turning Wikidata
   image references into sized Wikimedia Commons thumbnail URLs.

## Record schema (`castles.json`)

```jsonc
{
  "id": "way/12345",
  "name": "Bunratty Castle",
  "named": true,                 // false => OSM had no name (frontend may hide)
  "lat": 52.69, "lon": -8.81,
  "category": "tower_house",     // tower_house | manor | fortress | motte | castle
  "category_label": "Tower house",
  "ruin": false,                 // condition, independent of category
  "built_year": 1425,            // from Wikidata, may be null
  "builder": "Maccon Sioda...",  // from Wikidata, may be null
  "description": "castle in Ireland",   // short one-liner (Wikidata), may be null
  "summary": "Bunratty Castle is …",    // Wikipedia lead paragraph, may be null
  "thumbnail": "https://commons.wikimedia.org/...",  // may be null
  "wikipedia_url": "https://en.wikipedia.org/...",   // may be null
  "has_article": true,           // true when an English Wikipedia article exists
  "website": null, "opening_hours": null, "fee": null, "heritage": null,
  "osm_url": "https://www.openstreetmap.org/way/12345"
}
```

## Web UI (`web/`)

A static, dependency-free Mapbox GL JS frontend that reads `data/castles.geojson`:
clustered markers coloured by type, type/ruin/search filters, click-for-details
popups (photo, built date, builder, directions, Wikipedia), and `localStorage`
"visited" tracking.

```bash
# 1. Put your Mapbox token in .env (copy .env.example first):
cp .env.example .env        # then edit MAPBOX_TOKEN=pk.…
# 2. Build web/config.js from .env and serve in one step:
python3 serve.py            # -> http://localhost:8765/web/index.html
```

`serve.py` reads `.env` and generates `web/config.js` (git-ignored). Note that
Mapbox is client-side, so the token still reaches the browser — `.env` just
keeps it out of source control. **URL-restrict the token** in your Mapbox
dashboard for any public deployment.

Without a token the basemap won't render, but the searchable/filterable castle
list still works so the data is usable.

### Filters & details
- Type chips, **Ruins only**, **Named sites only**, **With Wikipedia article**,
  **Visited only**, plus name search.
- Popups show the **Wikipedia lead-paragraph summary** (or the short Wikidata
  description), built year, builder, photo, and Directions/Wikipedia/OSM links.
- A blue **W** badge marks the 304 castles that have an English Wikipedia
  article; the "With Wikipedia article" filter isolates them.

## Data sources & licensing

- **OpenStreetMap** — bulk source (coordinates, names, types). Licensed **ODbL**:
  attribute "© OpenStreetMap contributors" and keep derived data share-alike.
- **Wikidata** — enrichment (dates, builders, images). **CC0** (public domain).
- **Wikimedia Commons** — image thumbnails; individual files carry their own
  licenses — check before reuse beyond linking.

Always display attribution in any UI built on this data.

## Notes

- Visiting info (opening hours, fees) is sparse in OSM — expect to hand-curate
  it for the headline sites (Dublin, Kilkenny, Cashel, Bunratty, …).
- Re-run cheaply: `raw_osm.json` is cached, so `--from normalize` rebuilds
  without hammering the Overpass API.
