#!/usr/bin/env python3
"""
import_to_music.py — Import audio files into Apple Music.app via osascript.

Adds files/folders to Music.app's library. Files already in the library
folder are cataloged in-place (no copy). Files outside are copied in.

Usage:
  # Import all new album folders from download log:
  python3 main/import_to_music.py --from-log ~/Downloads/_staging/download_log.json

  # Import a specific folder:
  python3 main/import_to_music.py --folder ~/Music/Artist/Album

  # Dry run:
  python3 main/import_to_music.py --from-log path/to/log.json --dry-run
"""

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import List
import os

MUSIC_ROOT = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))


def safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", s).strip()


def folders_from_log(log_path: Path) -> List[Path]:
    """Get unique album folders from a fill.py download log."""
    with open(log_path) as f:
        log = json.load(f)

    dirs = set()
    for track in log.get("downloaded", []):
        artist = safe_name(track["artist"])
        album = safe_name(track["album"])
        folder = MUSIC_ROOT / artist / album
        if folder.exists():
            dirs.add(folder)

    return sorted(dirs)


def add_to_music(folder: Path) -> bool:
    """Add a folder to Music.app via osascript. Returns True on success."""
    posix = str(folder)
    script = f'tell application "Music" to add POSIX file "{posix}"'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def main():
    parser = argparse.ArgumentParser(description="Import files into Apple Music.app")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-log", type=Path,
                       help="Download log JSON from fill.py")
    group.add_argument("--folder", type=Path,
                       help="Single folder to import")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds between imports (default: 0.5)")
    args = parser.parse_args()

    if args.folder:
        folders = [args.folder.expanduser().resolve()]
    else:
        folders = folders_from_log(args.from_log)

    if not folders:
        print("No folders to import.")
        return

    file_count = sum(
        len([f for f in d.iterdir() if f.suffix.lower() in {".m4a", ".mp3", ".flac"}])
        for d in folders
    )
    print(f"{'[DRY RUN] ' if args.dry_run else ''}"
          f"Importing {file_count} files from {len(folders)} album folders\n")

    ok = 0
    failed = 0
    for i, folder in enumerate(folders, 1):
        n_files = len([f for f in folder.iterdir()
                       if f.suffix.lower() in {".m4a", ".mp3", ".flac"}])
        label = f"[{i}/{len(folders)}]"

        if args.dry_run:
            print(f"  {label} WOULD ADD  {folder.relative_to(MUSIC_ROOT)}  ({n_files} files)")
            ok += 1
            continue

        print(f"  {label} Adding  {folder.relative_to(MUSIC_ROOT)}  ({n_files} files)...",
              end="", flush=True)
        if add_to_music(folder):
            print(" OK")
            ok += 1
        else:
            print(" FAILED")
            failed += 1

        time.sleep(args.delay)

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Done.")
    print(f"  OK: {ok}  |  Failed: {failed}")


if __name__ == "__main__":
    main()
