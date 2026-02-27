"""
download_missing.py — download missing tracks from triton.squid.wtf (hifi-api).

Reads missing_from_local.txt, searches Tidal for each track, fetches lossless
FLAC manifests, and downloads to a staging folder in iTunes Artist/Album/ structure.

Usage:
    # Dry run (no downloads, just shows what would happen):
    python3 download_missing.py --missing output/missing_from_local.txt --staging ~/staging --dry-run

    # Real run:
    python3 download_missing.py --missing output/missing_from_local.txt --staging ~/staging
"""

import argparse
import base64
import json
import re
import time
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import requests

API_BASE = "https://triton.squid.wtf"
CONFIDENCE_THRESHOLD = 0.75
REQUEST_DELAY = 1.0  # seconds between API calls
AUDIO_EXTENSIONS = {".flac", ".m4a", ".mp3"}


# ---------------------------------------------------------------------------
# Normalization (shared with cross_check.py)
# ---------------------------------------------------------------------------

def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_artist(artist: str) -> str:
    # Strip feat. suffixes
    artist = re.sub(r"\s*(feat\.?|ft\.?|featuring|with)\s+.*", "", artist, flags=re.IGNORECASE)
    # Strip trailing parenthetical band descriptions, e.g. "UGK (Underground Kingz)"
    artist = re.sub(r"\s*\([^)]+\)\s*$", "", artist)
    return normalize(artist)


def normalize_title(title: str) -> str:
    title = re.sub(
        r"\s*[\(\[](remaster(ed)?|remix|live|bonus( track)?|deluxe|re-?issue"
        r"|single version|radio edit|album version|\d{4}( remaster)?)[^\)\]]*[\)\]]",
        "", title, flags=re.IGNORECASE,
    )
    return normalize(title)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def confidence(search_artist: str, search_title: str, result: dict) -> float:
    """Score a Tidal search result against our target (0–1)."""
    result_artist = result.get("artist", {}).get("name", "") or ""
    result_title = result.get("title", "") or ""
    a_score = similarity(normalize_artist(search_artist), normalize_artist(result_artist))
    t_score = similarity(normalize_title(search_title), normalize_title(result_title))
    return (a_score + t_score) / 2


# ---------------------------------------------------------------------------
# Parsing missing_from_local.txt
# ---------------------------------------------------------------------------

def parse_missing_txt(path: Path) -> list[dict]:
    """
    Parse the FULL MISSING TRACK LIST section of missing_from_local.txt.
    Returns list of {artist, album, title}.
    """
    tracks = []
    in_section = False
    artist = album = None

    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip("\n")

            if "FULL MISSING TRACK LIST" in stripped:
                in_section = True
                continue
            if not in_section:
                continue
            if not stripped.strip():
                continue

            if stripped.startswith("    - "):
                title = stripped.strip()[2:].strip()
                if artist and album:
                    tracks.append({"artist": artist, "album": album, "title": title})
            elif stripped.startswith("  "):
                album = stripped.strip()
            elif not stripped.startswith("=") and not stripped.startswith("-"):
                artist = stripped.strip()

    return tracks


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def api_get(path: str, params: dict = None) -> dict | None:
    url = f"{API_BASE}{path}"
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 429:
            print("  [rate limited] sleeping 10s...")
            time.sleep(10)
            r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [API error] {url} — {e}")
        return None


def search_track(artist: str, title: str) -> tuple[dict | None, float]:
    """Search for a track, return (best_result, confidence_score)."""
    query = f"{artist} {title}"
    data = api_get("/search/", {"s": query})
    time.sleep(REQUEST_DELAY)
    if not data or "data" not in data:
        return None, 0.0

    items = data["data"].get("items", [])
    if not items:
        return None, 0.0

    best = max(items, key=lambda r: confidence(artist, title, r))
    score = confidence(artist, title, best)
    return best, score


def get_flac_url(track_id: int, quality: str = "LOSSLESS") -> str | None:
    """Fetch track manifest and decode the FLAC URL."""
    data = api_get("/track/", {"id": track_id, "quality": quality})
    time.sleep(REQUEST_DELAY)
    if not data or "data" not in data:
        return None

    manifest_b64 = data["data"].get("manifest")
    if not manifest_b64:
        return None

    try:
        manifest = json.loads(base64.b64decode(manifest_b64).decode("utf-8"))
        urls = manifest.get("urls", [])
        return urls[0] if urls else None
    except Exception as e:
        print(f"  [manifest error] {e}")
        return None


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def safe_name(s: str) -> str:
    """Replace filesystem-unsafe characters."""
    return re.sub(r'[<>:"/\\|?*]', "_", s).strip()


