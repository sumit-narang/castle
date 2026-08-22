"""Stage 3.5 — backfill missing images from Wikimedia Commons.

After Wikidata enrichment most sites still have no photo (Wikidata's P18 only
covers the famous ones). We fill the gap with two free, key-less layers:

  1. the OSM ``wikimedia_commons`` tag — an exact Commons file already in our
     data, just never used until now;
  2. Wikimedia Commons **GeoSearch** — the nearest geotagged photo whose
     filename matches the castle's name (or at least says "castle"/"tower"),
     within a small radius. Most of these come from Geograph, which has
     systematically photographed nearly every grid square in Ireland.

Both layers write the same ``image`` field (a bare Commons filename) that the
build stage already turns into a thumbnail, so the frontend needs no change. We
also record ``image_source`` and, best-effort, ``image_credit`` / ``image_license``.

A small on-disk cache (``data/image_cache.json``) keyed by record id — including
negative results — keeps re-runs cheap and the Commons API unhammered.

Pure standard library. Reuses the resilient ``http_json`` from the enrich stage.
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
CACHE_PATH = DATA_DIR / "image_cache.json"

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

GEO_RADIUS_M = 400      # how far from the site to look for a geotagged photo
GEO_LIMIT = 12          # candidates to consider per site
CACHE_FLUSH_EVERY = 25  # persist progress periodically for long runs

# Words too generic to count as a name match between a site and a file title.
_STOPWORDS = {
    "castle", "castles", "house", "tower", "towerhouse", "ruin", "ruins",
    "the", "of", "and", "fort", "old", "near", "view", "site", "ireland",
    "county", "co", "abbey", "church", "hill", "road", "river", "bridge",
}
# File titles containing any of these are almost never the castle itself.
_TITLE_BLOCKLIST = (
    "placeholder", "off licence", "shopping centre", "car park", "crossroads",
    "petrol", "supermarket", "school", "gaa", "playground", "roundabout",
)
# A file title with one of these keywords is plausibly the structure even when
# the name tokens don't overlap (e.g. "Castles of Munster, Moyree").
_STRUCTURE_KEYWORDS = ("castle", "tower house", "towerhouse", "tower", "keep")


def _tokens(text: str) -> set[str]:
    """Significant lowercase word tokens (3+ letters), minus generic stopwords."""
    return {w for w in re.findall(r"[a-z]{3,}", text.lower())} - _STOPWORDS


def _bare_filename(commons_value: str) -> str | None:
    """Normalise a Commons reference to a bare ``Foo.jpg`` filename.

    Accepts ``File:Foo.jpg``, ``Foo.jpg`` or a full URL; rejects categories and
    non-image references.
    """
    value = commons_value.strip()
    if value.lower().startswith("category:"):
        return None
    value = value.rsplit("/", 1)[-1]            # strip any URL/path prefix
    if value.lower().startswith("file:"):
        value = value[5:]
    value = value.strip()
    if not re.search(r"\.(jpe?g|png|gif|tiff?|webp)$", value, re.IGNORECASE):
        return None
    return value or None


def commons_geosearch(lat: float, lon: float) -> list[dict]:
    """Return geotagged Commons files near a coordinate (namespace 6 = File)."""
    params = {
        "action": "query", "format": "json", "list": "geosearch",
        "gscoord": f"{lat}|{lon}", "gsradius": str(GEO_RADIUS_M),
        "gslimit": str(GEO_LIMIT), "gsnamespace": "6",
    }
    data = http_json(COMMONS_API + "?" + urllib.parse.urlencode(params))
    if not data:
        return []
    return data.get("query", {}).get("geosearch", [])


def pick_image(name: str, hits: list[dict]) -> str | None:
    """Choose the best file title for `name` from geosearch hits, or None.

    Prefers filenames that share a distinctive word with the castle's name; falls
    back to any nearby structure photo (castle/tower keyword). Ties break on
    proximity. Returns a ``File:...`` title.
    """
    name_tokens = _tokens(name)
    best_title, best_score = None, 0.0
    for hit in hits:
        title = hit["title"]
        low = title.lower()
        if any(bad in low for bad in _TITLE_BLOCKLIST):
            continue
        overlap = len(name_tokens & _tokens(title))
        is_structure = any(kw in low for kw in _STRUCTURE_KEYWORDS)
        if not overlap and not is_structure:
            continue
        # Name overlap dominates; a structure keyword is a weaker signal; then
        # nearer is better. dist is in metres (0..GEO_RADIUS_M).
        score = overlap * 1000 + (200 if is_structure else 0) - hit.get("dist", 0)
        if score > best_score or best_title is None:
            best_title, best_score = title, score
    return best_title


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip() or None


def get_credits(titles: list[str]) -> dict[str, dict]:
    """Best-effort artist + license for File: titles via imageinfo extmetadata."""
    credits: dict[str, dict] = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i : i + 50]
        params = {
            "action": "query", "format": "json", "prop": "imageinfo",
            "iiprop": "extmetadata", "titles": "|".join(chunk),
        }
        data = http_json(COMMONS_API + "?" + urllib.parse.urlencode(params))
        if not data:
            continue
        for page in data.get("query", {}).get("pages", {}).values():
            info = page.get("imageinfo")
            if not info:
                continue
            meta = info[0].get("extmetadata", {})
            credits[page["title"]] = {
                "credit": _strip_html(meta.get("Artist", {}).get("value")),
                "license": (meta.get("LicenseShortName", {}).get("value") or "").strip() or None,
            }
        time.sleep(0.2)
    return credits


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def run(refresh: bool = False) -> list[dict]:
    print("Stage 3.5: backfilling images from Wikimedia Commons...")
    source = ENRICHED_PATH if ENRICHED_PATH.exists() else NORMALIZED_PATH
    records = json.loads(source.read_text(encoding="utf-8"))

    cache: dict = {} if refresh else _load_cache()
    before = sum(1 for r in records if r.get("image"))

    # Layer 1: exact Commons file straight from the OSM tag (no network).
    from_tag = 0
    for record in records:
        if record.get("image"):
            continue
        tag = record.get("wikimedia_commons")
        if not tag:
            continue
        filename = _bare_filename(tag)
        if filename:
            record["image"] = filename
            record["image_source"] = "osm_commons_tag"
            from_tag += 1

    # Layer 2: geo-search for everything still imageless that has a name + coords.
    targets = [
        r for r in records
        if not r.get("image") and r.get("name") and r.get("lat") is not None
    ]
    print(f"  start: {before} imaged | +{from_tag} from OSM commons tag | "
          f"{len(targets)} to geo-search")

    from_geo = 0
    newly_chosen: dict[str, str] = {}  # File: title -> record id (for credits)
    processed = 0
    for record in targets:
        rid = record["id"]
        if rid in cache:
            hit = cache[rid]
        else:
            geos = commons_geosearch(record["lat"], record["lon"])
            title = pick_image(record["name"], geos)
            hit = {"title": title} if title else None
            cache[rid] = hit
            processed += 1
            time.sleep(0.25)  # be polite to the Commons API
            if processed % CACHE_FLUSH_EVERY == 0:
                CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                print(f"    geo-searched {processed} sites...")
        if hit and hit.get("title"):
            filename = _bare_filename(hit["title"])
            if filename:
                record["image"] = filename
                record["image_source"] = "commons_geosearch"
                newly_chosen[hit["title"]] = rid
                from_geo += 1

    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    # Best-effort credits/licence for the geo-search picks.
    if newly_chosen:
        print(f"  fetching credits for {len(newly_chosen)} geo-search images...")
        credits = get_credits(sorted(newly_chosen))
        by_id = {rid: title for title, rid in newly_chosen.items()}
        for record in records:
            title = by_id.get(record["id"])
            if title and credits.get(title):
                record["image_credit"] = credits[title]["credit"]
                record["image_license"] = credits[title]["license"]

    ENRICHED_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    after = sum(1 for r in records if r.get("image"))
    total = len(records)
    print(f"  images: {before} -> {after} of {total} "
          f"({100 * after // total}%)  [+{from_tag} tag, +{from_geo} geo]")
    return records


if __name__ == "__main__":
    import sys
    run(refresh="--refresh" in sys.argv)
