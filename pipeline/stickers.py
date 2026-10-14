"""Stage 3.8 — download a small photo per castle for the map's photo stickers.

The map turns each castle's photo into a die-cut sticker by drawing it onto a
canvas. That needs the image to be same-origin (or served with CORS headers),
and hot-linking hundreds of Wikimedia thumbnails from every visitor's browser
trips Wikimedia's rate limit. So we fetch each photo once, politely, into
``web/stickers/photos/`` and the frontend serves it locally.

Files already on disk are skipped, so re-runs only fetch photos for new or
changed castles. Pure standard library.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pipeline.enrich import USER_AGENT
from pipeline.overrides import id_to_filestem

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ENRICHED_PATH = DATA_DIR / "enriched.json"
NORMALIZED_PATH = DATA_DIR / "normalized.json"
PHOTO_DIR = ROOT / "web" / "stickers" / "photos"
PHOTO_URL_PREFIX = "stickers/photos/"   # relative to web/index.html

# upload.wikimedia.org thumbnail paths are derived from the filename's MD5.
# 250px is one of Wikimedia's standard (pre-cached) thumbnail widths.
STICKER_WIDTH = 250
COMMONS_EXTS = {"jpg", "jpeg", "png"}

REQUEST_GAP = 1.5   # seconds between downloads, to stay under the rate limit
MAX_RETRIES = 5
RETRY_WAIT = 20


def commons_sticker_url(image: str | None) -> str | None:
    """Direct upload.wikimedia.org thumbnail URL for a Commons filename."""
    if not image:
        return None
    filename = image.rsplit("/", 1)[-1].replace(" ", "_")
    if filename.rsplit(".", 1)[-1].lower() not in COMMONS_EXTS:
        return None
    digest = hashlib.md5(filename.encode("utf-8")).hexdigest()
    quoted = urllib.parse.quote(filename)
    return (f"https://upload.wikimedia.org/wikipedia/commons/thumb/"
            f"{digest[0]}/{digest[:2]}/{quoted}/{STICKER_WIDTH}px-{quoted}")


def source_url(record: dict) -> str | None:
    """Where to fetch this castle's sticker photo from, if anywhere."""
    image_url = record.get("image_url")
    if image_url:
        # A dropped-in local file is already same-origin; a pasted link is fetched.
        return image_url if image_url.startswith("http") else None
    return commons_sticker_url(record.get("image"))


def existing_photo(castle_id: str) -> Path | None:
    matches = sorted(PHOTO_DIR.glob(f"{id_to_filestem(castle_id)}.*"))
    return matches[0] if matches else None


def sticker_path(record: dict) -> str | None:
    """The sticker image the frontend should use, relative to web/index.html."""
    image_url = record.get("image_url")
    if image_url and not image_url.startswith("http"):
        return image_url
    photo = existing_photo(record["id"])
    return PHOTO_URL_PREFIX + photo.name if photo else None


def download(url: str) -> bytes | None:
    """GET bytes with backoff on throttling. Returns None if the file is missing."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404, 410):
                return None   # e.g. the original is narrower than STICKER_WIDTH
            if attempt < MAX_RETRIES:
                wait = int(exc.headers.get("Retry-After") or RETRY_WAIT * attempt)
                print(f"    HTTP {exc.code}; waiting {wait}s ({attempt}/{MAX_RETRIES})...")
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < MAX_RETRIES:
                print(f"    request failed ({exc}); retry {attempt}/{MAX_RETRIES}...")
                time.sleep(RETRY_WAIT)
                continue
            raise
    return None


def run() -> None:
    print("Stage 3.8: downloading sticker photos...")
    source = ENRICHED_PATH if ENRICHED_PATH.exists() else NORMALIZED_PATH
    records = json.loads(source.read_text(encoding="utf-8"))
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)

    todo = [(r, url) for r in records
            if (url := source_url(r)) and not existing_photo(r["id"])]
    print(f"  {len(todo)} to fetch")
    fetched = missing = 0
    for i, (record, url) in enumerate(todo, 1):
        data = download(url)
        if data:
            ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                ext = ".jpg"
            (PHOTO_DIR / f"{id_to_filestem(record['id'])}{ext}").write_bytes(data)
            fetched += 1
        else:
            missing += 1
        if i % 50 == 0:
            print(f"  {i}/{len(todo)} ({fetched} saved, {missing} unavailable)")
        time.sleep(REQUEST_GAP)
    print(f"  saved {fetched}, unavailable {missing}")


if __name__ == "__main__":
    run()
