"""Stage 1 — fetch raw castle data from OpenStreetMap via the Overpass API.

This is the bulk source: one request returns every castle-like feature tagged
in Ireland (the Republic + Northern Ireland), with coordinates and OSM tags.
Uses only the Python standard library.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Public Overpass mirrors. We try them in order so a single outage doesn't
# break the pipeline.
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Bounding box covering the whole island of Ireland (Republic + Northern
# Ireland): (south, west, north, east). A bbox is far more reliable than an
# area-name lookup, which depends on exact relation naming in OSM.
BBOX = "51.39,-10.7,55.45,-5.35"

# Castle-like features can be tagged a handful of ways in OSM, so we gather all
# of them and classify later in the normalize stage.
QUERY = f"""
[out:json][timeout:180];
(
  nwr["historic"="castle"]({BBOX});
  nwr["historic"="tower_house"]({BBOX});
  nwr["historic"="manor"]({BBOX});
  nwr["ruins"="castle"]({BBOX});
  nwr["building"="castle"]({BBOX});
);
out center tags;
"""

RAW_PATH = Path(__file__).resolve().parent.parent / "data" / "raw_osm.json"


def fetch(query: str = QUERY) -> dict:
    """Run an Overpass query, trying each mirror until one succeeds."""
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_error: Exception | None = None

    for endpoint in ENDPOINTS:
        try:
            print(f"  querying {endpoint} ...")
            request = urllib.request.Request(
                endpoint,
                data=body,
                headers={
                    "User-Agent": "castles-of-ireland-pipeline/1.0 (educational project)",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=200) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_error = exc
            print(f"  ! {endpoint} failed: {exc}. Trying next mirror...")
            time.sleep(2)

    raise RuntimeError(f"All Overpass mirrors failed. Last error: {last_error}")


def run() -> dict:
    print("Stage 1: fetching castles from OpenStreetMap (Overpass)...")
    data = fetch()
    elements = data.get("elements", [])
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  fetched {len(elements)} raw elements -> {RAW_PATH}")
    return data


if __name__ == "__main__":
    run()
