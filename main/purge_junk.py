#!/usr/bin/env python3
"""
purge_junk.py — Move confirmed junk out of the music library.

Actions:
  1. Move all files in staging dirs (_WRONG_AUDIO/, _DUPES/, _NEEDS_REVIEW/) to _TRASH/
  2. Scan main library for known junk patterns:
     - "I <N>.m4a" files where metadata title doesn't match (bad import artifacts)
     - Track numbers referencing a different album size
     - "MFiT master" in title (mastering artifacts)
     - Album metadata tag doesn't match parent folder (wrong-album tracks)
  3. Dry-run by default, confirm before applying

Usage:
  python3 main/purge_junk.py --dry-run
  python3 main/purge_junk.py
"""

import argparse
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import mutagen
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
import os

# ── Constants ────────────────────────────────────────────────────────────────

LIB_ROOT = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))
MEDIA_ROOT = LIB_ROOT.parent  # Media.localized/
STAGING_DIRS = ["_WRONG_AUDIO", "_DUPES", "_NEEDS_REVIEW"]
AUDIO_EXTS = {".m4a", ".mp3", ".flac"}


def normalize(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def get_metadata(path: Path) -> dict:
    """Read basic metadata from an audio file."""
    try:
        audio = mutagen.File(str(path), easy=False)
        if audio is None:
            return {}

        if isinstance(audio, MP4):
            tags = audio.tags or {}
            title = str(tags.get("\xa9nam", [""])[0])
            artist = str(tags.get("\xa9ART", [""])[0])
            album = str(tags.get("\xa9alb", [""])[0])
            trkn = tags.get("trkn", [(0, 0)])
            track_num = trkn[0][0] if trkn else 0
            track_total = trkn[0][1] if trkn and len(trkn[0]) > 1 else 0
            return {
                "title": title, "artist": artist, "album": album,
                "track_num": track_num, "track_total": track_total,
            }
        elif isinstance(audio, MP3):
            tags = audio.tags
            if not tags:
                return {}
            title = str(tags.get("TIT2", ""))
            artist = str(tags.get("TPE1", ""))
            album = str(tags.get("TALB", ""))
            trck = str(tags.get("TRCK", "0"))
            track_num = int(trck.split("/")[0]) if "/" in trck else int(trck or 0)
            return {
                "title": title, "artist": artist, "album": album,
                "track_num": track_num, "track_total": 0,
            }
    except Exception:
        return {}
    return {}


def find_staging_junk() -> List[Tuple[Path, str]]:
    """Find all audio files in staging directories."""
    junk = []
    for dirname in STAGING_DIRS:
        staging_dir = MEDIA_ROOT / dirname
        if not staging_dir.exists():
            continue
        for f in staging_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                junk.append((f, f"In staging dir '{dirname}'"))
    return junk


def find_library_junk() -> List[Tuple[Path, str]]:
    """Scan main library for known junk patterns."""
    junk = []

    # Group files by album folder for sibling analysis
    album_folders = defaultdict(list)
    for f in LIB_ROOT.rglob("*"):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS and not f.name.startswith("._"):
            album_folders[f.parent].append(f)

    for album_dir, files in album_folders.items():
        folder_name = album_dir.name
        sibling_count = len(files)

        for f in files:
            meta = get_metadata(f)
            if not meta:
                continue

            title = meta.get("title", "")
            album_tag = meta.get("album", "")
            track_num = meta.get("track_num", 0)
            track_total = meta.get("track_total", 0)

            # Pattern 1: "I <N>.m4a" where title doesn't start with "I "
            # These are bad import artifacts (e.g., "I 27.m4a" with title "I THINK")
            i_match = re.match(r"^I (\d+)\.m4a$", f.name)
            if i_match:
                # Check if there's already another file with the actual title
                if title:
                    title_filename = f"{title}.m4a"
                    # Check for a sibling with the matching title name
                    siblings_by_title = [
                        s for s in files
                        if s != f and normalize(s.stem) == normalize(title)
                    ]
                    if siblings_by_title:
                        junk.append((f, f"Bad import artifact: '{f.name}' is duplicate of "
                                       f"'{siblings_by_title[0].name}' (both title='{title}')"))
                        continue

            # Pattern 2: Track number total wildly different AND album tag mismatches folder
            # Only flag if BOTH conditions are true — incomplete albums are normal
            if (track_total > 0 and track_total > sibling_count * 3
                    and sibling_count >= 2 and album_tag):
                norm_tag = normalize(album_tag)
                norm_folder = normalize(folder_name)
                if norm_tag and norm_folder and norm_tag != norm_folder:
                    from difflib import SequenceMatcher
                    ratio = SequenceMatcher(None, norm_tag, norm_folder).ratio()
                    if ratio < 0.5:
                        junk.append((f, f"Track {track_num}/{track_total} in folder "
                                       f"with {sibling_count} files AND album mismatch: "
                                       f"tag='{album_tag}' vs folder='{folder_name}' "
                                       f"(title='{title}')"))
                        continue

            # Pattern 3: "MFiT master" in title
            if "mfit master" in title.lower() or "mfit" in title.lower():
                junk.append((f, f"Mastering artifact: title='{title}'"))
                continue

            # Pattern 4: Album tag doesn't match parent folder name
            if album_tag and folder_name:
                norm_tag = normalize(album_tag)
                norm_folder = normalize(folder_name)
                if norm_tag and norm_folder:
                    # Only flag if they're substantially different
                    # Allow for minor differences like "Deluxe Edition" suffix
                    if norm_tag != norm_folder and norm_tag not in norm_folder and norm_folder not in norm_tag:
                        # Check similarity — only flag if really different
                        from difflib import SequenceMatcher
                        ratio = SequenceMatcher(None, norm_tag, norm_folder).ratio()
                        if ratio < 0.6:
                            junk.append((f, f"Album mismatch: tag='{album_tag}' vs "
                                           f"folder='{folder_name}' (similarity={ratio:.2f}, "
                                           f"title='{title}')"))
                            continue

    return junk


def main():
    parser = argparse.ArgumentParser(description="Purge junk from music library")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no moves")
    args = parser.parse_args()

    print("Scanning for junk files...\n")

    staging_junk = find_staging_junk()
    library_junk = find_library_junk()
    all_junk = staging_junk + library_junk

    if not all_junk:
        print("No junk found. Library is clean.")
        return

    # Report
    print(f"{'[DRY RUN] ' if args.dry_run else ''}Found {len(all_junk)} junk files:\n")

    print(f"  Staging folder files: {len(staging_junk)}")
    print(f"  Library junk files:   {len(library_junk)}\n")

    print("=" * 70)
    if staging_junk:
        print("\n[STAGING FOLDERS]")
        for f, reason in staging_junk[:10]:
            rel = f.relative_to(MEDIA_ROOT)
            print(f"  {rel}")
            print(f"    {reason}")
        if len(staging_junk) > 10:
            print(f"  ... and {len(staging_junk) - 10} more staging files\n")

    if library_junk:
        print("\n[LIBRARY JUNK]")
        for f, reason in library_junk:
            rel = f.relative_to(LIB_ROOT)
            print(f"  {rel}")
            print(f"    {reason}")

    print("\n" + "=" * 70)

    if args.dry_run:
        print(f"\n[DRY RUN] Would move {len(all_junk)} files to _TRASH/")
        print("Run without --dry-run to apply.")
        return

    # Confirm
    response = input(f"\nMove {len(all_junk)} files to _TRASH/? [y/N]: ").strip().lower()
    if response != "y":
        print("Aborted.")
        return

    # Move to _TRASH with timestamp
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    trash_dir = MEDIA_ROOT / "_TRASH" / ts
    trash_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    for f, reason in all_junk:
        try:
            # Preserve relative path structure
            if str(f).startswith(str(LIB_ROOT)):
                rel = f.relative_to(LIB_ROOT)
                dest = trash_dir / "library" / rel
            else:
                rel = f.relative_to(MEDIA_ROOT)
                dest = trash_dir / rel

            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dest))
            moved += 1
        except Exception as e:
            print(f"  ERROR moving {f}: {e}")

    print(f"\nMoved {moved}/{len(all_junk)} files to {trash_dir}")

    # Clean up empty directories in staging
    for dirname in STAGING_DIRS:
        staging_dir = MEDIA_ROOT / dirname
        if staging_dir.exists():
            # Remove empty subdirectories
            for d in sorted(staging_dir.rglob("*"), reverse=True):
                if d.is_dir():
                    try:
                        d.rmdir()  # only removes if empty
                    except OSError:
                        pass
            # Remove staging dir itself if empty
            try:
                staging_dir.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    main()
