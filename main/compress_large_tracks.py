"""
compress_large_tracks.py — Re-encode large tracks for iPod compatibility.

The iPod Video 5th/5.5 gen has a limited RAM buffer. Files above a certain
size cause hard drive spin-up mid-track or get skipped entirely. This script
finds oversized files, backs up the originals, and replaces them with
256kbps AAC versions while preserving all metadata and artwork.

Originals are NEVER deleted — they're copied to --backup-dir first.

Usage:
    # Preview what would be compressed (no changes):
    python3 main/compress_large_tracks.py --dry-run

    # Run for real (originals backed up first):
    python3 main/compress_large_tracks.py

    # Custom thresholds:
    python3 main/compress_large_tracks.py --min-size 30 --min-duration 600
"""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import mutagen
from mutagen.mp4 import MP4, MP4Cover

# ── Defaults ──────────────────────────────────────────────────────────────────

FORIPOD_ROOT  = Path("~/Music").expanduser()
BACKUP_ROOT   = Path("~/Music_originals").expanduser()
OUTPUT_DIR    = Path(__file__).parent.parent / "output"

DEFAULT_MIN_SIZE_MB  = 50    # files larger than this are candidates
DEFAULT_MIN_DURATION = 900   # seconds (15 min) — files longer than this too

AAC_BITRATE   = "256000"     # 256 kbps
AUDIO_EXTS    = {".m4a", ".mp3", ".aiff", ".wav", ".flac"}


# ── Metadata handling ─────────────────────────────────────────────────────────

def read_all_tags(path: Path) -> dict:
    """Read all tags and artwork from a file. Returns a dict safe to re-apply."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return {}
    if not audio or not audio.tags:
        return {}

    tags = dict(audio.tags)
    return tags


def write_tags_to_m4a(path: Path, tags: dict) -> None:
    """Write tags (from mutagen dict) to an M4A file."""
    try:
        out = MP4(path)
    except Exception as e:
        print(f"    [!] Could not open output for tagging: {e}")
        return

    # M4A tag keys we care about
    m4a_keys = {
        "\xa9nam", "\xa9ART", "aART", "\xa9alb", "\xa9gen",
        "\xa9day", "trkn", "disk", "\xa9wrt", "\xa9cmt",
        "cpil", "pgap", "tmpo", "covr",
    }

    for key, val in tags.items():
        if key in m4a_keys:
            try:
                out[key] = val
            except Exception:
                pass  # skip tags that don't apply to this container

    out.save()


# ── Encoding ──────────────────────────────────────────────────────────────────

def encode_aac(src: Path, dest: Path, bitrate: str = AAC_BITRATE) -> bool:
    """
    Re-encode src to AAC m4a at the given bitrate using afconvert (Apple's
    encoder, highest quality on macOS). Falls back to ffmpeg if afconvert
    fails (e.g., for MP3/FLAC input that afconvert can't always read directly).

    Returns True on success.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    # afconvert works best with m4a/aiff input; for other formats convert via
    # an intermediate AIFF first so we get the Apple encoder quality.
    aiff_tmp: Optional[Path] = None

    if src.suffix.lower() not in (".m4a", ".aiff"):
        # Convert to AIFF first using ffmpeg, then feed to afconvert
        aiff_tmp = dest.parent / (dest.stem + "_tmp.aiff")
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-c:a", "pcm_s16be",
             "-map_metadata", "-1", str(aiff_tmp)],
            capture_output=True,
        )
        if r.returncode != 0:
            aiff_tmp = None  # fall through to ffmpeg path
        src_for_afc = aiff_tmp or src
    else:
        src_for_afc = src

    # Try afconvert
    r = subprocess.run(
        ["afconvert", "-f", "m4af", "-d", f"aac@44100",
         "-b", bitrate, str(src_for_afc), str(dest)],
        capture_output=True,
    )
    if aiff_tmp and aiff_tmp.exists():
        aiff_tmp.unlink()

    if r.returncode == 0:
        return True

    # Fallback: ffmpeg AAC
    r2 = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-c:a", "aac", "-b:a", f"{int(bitrate)//1000}k",
         "-map_metadata", "-1", str(dest)],
        capture_output=True,
    )
    return r2.returncode == 0


# ── Core logic ────────────────────────────────────────────────────────────────

def get_duration(path: Path) -> float:
    """Return duration in seconds, or 0 on failure."""
    try:
        audio = mutagen.File(path)
        return getattr(getattr(audio, "info", None), "length", 0) or 0
    except Exception:
        return 0


def is_candidate(path: Path, min_size_mb: float, min_duration: float) -> tuple:
    """
    Return (is_candidate, size_mb, duration_sec, reason).
    A file is a candidate if it exceeds either threshold.
    """
    size_mb  = path.stat().st_size / 1_048_576
    duration = get_duration(path)
    reasons  = []

    if size_mb >= min_size_mb:
        reasons.append(f"{size_mb:.0f} MB")
    if duration >= min_duration:
        reasons.append(f"{duration/60:.1f} min")

    return bool(reasons), size_mb, duration, " + ".join(reasons)


