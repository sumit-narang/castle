"""Stage 2 — normalize raw OSM elements into clean, typed castle records.

Turns messy OSM tags into a stable schema and classifies each castle into one
of four browse categories: tower_house, ruin, manor, castle (restored/intact).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_PATH = DATA_DIR / "raw_osm.json"
NORMALIZED_PATH = DATA_DIR / "normalized.json"

# Human-readable labels for the architectural categories we expose in the UI.
CATEGORY_LABELS = {
    "tower_house": "Tower house",
    "manor": "Manor / stately home",
    "fortress": "Fortress / fortified",
    "motte": "Motte & bailey",
    "castle": "Castle",
}

# Map the messy OSM `castle_type` values (see raw_osm.json) onto our categories.
# Tower houses dominate Irish castle stock, so they get the widest net.
CASTLE_TYPE_MAP = {
    # tower houses & related defensive residences
    "towerhouse": "tower_house",
    "tower_house": "tower_house",
    "tower": "tower_house",
    "15th_century_towerhouse": "tower_house",
    "hall_house": "tower_house",
    "hallhouse": "tower_house",
    "fortified_house": "tower_house",
    "farm_castle": "tower_house",
    # manors / stately residences
    "manor": "manor",
    "stately": "manor",
    "palace": "manor",
    "residential": "manor",
    "gothic_georgian": "manor",
    # fortifications
    "defensive": "fortress",
    "fortress": "fortress",
    "fortification": "fortress",
    "masonry_castle": "fortress",
    "masonry": "fortress",
    # earthworks
    "motte_and_bailey": "motte",
}


def classify_type(tags: dict) -> str:
    """Determine the architectural category (independent of ruin condition)."""
    castle_type = (tags.get("castle_type") or "").lower()
    historic = (tags.get("historic") or "").lower()

    if castle_type in CASTLE_TYPE_MAP:
        return CASTLE_TYPE_MAP[castle_type]
    if historic == "tower_house":
        return "tower_house"
    if historic == "manor":
        return "manor"
    return "castle"


def is_ruin(tags: dict) -> bool:
    """Condition is a separate axis from type: a tower house can be a ruin."""
    ruins = (tags.get("ruins") or "").lower()
    historic = (tags.get("historic") or "").lower()
    return (
        ruins in ("castle", "yes", "tower_house", "towerhouse")
        or historic == "ruins"
        or tags.get("building") == "ruins"
        or (tags.get("castle_type") or "").lower() == "archaeological_site"
    )


def coordinates(element: dict) -> tuple[float, float] | None:
    """Extract a single lat/lon. Nodes carry it directly; ways/relations use
    the 'center' Overpass gives us via `out center`."""
    if "lat" in element and "lon" in element:
        return element["lat"], element["lon"]
    center = element.get("center")
    if center:
        return center["lat"], center["lon"]
    return None


def wikidata_id(tags: dict) -> str | None:
    qid = tags.get("wikidata")
    if qid and qid.startswith("Q") and qid[1:].isdigit():
        return qid
    return None


def normalize_element(element: dict) -> dict | None:
    tags = element.get("tags", {})
    name = tags.get("name") or tags.get("name:en") or tags.get("alt_name")
    coords = coordinates(element)
    if coords is None:
        return None  # unusable without a location

    lat, lon = coords
    category = classify_type(tags)

    return {
        "id": f"{element['type']}/{element['id']}",
        "osm_type": element["type"],
        "osm_id": element["id"],
        "name": name,  # may be None; the build stage flags these
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "category": category,
        "category_label": CATEGORY_LABELS[category],
        "ruin": is_ruin(tags),
        "wikidata": wikidata_id(tags),
        "wikipedia": tags.get("wikipedia"),
        # An exact Commons file/category straight from OSM — a free image source
        # the image stage uses before falling back to geo-search.
        "wikimedia_commons": tags.get("wikimedia_commons"),
        # Archaeological Survey of Ireland record number (for future descriptions).
        "smr": tags.get("ref:IE:smr"),
        "heritage": tags.get("heritage"),
        "website": tags.get("website") or tags.get("contact:website"),
        "opening_hours": tags.get("opening_hours"),
        "fee": tags.get("fee"),
        "raw_castle_type": tags.get("castle_type"),
    }


# Fields worth preserving when merging duplicate records; also used to score
# which record in a duplicate cluster is the "richest" one to keep.
_INFORMATIVE_FIELDS = (
    "wikidata", "wikipedia", "wikimedia_commons", "smr", "website", "heritage",
    "opening_hours", "fee", "image", "thumbnail", "summary", "description",
    "built_year", "builder",
)
# When two records tie on richness, prefer the building footprint over a point.
_TYPE_RANK = {"way": 0, "relation": 1, "node": 2}
DEDUPE_RADIUS_M = 150  # same-name records closer than this are one physical site


def _haversine_m(a: dict, b: dict) -> float:
    R = 6371000
    dlat = math.radians(b["lat"] - a["lat"])
    dlon = math.radians(b["lon"] - a["lon"])
    lat_a, lat_b = math.radians(a["lat"]), math.radians(b["lat"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def deduplicate_colocated(records: list[dict], radius_m: float = DEDUPE_RADIUS_M):
    """Merge records that share a name and sit within `radius_m` of each other.

    OSM often maps one castle several times — as a point plus a building outline,
    or as several building-part polygons. Same-name records far apart (distinct
    castles that happen to share a name) are left untouched. The richest record in
    a cluster is kept, backfilled with any fields its duplicates had.
    """
    def rec_type(r: dict) -> str:
        return r.get("osm_type") or r["id"].split("/")[0]

    def score(r: dict) -> int:
        return sum(1 for k in _INFORMATIVE_FIELDS if r.get(k))

    by_name: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        name = (r.get("name") or "").strip().lower()
        if name:
            by_name[name].append(i)

    drop: set[int] = set()
    for indices in by_name.values():
        if len(indices) < 2:
            continue
        # Greedy transitive clustering: an index joins a cluster if it is within
        # radius of any member already in it.
        clusters: list[list[int]] = []
        for i in indices:
            for cluster in clusters:
                if any(_haversine_m(records[i], records[j]) <= radius_m for j in cluster):
                    cluster.append(i)
                    break
            else:
                clusters.append([i])
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            keep = max(cluster, key=lambda j: (
                score(records[j]), -_TYPE_RANK.get(rec_type(records[j]), 9),
                -records[j].get("osm_id", 0),
            ))
            for j in cluster:
                if j == keep:
                    continue
                for k in _INFORMATIVE_FIELDS:  # never lose data from a dropped twin
                    if not records[keep].get(k) and records[j].get(k):
                        records[keep][k] = records[j][k]
                drop.add(j)

    deduped = [r for i, r in enumerate(records) if i not in drop]
    return deduped, len(drop)


def run() -> list[dict]:
    print("Stage 2: normalizing and classifying...")
    raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    elements = raw.get("elements", [])

    records: list[dict] = []
    seen_wikidata: dict[str, dict] = {}
    for element in elements:
        record = normalize_element(element)
        if record is None:
            continue
        # OSM sometimes has the same castle as both a node and a way/relation.
        # De-duplicate on Wikidata ID when present, keeping the richer record.
        qid = record["wikidata"]
        if qid and qid in seen_wikidata:
            existing = seen_wikidata[qid]
            # Prefer the one that actually has a name.
            if not existing["name"] and record["name"]:
                records[records.index(existing)] = record
                seen_wikidata[qid] = record
            continue
        if qid:
            seen_wikidata[qid] = record
        records.append(record)

    # Second dedupe pass: same-name records mapped multiple times (point + outline,
    # or several building-part polygons) that share no Wikidata ID.
    records, merged = deduplicate_colocated(records)
    if merged:
        print(f"  merged {merged} co-located same-name duplicate(s)")

    NORMALIZED_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    counts: dict[str, int] = {}
    named = 0
    for r in records:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
        if r["name"]:
            named += 1
    print(f"  {len(records)} records ({named} named) -> {NORMALIZED_PATH}")
    print(f"  by category: {counts}")
    return records


if __name__ == "__main__":
    run()
