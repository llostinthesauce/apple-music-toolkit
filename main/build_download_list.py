#!/usr/bin/env python3
"""
build_download_list.py — Generate download list from diff + wholeness reports.

Collects all tracks that need downloading and outputs JSON for fill.py.

Usage:
  python3 main/build_download_list.py
  python3 main/build_download_list.py --diff output/cloud_diff_report.json
  python3 main/build_download_list.py --diff output/cloud_diff_report.json \\
    --wholeness output/wholeness_tidal_report.json
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

OUTPUT_DIR = Path(__file__).parent.parent / "output"


def norm_key(artist: str, album: str, title: str) -> str:
    """Create a dedup key."""
    import re
    def n(s):
        return re.sub(r"[^a-z0-9]", "", s.lower()) if s else ""
    return f"{n(artist)}|{n(album)}|{n(title)}"


def main():
    parser = argparse.ArgumentParser(description="Build download list from reports")
    parser.add_argument("--diff", type=Path, default=OUTPUT_DIR / "cloud_diff_report.json",
                        help="Cloud diff report JSON")
    parser.add_argument("--wholeness", type=Path, default=None,
                        help="Wholeness report JSON (optional)")
    parser.add_argument("--min-tracks", type=int, default=4,
                        help="Min cloud tracks for MISSING albums (default: 4)")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "download_list.json",
                        help="Output JSON path")
    args = parser.parse_args()

    seen: Set[str] = set()
    download_list: List[Dict[str, str]] = []

    def add_track(artist: str, album: str, title: str, source: str):
        key = norm_key(artist, album, title)
        if key not in seen:
            seen.add(key)
            download_list.append({
                "artist": artist, "album": album, "title": title, "source": source,
            })

    # 1. From cloud diff: MISSING albums and missing tracks from PARTIAL
    if args.diff.exists():
        with open(args.diff) as f:
            diff = json.load(f)

        for album in diff.get("albums", []):
            artist = album["artist"]
            album_name = album["album"]
            status = album["status"]

            if status == "MISSING" and album.get("cloud_count", 0) >= args.min_tracks:
                # All tracks from missing albums
                for t in album.get("missing", []):
                    add_track(artist, album_name, t["name"], "diff_missing_album")

            elif status in ("PARTIAL", "EXTRA_TRACKS"):
                # Just the missing tracks
                for t in album.get("missing", []):
                    add_track(artist, album_name, t["name"], "diff_partial")

        print(f"From diff report: {len(download_list)} tracks")

    # 2. From wholeness report: DOWNLOAD actions
    if args.wholeness and args.wholeness.exists():
        before = len(download_list)
        with open(args.wholeness) as f:
            wholeness = json.load(f)

        for album in wholeness.get("albums", []):
            if album.get("status") != "CHECKED":
                continue
            artist = album["artist"]
            album_name = album["album"]

            for action in album.get("actions", []):
                if action["action"] == "DOWNLOAD":
                    add_track(artist, album_name, action["tidal_title"],
                              "wholeness_download")

        print(f"From wholeness report: {len(download_list) - before} additional tracks")

    if not download_list:
        print("No tracks to download.")
        return

    # Group by album for summary
    by_album: Dict[str, int] = defaultdict(int)
    for t in download_list:
        by_album[f"{t['artist']} — {t['album']}"] += 1

    # Save
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(download_list, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"DOWNLOAD LIST SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total tracks:  {len(download_list)}")
    print(f"  Total albums:  {len(by_album)}")
    print(f"\n  Top albums by missing track count:")
    for album, count in sorted(by_album.items(), key=lambda x: -x[1])[:20]:
        print(f"    {count:3d}  {album}")
    print(f"\n  Saved to: {args.output}")


if __name__ == "__main__":
    main()
