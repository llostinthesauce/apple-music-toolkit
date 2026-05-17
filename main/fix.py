#!/usr/bin/env python3
"""
fix.py — Targeted Fix Runner for Library Validation Report

Reads the JSON report from validate.py and applies fixes per category.
Always runs in --dry-run mode first; requires confirmation to apply.

Usage:
  python3 main/fix.py --dry-run          # preview all fixes
  python3 main/fix.py --dry-run --check duplicates format
  python3 main/fix.py                    # interactive: dry-run then confirm
  python3 main/fix.py --report output/validation_20260101_120000.json

Fixable categories:
  duplicates  — move lower-quality copy to _DUPES/
  artwork     — fetch missing artwork via CoverArtArchive (MusicBrainz)
  format      — move MP3s to _WRONG_AUDIO/ (manual transcode via amt.sh #10)
  orphans     — report only (no auto-delete; manual review required)
  metadata    — report only (use amt.sh #1 Align or #4 History)
  cloud_drift — report only (LOCAL library only; never touch cloud)
"""

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

import requests
from mutagen.mp4 import MP4, MP4Cover
import os

# ── Constants ──────────────────────────────────────────────────────────────────

DEFAULT_REPORT = Path(__file__).parent.parent / "output" / "latest_report.json"
LIB_ROOT = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))
DUPES_DIR = LIB_ROOT.parent / "_DUPES"
WRONG_AUDIO_DIR = LIB_ROOT.parent / "_WRONG_AUDIO"

MB_SEARCH_URL = "https://musicbrainz.org/ws/2/release/"
CAA_URL = "https://coverartarchive.org/release/"
MB_USER_AGENT = "AMT-Validator/1.0 (https://github.com/llostinthesauce/musicmasters)"

FIXABLE = {"duplicate", "artwork", "format"}
REPORT_ONLY = {"metadata", "orphans", "cloud_drift"}


# ── Artwork helpers ────────────────────────────────────────────────────────────

def _mb_search(artist: str, album: str) -> str | None:
    try:
        r = requests.get(
            MB_SEARCH_URL,
            params={"query": f'artist:"{artist}" AND release:"{album}"',
                    "fmt": "json", "limit": 1},
            headers={"User-Agent": MB_USER_AGENT},
            timeout=10,
        )
        releases = r.json().get("releases", [])
        return releases[0]["id"] if releases else None
    except Exception:
        return None


def _caa_image_url(mbid: str) -> str | None:
    try:
        r = requests.get(f"{CAA_URL}{mbid}",
                         headers={"User-Agent": MB_USER_AGENT}, timeout=10)
        if r.status_code == 404:
            return None
        for img in r.json().get("images", []):
            if img.get("front") and img.get("image"):
                return img["image"]
    except Exception:
        return None
    return None


def _embed_artwork(path: Path, image_data: bytes, mime: str) -> bool:
    try:
        audio = MP4(path)
        fmt = MP4Cover.FORMAT_PNG if "png" in mime else MP4Cover.FORMAT_JPEG
        audio.tags["covr"] = [MP4Cover(image_data, imageformat=fmt)]
        audio.save()
        return True
    except Exception as e:
        print(f"    [ERROR] embed failed: {e}")
        return False


def fetch_and_embed_artwork(path: Path, artist: str, album: str,
                             dry_run: bool) -> tuple[bool, str]:
    """Returns (success, message)."""
    mbid = _mb_search(artist, album)
    if not mbid:
        return False, f"No MusicBrainz match for '{artist}' / '{album}'"

    img_url = _caa_image_url(mbid)
    if not img_url:
        return False, f"No cover art on CoverArtArchive (mbid={mbid})"

    if dry_run:
        return True, f"Would embed artwork from {img_url}"

    try:
        r = requests.get(img_url, headers={"User-Agent": MB_USER_AGENT},
                         timeout=15)
        r.raise_for_status()
        mime = r.headers.get("Content-Type", "image/jpeg")
        ok = _embed_artwork(path, r.content, mime)
        return ok, f"Embedded artwork from {img_url}" if ok else "Embed failed"
    except Exception as e:
        return False, f"Download error: {e}"


