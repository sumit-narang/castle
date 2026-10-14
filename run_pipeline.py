#!/usr/bin/env python3
"""Castles of Ireland — data pipeline orchestrator.

Runs the four stages end to end:
  1. overpass  — fetch raw castles from OpenStreetMap
  2. normalize — clean + classify into typed records
  3. enrich       — add facts from Wikidata
  4. images       — backfill photos from Wikimedia Commons (tag + geo-search)
  5. descriptions — add monument descriptions from the Archaeological Survey of Ireland
  6. overrides    — apply your own photos & descriptions
  7. stickers     — download a small photo per castle for the map's photo stickers
  8. build        — emit castles.geojson + castles.json

Usage:
  python3 run_pipeline.py            # full run
  python3 run_pipeline.py --no-enrich   # skip the (slower) Wikidata stage
  python3 run_pipeline.py --no-images   # skip the Commons image backfill
  python3 run_pipeline.py --no-descriptions   # skip the SMR description backfill
  python3 run_pipeline.py --no-stickers  # skip the sticker photo download
  python3 run_pipeline.py --from normalize   # reuse raw_osm.json, restart later

Pure standard library — no pip install required.
"""

from __future__ import annotations

import sys

from pipeline import build, descriptions, enrich, images, normalize, overpass, overrides, stickers

STAGES = ["overpass", "normalize", "enrich", "images", "descriptions", "overrides", "stickers", "build"]


def main(argv: list[str]) -> int:
    start = "overpass"
    skip_enrich = "--no-enrich" in argv
    skip_images = "--no-images" in argv
    skip_descriptions = "--no-descriptions" in argv
    skip_stickers = "--no-stickers" in argv
    if "--from" in argv:
        start = argv[argv.index("--from") + 1]
        if start not in STAGES:
            print(f"Unknown stage '{start}'. Choose from: {', '.join(STAGES)}")
            return 1

    active = STAGES[STAGES.index(start) :]
    print(f"Running stages: {', '.join(active)}\n")

    if "overpass" in active:
        overpass.run()
    if "normalize" in active:
        normalize.run()
    if "enrich" in active and not skip_enrich:
        enrich.run()
    elif "enrich" in active:
        print("Stage 3: skipped (--no-enrich)")
    if "images" in active and not skip_images:
        images.run()
    elif "images" in active:
        print("Stage 3.5: skipped (--no-images)")
    if "descriptions" in active and not skip_descriptions:
        descriptions.run()
    elif "descriptions" in active:
        print("Stage 3.6: skipped (--no-descriptions)")
    if "overrides" in active:
        overrides.run()
    if "stickers" in active and not skip_stickers:
        stickers.run()
    elif "stickers" in active:
        print("Stage 3.8: skipped (--no-stickers)")
    if "build" in active:
        build.run()

    print("\nDone. Outputs are in data/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
