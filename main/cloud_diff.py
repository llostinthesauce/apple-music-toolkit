#!/usr/bin/env python3
"""
cloud_diff.py — Smart cloud-local library diff with fuzzy matching.

Parses the cloud Apple Music XML for the canonical track/album list,
scans the local filesystem via mutagen for actual files, and matches
using a cascading normalization strategy.

Supersedes: reconcile_libraries.py (naive matching)

Usage:
  python3 main/cloud_diff.py \\
    --cloud ~/Desktop/Library.xml \\
    --lib-root ~/Music
"""

import argparse
import json
import plistlib
import re
import sys
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import mutagen
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3

# ── Constants ────────────────────────────────────────────────────────────────

AUDIO_EXTS = {".m4a", ".mp3", ".flac"}
MIN_ALBUM_TRACKS = 4  # Only flag "MISSING" for albums with 4+ cloud tracks


# ── Normalization ────────────────────────────────────────────────────────────

def norm(s: str) -> str:
    """Normalize string for comparison: lowercase, strip non-alphanumeric."""
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def strip_track_prefix(name: str) -> str:
    """Strip leading track/disc-number prefixes.

    Handles: '01 ', '1-04 ', '7-04 ', '02.', '2-01 ', etc.
    """
    return re.sub(r"^\d{1,2}[-.]?\d{0,2}\s+", "", name)


def strip_feat(name: str) -> str:
    """Strip feat./featuring suffixes from track name."""
    # Remove (feat. X), [feat. X], (Featuring X), etc.
    name = re.sub(
        r"\s*[\(\[](feat\.?|ft\.?|featuring)\s+[^\)\]]*[\)\]]",
        "", name, flags=re.IGNORECASE
    )
    # Remove trailing " feat. X" without parens
    name = re.sub(
        r"\s+(feat\.?|ft\.?|featuring)\s+.*$",
        "", name, flags=re.IGNORECASE
    )
    return name


def strip_suffix(name: str) -> str:
    """Strip common suffixes like (Live), (Remastered), (Acoustic Live), etc."""
    return re.sub(
        r"\s*[\(\[](live|acoustic live|acoustic|remaster(ed)?|"
        r"\d{4}\s+remaster(ed)?|bonus track|deluxe|radio edit|"
        r"album version|single version|re-?issue)[^\)\]]*[\)\]]",
        "", name, flags=re.IGNORECASE
    )


def fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ── Metadata reading ─────────────────────────────────────────────────────────

def read_local_metadata(path: Path) -> Optional[Dict[str, Any]]:
    """Read metadata from a local audio file via mutagen."""
    try:
        audio = mutagen.File(str(path), easy=False)
        if audio is None:
            return None

        info = {
            "path": str(path),
            "title": "",
            "artist": "",
            "album_artist": "",
            "album": "",
            "track_num": 0,
        }

        if isinstance(audio, MP4):
            tags = audio.tags or {}
            info["title"] = str(tags.get("\xa9nam", [""])[0])
            info["artist"] = str(tags.get("\xa9ART", [""])[0])
            info["album_artist"] = str(tags.get("aART", [""])[0])
            info["album"] = str(tags.get("\xa9alb", [""])[0])
            trkn = tags.get("trkn", [(0, 0)])
            info["track_num"] = trkn[0][0] if trkn else 0
        elif isinstance(audio, MP3):
            tags = audio.tags
            if tags:
                info["title"] = str(tags.get("TIT2", ""))
                info["artist"] = str(tags.get("TPE1", ""))
                info["album_artist"] = str(tags.get("TPE2", ""))
                info["album"] = str(tags.get("TALB", ""))
                trck = str(tags.get("TRCK", "0"))
                info["track_num"] = int(trck.split("/")[0]) if trck else 0

        return info
    except Exception:
        return None


# ── Cloud XML parsing ────────────────────────────────────────────────────────