# ── Fix handlers ───────────────────────────────────────────────────────────────

def fix_duplicates(issues: list[dict], dry_run: bool) -> list[str]:
    """Move lower-quality duplicate to _DUPES/."""
    lines = []
    DUPES_DIR.mkdir(parents=True, exist_ok=True)

    for issue in issues:
        src = Path(issue["path"])
        if not src.exists():
            lines.append(f"  SKIP  (already gone) {src.name}")
            continue

        dest = DUPES_DIR / src.name
        # Avoid name collision in _DUPES
        counter = 1
        while dest.exists():
            dest = DUPES_DIR / f"{src.stem}_{counter}{src.suffix}"
            counter += 1

        if dry_run:
            lines.append(f"  MOVE  {src.relative_to(LIB_ROOT.parent)}")
            lines.append(f"     →  _DUPES/{dest.name}")
            lines.append(f"        keep: {Path(issue['keep']).name}")
        else:
            shutil.move(str(src), str(dest))
            lines.append(f"  MOVED {src.name}  →  _DUPES/{dest.name}")

    return lines


def fix_artwork(issues: list[dict], dry_run: bool) -> list[str]:
    """Attempt to fetch and embed artwork for each track via MusicBrainz."""
    lines = []
    # Group by album to reduce API calls
    seen_albums: dict[tuple, bool] = {}

    for issue in issues:
        path = Path(issue["path"])
        if not path.exists():
            lines.append(f"  SKIP  (not found) {path.name}")
            continue
        if path.suffix.lower() != ".m4a":
            lines.append(f"  SKIP  (not m4a, embed not supported) {path.name}")
            continue

        # Extract artist/album from the detail string or re-read tags
        detail = issue.get("detail", "")
        match = re.search(r"\(([^—]+) — ([^)]+)\)", detail)
        artist = match.group(1).strip() if match else ""
        album = match.group(2).strip() if match else ""

        if not artist or not album:
            lines.append(f"  SKIP  (can't extract artist/album) {path.name}")
            continue

        album_key = (artist.lower(), album.lower())
        if album_key in seen_albums and not seen_albums[album_key]:
            lines.append(f"  SKIP  (album lookup failed earlier) {path.name}")
            continue

        ok, msg = fetch_and_embed_artwork(path, artist, album, dry_run)
        seen_albums[album_key] = ok
        status = "WOULD" if dry_run else ("OK   " if ok else "FAIL ")
        lines.append(f"  {status} {path.name}")
        lines.append(f"        {msg}")
        time.sleep(0.3)  # MusicBrainz rate limit

    return lines


def fix_format(issues: list[dict], dry_run: bool) -> list[str]:
    """
    Move MP3 files to _WRONG_AUDIO/ for manual transcoding via amt.sh #10.
    AAC files are flagged only — lossless re-encode requires source files.
    """
    lines = []
    WRONG_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    for issue in issues:
        path = Path(issue["path"])
        action = issue.get("action", "")

        if action == "flag_aac":
            lines.append(f"  FLAG  (AAC — manual review) {path.name}")
            continue

        if not path.exists():
            lines.append(f"  SKIP  (not found) {path.name}")
            continue

        dest = WRONG_AUDIO_DIR / path.name
        counter = 1
        while dest.exists():
            dest = WRONG_AUDIO_DIR / f"{path.stem}_{counter}{path.suffix}"
            counter += 1

        if dry_run:
            lines.append(f"  MOVE  {path.name}")
            lines.append(f"     →  _WRONG_AUDIO/{dest.name}  (transcode via amt.sh #10)")
        else:
            shutil.move(str(path), str(dest))
            lines.append(f"  MOVED {path.name}  →  _WRONG_AUDIO/{dest.name}")

    return lines


