#!/usr/bin/env python3
"""
Extract .m3u playlists directly from an Apple Music Library.xml export.

Writes one .m3u per user playlist. Each entry uses track metadata and the
original file path from the XML "Location" field when available.
"""

import argparse
import plistlib
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


SYSTEM_PLAYLIST_KEYS = {
    "Master",
    "Music Videos",
    "Movies",
    "TV Shows",
    "Podcasts",
    "Audiobooks",
    "Voice Memos",
    "Purchased",
    "Downloaded",
}


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "Untitled Playlist"


def location_to_path(location: str) -> str:
    if not location:
        return ""
    parsed = urlparse(location)
    if parsed.scheme != "file":
        return location
    path = unquote(parsed.path or "")
    if not path:
        return ""
    return path


def parse_library(source: Path):
    with source.open("rb") as handle:
        data = plistlib.load(handle)

    tracks: dict[int, dict] = {}
    for track_id_raw, info in data.get("Tracks", {}).items():
        try:
            track_id = int(track_id_raw)
        except Exception:
            continue
        tracks[track_id] = {
            "title": info.get("Name", "") or "",
            "artist": info.get("Artist", "") or "",
            "album": info.get("Album", "") or "",
            "duration_ms": int(info.get("Total Time", 0) or 0),
            "location": location_to_path(info.get("Location", "") or ""),
        }

    playlists: list[dict] = []
    for pl in data.get("Playlists", []):
        if pl.get("Master") or pl.get("Distinguished Kind") or pl.get("Name") in SYSTEM_PLAYLIST_KEYS:
            continue
        name = pl.get("Name", "Untitled Playlist")
        track_ids = [int(item["Track ID"]) for item in pl.get("Playlist Items", []) if "Track ID" in item]
        playlists.append({"name": name, "track_ids": track_ids})

    return tracks, playlists


def write_playlist_m3u(output_dir: Path, filename_stem: str, entries: list[dict]):
    out_path = output_dir / f"{filename_stem}.m3u"
    lines = ["#EXTM3U"]
    for entry in entries:
        duration = max(0, int(entry["duration_ms"] // 1000))
        artist = entry["artist"].strip()
        title = entry["title"].strip()
        label = f"{artist} - {title}" if artist else title
        lines.append(f"#EXTINF:{duration},{label}")
        if entry["location"]:
            lines.append(entry["location"])
        else:
            lines.append(f"#MISSING_PATH {label}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Extract .m3u playlists from Apple Music Library.xml")
    parser.add_argument("--source", required=True, type=Path, help="Path to Library.xml export")
    parser.add_argument("--output", required=True, type=Path, help="Output folder for .m3u files")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Error: source file not found: {args.source}", file=sys.stderr)
        sys.exit(1)

    args.output.mkdir(parents=True, exist_ok=True)
    tracks, playlists = parse_library(args.source)

    written = 0
    total_entries = 0
    used_names: dict[str, int] = {}
    for playlist in playlists:
        rows = [tracks[tid] for tid in playlist["track_ids"] if tid in tracks]
        base = safe_filename(playlist["name"])
        count = used_names.get(base, 0) + 1
        used_names[base] = count
        filename_stem = base if count == 1 else f"{base}__{count}"
        write_playlist_m3u(args.output, filename_stem, rows)
        written += 1
        total_entries += len(rows)

    print(f"Playlists written: {written}")
    print(f"Total entries: {total_entries}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