def parse_cloud_xml(xml_path: Path) -> Dict[str, List[Dict]]:
    """Parse cloud XML, group tracks by (norm_artist, norm_album)."""
    with open(xml_path, "rb") as f:
        data = plistlib.load(f)

    tracks = data.get("Tracks", {})
    albums = defaultdict(list)

    for tid, t in tracks.items():
        artist = t.get("Album Artist") or t.get("Artist") or "Unknown"
        album = t.get("Album") or "Unknown"
        name = t.get("Name") or "Unknown"
        track_num = t.get("Track Number", 0)

        key = (norm(artist), norm(album))
        albums[key].append({
            "name": name,
            "artist": artist,
            "album": album,
            "track_num": track_num,
            "norm_name": norm(name),
        })

    return dict(albums)


# ── Local filesystem scan ────────────────────────────────────────────────────

def scan_local_library(lib_root: Path) -> Dict[str, List[Dict]]:
    """Scan local library filesystem, group by (norm_artist, norm_album)."""
    albums = defaultdict(list)
    staging_dirs = {"_DUPES", "_NEEDS_REVIEW", "_WRONG_AUDIO", "_TRASH",
                    "_STAGING_DOWNLOADS", "Automatically Add to Music.localized"}

    count = 0
    for f in lib_root.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix.lower() not in AUDIO_EXTS:
            continue
        if f.name.startswith("._"):
            continue
        # Skip staging/special directories
        if any(s in f.parts for s in staging_dirs):
            continue

        meta = read_local_metadata(f)
        if not meta or not meta["title"]:
            continue

        artist = meta["album_artist"] or meta["artist"] or "Unknown"
        album = meta["album"] or "Unknown"

        key = (norm(artist), norm(album))
        albums[key].append({
            "name": meta["title"],
            "artist": artist,
            "album": album,
            "track_num": meta["track_num"],
            "path": meta["path"],
            "norm_name": norm(meta["title"]),
            "filename": f.name,
        })
        count += 1
        if count % 500 == 0:
            print(f"  Scanned {count} files...")

    print(f"  Total: {count} audio files scanned")
    return dict(albums)


# ── Matching engine ──────────────────────────────────────────────────────────

