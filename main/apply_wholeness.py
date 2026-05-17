#!/usr/bin/env python3
"""
apply_wholeness.py — Apply fixes from the wholeness report.

Moves QUARANTINE tracks to _TRASH/, skips KEEP and DOWNLOAD actions.

Safety: Skips albums where Tidal returned suspiciously few tracks
(likely a bad search match — e.g. single/EP instead of the full album).

Usage:
  python3 main/apply_wholeness.py --report output/wholeness_tidal_report.json --dry-run
  python3 main/apply_wholeness.py --report output/wholeness_tidal_report.json
"""

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
import os

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", str(Path.home() / "Music")))

# Min Tidal tracks required to trust quarantine recommendations.
# If Tidal says an album has <=3 tracks but we have 10+ local files,
# it's almost certainly a bad search match (single/EP instead of album).
MIN_TIDAL_TRACKS = 4


def main():
    parser = argparse.ArgumentParser(description="Apply wholeness fixes")
    parser.add_argument("--report", required=True, type=Path,
                        help="Wholeness report JSON")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--min-tidal", type=int, default=MIN_TIDAL_TRACKS,
                        help="Skip albums where Tidal returned fewer than N tracks "
                             "(bad match protection, default: 4)")
    args = parser.parse_args()

    with open(args.report) as f:
        report = json.load(f)

    quarantine_files: List[Tuple[Path, str]] = []
    skipped_albums: List[str] = []

    for album in report.get("albums", []):
        if album.get("status") != "CHECKED":
            continue

        tidal_count = album.get("tidal_track_count", 0)
        local_count = album.get("local_file_count", 0)
        artist = album.get("artist", "")
        album_name = album.get("album", "")

        actions = album.get("actions", [])
        keep_count = sum(1 for a in actions if a["action"] == "KEEP")
        q_count = sum(1 for a in actions if a["action"] == "QUARANTINE")
        is_known_bad = any(
            a.get("reason", "") == "Known wrong-audio album" for a in actions
        )

        # Safety filter 1: Tidal returned very few tracks but we have many
        # local files — almost certainly matched a single/EP, not the album
        if tidal_count < args.min_tidal and local_count >= args.min_tidal:
            skipped_albums.append(
                f"[few-tracks] {artist} — {album_name} "
                f"(Tidal: {tidal_count}, Local: {local_count})"
            )
            continue

        # Safety filter 2: zero tracks matched but we have a real album
        # locally — the track-level matching failed (wrong Tidal edition,
        # different track names, etc). Skip unless it's a known-bad album.
        if keep_count == 0 and local_count >= args.min_tidal and not is_known_bad:
            skipped_albums.append(
                f"[zero-match] {artist} — {album_name} "
                f"(Tidal: {tidal_count}, Local: {local_count}, KEEP: 0)"
            )
            continue

        for action in actions:
            if action["action"] == "QUARANTINE":
                path = Path(action.get("local_path", ""))
                if path.exists():
                    reason = action.get("reason", "wholeness check")
                    quarantine_files.append((path, reason))

    if skipped_albums:
        print(f"SKIPPED {len(skipped_albums)} albums (bad Tidal match — too few tracks):")
        for s in skipped_albums:
            print(f"  {s}")
        print()

    if not quarantine_files:
        print("No files to quarantine.")
        return

    print(f"{'[DRY RUN] ' if args.dry_run else ''}"
          f"Found {len(quarantine_files)} files to quarantine:\n")

    for f, reason in quarantine_files:
        print(f"  {f.name:45s} | {reason}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would move {len(quarantine_files)} files to _TRASH/")
        return

    response = input(f"\nMove {len(quarantine_files)} files to _TRASH/? [y/N]: ").strip().lower()
    if response != "y":
        print("Aborted.")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    trash_dir = MEDIA_ROOT / "_TRASH" / f"wholeness_{ts}"
    trash_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for f, reason in quarantine_files:
        try:
            dest = trash_dir / f.name
            # Handle name conflicts
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                i = 1
                while dest.exists():
                    dest = trash_dir / f"{stem}_{i}{suffix}"
                    i += 1
            shutil.move(str(f), str(dest))
            moved += 1
        except Exception as e:
            print(f"  ERROR: {f}: {e}")

    print(f"\nMoved {moved}/{len(quarantine_files)} files to {trash_dir}")


if __name__ == "__main__":
    main()
