#!/usr/bin/env python3
"""
strip_prefixes.py — Remove track-number prefixes from filenames.

Scans the library for files whose names start with patterns like:
  "01 Track.m4a", "1-04 Track.m4a", "7-04 Track.m4a", "02.Track.m4a"

Strips the prefix, renames the file. Does NOT touch metadata.

Usage:
  python3 main/strip_prefixes.py --dry-run
  python3 main/strip_prefixes.py
"""

import argparse
import re
from pathlib import Path
from typing import List, Tuple
import os

LIB_ROOT = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))
AUDIO_EXTS = {".m4a", ".mp3", ".flac"}
SKIP_DIRS = {"_DUPES", "_NEEDS_REVIEW", "_WRONG_AUDIO", "_TRASH",
             "_STAGING_DOWNLOADS", "Automatically Add to Music.localized"}

# Matches: "01 ", "1-04 ", "7-04 ", "02.", "2-01 ", etc.
PREFIX_RE = re.compile(r"^\d{1,2}[-.]?\d{0,2}\s+")


def find_prefixed_files(lib_root: Path) -> List[Tuple[Path, str]]:
    """Find all audio files with track-number prefixes in their names."""
    results = []
    for f in lib_root.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() not in AUDIO_EXTS:
            continue
        if f.name.startswith("._"):
            continue
        if any(s in f.parts for s in SKIP_DIRS):
            continue

        stem = f.stem
        match = PREFIX_RE.match(stem)
        if match:
            new_stem = PREFIX_RE.sub("", stem).strip()
            if new_stem:  # don't strip if it would leave empty
                results.append((f, new_stem + f.suffix))

    return results


def main():
    parser = argparse.ArgumentParser(description="Strip track-number prefixes from filenames")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no renames")
    parser.add_argument("--lib-root", type=Path, default=LIB_ROOT,
                        help="Library root directory")
    args = parser.parse_args()

    print(f"Scanning {args.lib_root} for prefixed filenames...\n")
    prefixed = find_prefixed_files(args.lib_root)

    if not prefixed:
        print("No prefixed filenames found.")
        return

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Found {len(prefixed)} files with prefixes:\n")

    # Group by album folder for readability
    by_folder = {}
    for f, new_name in prefixed:
        folder = f.parent
        if folder not in by_folder:
            by_folder[folder] = []
        by_folder[folder].append((f, new_name))

    for folder in sorted(by_folder.keys()):
        rel_folder = folder.relative_to(args.lib_root)
        items = by_folder[folder]
        print(f"  {rel_folder}/ ({len(items)} files)")
        for f, new_name in items[:3]:
            print(f"    {f.name} -> {new_name}")
        if len(items) > 3:
            print(f"    ... and {len(items) - 3} more")

    print(f"\n  Total: {len(prefixed)} renames across {len(by_folder)} albums")

    if args.dry_run:
        print(f"\n[DRY RUN] No files renamed.")
        return

    response = input(f"\nRename {len(prefixed)} files? [y/N]: ").strip().lower()
    if response != "y":
        print("Aborted.")
        return

    renamed = 0
    conflicts = 0
    for f, new_name in prefixed:
        new_path = f.parent / new_name
        if new_path.exists() and new_path != f:
            print(f"  CONFLICT: {new_name} already exists in {f.parent.name}/")
            conflicts += 1
            continue
        try:
            f.rename(new_path)
            renamed += 1
        except Exception as e:
            print(f"  ERROR: {f.name} -> {new_name}: {e}")

    print(f"\nRenamed {renamed}/{len(prefixed)} files ({conflicts} conflicts skipped)")


if __name__ == "__main__":
    main()