def match_tracks(
    cloud_tracks: List[Dict],
    local_tracks: List[Dict],
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Match cloud tracks to local tracks using cascading normalization.

    Returns: (matched, missing_from_local, extra_in_local)
    """
    matched = []
    unmatched_cloud = list(range(len(cloud_tracks)))
    unmatched_local = list(range(len(local_tracks)))

    # Level 0: Exact normalized name match
    _match_level(cloud_tracks, local_tracks, unmatched_cloud, unmatched_local,
                 matched, level=0,
                 cloud_norm=lambda t: t["norm_name"],
                 local_norm=lambda t: t["norm_name"])

    # Level 1: Strip track-number prefixes from local name
    _match_level(cloud_tracks, local_tracks, unmatched_cloud, unmatched_local,
                 matched, level=1,
                 cloud_norm=lambda t: t["norm_name"],
                 local_norm=lambda t: norm(strip_track_prefix(t["name"])))

    # Level 2: Strip feat. from both
    _match_level(cloud_tracks, local_tracks, unmatched_cloud, unmatched_local,
                 matched, level=2,
                 cloud_norm=lambda t: norm(strip_feat(t["name"])),
                 local_norm=lambda t: norm(strip_feat(t["name"])))

    # Level 3: Strip suffixes (Live, Remastered, etc.) from both
    _match_level(cloud_tracks, local_tracks, unmatched_cloud, unmatched_local,
                 matched, level=3,
                 cloud_norm=lambda t: norm(strip_suffix(strip_feat(t["name"]))),
                 local_norm=lambda t: norm(strip_suffix(strip_feat(t["name"]))))

    # Level 4: Fuzzy matching (ratio > 0.85)
    still_unmatched_cloud = []
    for ci in list(unmatched_cloud):
        ct = cloud_tracks[ci]
        cloud_n = norm(strip_suffix(strip_feat(ct["name"])))
        best_score = 0.0
        best_li = None
        for li in unmatched_local:
            lt = local_tracks[li]
            local_n = norm(strip_suffix(strip_feat(lt["name"])))
            score = fuzzy_ratio(cloud_n, local_n)
            if score > best_score:
                best_score = score
                best_li = li
        if best_score >= 0.85 and best_li is not None:
            lt = local_tracks[best_li]
            matched.append({
                "cloud_name": ct["name"],
                "local_name": lt["name"],
                "local_path": lt.get("path", ""),
                "match_level": 4,
                "fuzzy_score": round(best_score, 3),
            })
            unmatched_local.remove(best_li)
        else:
            still_unmatched_cloud.append(ci)
    unmatched_cloud = still_unmatched_cloud

    # Build results
    missing = [
        {"name": cloud_tracks[ci]["name"], "track_num": cloud_tracks[ci].get("track_num", 0)}
        for ci in unmatched_cloud
    ]
    extra = [
        {"name": local_tracks[li]["name"], "local_path": local_tracks[li].get("path", ""),
         "filename": local_tracks[li].get("filename", "")}
        for li in unmatched_local
    ]

    return matched, missing, extra


def _match_level(
    cloud_tracks, local_tracks, unmatched_cloud, unmatched_local,
    matched, level, cloud_norm, local_norm
):
    """Match at a specific normalization level, modifying lists in place."""
    # Build local lookup
    local_by_norm = defaultdict(list)
    for li in unmatched_local:
        n = local_norm(local_tracks[li])
        if n:
            local_by_norm[n].append(li)

    newly_matched_cloud = []
    newly_matched_local = set()

    for ci in unmatched_cloud:
        cn = cloud_norm(cloud_tracks[ci])
        if not cn:
            continue
        candidates = local_by_norm.get(cn, [])
        for li in candidates:
            if li not in newly_matched_local:
                lt = local_tracks[li]
                ct = cloud_tracks[ci]
                matched.append({
                    "cloud_name": ct["name"],
                    "local_name": lt["name"],
                    "local_path": lt.get("path", ""),
                    "match_level": level,
                })
                newly_matched_cloud.append(ci)
                newly_matched_local.add(li)
                break

    for ci in newly_matched_cloud:
        unmatched_cloud.remove(ci)
    for li in newly_matched_local:
        unmatched_local.remove(li)


# ── Album categorization ────────────────────────────────────────────────────

def categorize_album(
    cloud_count: int,
    local_count: int,
    matched: List[Dict],
    missing: List[Dict],
    extra: List[Dict],
) -> str:
    """Determine album status based on matching results."""
    if not missing and not extra:
        return "PERFECT"

    if not missing and extra:
        return "EXTRA_TRACKS"

    # Check if all matches were at level 1 (prefix only)
    match_levels = [m.get("match_level", 0) for m in matched]
    if not missing and all(l <= 1 for l in match_levels) and any(l == 1 for l in match_levels):
        return "PREFIX_ONLY"

    # Check if all resolved by feat. normalization
    if not missing and all(l <= 2 for l in match_levels) and any(l == 2 for l in match_levels):
        return "FEAT_ONLY"

    if local_count == 0 and cloud_count >= MIN_ALBUM_TRACKS:
        return "MISSING"

    return "PARTIAL"


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Smart cloud-local library diff")
    parser.add_argument("--cloud", required=True, type=Path,
                        help="Path to cloud Apple Music XML export")
    parser.add_argument("--lib-root", required=True, type=Path,
                        help="Local library root directory")
    parser.add_argument("--output-dir", default="output",
                        help="Directory for report files (default: output/)")
    parser.add_argument("--min-tracks", type=int, default=MIN_ALBUM_TRACKS,
                        help="Min cloud tracks to flag as MISSING (default: 4)")
    args = parser.parse_args()

    if not args.cloud.exists():
        print(f"Error: cloud XML not found: {args.cloud}", file=sys.stderr)
        sys.exit(1)
    if not args.lib_root.exists():
        print(f"Error: library root not found: {args.lib_root}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(__file__).parent.parent / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Parsing cloud XML: {args.cloud}")
    cloud_albums = parse_cloud_xml(args.cloud)
    print(f"  {len(cloud_albums)} cloud albums")

    print(f"\nScanning local library: {args.lib_root}")
    local_albums = scan_local_library(args.lib_root)
    print(f"  {len(local_albums)} local albums\n")

    # Process each album
    results = []
    summary = defaultdict(int)

    # Process cloud albums
    for key, cloud_tracks in cloud_albums.items():
        local_tracks = local_albums.get(key, [])
        artist = cloud_tracks[0]["artist"]
        album = cloud_tracks[0]["album"]

        matched, missing, extra = match_tracks(cloud_tracks, local_tracks)
        status = categorize_album(
            len(cloud_tracks), len(local_tracks), matched, missing, extra
        )

        # Skip single-track albums for MISSING status
        if status == "MISSING" and len(cloud_tracks) < args.min_tracks:
            continue

        summary[status] += 1

        # Only include non-PERFECT albums in detailed report
        if status != "PERFECT":
            results.append({
                "artist": artist,
                "album": album,
                "status": status,
                "cloud_count": len(cloud_tracks),
                "local_count": len(local_tracks),
                "matched_count": len(matched),
                "matched": matched,
                "missing": missing,
                "extra": extra,
            })
        else:
            summary["PERFECT"] = summary.get("PERFECT", 0)

    # Count PERFECT albums (not in results since we skip them)
    perfect_count = sum(
        1 for key in cloud_albums
        if key in local_albums
        and not match_tracks(cloud_albums[key], local_albums[key])[1]  # no missing
        and not match_tracks(cloud_albums[key], local_albums[key])[2]  # no extra
    )
    summary["PERFECT"] = perfect_count

    # Find local-only albums
    local_only = []
    for key, local_tracks in local_albums.items():
        if key not in cloud_albums:
            artist = local_tracks[0]["artist"]
            album = local_tracks[0]["album"]
            local_only.append({
                "artist": artist,
                "album": album,
                "status": "LOCAL_ONLY",
                "local_count": len(local_tracks),
            })
    summary["LOCAL_ONLY"] = len(local_only)

    # Sort results by severity
    status_order = {"MISSING": 0, "PARTIAL": 1, "EXTRA_TRACKS": 2,
                    "PREFIX_ONLY": 3, "FEAT_ONLY": 4}
    results.sort(key=lambda r: (status_order.get(r["status"], 99),
                                 -len(r.get("missing", []))))

    # Build report
    report = {
        "generated_at": datetime.now().isoformat(),
        "cloud_xml": str(args.cloud),
        "lib_root": str(args.lib_root),
        "summary": dict(summary),
        "total_cloud_albums": len(cloud_albums),
        "total_local_albums": len(local_albums),
        "total_missing_tracks": sum(len(r.get("missing", [])) for r in results),
        "total_extra_tracks": sum(len(r.get("extra", [])) for r in results),
        "albums": results,
        "local_only": local_only,
    }

    # Save JSON
    json_path = output_dir / "cloud_diff_report.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Print summary
    print("=" * 64)
    print("  CLOUD-LOCAL DIFF REPORT")
    print("=" * 64)
    print(f"  Cloud albums:   {len(cloud_albums)}")
    print(f"  Local albums:   {len(local_albums)}")
    print(f"  Local-only:     {len(local_only)} (kept, not in cloud)\n")

    for status in ["PERFECT", "PREFIX_ONLY", "FEAT_ONLY", "PARTIAL",
                    "EXTRA_TRACKS", "MISSING", "LOCAL_ONLY"]:
        count = summary.get(status, 0)
        icon = "+" if status == "PERFECT" else "-" if status in ("MISSING", "PARTIAL") else "~"
        print(f"  {icon} {status:<16} {count:>5}")

    print(f"\n  Total missing tracks: {report['total_missing_tracks']}")
    print(f"  Total extra tracks:   {report['total_extra_tracks']}")

    # Show worst albums
    print("\n" + "-" * 64)
    print("\n  TOP PROBLEM ALBUMS:\n")
    for r in results[:25]:
        miss = len(r.get("missing", []))
        extra = len(r.get("extra", []))
        print(f"  [{r['status']:<14}] {r['artist'][:25]:25s} — {r['album'][:35]:35s} "
              f"cloud:{r['cloud_count']:3d} local:{r['local_count']:3d} "
              f"miss:{miss:3d} extra:{extra:3d}")

    print(f"\n  Report saved to: {json_path}")


if __name__ == "__main__":
    main()
