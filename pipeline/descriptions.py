"""Stage 3.6 — add monument descriptions from the Archaeological Survey of Ireland.

Many sites have no Wikipedia article and so no descriptive text. But 350 of them
carry an OSM ``ref:IE:smr`` number (Sites & Monuments Record), which maps to the
National Monuments Service's open dataset — and that database has a real, sourced
prose description (``WEB_NOTES``) for most monuments.

We query the public **SMROpenData** ArcGIS FeatureServer in batches and attach
``smr_description``, ``smr_class`` (e.g. "Castle - tower house") and ``smr_url``
(the monument's page on the National Monuments viewer). The build stage uses the
description as a fallback when there is no Wikipedia summary / Wikidata blurb.

SMR numbers in the service are padded to 13 chars with trailing dashes
(``LI022-075----``); OSM values vary, so we normalise before matching.

Pure standard library. Reuses the resilient ``http_json`` from the enrich stage,
and caches by SMR number in ``data/desc_cache.json``.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

from pipeline.enrich import http_json

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ENRICHED_PATH = DATA_DIR / "enriched.json"
NORMALIZED_PATH = DATA_DIR / "normalized.json"
CACHE_PATH = DATA_DIR / "desc_cache.json"

# Archaeological Survey of Ireland — open SMR layer (National Monuments Service).
SMR_QUERY = (
    "https://services-eu1.arcgis.com/HyjXgkV6KGMSF3jt/arcgis/rest/services/"
    "SMROpenData/FeatureServer/0/query"
)
SMR_WIDTH = 13          # service pads SMRS to 13 chars with trailing dashes
BATCH_SIZE = 80         # SMR numbers per `SMRS IN (...)` request
DESC_MAX_CHARS = 700    # keep the data file lean; UI clamps further


def normalize_smr(value: str) -> str | None:
    """Turn an OSM SMR value into the service's 13-char padded form.

    'LI022-075----' -> 'LI022-075----'   (already padded)
    'CO108-051003'  -> 'CO108-051003-'   (pad short values)
    'KK023-071001'  -> 'KK023-071001-'
    """
    core = (value or "").strip().rstrip("-")
    if not re.match(r"^[A-Z]{2}\d", core):  # sanity: must look like 'LI022-...'
        return None
    return core.ljust(SMR_WIDTH, "-")[:SMR_WIDTH]


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<[^>]+>", "", text)        # drop any stray HTML
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    if len(text) > DESC_MAX_CHARS:
        text = text[:DESC_MAX_CHARS].rsplit(" ", 1)[0].rstrip(",.;") + "…"
    return text


def fetch_smr(smrs: list[str]) -> dict[str, dict]:
    """Look up SMR records in batches; return {smr_number: {description,class,url}}."""
    found: dict[str, dict] = {}
    for i in range(0, len(smrs), BATCH_SIZE):
        chunk = smrs[i : i + BATCH_SIZE]
        in_list = ",".join("'" + s.replace("'", "''") + "'" for s in chunk)
        params = {
            "where": f"SMRS IN ({in_list})",
            "outFields": "SMRS,MONUMENT_CLASS,WEB_NOTES,WEBSITE_LINK",
            "returnGeometry": "false",
            "f": "json",
        }
        print(f"    SMR {i + 1}-{i + len(chunk)} of {len(smrs)}...")
        data = http_json(SMR_QUERY + "?" + urllib.parse.urlencode(params))
        for feat in (data or {}).get("features", []):
            attrs = feat.get("attributes", {})
            key = attrs.get("SMRS")
            if not key:
                continue
            found[key] = {
                "smr_description": _clean(attrs.get("WEB_NOTES")),
                "smr_class": (attrs.get("MONUMENT_CLASS") or "").strip() or None,
                "smr_url": (attrs.get("WEBSITE_LINK") or "").strip() or None,
            }
        time.sleep(0.3)  # be polite
    return found


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def run(refresh: bool = False) -> list[dict]:
    print("Stage 3.6: adding descriptions from the Archaeological Survey of Ireland...")
    source = ENRICHED_PATH if ENRICHED_PATH.exists() else NORMALIZED_PATH
    records = json.loads(source.read_text(encoding="utf-8"))

    # Map each record to its normalised SMR key.
    rec_smr: dict[str, str] = {}
    for r in records:
        key = normalize_smr(r.get("smr"))
        if key:
            rec_smr[r["id"]] = key
    wanted = sorted(set(rec_smr.values()))
    print(f"  {len(rec_smr)} records carry a usable SMR number ({len(wanted)} unique)")

    cache = {} if refresh else _load_cache()
    missing = [s for s in wanted if s not in cache]
    if missing:
        print(f"  querying {len(missing)} SMR numbers (the rest are cached)...")
        cache.update(fetch_smr(missing))
        # Record misses too, so re-runs don't re-query dead numbers.
        for s in missing:
            cache.setdefault(s, {})
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    described = filled_gap = 0
    for r in records:
        key = rec_smr.get(r["id"])
        info = cache.get(key) if key else None
        if not info:
            continue
        for field in ("smr_description", "smr_class", "smr_url"):
            if info.get(field):
                r[field] = info[field]
        if info.get("smr_description"):
            described += 1
            if not r.get("summary") and not r.get("description"):
                filled_gap += 1

    ENRICHED_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  SMR descriptions attached: {described} "
          f"({filled_gap} of them on sites that previously had no text at all)")
    return records


if __name__ == "__main__":
    import sys
    run(refresh="--refresh" in sys.argv)