def staging_path(staging_root: Path, artist: str, album: str, title: str, ext: str = ".flac") -> Path:
    return staging_root / safe_name(artist) / safe_name(album) / f"{safe_name(title)}{ext}"


def already_exists(staging_root: Path, artist: str, album: str, title: str) -> bool:
    for ext in AUDIO_EXTENSIONS:
        if staging_path(staging_root, artist, album, title, ext).exists():
            return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Download missing tracks from triton.squid.wtf")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--missing", type=Path, help="Path to missing_from_local.txt")
    group.add_argument("--json", type=Path, help="Path to JSON list of {artist,album,title} dicts")
    parser.add_argument("--staging", required=True, type=Path, help="Staging folder for downloads")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no downloads")
    parser.add_argument("--limit", type=int, default=0, help="Max tracks to process (0 = all)")
    parser.add_argument("--quality", default="LOSSLESS", choices=["LOSSLESS", "HI_RES_LOSSLESS", "HIGH"],
                        help="Audio quality (default: LOSSLESS = 16-bit/44.1kHz FLAC)")
    args = parser.parse_args()

    if args.json:
        print(f"Loading tracks from {args.json}...")
        with open(args.json, encoding="utf-8") as f:
            tracks = json.load(f)
    else:
        print(f"Parsing missing tracks from {args.missing}...")
        tracks = parse_missing_txt(args.missing)
    print(f"  {len(tracks)} tracks to process")

    if args.limit:
        tracks = tracks[:args.limit]
        print(f"  (limited to first {args.limit})")

    if args.dry_run:
        print("\n[DRY RUN] No files will be downloaded.\n")
    else:
        args.staging.mkdir(parents=True, exist_ok=True)

    log = {
        "downloaded": [],
        "skipped_exists": [],
        "low_confidence": [],
        "not_found": [],
        "failed": [],
    }

    for i, track in enumerate(tracks, 1):
        artist = track["artist"]
        album  = track["album"]
        title  = track["title"]
        prefix = f"[{i}/{len(tracks)}]"

        # Skip if already staged
        if not args.dry_run and already_exists(args.staging, artist, album, title):
            print(f"{prefix} SKIP (exists)  {artist} — {title}")
            log["skipped_exists"].append(track)
            continue

        # Search
        result, score = search_track(artist, title)

        if result is None:
            print(f"{prefix} NOT FOUND      {artist} — {title}")
            log["not_found"].append(track)
            continue

        result_artist = result.get("artist", {}).get("name", "")
        result_title  = result.get("title", "")
        track_id      = result.get("id")

        if score < CONFIDENCE_THRESHOLD:
            print(f"{prefix} LOW CONF {score:.2f}  {artist} — {title}")
            print(f"       best match: {result_artist} — {result_title} (id={track_id})")
            log["low_confidence"].append({**track, "match_artist": result_artist,
                                          "match_title": result_title, "score": score})
            continue

        if args.dry_run:
            print(f"{prefix} WOULD DL {score:.2f}  {artist} — {title}")
            print(f"       match:      {result_artist} — {result_title} (id={track_id})")
            log["downloaded"].append(track)
            continue

        # Get FLAC URL
        flac_url = get_flac_url(track_id, args.quality)
        if not flac_url:
            print(f"{prefix} FAILED (no URL) {artist} — {title}")
            log["failed"].append(track)
            continue

        # Download
        dest = staging_path(args.staging, artist, album, title)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            resp = requests.get(flac_url, timeout=60, stream=True)
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
            size_mb = dest.stat().st_size / 1_048_576
            print(f"{prefix} OK {score:.2f}  {artist} — {title}  ({size_mb:.1f} MB)")
            log["downloaded"].append(track)
        except Exception as e:
            print(f"{prefix} FAILED (download) {artist} — {title} — {e}")
            if dest.exists():
                dest.unlink()
            log["failed"].append(track)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    if args.dry_run:
        print(f"  Would download:   {len(log['downloaded'])}")
    else:
        print(f"  Downloaded:       {len(log['downloaded'])}")
        print(f"  Skipped (exists): {len(log['skipped_exists'])}")
    print(f"  Low confidence:   {len(log['low_confidence'])}")
    print(f"  Not found:        {len(log['not_found'])}")
    print(f"  Failed:           {len(log['failed'])}")

    # Write log
    log_path = args.staging / "download_log.json" if not args.dry_run else Path("output/dry_run_log.json")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    print(f"\nLog written to {log_path}")


if __name__ == "__main__":
    main()
