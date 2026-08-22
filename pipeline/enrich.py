"""Stage 3 — enrich named castles with facts from Wikidata.

For every record that carries a Wikidata ID (from OSM), we pull built-date,
architect/builder, an image filename and the English Wikipedia URL.

We use the MediaWiki **`wbgetentities`** action API rather than the SPARQL
Query Service (WDQS). They are separate services: WDQS suffers outages and
aggressive rate-limiting, while the entities API is stable and lets us fetch
50 entities per request. Wikidata is CC0, so this data is free to use.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
NORMALIZED_PATH = DATA_DIR / "normalized.json"
ENRICHED_PATH = DATA_DIR / "enriched.json"

API_ENDPOINT = "https://www.wikidata.org/w/api.php"
# REST summary endpoint: edge-cached and built for high request volumes,
# unlike the action API's prop=extracts which throttles anonymous callers.
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
BATCH_SIZE = 50  # wbgetentities hard limit for anonymous callers
MAX_RETRIES = 5
RETRY_WAIT = 5  # base seconds; grows with each attempt / honours Retry-After
SUMMARY_MAX_CHARS = 700  # keep the data file lean; UI clamps further

# Wikimedia's User-Agent policy asks for an identifiable agent + contact.
USER_AGENT = "castles-of-ireland-pipeline/1.0 (https://github.com/example/castles-of-ireland; educational project)"

# Wikidata property IDs we care about.
P_INCEPTION = "P571"
P_ARCHITECT = "P84"
P_IMAGE = "P18"


def http_json(url: str):
    """GET a URL and parse JSON, with backoff on 429/transient errors.

    Returns None for a 404 (article not found) so callers can skip it.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (429, 503) and attempt < MAX_RETRIES:
                wait = int(exc.headers.get("Retry-After") or RETRY_WAIT * attempt)
                print(f"    throttled ({exc.code}); waiting {wait}s ({attempt}/{MAX_RETRIES})...")
                time.sleep(wait)
                continue
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < MAX_RETRIES:
                print(f"    request failed ({exc}); retry {attempt}/{MAX_RETRIES}...")
                time.sleep(RETRY_WAIT)
                continue
            raise
    raise RuntimeError(f"exhausted retries for {url}")


def api_get(params: dict, endpoint: str = API_ENDPOINT) -> dict:
    return http_json(endpoint + "?" + urllib.parse.urlencode(params))


def get_entities(ids: list[str], props: str) -> dict:
    """Fetch entities in chunks of BATCH_SIZE, merged into one dict by ID."""
    merged: dict[str, dict] = {}
    for i in range(0, len(ids), BATCH_SIZE):
        chunk = ids[i : i + BATCH_SIZE]
        print(f"    entities {i + 1}-{i + len(chunk)} of {len(ids)} (props={props})...")
        data = api_get(
            {
                "action": "wbgetentities",
                "ids": "|".join(chunk),
                "props": props,
                "languages": "en",
                "format": "json",
            }
        )
        merged.update(data.get("entities", {}))
        time.sleep(0.3)  # be polite
    return merged


def get_wikipedia_summaries(titles: list[str]) -> dict[str, str]:
    """Fetch each article's lead paragraph via the REST summary endpoint.

    Returns a dict keyed by the requested article title. One request per title,
    but the endpoint is CDN-cached and far more tolerant than prop=extracts.
    """
    summaries: dict[str, str] = {}
    for i, title in enumerate(titles, start=1):
        if i % 25 == 0 or i == 1:
            print(f"    summary {i} of {len(titles)}...")
        # REST path wants underscores; quote the rest (e.g. apostrophes, slashes).
        slug = urllib.parse.quote(title.replace(" ", "_"), safe="")
        data = http_json(WIKIPEDIA_SUMMARY + slug)
        if not data:
            continue
        extract = (data.get("extract") or "").strip()
        if not extract:
            continue
        if len(extract) > SUMMARY_MAX_CHARS:
            extract = extract[:SUMMARY_MAX_CHARS].rsplit(" ", 1)[0] + "…"
        summaries[title] = extract
        time.sleep(0.1)  # gentle pacing; the endpoint is cached
    return summaries


