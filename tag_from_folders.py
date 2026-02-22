"""
tag_from_folders.py — write artist/album/title tags to .m4a files from folder structure.

For a library organized as:
    root/Artist/Album/Track.m4a

Reads the folder path and writes the corresponding embedded tags:
    ©ART  = Artist folder name
    ©alb  = Album folder name
    ©nam  = Track filename stem (strips leading "01 " track numbers)

Works entirely offline — no internet, no API calls, no fingerprinting.
Skips files that already have all three tags set.

Usage:
    python3 tag_from_folders.py --root /mnt/music/foriPod [--dry-run]
"""

import argparse
import re
from pathlib import Path

AUDIO_EXTS = {".m4a", ".mp4"}


def clean_title(stem: str) -> str:
    """Strip leading track numbers like '01 ', '1 ', '01. '"""
    return re.sub(r"^\d+[\s.\-]+", "", stem).strip()


def tag_file(path: Path, artist: str, album: str, dry_run: bool) -> str:
    try:
        from mutagen.mp4 import MP4
        tags = MP4(path)

        existing_artist = tags.tags.get("©ART", [""])[0] if tags.tags else ""
        existing_album  = tags.tags.get("©alb", [""])[0] if tags.tags else ""
        existing_title  = tags.tags.get("©nam", [""])[0] if tags.tags else ""

        if existing_artist and existing_album and existing_title:
            return "SKIP"

        title = clean_title(path.stem)

        if dry_run:
            return f"WOULD TAG → artist={artist!r} album={album!r} title={title!r}"

        if tags.tags is None:
            tags.add_tags()
        if not existing_artist:
            tags["©ART"] = [artist]
        if not existing_album:
            tags["©alb"] = [album]
        if not existing_title:
            tags["©nam"] = [title]
        tags.save()
        return f"TAGGED"

    except Exception as e:
        return f"ERROR: {e}"


def main():
    parser = argparse.ArgumentParser(description="Write tags from folder structure to .m4a files.")
    parser.add_argument("--root", required=True, type=Path, help="Root music folder (Artist/Album/Track.m4a)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes written")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.exists():
        print(f"Error: {root} not found")
        return

    tagged = skipped = errors = 0

    for artist_dir in sorted(root.iterdir()):
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        artist = artist_dir.name

        for album_dir in sorted(artist_dir.iterdir()):
            if not album_dir.is_dir() or album_dir.name.startswith("."):
                continue
            album = album_dir.name

            for track in sorted(album_dir.rglob("*")):
                if track.suffix.lower() not in AUDIO_EXTS:
                    continue
                if track.name.startswith("._"):  # macOS resource fork sidecars
                    continue
                result = tag_file(track, artist, album, args.dry_run)
                rel = track.relative_to(root)
                if result == "SKIP":
                    skipped += 1
                elif result.startswith("ERROR"):
                    print(f"  {result}  {rel}")
                    errors += 1
                else:
                    print(f"  {result}  {rel}")
                    tagged += 1

    verb = "Would tag" if args.dry_run else "Tagged"
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Done.")
    print(f"  {verb}:   {tagged}")
    print(f"  Skipped: {skipped} (already have tags)")
    if errors:
        print(f"  Errors:  {errors}")


if __name__ == "__main__":
    main()
