#!/usr/bin/env python3
"""
strip_meta_prefixes.py — Remove track-number prefixes from metadata title tags.

Many files have titles like "06 Stop" or "01 Sniper Elite" where the track number
leaked into the title field. This strips those prefixes from the actual metadata,
not just the filename.

Usage:
  python3 main/strip_meta_prefixes.py --dry-run
  python3 main/strip_meta_prefixes.py
"""

import argparse
import re
from pathlib import Path
from typing import List, Tuple

import mutagen
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
import os

LIB_ROOT = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))
AUDIO_EXTS = {".m4a", ".mp3"}
SKIP_DIRS = {"_DUPES", "_NEEDS_REVIEW", "_WRONG_AUDIO", "_TRASH",
             "_STAGING_DOWNLOADS", "Automatically Add to Music.localized"}

# Matches: "01 ", "1-04 ", "7-04 ", "02 ", etc. at start of title
PREFIX_RE = re.compile(r"^\d{1,2}[-.]?\d{0,2}\s+")


def find_prefixed_titles(lib_root: Path) -> List[Tuple[Path, str, str]]:
    """Find files with track-number prefixes in their title metadata."""
    results = []
    count = 0

    for f in lib_root.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in AUDIO_EXTS:
            continue
        if f.name.startswith("._"):
            continue
        if any(s in f.parts for s in SKIP_DIRS):
            continue

        try:
            audio = mutagen.File(str(f), easy=False)
            if audio is None:
                continue

            if isinstance(audio, MP4):
                tags = audio.tags or {}
                title = str(tags.get("\xa9nam", [""])[0])
            elif isinstance(audio, MP3):
                tags = audio.tags
                title = str(tags.get("TIT2", "")) if tags else ""
            else:
                continue

            match = PREFIX_RE.match(title)
            if match:
                prefix_str = match.group().strip()
                new_title = PREFIX_RE.sub("", title).strip()
                if not new_title:
                    continue

                # Only strip if the prefix number matches the track number
                # This avoids false positives like "12:51", "40 Days", "25 To Life"
                if isinstance(audio, MP4):
                    trkn = tags.get("trkn", [(0, 0)])
                    track_num = trkn[0][0] if trkn else 0
                elif isinstance(audio, MP3):
                    trck = str(tags.get("TRCK", "0")) if tags else "0"
                    track_num = int(trck.split("/")[0]) if trck else 0
                else:
                    track_num = 0

                # Extract the leading number from the prefix
                prefix_nums = re.findall(r"\d+", prefix_str)
                if not prefix_nums:
                    continue

                # Check: does any number in the prefix match the track number?
                prefix_first = int(prefix_nums[0])
                # Also handle disc-track like "1-04" where second num is track
                prefix_last = int(prefix_nums[-1]) if len(prefix_nums) > 1 else prefix_first

                if track_num > 0 and (prefix_first == track_num or prefix_last == track_num):
                    results.append((f, title, new_title))

        except Exception:
            continue

        count += 1
        if count % 1000 == 0:
            print(f"  Scanned {count} files...")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Strip track-number prefixes from metadata title tags"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--lib-root", type=Path, default=LIB_ROOT)
    args = parser.parse_args()

    print(f"Scanning {args.lib_root} for prefixed titles...\n")
    prefixed = find_prefixed_titles(args.lib_root)

    if not prefixed:
        print("No prefixed titles found.")
        return

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Found {len(prefixed)} files "
          f"with prefixed titles:\n")

    for f, old_title, new_title in prefixed[:30]:
        rel = f.relative_to(args.lib_root)
        print(f"  {str(rel.parent):50s} '{old_title}' -> '{new_title}'")
    if len(prefixed) > 30:
        print(f"  ... and {len(prefixed) - 30} more")

    if args.dry_run:
        print(f"\n[DRY RUN] Would fix {len(prefixed)} titles.")
        return

    response = input(f"\nFix {len(prefixed)} title tags? [y/N]: ").strip().lower()
    if response != "y":
        print("Aborted.")
        return

    fixed = 0
    for f, old_title, new_title in prefixed:
        try:
            audio = mutagen.File(str(f), easy=False)
            if isinstance(audio, MP4):
                audio.tags["\xa9nam"] = [new_title]
            elif isinstance(audio, MP3):
                from mutagen.id3 import TIT2
                audio.tags.add(TIT2(encoding=3, text=[new_title]))
            audio.save()
            fixed += 1
        except Exception as e:
            print(f"  ERROR: {f.name}: {e}")

    print(f"\nFixed {fixed}/{len(prefixed)} titles.")


if __name__ == "__main__":
    main()
