"""
audit_missing_tracks.py — identify albums with missing tracks.

METHOD C (offline): For every album folder, check if trkn total > 0 but
file count < total. Flags the numeric gap.

METHOD A (MusicBrainz): For every album, query canonical tracklist. Fuzzy-
match canonical track titles against filenames on disk. Reports missing
track titles and numbers (not just counts).

Output:
    output/missing_tracks.csv  — artist, album, track_num, track_title, source
    output/missing_tracks.json — {artist: {album: [{num, title, source}]}}

Usage:
    python3 audit_missing_tracks.py \\
        --root ~/Music/foriPod/Media.localized/Music/Music \\
        --output output/ \\
        [--skip-mb]
"""

import argparse
import csv
import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import mutagen
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3

from mb_client import lookup_album, normalize

AUDIO_EXTS = {".m4a", ".mp4", ".mp3"}


# ---------------------------------------------------------------------------
# Tag reading
# ---------------------------------------------------------------------------

def get_trkn(path: Path) -> tuple[int, int]:
    try:
        audio = mutagen.File(path)
        if isinstance(audio, MP4):
            trkn = (audio.tags or {}).get("trkn", [(0, 0)])[0]
            return (trkn[0], trkn[1] if len(trkn) > 1 else 0)
        elif isinstance(audio, MP3):
            trck_frame = audio.tags.get("TRCK") if audio.tags else None
            trck = str(trck_frame.text[0]) if trck_frame else ""
            if not trck:
                return (0, 0)
            parts = trck.split("/")
            try:
                num = int(parts[0])
                total = int(parts[1]) if len(parts) > 1 and parts[1] else 0
                return (num, total)
            except ValueError:
                return (0, 0)
    except Exception:
        pass
    return (0, 0)


# ---------------------------------------------------------------------------
# Album collection
# ---------------------------------------------------------------------------

def collect_albums(root: Path) -> dict:
    """Returns {(artist, album): [Path, ...]}"""
    albums = defaultdict(list)
    for artist_dir in sorted(root.iterdir()):
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        for album_dir in sorted(artist_dir.iterdir()):
            if not album_dir.is_dir() or album_dir.name.startswith("."):
                continue
            for track in sorted(album_dir.iterdir()):
                if track.suffix.lower() not in AUDIO_EXTS or track.name.startswith("._"):
                    continue
                albums[(artist_dir.name, album_dir.name)].append(track)
    return albums


# ---------------------------------------------------------------------------
# Method C: trkn gap detection
# ---------------------------------------------------------------------------

def method_c(albums: dict) -> list[dict]:
    """
    Flag albums where trkn total > 0 and file count < total.
    Returns list of {artist, album, track_num, track_title, source}.
    """
    missing = []
    for (artist, album), tracks in albums.items():
        trkn_data = [get_trkn(p) for p in tracks]
        totals = [t for _, t in trkn_data if t > 0]
        if not totals:
            continue
        total = max(totals)
        present = {num for num, _ in trkn_data if num > 0}
        # Only report gaps when file count is also less than total (avoids false positives
        # from files with unreadable tags that exist on disk but returned num=0)
        if len(tracks) < total:
            for n in range(1, total + 1):
                if n not in present:
                    missing.append({
                        "artist": artist,
                        "album": album,
                        "track_num": n,
                        "track_title": "",
                        "source": "trkn_gap",
                    })
    return missing


# ---------------------------------------------------------------------------
# Method A: MusicBrainz canonical tracklist
# ---------------------------------------------------------------------------

def strip_track_num(stem: str) -> str:
    """Remove leading track number from filename stem for title matching."""
    return re.sub(r"^\d+[\s.\-]+", "", stem).strip()


def method_a(albums: dict) -> list[dict]:
    """
    Query MusicBrainz for every album. Compare canonical tracklist against
    files on disk by fuzzy title matching. Returns missing tracks with titles.
    """
    missing = []
    total_albums = len(albums)
    for i, ((artist, album), tracks) in enumerate(albums.items(), 1):
        print(f"  [{i}/{total_albums}] {artist} / {album}", end="  ", flush=True)
        result = lookup_album(artist, album)
        if not result:
            print("no MB match")
            continue

        canonical = result["tracks"]
        disk_titles = {normalize(strip_track_num(p.stem)) for p in tracks}

        missing_count = 0
        for t in canonical:
            ct_norm = normalize(t["title"])
            if ct_norm in disk_titles:
                continue
            matched = any(
                SequenceMatcher(None, ct_norm, d).ratio() >= 0.80
                for d in disk_titles
            )
            if not matched:
                missing.append({
                    "artist": artist,
                    "album": album,
                    "track_num": t["num"],
                    "track_title": t["title"],
                    "source": "musicbrainz",
                })
                missing_count += 1

        print(f"{missing_count} missing" if missing_count else "complete")

    return missing


# ---------------------------------------------------------------------------
# Deduplication & output
# ---------------------------------------------------------------------------

def deduplicate(records: list[dict]) -> list[dict]:
    """
    Merge method_c and method_a results. Prefer musicbrainz entries (have titles).
    Deduplicate by (artist, album, track_num).
    """
    by_key: dict[tuple, dict] = {}
    for r in records:
        key = (r["artist"], r["album"], r["track_num"])
        if key not in by_key or r["source"] == "musicbrainz":
            by_key[key] = r
    return sorted(
        by_key.values(),
        key=lambda r: (r["artist"].casefold(), r["album"].casefold(), r["track_num"])
    )


def write_csv(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["artist", "album", "track_num", "track_title", "source"])
        writer.writeheader()
        writer.writerows(records)
    print(f"CSV written: {path} ({len(records)} records)")


def write_json(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree: dict = {}
    for r in records:
        tree.setdefault(r["artist"], {}).setdefault(r["album"], []).append({
            "track_num": r["track_num"],
            "track_title": r["track_title"],
            "source": r["source"],
        })
    path.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON written: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Audit missing tracks in music library.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--skip-mb", action="store_true", help="Run Method C only (offline)")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.exists():
        print(f"Error: {root} not found")
        return

    print(f"Scanning {root} ...")
    albums = collect_albums(root)
    print(f"Found {len(albums)} albums\n")

    print("=== Method C: trkn gap detection ===")
    c_missing = method_c(albums)
    print(f"Method C: {len(c_missing)} missing track slots detected\n")

    if not args.skip_mb:
        print("=== Method A: MusicBrainz canonical tracklist ===")
        a_missing = method_a(albums)
        print(f"\nMethod A: {len(a_missing)} missing tracks detected\n")
        all_missing = deduplicate(c_missing + a_missing)
    else:
        all_missing = deduplicate(c_missing)

    print(f"Total unique missing tracks: {len(all_missing)}")

    out = args.output.expanduser().resolve()
    write_csv(all_missing, out / "missing_tracks.csv")
    write_json(all_missing, out / "missing_tracks.json")

    by_artist: dict[str, int] = defaultdict(int)
    for r in all_missing:
        by_artist[r["artist"]] += 1
    print("\nTop 15 artists with most missing tracks:")
    for artist, count in sorted(by_artist.items(), key=lambda x: x[1], reverse=True)[:15]:
        print(f"  {count:>4}  {artist}")


if __name__ == "__main__":
    main()
