"""Stage 3.7 — apply your own photos & descriptions, on top of the auto sources.

Two ways to add a photo, both keyed to a castle's `id` so they survive re-runs:

1. **Drop an image file** into `web/images/`, named after the castle id with the
   slash turned into an underscore, e.g. castle `way/1023389562` -> save your
   photo as `way_1023389562.jpg` (`.jpg/.jpeg/.png/.webp/.gif` all fine). The
   image is served locally — no external link, works offline. This is the easy way.

2. **`data/overrides.csv`** (exported from the Google Sheet) — for pasting a photo
   URL and/or a description in bulk. Columns: `id`, `image_url`, `description`,
   `credit`. Blanks are skipped.

A dropped file wins over a CSV link wins over the automatic Wikidata/Commons/SMR
sources. Pure standard library.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ENRICHED_PATH = DATA_DIR / "enriched.json"
NORMALIZED_PATH = DATA_DIR / "normalized.json"
OVERRIDES_PATH = DATA_DIR / "overrides.csv"
IMAGES_DIR = ROOT / "web" / "images"          # drop your photo files here
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

COMMONS_THUMB = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width=600"


def id_to_filestem(castle_id: str) -> str:
    """way/1023389562 -> way_1023389562 (a filesystem-safe stem)."""
    return castle_id.replace("/", "_")


def normalize_image_url(url: str) -> str | None:
    """Use a pasted URL directly, but turn a Commons *File:* page link into a
    ready-to-render thumbnail URL so those work too."""
    url = (url or "").strip()
    if not url:
        return None
    m = re.search(r"commons\.wikimedia\.org/wiki/(?:File|Datei):(.+)$", url, re.IGNORECASE)
    if m:
        filename = urllib.parse.unquote(m.group(1)).replace(" ", "_")
        return COMMONS_THUMB.format(name=urllib.parse.quote(filename))
    return url


def apply_csv(by_id: dict) -> tuple[int, int, int]:
    if not OVERRIDES_PATH.exists():
        return 0, 0, 0
    imgs = descs = unmatched = 0
    with OVERRIDES_PATH.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rid = (row.get("id") or "").strip()
            rec = by_id.get(rid)
            if not rid:
                continue
            if rec is None:
                unmatched += 1
                continue
            image_url = normalize_image_url(row.get("image_url", ""))
            if image_url:
                rec["image_url"] = image_url
                rec["image_source"] = "manual"
                rec["image_credit"] = (row.get("credit") or "").strip() or None
                rec["image_license"] = None
                imgs += 1
            desc = (row.get("description") or "").strip()
            if desc:
                rec["manual_description"] = desc
                descs += 1
    return imgs, descs, unmatched


def apply_local_images(by_id: dict) -> tuple[int, int]:
    """Link any image file in web/images/ whose name matches a castle id."""
    if not IMAGES_DIR.exists():
        return 0, 0
    stem_to_id = {id_to_filestem(cid): cid for cid in by_id}
    linked = unknown = 0
    for path in sorted(IMAGES_DIR.iterdir()):
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        cid = stem_to_id.get(path.stem)
        if cid is None:
            unknown += 1
            continue
        rec = by_id[cid]
        rec["image_url"] = f"images/{path.name}"   # relative to web/index.html
        rec["image_source"] = "manual_upload"
        rec.setdefault("image_credit", None)
        linked += 1
    return linked, unknown


def run() -> list[dict]:
    print("Stage 3.7: applying manual overrides...")
    source = ENRICHED_PATH if ENRICHED_PATH.exists() else NORMALIZED_PATH
    records = json.loads(source.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in records}

    imgs, descs, unmatched = apply_csv(by_id)          # CSV first...
    uploaded, unknown = apply_local_images(by_id)      # ...local files win.

    if not (imgs or descs or uploaded):
        print("  no overrides found (empty web/images/ and no overrides.csv) — skipping")
        return records

    ENRICHED_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    bits = []
    if uploaded:
        bits.append(f"{uploaded} uploaded photo(s)")
    if imgs:
        bits.append(f"{imgs} linked photo(s)")
    if descs:
        bits.append(f"{descs} description(s)")
    print("  applied " + ", ".join(bits))
    if unmatched:
        print(f"  note: {unmatched} CSV row(s) had an id not in the dataset")
    if unknown:
        print(f"  note: {unknown} file(s) in web/images/ didn't match any castle id")
    return records


if __name__ == "__main__":
    run()
