#!/usr/bin/env python3
"""
validate.py — Autonomous Music Library Validation Suite

Checks:
  1. Duplicate tracks (same title+artist, similar duration)
  2. Missing/incomplete metadata (title, artist, album, track number)
  3. Missing embedded artwork
  4. Format compliance (ALAC .m4a preferred; flags AAC .m4a and .mp3)
  5. Orphaned files (in staging dirs or outside Artist/Album structure)
  6. Cloud sync drift (optional, requires --xml export from Apple Music)

Usage:
  python3 main/validate.py
  python3 main/validate.py --root /path/to/library
  python3 main/validate.py --xml ~/Desktop/Library.xml  # enables cloud check
  python3 main/validate.py --check duplicates metadata  # run specific checks
"""

import argparse
import json
import plistlib
import re
import sys
import urllib.parse
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import mutagen
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
import os

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_LIB_ROOT = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))
AUDIO_EXTS = {".m4a", ".mp3"}
STAGING_DIRS = {"_DUPES", "_NEEDS_REVIEW", "_WRONG_AUDIO"}
# Duration tolerance (seconds) for duplicate detection
DUP_DURATION_TOLERANCE = 10
# Required metadata fields
REQUIRED_TAGS_M4A = {"\xa9nam": "title", "\xa9ART": "artist", "\xa9alb": "album"}
REQUIRED_TAGS_MP3 = {"TIT2": "title", "TPE1": "artist", "TALB": "album"}
# Albums to skip for artwork checks
SKIP_ALBUMS = {"No Love Deep Web"}


# ── Tag helpers ────────────────────────────────────────────────────────────────

def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def get_m4a_tag(tags, key: str) -> str:
    val = tags.get(key, [""])
    return str(val[0]) if val else ""


def get_track_info(path: Path) -> dict | None:
    """Extract metadata and codec info from an audio file."""
    try:
        audio = mutagen.File(path, easy=False)
        if audio is None:
            return None

        info = {"path": str(path), "ext": path.suffix.lower(), "duration": 0,
                "title": "", "artist": "", "album": "", "track_num": "",
                "has_artwork": False, "codec": "unknown", "bitrate": 0}

        info["duration"] = int(getattr(audio.info, "length", 0))
        info["bitrate"] = int(getattr(audio.info, "bitrate", 0) / 1000)

        if isinstance(audio, MP4):
            tags = audio.tags or {}
            info["title"] = get_m4a_tag(tags, "\xa9nam")
            info["artist"] = get_m4a_tag(tags, "\xa9ART")
            info["album"] = get_m4a_tag(tags, "\xa9alb")
            raw_track = tags.get("trkn", [(0, 0)])
            info["track_num"] = str(raw_track[0][0]) if raw_track else ""
            info["has_artwork"] = bool(tags.get("covr"))
            codec = getattr(audio.info, "codec", "") or ""
            info["codec"] = "alac" if "alac" in codec.lower() else "aac"

        elif isinstance(audio, MP3):
            tags = audio.tags
            if tags:
                info["title"] = str(tags.get("TIT2", ""))
                info["artist"] = str(tags.get("TPE1", ""))
                info["album"] = str(tags.get("TALB", ""))
                info["track_num"] = str(tags.get("TRCK", ""))
                info["has_artwork"] = any(
                    k.startswith("APIC") for k in tags.keys()
                )
            info["codec"] = "mp3"

        return info
    except Exception as e:
        return {"path": str(path), "ext": path.suffix.lower(), "error": str(e)}


# ── Checks ─────────────────────────────────────────────────────────────────────

