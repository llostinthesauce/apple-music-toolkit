#!/usr/bin/env python3
"""
wholeness_tidal.py — Check album wholeness against Tidal tracklists via triton API.

For albums that are PARTIAL or have EXTRA_TRACKS in the cloud diff report,
verify against Tidal's canonical tracklist what should be there.

Supersedes: check_wholeness.py (MusicBrainz-based)

Usage:
  # Check specific album:
  python3 main/wholeness_tidal.py --artist "J Dilla" --album "Donuts" \\
    --lib-root ~/Music

  # Check all problem albums from diff report:
  python3 main/wholeness_tidal.py --report output/cloud_diff_report.json \\
    --status PARTIAL EXTRA_TRACKS
"""

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mutagen
import requests
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
import os

# ── Constants ────────────────────────────────────────────────────────────────

API_BASE = "https://triton.squid.wtf"
REQUEST_DELAY = 1.0
AUDIO_EXTS = {".m4a", ".mp3", ".flac"}
LIB_ROOT = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))

# Albums with known wrong audio — always re-download all tracks
KNOWN_BAD_ALBUMS = [
    ("Aphex Twin", "Selected Ambient Works 85-92"),
    ("toe", "The Book About My Idle Plot On A Vague Anxiety"),
    ("The Hellp", "LL"),
    ("Childish Gambino", "Camp"),
    ("Childish Gambino", "3.15.20"),
    ("Kendrick Lamar", "DAMN."),
    ("Bladee", "Working on Dying"),
    ("Bladee", "Cold Visions"),
    ("Duster", "Stratosphere"),
    ("Alex G", "Trick"),
    ("Skepta", "Konnichiwa"),
    ("N.E.R.D", "In Search Of"),
    ("Gorillaz", "Electrospective"),
    ("black midi", "Cavalcovers"),
]


