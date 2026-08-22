"""Stage 4 — produce the final, app-ready outputs.

Writes two files into data/:
  - castles.geojson : standard GeoJSON FeatureCollection (for any map lib / GIS)
  - castles.json    : a slim flat array, easiest for a JS frontend to consume

Unnamed records are kept but flagged, so the frontend can choose to hide them.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ENRICHED_PATH = DATA_DIR / "enriched.json"
NORMALIZED_PATH = DATA_DIR / "normalized.json"
GEOJSON_PATH = DATA_DIR / "castles.geojson"
APP_JSON_PATH = DATA_DIR / "castles.json"

COMMONS_THUMB = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width=600"


def commons_thumb(image: str | None) -> str | None:
    """Turn a Wikidata P18 image (a bare Commons filename, possibly with a URL
    prefix) into a sized Commons thumbnail URL."""
    if not image:
        return None
    filename = image.rsplit("/", 1)[-1]
    return COMMONS_THUMB.format(name=urllib.parse.quote(filename))


def describe(record: dict) -> tuple[str | None, str | None]:
    """Pick the best descriptive text and name its source, in priority order:
    manual override > Wikipedia lead > Wikidata one-liner > Archaeological Survey."""
    if record.get("manual_description"):
        return record["manual_description"], "Added manually"
    if record.get("summary"):
        return record["summary"], "Wikipedia"
    if record.get("description"):
        return record["description"], "Wikidata"
    if record.get("smr_description"):
        return record["smr_description"], "National Monuments Service (Archaeological Survey of Ireland)"
    return None, None


def to_app_record(record: dict) -> dict:
    text, text_source = describe(record)
    return {
        "id": record["id"],
        "name": record["name"] or "Unnamed castle",
        "named": bool(record["name"]),
        "lat": record["lat"],
        "lon": record["lon"],
        "category": record["category"],
        "category_label": record["category_label"],
        "ruin": record.get("ruin", False),
        "built_year": record.get("built_year"),
        "builder": record.get("builder"),
        "description": text,                        # best available blurb
        "description_source": text_source,          # provenance for attribution
        "smr_url": record.get("smr_url"),           # National Monuments record page
        "summary": record.get("summary"),          # lead paragraph (Wikipedia)
        "thumbnail": record.get("image_url") or commons_thumb(record.get("image")),
        "image_credit": record.get("image_credit"),      # photographer (geo-search)
        "image_license": record.get("image_license"),    # e.g. "CC BY-SA 2.0"
        "image_source": record.get("image_source"),       # how we found the photo
        "wikipedia_url": record.get("wikipedia_url"),
        "has_article": bool(record.get("wikipedia_url")),
        "website": record.get("website"),
        "opening_hours": record.get("opening_hours"),
        "fee": record.get("fee"),
        "heritage": record.get("heritage"),
        "osm_url": f"https://www.openstreetmap.org/{record['osm_type']}/{record['osm_id']}",
    }


def run() -> None:
    print("Stage 4: building app outputs...")
    # Prefer enriched data, but fall back to normalized when --no-enrich was used.
    source = ENRICHED_PATH if ENRICHED_PATH.exists() else NORMALIZED_PATH
    print(f"  source: {source.name}")
    records = json.loads(source.read_text(encoding="utf-8"))
    app_records = [to_app_record(r) for r in records]

    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {k: v for k, v in r.items() if k not in ("lat", "lon")},
        }
        for r in app_records
    ]
    geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "source": "OpenStreetMap (ODbL) + Wikidata (CC0)",
            "count": len(features),
        },
        "features": features,
    }

    GEOJSON_PATH.write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
    APP_JSON_PATH.write_text(
        json.dumps(app_records, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  wrote {len(app_records)} features -> {GEOJSON_PATH.name}, {APP_JSON_PATH.name}")


if __name__ == "__main__":
    run()