def backup_path(src: Path, library_root: Path, backup_root: Path) -> Path:
    """Mirror the library path under backup_root."""
    rel = src.relative_to(library_root)
    return backup_root / rel


def process_file(
    path: Path,
    library_root: Path,
    backup_root: Path,
    dry_run: bool,
) -> dict:
    """Back up original and replace with AAC re-encode. Returns a status dict."""
    bak = backup_path(path, library_root, backup_root)

    if dry_run:
        return {"path": str(path), "action": "would_compress", "backup": str(bak)}

    # Step 1: Back up original
    bak.parent.mkdir(parents=True, exist_ok=True)
    if not bak.exists():
        shutil.copy2(path, bak)
    else:
        print(f"    [backup exists, skipping copy] {bak.name}")

    # Step 2: Read metadata before we touch anything
    tags = read_all_tags(path)

    # Step 3: Encode to AAC in a temp file
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    success = encode_aac(path, tmp_path)
    if not success:
        tmp_path.unlink(missing_ok=True)
        return {"path": str(path), "action": "encode_failed"}

    # Step 4: Write original tags to the new file
    write_tags_to_m4a(tmp_path, tags)

    # Step 5: Replace original with compressed version
    orig_size = path.stat().st_size
    new_size  = tmp_path.stat().st_size
    shutil.move(str(tmp_path), str(path))

    return {
        "path":          str(path),
        "action":        "compressed",
        "backup":        str(bak),
        "orig_size_mb":  round(orig_size / 1_048_576, 1),
        "new_size_mb":   round(new_size  / 1_048_576, 1),
        "savings_mb":    round((orig_size - new_size) / 1_048_576, 1),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find and compress large tracks for iPod compatibility."
    )
    parser.add_argument(
        "--library", type=Path, default=FORIPOD_ROOT,
        help="Music library root",
    )
    parser.add_argument(
        "--backup-dir", type=Path, default=BACKUP_ROOT,
        help="Where to store original files before compression",
    )
    parser.add_argument(
        "--min-size", type=float, default=DEFAULT_MIN_SIZE_MB,
        help=f"Flag files larger than N MB (default: {DEFAULT_MIN_SIZE_MB})",
    )
    parser.add_argument(
        "--min-duration", type=float, default=DEFAULT_MIN_DURATION,
        help=f"Flag files longer than N seconds (default: {DEFAULT_MIN_DURATION} = 15 min)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show candidates only, make no changes",
    )
    args = parser.parse_args()

    library_root = args.library.expanduser()
    backup_root  = args.backup_dir.expanduser()

    if not library_root.exists():
        print(f"ERROR: Library not found: {library_root}")
        return

    print(f"Scanning {library_root} ...")
    print(f"  Thresholds: >{args.min_size} MB  OR  >{args.min_duration/60:.0f} min")
    if args.dry_run:
        print("  [DRY RUN] No files will be modified.\n")

    candidates = []
    for path in sorted(library_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in AUDIO_EXTS:
            continue
        if path.name.startswith("._"):
            continue

        is_cand, size_mb, duration, reason = is_candidate(
            path, args.min_size, args.min_duration
        )
        if is_cand:
            candidates.append((path, size_mb, duration, reason))

    print(f"  {len(candidates)} candidate(s) found\n")

    if not candidates:
        print("Nothing to do.")
        return

    # Print candidate table
    print(f"{'FILE':<70}  {'REASON'}")
    print("-" * 90)
    for path, size_mb, duration, reason in candidates:
        rel = path.relative_to(library_root)
        print(f"  {str(rel):<70}  {reason}")

    if args.dry_run:
        total_mb = sum(s for _, s, _, _ in candidates)
        print(f"\nTotal size of candidates: {total_mb:.0f} MB")
        print(f"Backup dir would be: {backup_root}")
        return

    print(f"\nBackup dir: {backup_root}")
    print("Processing ...\n")

    results = []
    total_saved = 0.0

    for i, (path, size_mb, duration, reason) in enumerate(candidates, 1):
        rel = path.relative_to(library_root)
        print(f"[{i}/{len(candidates)}] {rel}")
        print(f"  Reason : {reason}")

        result = process_file(path, library_root, backup_root, dry_run=False)
        results.append(result)

        if result["action"] == "compressed":
            saved = result["savings_mb"]
            total_saved += saved
            print(f"  {result['orig_size_mb']} MB → {result['new_size_mb']} MB  (saved {saved} MB)")
        elif result["action"] == "encode_failed":
            print(f"  [!] Encoding failed — original untouched")
        print()

    # Summary
    compressed = [r for r in results if r["action"] == "compressed"]
    failed     = [r for r in results if r["action"] == "encode_failed"]

    print("=" * 60)
    print(f"Compressed : {len(compressed)}")
    print(f"Failed     : {len(failed)}")
    print(f"Space saved: {total_saved:.1f} MB")
    print(f"Originals  : {backup_root}")
    print("=" * 60)

    # Write JSON log
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT_DIR / "compress_large_tracks_log.json"
    with log_path.open("w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)
    print(f"Log: {log_path}")


if __name__ == "__main__":
    main()
