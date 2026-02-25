"""
fix_track_numbers.py — write trkn (track number/total) metadata from filenames.

PHASE 1 (offline, --skip-mb to stop here):
  Parses leading digits from filename: "01 Track Name.m4a" → trkn=(1, 0)
  Skips files that already have trkn[0] > 0.
  Writes total as 0 when unknown.

PHASE 2 (MusicBrainz, requires internet):
  For albums with any unresolved tracks, queries MusicBrainz for canonical
  tracklist. For files with trkn[0] > 0: fills in track total. For files
  with trkn[0] == 0: fuzzy-matches filename title against MB tracklist
  (similarity >= 0.80) and assigns track number + total.

Usage:
    python3 fix_track_numbers.py --root ~/Music/foriPod/Media.localized/Music/Music [--dry-run] [--skip-mb]
"""

import argparse
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import mutagen
from mutagen.mp4 import MP4, MP4Tags
from mutagen.mp3 import MP3
from mutagen.id3 import TRCK

from mb_client import lookup_album, normalize as normalize_title

AUDIO_EXTS = {".m4a", ".mp4", ".mp3"}
TRACK_RE = re.compile(r"^(\d+)")


def parse_num_from_filename(stem: str) -> int | None:
    """Extract leading track number from filename stem. Returns None if not found."""
    m = TRACK_RE.match(stem)
    return int(m.group(1)) if m else None



def match_by_title(stem: str, mb_tracks: list) -> dict | None:
    """
    Fuzzy-match a filename stem against MusicBrainz track titles.
    Strips leading track number prefix before matching.
    Returns the best-matching MB track dict if similarity >= 0.80, else None.
    """
    clean = re.sub(r"^\d+[\s.\-]+", "", stem).strip()
    norm = normalize_title(clean)
    if not norm:
        return None
    best, best_score = None, 0.0
    for track in mb_tracks:
        score = SequenceMatcher(None, norm, normalize_title(track["title"])).ratio()
        if score > best_score:
            best_score = score
            best = track
    return best if best_score >= 0.80 else None


def get_trkn_m4a(tags: MP4Tags | None) -> tuple[int, int]:
    if not tags:
        return (0, 0)
    trkn = tags.get("trkn")
    if not trkn:
        return (0, 0)
    val = trkn[0]
    num = val[0] if len(val) > 0 else 0
    total = val[1] if len(val) > 1 else 0
    return (num, total)


def set_trkn_m4a(path: Path, num: int, total: int, dry_run: bool) -> str:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    audio.tags["trkn"] = [(num, total)]
    if not dry_run:
        audio.save()
    return f"trkn=({num},{total})"


def get_trkn_mp3(audio: MP3) -> tuple[int, int]:
    trck_frame = audio.tags.get("TRCK") if audio.tags else None
    trck = str(trck_frame.text[0]) if trck_frame else ""
    if not trck:
        return (0, 0)
    parts = trck.split("/")
    try:
        num = int(parts[0])
        total = int(parts[1]) if len(parts) > 1 else 0
        return (num, total)
    except ValueError:
        return (0, 0)


def set_trkn_mp3(path: Path, num: int, total: int, dry_run: bool) -> str:
    audio = MP3(path)
    if audio.tags is None:
        audio.add_tags()
    audio.tags.add(TRCK(encoding=3, text=f"{num}/{total}" if total else str(num)))
    if not dry_run:
        audio.save()
    return f"trkn=({num},{total})"


def collect_albums(root: Path) -> dict:
    """
    Returns dict: {(artist, album): [Path, ...]}
    Walks root/Artist/Album/*.{m4a,mp3}
    """
    albums = defaultdict(list)
    for artist_dir in sorted(root.iterdir()):
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        for album_dir in sorted(artist_dir.iterdir()):
            if not album_dir.is_dir() or album_dir.name.startswith("."):
                continue
            for track in sorted(album_dir.iterdir()):
                if track.suffix.lower() not in AUDIO_EXTS:
                    continue
                if track.name.startswith("._"):
                    continue
                albums[(artist_dir.name, album_dir.name)].append(track)
    return albums


def phase1(albums: dict, dry_run: bool) -> dict:
    """
    Write trkn from filenames. Returns {(artist, album): needs_mb_total: bool}
    """
    fixed = skipped = errors = 0
    needs_total: dict[tuple, bool] = {}

    for (artist, album), tracks in albums.items():
        album_needs_total = False
        for path in tracks:
            try:
                if path.suffix.lower() in {".m4a", ".mp4"}:
                    audio = MP4(path)
                    num, total = get_trkn_m4a(audio.tags)
                else:
                    audio = MP3(path)
                    num, total = get_trkn_mp3(audio)

                if num > 0:
                    skipped += 1
                    if total == 0:
                        album_needs_total = True
                    continue

                parsed = parse_num_from_filename(path.stem)
                if parsed is None:
                    print(f"  SKIP (no number in filename): {path.relative_to(path.parent.parent.parent)}")
                    skipped += 1
                    album_needs_total = True  # Phase 2 will attempt title matching
                    continue

                if path.suffix.lower() in {".m4a", ".mp4"}:
                    result = set_trkn_m4a(path, parsed, 0, dry_run)
                else:
                    result = set_trkn_mp3(path, parsed, 0, dry_run)

                verb = "WOULD SET" if dry_run else "SET"
                print(f"  {verb} {result}: {path.relative_to(path.parent.parent.parent)}")
                fixed += 1
                album_needs_total = True

            except Exception as e:
                print(f"  ERROR {path.name}: {e}")
                errors += 1

        needs_total[(artist, album)] = album_needs_total

    verb = "Would fix" if dry_run else "Fixed"
    print(f"\nPhase 1 done. {verb}: {fixed}  Skipped: {skipped}  Errors: {errors}")
    return needs_total