def norm(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def strip_feat(name: str) -> str:
    name = re.sub(
        r"\s*[\(\[](feat\.?|ft\.?|featuring)\s+[^\)\]]*[\)\]]",
        "", name, flags=re.IGNORECASE
    )
    name = re.sub(
        r"\s+(feat\.?|ft\.?|featuring)\s+.*$",
        "", name, flags=re.IGNORECASE
    )
    return name


def strip_suffix(name: str) -> str:
    return re.sub(
        r"\s*[\(\[](live|acoustic|remaster(ed)?|"
        r"\d{4}\s+remaster(ed)?|bonus track|deluxe)[^\)\]]*[\)\]]",
        "", name, flags=re.IGNORECASE
    )


def fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ── Triton API ───────────────────────────────────────────────────────────────

def api_get(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    url = f"{API_BASE}{path}"
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 429:
            print("  [rate limited] sleeping 10s...")
            time.sleep(10)
            r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [API error] {url} — {e}")
        return None


def search_album(artist: str, album: str) -> Optional[int]:
    """Search for an album on Tidal, return album_id."""
    query = f"{artist} {album}"
    data = api_get("/search/", {"s": query})
    time.sleep(REQUEST_DELAY)
    if not data or "data" not in data:
        return None

    items = data["data"].get("items", [])
    if not items:
        return None

    # Find best album match from track results
    album_ids = {}
    for item in items:
        a = item.get("album", {})
        aid = a.get("id")
        atitle = a.get("title", "")
        if aid and atitle:
            score = fuzzy_ratio(norm(album), norm(atitle))
            if score > album_ids.get(aid, (0, ""))[0]:
                album_ids[aid] = (score, atitle)

    if not album_ids:
        return None

    # Return the best matching album
    best_id = max(album_ids, key=lambda k: album_ids[k][0])
    best_score, best_title = album_ids[best_id]

    if best_score < 0.5:
        print(f"  Low album match: '{album}' -> '{best_title}' (score={best_score:.2f})")
        return None

    return best_id


def get_album_tracks(album_id: int) -> Optional[List[Dict]]:
    """Get full tracklist for a Tidal album."""
    data = api_get("/album/", {"id": album_id})
    time.sleep(REQUEST_DELAY)
    if not data or "data" not in data:
        return None

    album_data = data["data"]
    items = album_data.get("items", [])

    tracks = []
    for entry in items:
        item = entry.get("item", {})
        tracks.append({
            "title": item.get("title", ""),
            "track_num": item.get("trackNumber", 0),
            "volume_num": item.get("volumeNumber", 1),
            "duration": item.get("duration", 0),
            "tidal_id": item.get("id"),
            "artist": item.get("artist", {}).get("name", ""),
        })

    return tracks


def identify_track_album(artist: str, title: str) -> Optional[str]:
    """Search Tidal for a track and return which album it belongs to."""
    query = f"{artist} {title}"
    data = api_get("/search/", {"s": query})
    time.sleep(REQUEST_DELAY)
    if not data or "data" not in data:
        return None

    items = data["data"].get("items", [])
    for item in items:
        result_title = item.get("title", "")
        result_artist = item.get("artist", {}).get("name", "")
        score = fuzzy_ratio(norm(title), norm(result_title))
        if score > 0.7:
            album_title = item.get("album", {}).get("title", "")
            return album_title

    return None


# ── Local file scanning ──────────────────────────────────────────────────────

def scan_album_folder(artist: str, album: str, lib_root: Path) -> List[Dict]:
    """Find local files for an artist/album by scanning filesystem."""
    # Try exact folder match first
    artist_dir = lib_root / artist
    if not artist_dir.exists():
        # Try normalized search
        for d in lib_root.iterdir():
            if d.is_dir() and norm(d.name) == norm(artist):
                artist_dir = d
                break
        else:
            return []

    album_dir = artist_dir / album
    if not album_dir.exists():
        for d in artist_dir.iterdir():
            if d.is_dir() and norm(d.name) == norm(album):
                album_dir = d
                break
        else:
            # Try fuzzy
            for d in artist_dir.iterdir():
                if d.is_dir() and fuzzy_ratio(norm(d.name), norm(album)) > 0.7:
                    album_dir = d
                    break
            else:
                return []

    files = []
    for f in album_dir.iterdir():
        if not f.is_file() or f.suffix.lower() not in AUDIO_EXTS or f.name.startswith("._"):
            continue
        try:
            audio = mutagen.File(str(f), easy=False)
            if audio is None:
                continue
            if isinstance(audio, MP4):
                tags = audio.tags or {}
                title = str(tags.get("\xa9nam", [""])[0])
                trkn = tags.get("trkn", [(0, 0)])
                track_num = trkn[0][0] if trkn else 0
            elif isinstance(audio, MP3):
                tags = audio.tags or {}
                title = str(tags.get("TIT2", ""))
                trck = str(tags.get("TRCK", "0"))
                track_num = int(trck.split("/")[0]) if trck else 0
            else:
                continue

            files.append({
                "title": title,
                "track_num": track_num,
                "path": str(f),
                "filename": f.name,
            })
        except Exception:
            continue

    return files


# ── Matching ─────────────────────────────────────────────────────────────────

def match_against_tidal(
    tidal_tracks: List[Dict],
    local_files: List[Dict],
) -> List[Dict]:
    """Match local files against Tidal tracklist, produce action items."""
    actions = []
    matched_local = set()

    for tt in tidal_tracks:
        tidal_norm = norm(tt["title"])
        tidal_stripped = norm(strip_feat(strip_suffix(tt["title"])))
        found = False

        for i, lf in enumerate(local_files):
            if i in matched_local:
                continue
            local_norm_name = norm(lf["title"])
            local_stripped = norm(strip_feat(strip_suffix(lf["title"])))

            # Match cascade
            if (tidal_norm == local_norm_name
                    or tidal_stripped == local_stripped
                    or fuzzy_ratio(tidal_stripped, local_stripped) > 0.85):
                actions.append({
                    "action": "KEEP",
                    "tidal_title": tt["title"],
                    "tidal_track_num": tt["track_num"],
                    "local_title": lf["title"],
                    "local_path": lf["path"],
                })
                matched_local.add(i)
                found = True
                break

        if not found:
            actions.append({
                "action": "DOWNLOAD",
                "tidal_title": tt["title"],
                "tidal_track_num": tt["track_num"],
                "tidal_id": tt.get("tidal_id"),
                "duration": tt.get("duration", 0),
            })

    # Check for extra local files not in Tidal
    for i, lf in enumerate(local_files):
        if i not in matched_local:
            actions.append({
                "action": "QUARANTINE",
                "local_title": lf["title"],
                "local_path": lf["path"],
                "reason": "Not in Tidal tracklist",
            })

    return actions


# ── Main ─────────────────────────────────────────────────────────────────────

def check_album(artist: str, album: str, lib_root: Path) -> Optional[Dict]:
    """Check a single album's wholeness against Tidal."""
    print(f"\n  Checking: {artist} — {album}")

    # Check if known bad
    is_known_bad = any(
        norm(a) == norm(artist) and norm(b) == norm(album)
        for a, b in KNOWN_BAD_ALBUMS
    )

    # Search Tidal
    album_id = search_album(artist, album)
    if album_id is None:
        print(f"    Not found on Tidal")
        return {"artist": artist, "album": album, "status": "NOT_FOUND"}

    # Get Tidal tracklist
    tidal_tracks = get_album_tracks(album_id)
    if not tidal_tracks:
        print(f"    Could not fetch tracklist")
        return {"artist": artist, "album": album, "status": "API_ERROR"}

    # Get local files
    local_files = scan_album_folder(artist, album, lib_root)

    # If known bad, mark all local files for quarantine and all tidal tracks for download
    if is_known_bad:
        actions = []
        for tt in tidal_tracks:
            actions.append({
                "action": "DOWNLOAD",
                "tidal_title": tt["title"],
                "tidal_track_num": tt["track_num"],
                "tidal_id": tt.get("tidal_id"),
                "duration": tt.get("duration", 0),
            })
        for lf in local_files:
            actions.append({
                "action": "QUARANTINE",
                "local_title": lf["title"],
                "local_path": lf["path"],
                "reason": "Known wrong-audio album",
            })
        print(f"    KNOWN BAD: {len(tidal_tracks)} to download, {len(local_files)} to quarantine")
    else:
        actions = match_against_tidal(tidal_tracks, local_files)

    keep = sum(1 for a in actions if a["action"] == "KEEP")
    download = sum(1 for a in actions if a["action"] == "DOWNLOAD")
    quarantine = sum(1 for a in actions if a["action"] == "QUARANTINE")

    print(f"    Tidal: {len(tidal_tracks)} tracks | Local: {len(local_files)} files")
    print(f"    KEEP: {keep} | DOWNLOAD: {download} | QUARANTINE: {quarantine}")

    return {
        "artist": artist,
        "album": album,
        "tidal_album_id": album_id,
        "tidal_track_count": len(tidal_tracks),
        "local_file_count": len(local_files),
        "status": "CHECKED",
        "actions": actions,
    }


def main():
    parser = argparse.ArgumentParser(description="Check album wholeness against Tidal")
    parser.add_argument("--artist", help="Artist name")
    parser.add_argument("--album", help="Album name")
    parser.add_argument("--report", type=Path, help="Cloud diff report JSON")
    parser.add_argument("--status", nargs="+", default=["PARTIAL", "EXTRA_TRACKS"],
                        help="Album statuses to check (default: PARTIAL EXTRA_TRACKS)")
    parser.add_argument("--lib-root", type=Path, default=LIB_ROOT,
                        help="Library root directory")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max albums to check (0 = all)")
    parser.add_argument("--min-local", type=int, default=0,
                        help="Only check albums with at least N local files")
    parser.add_argument("--min-extra", type=int, default=0,
                        help="Only check albums with at least N extra tracks")
    parser.add_argument("--output-dir", default="output",
                        help="Directory for report files")
    args = parser.parse_args()

    output_dir = Path(__file__).parent.parent / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    albums_to_check = []

    if args.artist and args.album:
        albums_to_check.append({"artist": args.artist, "album": args.album})
    elif args.report:
        with open(args.report) as f:
            diff = json.load(f)
        for album in diff.get("albums", []):
            if album["status"] not in args.status:
                continue
            local_count = album.get("local_count", 0)
            extra_count = len(album.get("extra", []))
            if args.min_local and local_count < args.min_local:
                if args.min_extra and extra_count < args.min_extra:
                    continue
                elif not args.min_extra:
                    continue
            albums_to_check.append({
                "artist": album["artist"],
                "album": album["album"],
            })
        print(f"Loaded {len(albums_to_check)} albums from diff report "
              f"(status: {', '.join(args.status)})")
    else:
        print("Error: provide --artist/--album or --report", file=__import__("sys").stderr)
        return

    if args.limit:
        albums_to_check = albums_to_check[:args.limit]
        print(f"Limited to first {args.limit} albums")

    results = []
    for i, album_info in enumerate(albums_to_check, 1):
        print(f"\n[{i}/{len(albums_to_check)}]", end="")
        result = check_album(album_info["artist"], album_info["album"], args.lib_root)
        if result:
            results.append(result)

    # Summary
    checked = sum(1 for r in results if r["status"] == "CHECKED")
    not_found = sum(1 for r in results if r["status"] == "NOT_FOUND")
    total_download = sum(
        sum(1 for a in r.get("actions", []) if a["action"] == "DOWNLOAD")
        for r in results
    )
    total_quarantine = sum(
        sum(1 for a in r.get("actions", []) if a["action"] == "QUARANTINE")
        for r in results
    )
    total_keep = sum(
        sum(1 for a in r.get("actions", []) if a["action"] == "KEEP")
        for r in results
    )

    print("\n" + "=" * 64)
    print("  WHOLENESS CHECK SUMMARY")
    print("=" * 64)
    print(f"  Albums checked:   {checked}")
    print(f"  Not on Tidal:     {not_found}")
    print(f"  Tracks to KEEP:   {total_keep}")
    print(f"  Tracks to DL:     {total_download}")
    print(f"  Tracks to TRASH:  {total_quarantine}")

    # Save report
    report = {
        "generated_at": datetime.now().isoformat(),
        "albums_checked": len(results),
        "summary": {
            "checked": checked,
            "not_found": not_found,
            "total_keep": total_keep,
            "total_download": total_download,
            "total_quarantine": total_quarantine,
        },
        "albums": results,
    }

    json_path = output_dir / "wholeness_tidal_report.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n  Report saved to: {json_path}")


if __name__ == "__main__":
    main()