def check_duplicates(tracks: list[dict]) -> list[dict]:
    """Group tracks by normalized title+artist; flag groups with close durations."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for t in tracks:
        key = (normalize(t["title"]), normalize(t["artist"]))
        if key[0]:  # skip blank-title tracks
            groups[key].append(t)

    issues = []
    for (title_key, artist_key), group in groups.items():
        if len(group) < 2:
            continue
        durations = [t["duration"] for t in group]
        duration_spread = max(durations) - min(durations)

        # Same duration window → true duplicate
        if duration_spread <= DUP_DURATION_TOLERANCE:
            # Keep ALAC over AAC, highest bitrate otherwise
            def rank(t):
                codec_score = {"alac": 2, "aac": 1, "mp3": 0}.get(t.get("codec", ""), 0)
                return (codec_score, t.get("bitrate", 0))
            sorted_group = sorted(group, key=rank, reverse=True)
            for dup in sorted_group[1:]:
                issues.append({
                    "check": "duplicate",
                    "path": dup["path"],
                    "detail": f"Duplicate of '{sorted_group[0]['path']}' "
                              f"(title='{dup['title']}', artist='{dup['artist']}')",
                    "keep": sorted_group[0]["path"],
                })
    return issues


def check_metadata(tracks: list[dict]) -> list[dict]:
    """Flag tracks missing title, artist, album, or track number."""
    issues = []
    for t in tracks:
        if "error" in t:
            continue
        missing = []
        if not t.get("title"):
            missing.append("title")
        if not t.get("artist"):
            missing.append("artist")
        if not t.get("album"):
            missing.append("album")
        if not t.get("track_num"):
            missing.append("track_number")
        if missing:
            issues.append({
                "check": "metadata",
                "path": t["path"],
                "detail": f"Missing fields: {', '.join(missing)}",
                "missing_fields": missing,
            })
    return issues


def check_artwork(tracks: list[dict]) -> list[dict]:
    """Flag tracks without embedded artwork."""
    issues = []
    for t in tracks:
        if "error" in t:
            continue
        if t.get("album") in SKIP_ALBUMS:
            continue
        if not t.get("has_artwork"):
            issues.append({
                "check": "artwork",
                "path": t["path"],
                "detail": f"No embedded artwork ({t.get('artist', '?')} — {t.get('album', '?')})",
            })
    return issues


def check_format(tracks: list[dict]) -> list[dict]:
    """Flag non-ALAC files: .mp3 files and AAC .m4a files."""
    issues = []
    for t in tracks:
        if "error" in t:
            continue
        codec = t.get("codec", "unknown")
        if codec == "mp3":
            issues.append({
                "check": "format",
                "path": t["path"],
                "detail": "MP3 file — should be transcoded to ALAC .m4a",
                "action": "transcode_to_alac",
            })
        elif codec == "aac":
            issues.append({
                "check": "format",
                "path": t["path"],
                "detail": f"AAC .m4a (lossy) — consider ALAC re-encode from source if available",
                "action": "flag_aac",
            })
    return issues


def check_orphans(lib_root: Path) -> list[dict]:
    """Find files in staging dirs or not inside Artist/Album folders."""
    issues = []

    # Files in known staging dirs
    for staging in STAGING_DIRS:
        staging_path = lib_root.parent / staging
        if staging_path.exists():
            for f in staging_path.rglob("*"):
                if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                    issues.append({
                        "check": "orphan",
                        "path": str(f),
                        "detail": f"File sitting in staging dir '{staging}' — needs review or deletion",
                    })

    # Audio files directly in lib_root (not in Artist/Album subdirs)
    for f in lib_root.iterdir():
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
            issues.append({
                "check": "orphan",
                "path": str(f),
                "detail": "Audio file at library root — not inside Artist/Album folder",
            })

    return issues


def check_cloud_drift(lib_root: Path, xml_path: Path) -> list[dict]:
    """
    Compare local files against an Apple Music XML export.
    Flags tracks in the XML with no matching local file.
    ONLY reads — never touches cloud library.
    """
    issues = []
    try:
        with open(xml_path, "rb") as f:
            data = plistlib.load(f)
    except Exception as e:
        return [{"check": "cloud_drift", "path": str(xml_path),
                 "detail": f"Could not load XML: {e}"}]

    xml_tracks = data.get("Tracks", {})
    local_files = {
        p.name.lower(): p
        for p in lib_root.rglob("*")
        if p.suffix.lower() in AUDIO_EXTS
    }

    for tid, t in xml_tracks.items():
        location = t.get("Location", "")
        if not location:
            continue
        # Only check tracks that have a local file location (not cloud-only)
        if not location.startswith("file://"):
            continue
        decoded = urllib.parse.unquote(location.replace("file://", ""))
        local_path = Path(decoded)
        if not local_path.exists():
            issues.append({
                "check": "cloud_drift",
                "path": decoded,
                "detail": (
                    f"XML references '{t.get('Name', '?')}' by "
                    f"{t.get('Artist', '?')} but file not found locally"
                ),
                "name": t.get("Name", ""),
                "artist": t.get("Artist", ""),
                "album": t.get("Album", ""),
            })

    return issues


# ── Report ─────────────────────────────────────────────────────────────────────

def build_report(issues: list[dict], lib_root: Path, total_tracks: int,
                 elapsed: float) -> dict:
    by_check: dict[str, list] = defaultdict(list)
    for issue in issues:
        by_check[issue["check"]].append(issue)

    return {
        "generated_at": datetime.now().isoformat(),
        "library_root": str(lib_root),
        "total_tracks_scanned": total_tracks,
        "elapsed_seconds": round(elapsed, 1),
        "summary": {k: len(v) for k, v in by_check.items()},
        "total_issues": len(issues),
        "issues": issues,
    }


def print_report(report: dict) -> None:
    print("\n" + "=" * 64)
    print("  LIBRARY VALIDATION REPORT")
    print(f"  {report['generated_at']}")
    print("=" * 64)
    print(f"  Library : {report['library_root']}")
    print(f"  Scanned : {report['total_tracks_scanned']} tracks in {report['elapsed_seconds']}s")
    print(f"  Issues  : {report['total_issues']} total\n")

    summary = report["summary"]
    checks = [
        ("duplicate",    "Duplicate tracks"),
        ("metadata",     "Missing metadata"),
        ("artwork",      "Missing artwork"),
        ("format",       "Format violations"),
        ("orphan",       "Orphaned files"),
        ("cloud_drift",  "Cloud sync drift"),
    ]
    for key, label in checks:
        count = summary.get(key, 0)
        status = "✓" if count == 0 else "✗"
        print(f"  {status}  {label:<24} {count:>5} issue(s)")

    print("\n" + "-" * 64)
    for key, label in checks:
        issues = [i for i in report["issues"] if i["check"] == key]
        if not issues:
            continue
        print(f"\n[{label.upper()}] — {len(issues)} issue(s)")
        for i in issues[:20]:  # cap at 20 per category in stdout
            rel = Path(i["path"]).name
            print(f"  • {rel}")
            print(f"    {i['detail']}")
        if len(issues) > 20:
            print(f"  … and {len(issues) - 20} more (see JSON report)")

    print("\n" + "=" * 64)


# ── Main ───────────────────────────────────────────────────────────────────────

ALL_CHECKS = ["duplicates", "metadata", "artwork", "format", "orphans", "cloud"]

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate music library integrity."
    )
    parser.add_argument(
        "--root", default=str(DEFAULT_LIB_ROOT),
        help="Local music library root directory"
    )
    parser.add_argument(
        "--xml", default=None,
        help="Path to Apple Music Library XML export (enables cloud drift check)"
    )
    parser.add_argument(
        "--check", nargs="+", choices=ALL_CHECKS, default=ALL_CHECKS,
        help="Which checks to run (default: all)"
    )
    parser.add_argument(
        "--output-dir", default="output",
        help="Directory for report files (default: output/)"
    )
    args = parser.parse_args()

    lib_root = Path(args.root)
    if not lib_root.exists():
        print(f"Error: library root not found: {lib_root}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(__file__).parent.parent / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Scanning {lib_root} ...")
    start = datetime.now()

    # Collect all audio files (exclude staging dirs)
    audio_files = [
        p for p in lib_root.rglob("*")
        if p.suffix.lower() in AUDIO_EXTS
        and not p.name.startswith("._")
        and not any(s in p.parts for s in STAGING_DIRS)
    ]
    print(f"Found {len(audio_files)} audio files — loading metadata ...")

    tracks = []
    for i, path in enumerate(audio_files):
        if i % 500 == 0 and i > 0:
            print(f"  {i}/{len(audio_files)} ...")
        info = get_track_info(path)
        if info:
            tracks.append(info)

    all_issues: list[dict] = []
    checks = set(args.check)

    if "duplicates" in checks:
        print("Running: duplicate check ...")
        all_issues.extend(check_duplicates(tracks))

    if "metadata" in checks:
        print("Running: metadata check ...")
        all_issues.extend(check_metadata(tracks))

    if "artwork" in checks:
        print("Running: artwork check ...")
        all_issues.extend(check_artwork(tracks))

    if "format" in checks:
        print("Running: format check ...")
        all_issues.extend(check_format(tracks))

    if "orphans" in checks:
        print("Running: orphan check ...")
        all_issues.extend(check_orphans(lib_root))

    if "cloud" in checks:
        if args.xml:
            print("Running: cloud drift check ...")
            all_issues.extend(check_cloud_drift(lib_root, Path(args.xml)))
        else:
            print("Skipping cloud drift check (no --xml provided)")

    elapsed = (datetime.now() - start).total_seconds()
    report = build_report(all_issues, lib_root, len(tracks), elapsed)

    # Save JSON report
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"validation_{ts}.json"
    latest_path = output_dir / "latest_report.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    with open(latest_path, "w") as f:
        json.dump(report, f, indent=2)

    print_report(report)
    print(f"\n  Report saved to: {json_path}")
    print(f"  Latest report  : {latest_path}")
    print(f"\n  Run fixes with : python3 main/fix.py --dry-run")


if __name__ == "__main__":
    main()