def report_only(check: str, issues: list[dict]) -> list[str]:
    labels = {
        "metadata":    "metadata issues (use amt.sh #1 Align or #4 History to fix)",
        "orphans":     "orphaned files (review manually)",
        "cloud_drift": "cloud drift items (LOCAL library only — never auto-fixed)",
    }
    label = labels.get(check, check)
    lines = [f"\n  [{check.upper()}] — {len(issues)} {label}"]
    for i in issues[:30]:
        lines.append(f"    • {Path(i['path']).name}")
        lines.append(f"      {i['detail']}")
    if len(issues) > 30:
        lines.append(f"    … and {len(issues) - 30} more in the JSON report")
    return lines


# ── Main ───────────────────────────────────────────────────────────────────────

ALL_CHECKS = list(FIXABLE) + list(REPORT_ONLY)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply fixes from a validate.py report."
    )
    parser.add_argument(
        "--report", default=str(DEFAULT_REPORT),
        help="Path to JSON report from validate.py"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without modifying anything"
    )
    parser.add_argument(
        "--check", nargs="+", choices=ALL_CHECKS, default=ALL_CHECKS,
        help="Which fix categories to run"
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip confirmation prompt (apply immediately)"
    )
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"Error: report not found: {report_path}", file=sys.stderr)
        print("Run validate.py first.", file=sys.stderr)
        sys.exit(1)

    with open(report_path) as f:
        report = json.load(f)

    issues_by_check: dict[str, list] = {}
    for issue in report.get("issues", []):
        issues_by_check.setdefault(issue["check"], []).append(issue)

    checks = set(args.check)
    summary = report.get("summary", {})

    print("\n" + "=" * 64)
    print(f"  FIX RUNNER  {'(DRY RUN)' if args.dry_run else '(LIVE MODE)'}")
    print(f"  Report from: {report.get('generated_at', '?')}")
    print("=" * 64)
    print(f"  Issues in report: {report.get('total_issues', 0)}")
    for k, v in summary.items():
        fixable = "auto-fix" if k in FIXABLE else "report only"
        print(f"    {k:<16} {v:>5}  ({fixable})")

    # Phase 1: always dry-run first unless --yes
    if not args.dry_run and not args.yes:
        print("\n  Running DRY RUN first ...\n")
        dry_lines = _run_fixes(issues_by_check, checks, dry_run=True)
        for line in dry_lines:
            print(line)
        print("\n" + "-" * 64)
        answer = input("  Apply these changes? [y/N] ").strip().lower()
        if answer != "y":
            print("  Aborted — no changes made.")
            return
        print("\n  Applying fixes ...\n")
        live_lines = _run_fixes(issues_by_check, checks, dry_run=False)
        for line in live_lines:
            print(line)
    else:
        lines = _run_fixes(issues_by_check, checks, dry_run=args.dry_run)
        for line in lines:
            print(line)

    print("\n" + "=" * 64)
    if args.dry_run:
        print("  Dry run complete. Run without --dry-run to apply.")
    else:
        print("  Done. Re-run validate.py to confirm fixes.")
        print("  python3 main/validate.py")
    print("=" * 64 + "\n")


def _run_fixes(issues_by_check: dict, checks: set, dry_run: bool) -> list[str]:
    lines = []
    for check in ["duplicate", "format", "artwork", "metadata", "orphans", "cloud_drift"]:
        if check not in checks:
            # Also handle plural forms passed via CLI args
            if check + "s" not in checks:
                continue
        issues = issues_by_check.get(check, [])
        if not issues:
            lines.append(f"\n  [{check.upper()}] — nothing to fix")
            continue

        lines.append(f"\n  [{check.upper()}] — {len(issues)} issue(s)")

        if check == "duplicate":
            lines.extend(fix_duplicates(issues, dry_run))
        elif check == "artwork":
            lines.extend(fix_artwork(issues, dry_run))
        elif check == "format":
            lines.extend(fix_format(issues, dry_run))
        elif check in REPORT_ONLY:
            lines.extend(report_only(check, issues))

    return lines


if __name__ == "__main__":
    main()