def first_claim_value(entity: dict, prop: str):
    """Return the datavalue.value of an entity's first claim for `prop`, or None."""
    claims = entity.get("claims", {}).get(prop)
    if not claims:
        return None
    snak = claims[0].get("mainsnak", {})
    if snak.get("snaktype") != "value":
        return None
    return snak.get("datavalue", {}).get("value")


def year_from_time(value) -> int | None:
    """Wikidata time values look like '+1612-01-01T00:00:00Z' (or '-' for BCE)."""
    if not value:
        return None
    time_str = value.get("time") if isinstance(value, dict) else value
    if not time_str:
        return None
    try:
        sign = -1 if time_str.startswith("-") else 1
        digits = time_str.lstrip("+-").split("-")[0]
        return sign * int(digits)
    except (ValueError, IndexError):
        return None


def run() -> list[dict]:
    print("Stage 3: enriching from Wikidata (wbgetentities API)...")
    records = json.loads(NORMALIZED_PATH.read_text(encoding="utf-8"))
    qids = sorted({r["wikidata"] for r in records if r["wikidata"]})
    print(f"  {len(qids)} records have a Wikidata ID")

    if not qids:
        ENRICHED_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return records

    entities = get_entities(qids, props="claims|sitelinks/urls|descriptions")

    facts: dict[str, dict] = {}
    architect_ids: set[str] = set()
    title_to_qid: dict[str, str] = {}  # enwiki article title -> our castle QID
    for qid, entity in entities.items():
        entry: dict = {}

        entry["built_year"] = year_from_time(first_claim_value(entity, P_INCEPTION))

        image = first_claim_value(entity, P_IMAGE)
        if isinstance(image, str):
            entry["image"] = image  # bare filename, e.g. "Bunratty Castle.jpg"

        architect = first_claim_value(entity, P_ARCHITECT)
        if isinstance(architect, dict) and architect.get("id"):
            entry["_architect_id"] = architect["id"]
            architect_ids.add(architect["id"])

        # Short one-line Wikidata description (covers many sites that lack an article).
        desc = entity.get("descriptions", {}).get("en", {}).get("value")
        if desc:
            entry["description"] = desc

        sitelinks = entity.get("sitelinks", {})
        enwiki = sitelinks.get("enwiki")
        if enwiki and enwiki.get("url"):
            entry["wikipedia_url"] = enwiki["url"]
            if enwiki.get("title"):
                title_to_qid[enwiki["title"]] = qid

        facts[qid] = {k: v for k, v in entry.items() if v is not None}

    # Wikipedia lead paragraphs — the legitimate, sourced version of a descriptive blurb.
    if title_to_qid:
        print(f"  fetching {len(title_to_qid)} Wikipedia summaries...")
        summaries = get_wikipedia_summaries(sorted(title_to_qid))
        for title, summary in summaries.items():
            qid = title_to_qid.get(title)
            if qid:
                facts[qid]["summary"] = summary

    # Second pass: resolve architect/builder QIDs to human-readable labels.
    if architect_ids:
        print(f"  resolving {len(architect_ids)} builder/architect labels...")
        label_entities = get_entities(sorted(architect_ids), props="labels")
        labels = {
            qid: ent.get("labels", {}).get("en", {}).get("value")
            for qid, ent in label_entities.items()
        }
        for entry in facts.values():
            aid = entry.pop("_architect_id", None)
            if aid and labels.get(aid):
                entry["builder"] = labels[aid]

    enriched = 0
    for record in records:
        qid = record["wikidata"]
        if qid and facts.get(qid):
            record.update(facts[qid])
            enriched += 1

    ENRICHED_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with_year = sum(1 for r in records if r.get("built_year"))
    with_image = sum(1 for r in records if r.get("image"))
    with_summary = sum(1 for r in records if r.get("summary"))
    with_desc = sum(1 for r in records if r.get("description"))
    print(f"  enriched {enriched} records -> {ENRICHED_PATH}")
    print(f"  built_year: {with_year}, image: {with_image}, "
          f"wiki summary: {with_summary}, short description: {with_desc}")
    return records


if __name__ == "__main__":
    run()