def phase2(albums: dict, needs_total: dict, dry_run: bool) -> None:
    """
    For each album needing work, query MusicBrainz and:
      - Files with trkn[0] > 0 but total == 0: fill in total only
      - Files with trkn[0] == 0: fuzzy-match title against MB tracklist to assign num + total
    """
    targets = [(a, alb) for (a, alb), needed in needs_total.items() if needed]
    print(f"\nPhase 2: querying MusicBrainz for {len(targets)} albums...")

    total_updated = total_skipped = total_no_match = total_unmatched = 0

    for artist, album in targets:
        result = lookup_album(artist, album)
        if not result:
            print(f"  NO MATCH: {artist} / {album}")
            total_no_match += 1
            continue

        mb_total = result["count"]
        mb_tracks = result["tracks"]
        matched_files = 0
        total_filled = 0
        unmatched_files = 0

        for path in albums[(artist, album)]:
            try:
                if path.suffix.lower() in {".m4a", ".mp4"}:
                    audio = MP4(path)
                    num, existing_total = get_trkn_m4a(audio.tags)
                else:
                    audio = MP3(path)
                    num, existing_total = get_trkn_mp3(audio)

                if num > 0:
                    # Already has track number — just update total if needed
                    if existing_total == mb_total:
                        total_skipped += 1
                        continue
                    verb = "WOULD SET" if dry_run else "SET"
                    print(f"    {verb} trkn=({num},{mb_total}) total fill: {path.name}")
                    if path.suffix.lower() in {".m4a", ".mp4"}:
                        set_trkn_m4a(path, num, mb_total, dry_run)
                    else:
                        set_trkn_mp3(path, num, mb_total, dry_run)
                    total_updated += 1
                    total_filled += 1
                else:
                    # No track number — try title matching
                    mb_track = match_by_title(path.stem, mb_tracks)
                    if mb_track is None:
                        print(f"    UNMATCHED: {path.name}")
                        unmatched_files += 1
                        total_unmatched += 1
                        continue
                    assigned_num = mb_track["num"]
                    if assigned_num == 0:
                        print(f"    UNMATCHED (num=0): {path.name}")
                        unmatched_files += 1
                        total_unmatched += 1
                        continue
                    verb = "WOULD SET" if dry_run else "SET"
                    print(f"    {verb} trkn=({assigned_num},{mb_total}) via title match: {path.name}")
                    if path.suffix.lower() in {".m4a", ".mp4"}:
                        set_trkn_m4a(path, assigned_num, mb_total, dry_run)
                    else:
                        set_trkn_mp3(path, assigned_num, mb_total, dry_run)
                    matched_files += 1
                    total_updated += 1

            except Exception as e:
                print(f"    ERROR {path.name}: {e}")

        parts = []
        if matched_files:
            parts.append(f"{matched_files} title-matched")
        if total_filled:
            parts.append(f"{total_filled} total-filled")
        if unmatched_files:
            parts.append(f"{unmatched_files} unmatched")
        if not parts:
            parts.append("no changes")
        print(f"  [{artist} / {album}] MB={mb_total} tracks — {', '.join(parts)}")

    verb = "Would update" if dry_run else "Updated"
    print(f"\nPhase 2 done.")
    print(f"  {verb}: {total_updated}")
    print(f"  Already correct: {total_skipped}")
    print(f"  No MB match: {total_no_match}")
    print(f"  Unmatched files (title match failed): {total_unmatched}")


def main():
    parser = argparse.ArgumentParser(description="Fix trkn metadata from filenames + MusicBrainz.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-mb", action="store_true", help="Run Phase 1 only (offline)")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.exists():
        print(f"Error: {root} not found")
        return

    print(f"Scanning {root} ...")
    albums = collect_albums(root)
    print(f"Found {len(albums)} albums, {sum(len(v) for v in albums.values())} tracks\n")

    print("=== Phase 1: filename-based trkn ===")
    needs_total = phase1(albums, args.dry_run)

    if not args.skip_mb:
        print("\n=== Phase 2: MusicBrainz total fill-in ===")
        phase2(albums, needs_total, args.dry_run)
    else:
        print("\n--skip-mb set, skipping Phase 2.")


if __name__ == "__main__":
    main()
