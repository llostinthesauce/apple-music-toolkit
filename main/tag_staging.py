#!/usr/bin/env python3
"""
tag_staging.py — Tag files in staging directory using folder structure as metadata.

Staging layout from fill.py:  Artist/Album/Title.m4a
This script reads that structure and writes proper metadata tags.

Also fetches track numbers from Tidal when possible.

Usage:
  python3 main/tag_staging.py --staging ~/Downloads/_staging --dry-run
  python3 main/tag_staging.py --staging ~/Downloads/_staging
"""

import argparse
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from mutagen.mp4 import MP4, MP4Cover

API_BASE = "https://triton.squid.wtf"
REQUEST_DELAY = 1.0
AUDIO_EXTS = {".m4a", ".mp3", ".flac"}


def api_get(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    url = f"{API_BASE}{path}"
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(10)
            r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [API error] {url} — {e}")
        return None


def norm(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def get_album_tracklist(artist: str, album: str) -> Optional[List[Dict]]:
    """Search Tidal for an album and return its tracklist with track numbers."""
    from difflib import SequenceMatcher

    query = f"{artist} {album}"
    data = api_get("/search/", {"s": query})
    time.sleep(REQUEST_DELAY)
    if not data or "data" not in data:
        return None

    items = data["data"].get("items", [])
    if not items:
        return None

    # Find best album match
    album_ids = {}
    for item in items:
        a = item.get("album", {})
        aid = a.get("id")
        atitle = a.get("title", "")
        if aid and atitle:
            score = SequenceMatcher(None, norm(album), norm(atitle)).ratio()
            if score > album_ids.get(aid, (0,))[0]:
                album_ids[aid] = (score, atitle)

    if not album_ids:
        return None

    best_id = max(album_ids, key=lambda k: album_ids[k][0])
    best_score = album_ids[best_id][0]
    if best_score < 0.5:
        return None

    # Get tracklist
    data = api_get("/album/", {"id": best_id})
    time.sleep(REQUEST_DELAY)
    if not data or "data" not in data:
        return None

    tracks = []
    for entry in data["data"].get("items", []):
        item = entry.get("item", {})
        tracks.append({
            "title": item.get("title", ""),
            "track_num": item.get("trackNumber", 0),
            "volume_num": item.get("volumeNumber", 1),
            "artist": item.get("artist", {}).get("name", ""),
        })
    return tracks


def match_track_number(filename: str, tracklist: List[Dict]) -> int:
    """Find the track number for a file by matching title against tracklist."""
    from difflib import SequenceMatcher

    stem = Path(filename).stem
    stem_norm = norm(stem)

    for t in tracklist:
        if norm(t["title"]) == stem_norm:
            return t["track_num"]
        if SequenceMatcher(None, norm(t["title"]), stem_norm).ratio() > 0.85:
            return t["track_num"]
    return 0


def main():
    parser = argparse.ArgumentParser(description="Tag staging files from folder structure")
    parser.add_argument("--staging", required=True, type=Path, help="Staging root")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--skip-tidal", action="store_true",
                        help="Skip Tidal lookups (no track numbers)")
    args = parser.parse_args()

    staging = args.staging.expanduser().resolve()
    if not staging.exists():
        print(f"Error: {staging} not found")
        return

    # Discover files: staging/Artist/Album/Title.ext
    files_to_tag: List[Tuple[Path, str, str, str]] = []  # (path, artist, album, title)
    for f in sorted(staging.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in AUDIO_EXTS:
            continue
        if f.name.startswith("._") or f.name == "download_log.json":
            continue

        rel = f.relative_to(staging)
        parts = rel.parts
        if len(parts) < 3:
            continue

        artist = parts[0]
        album = parts[1]
        title = f.stem
        files_to_tag.append((f, artist, album, title))

    if not files_to_tag:
        print("No files to tag.")
        return

    print(f"Found {len(files_to_tag)} files to tag")

    # Group by album for Tidal lookup
    albums_seen = {}  # (artist, album) -> tracklist or None
    tagged = 0
    skipped = 0

    for fpath, artist, album, title in files_to_tag:
        key = (artist, album)

        # Fetch tracklist once per album
        if key not in albums_seen and not args.skip_tidal:
            print(f"\n  Looking up: {artist} — {album}")
            albums_seen[key] = get_album_tracklist(artist, album)
            tracklist = albums_seen[key]
            if tracklist:
                print(f"    Found {len(tracklist)} tracks")
            else:
                print(f"    Not found on Tidal (will tag without track numbers)")
        elif key not in albums_seen:
            albums_seen[key] = None

        tracklist = albums_seen[key]
        track_num = 0
        if tracklist:
            track_num = match_track_number(fpath.name, tracklist)

        if args.dry_run:
            print(f"  WOULD TAG: {fpath.name:45s} | {artist} — {album} — {title} (#{track_num})")
            tagged += 1
            continue

        if fpath.suffix.lower() == ".m4a":
            try:
                audio = MP4(str(fpath))
                audio["\xa9nam"] = [title]
                audio["\xa9ART"] = [artist]
                audio["\xa9alb"] = [album]
                audio["aART"] = [artist]  # album artist
                if track_num > 0:
                    total = len(tracklist) if tracklist else 0
                    audio["trkn"] = [(track_num, total)]
                audio.save()
                tagged += 1
            except Exception as e:
                print(f"  ERROR: {fpath.name}: {e}")
        else:
            print(f"  SKIP (not m4a): {fpath.name}")
            skipped += 1

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Tagged {tagged} files"
          f"{f', skipped {skipped}' if skipped else ''}")


if __name__ == "__main__":
    main()
