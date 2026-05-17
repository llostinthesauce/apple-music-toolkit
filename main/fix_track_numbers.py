#!/usr/bin/env python3
"""
fix_track_numbers.py — Add missing track numbers by looking up albums on Tidal.

Scans library for M4A files without trkn tags, groups by album,
looks up each album on Tidal, and writes track numbers.

Usage:
  python3 main/fix_track_numbers.py --lib-root ~/Music --dry-run
  python3 main/fix_track_numbers.py --lib-root ~/Music
"""

import argparse
import re
import time
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

import requests
from mutagen.mp4 import MP4

API_BASE = "https://triton.squid.wtf"
REQUEST_DELAY = 0.5


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
    """Search Tidal for an album and return its tracklist."""
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
        })
    return tracks


def match_track_number(filename: str, tracklist: List[Dict]) -> int:
    """Find the track number for a file by fuzzy-matching title."""
    stem = Path(filename).stem
    stem_norm = norm(stem)

    # Exact normalized match
    for t in tracklist:
        if norm(t["title"]) == stem_norm:
            return t["track_num"]

    # Fuzzy match
    best_score = 0.0
    best_num = 0
    for t in tracklist:
        score = SequenceMatcher(None, norm(t["title"]), stem_norm).ratio()
        if score > best_score:
            best_score = score
            best_num = t["track_num"]

    if best_score > 0.75:
        return best_num
    return 0


def main():
    parser = argparse.ArgumentParser(description="Fix missing track numbers via Tidal lookup")
    parser.add_argument("--lib-root", required=True, type=Path, help="Library root")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    lib_root = args.lib_root.expanduser().resolve()

    # Find files missing track numbers
    albums = defaultdict(list)  # (artist_dir, album_dir) -> [file_paths]
    total_missing = 0

    for f in sorted(lib_root.rglob("*.m4a")):
        if f.name.startswith("._"):
            continue
        try:
            audio = MP4(str(f))
            trkn = audio.tags.get("trkn", [(0, 0)])
            if trkn[0][0] > 0:
                continue  # already has track number
        except Exception:
            continue

        rel = f.relative_to(lib_root)
        parts = rel.parts
        if len(parts) < 3:
            continue

        artist_dir = parts[0]
        album_dir = parts[1]
        albums[(artist_dir, album_dir)].append(f)
        total_missing += 1

    print(f"Found {total_missing} files missing track numbers across {len(albums)} albums\n")

    fixed = 0
    not_found = 0
    no_match = 0

    for (artist_dir, album_dir), files in sorted(albums.items()):
        print(f"  [{artist_dir}] {album_dir} ({len(files)} files)", end="", flush=True)

        tracklist = get_album_tracklist(artist_dir, album_dir)
        if not tracklist:
            print(" — NOT FOUND on Tidal")
            not_found += len(files)
            continue

        album_fixed = 0
        for fpath in files:
            trkn = match_track_number(fpath.name, tracklist)
            if trkn == 0:
                no_match += 1
                continue

            if args.dry_run:
                album_fixed += 1
                fixed += 1
                continue

            try:
                audio = MP4(str(fpath))
                audio["trkn"] = [(trkn, len(tracklist))]
                audio.save()
                album_fixed += 1
                fixed += 1
            except Exception as e:
                print(f"\n    ERROR: {fpath.name}: {e}")

        print(f" — fixed {album_fixed}/{len(files)}")

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Done.")
    print(f"  Fixed:     {fixed}")
    print(f"  Not found: {not_found}")
    print(f"  No match:  {no_match}")


if __name__ == "__main__":
    main()
