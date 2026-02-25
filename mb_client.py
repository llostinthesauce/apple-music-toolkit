"""
mb_client.py — MusicBrainz API client with rate limiting and disk cache.

API docs: https://musicbrainz.org/doc/MusicBrainz_API
Rate limit: 1 request/second (enforced by this module)
Cache: ~/.cache/amt_mb_cache.json — persists across runs
"""

import json
import re
import time
import unicodedata
from pathlib import Path

import requests

CACHE_PATH = Path.home() / ".cache" / "amt_mb_cache.json"
MB_BASE = "https://musicbrainz.org/ws/2"
HEADERS = {"User-Agent": "apple-music-toolkit/1.0 (local-use)"}
MIN_INTERVAL = 1.05  # seconds between requests (MusicBrainz policy: 1/sec)

_cache: dict = {}
_last_request_time: float = 0.0


def _load_cache() -> None:
    global _cache
    if CACHE_PATH.exists():
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}


def _save_cache() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _rate_limited_get(url: str, params: dict) -> dict | None:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        _last_request_time = time.time()
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        _last_request_time = time.time()
        return None


def normalize(text: str) -> str:
    """Lowercase, strip accents, remove punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def lookup_album(artist: str, album: str) -> dict | None:
    """
    Query MusicBrainz for a release matching artist+album.

    Returns dict with keys:
        title     str   canonical album title
        tracks    list  [{"num": int, "title": str}, ...]
        count     int   total track count

    Returns None if no confident match found (score < 85).

    Implementation uses a two-step lookup: the release search endpoint does not
    return track data even with inc=recordings, so we first find the MBID via
    search, then fetch the full release by ID with inc=recordings.
    """
    if not _cache:
        _load_cache()
    cache_key = f"{normalize(artist)}||{normalize(album)}"
    if cache_key in _cache:
        return _cache[cache_key]

    # Step 1: search for the release to get MBID and score
    query = f'artist:"{artist}" AND release:"{album}"'
    search_data = _rate_limited_get(
        f"{MB_BASE}/release",
        {"query": query, "fmt": "json", "limit": 5},
    )

    result = None
    _save_cache()
    if search_data and search_data.get("releases"):
        for release in search_data["releases"]:
            score = int(release.get("score", 0))
            if score < 85:
                continue

            # Step 2: fetch full release by MBID to get track listings
            mbid = release.get("id")
            if not mbid:
                continue

            release_data = _rate_limited_get(
                f"{MB_BASE}/release/{mbid}",
                {"fmt": "json", "inc": "recordings"},
            )
            if not release_data:
                continue

            # Extract tracks from media
            tracks = []
            for medium in release_data.get("media", []):
                for track in medium.get("tracks", []):
                    num = track.get("number") or track.get("position", 0)
                    try:
                        num = int(num)
                    except (ValueError, TypeError):
                        num = 0
                    title = (track.get("recording") or {}).get("title") or track.get("title", "")
                    tracks.append({"num": num, "title": title})

            if tracks:
                result = {
                    "title": release_data.get("title", album),
                    "tracks": sorted(tracks, key=lambda t: t["num"]),
                    "count": len(tracks),
                }
                break

    _cache[cache_key] = result
    _save_cache()
    return result
