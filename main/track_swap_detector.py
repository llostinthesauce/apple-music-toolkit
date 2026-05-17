"""
track_swap_detector.py — Read-only diagnostic for audio/metadata mismatches.

For each track in a target album (or all albums), fingerprints the audio,
looks it up on AcoustID, and compares the result against the embedded
metadata. Reports suspected swaps and intra-album acoustic duplicates.

No files are moved or modified.
"""

import argparse
import json
import os
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import mutagen
import requests

# ── Paths & constants ────────────────────────────────────────────────────────

FORIPOD_ROOT = Path(os.environ.get("MUSIC_ROOT", "~/Music/Music")).expanduser()
FP_CACHE_FILE = Path(__file__).parent.parent / ".audio_fingerprints.json"
LOOKUP_CACHE_FILE = Path(__file__).parent.parent / ".acoustid_lookup_cache.json"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

ACOUSTID_API_KEY = ""  # Pass via --api-key or ACOUSTID_API_KEY env var
AUDIO_EXTS = {".m4a", ".mp3", ".flac", ".wav", ".aiff", ".aac"}

# Minimum AcoustID score to trust an identification
MIN_SCORE = 0.70


# ── Fingerprinting ────────────────────────────────────────────────────────────
#
# AcoustID requires the *compressed* chromaprint format (base64) plus the real
# track duration in seconds. fpcalc without -raw outputs both. The existing
# .audio_fingerprints.json cache was built with -raw (integer arrays) which the
# API rejects with 400. We use a separate cache file here so the two don't mix.

# Separate cache for compressed fingerprints + durations used for AcoustID.
ACOUSTID_FP_CACHE_FILE = Path(__file__).parent.parent / ".acoustid_fp_cache.json"


def fingerprint_file(path: Path) -> Optional[Tuple[str, int]]:
    """
    Run fpcalc (no -raw) on a file.
    Returns (compressed_fingerprint, duration_seconds) or None on failure.
    """
    try:
        result = subprocess.run(
            ["fpcalc", "-length", "120", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        fp = None
        duration = 120
        for line in result.stdout.split("\n"):
            if line.startswith("FINGERPRINT="):
                fp = line.split("=", 1)[1].strip()
            elif line.startswith("DURATION="):
                try:
                    duration = int(float(line.split("=", 1)[1].strip()))
                except ValueError:
                    pass
        if fp:
            return (fp, duration)
    except Exception:
        pass
    return None


def load_acoustid_fp_cache() -> Dict[str, dict]:
    if ACOUSTID_FP_CACHE_FILE.exists():
        with ACOUSTID_FP_CACHE_FILE.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_acoustid_fp_cache(cache: Dict[str, dict]) -> None:
    with ACOUSTID_FP_CACHE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(cache, fh)


def get_fingerprint(path: Path, fp_cache: Dict[str, dict]) -> Optional[Tuple[str, int]]:
    key = str(path)
    if key in fp_cache:
        entry = fp_cache[key]
        return (entry["fp"], entry["duration"])
    result = fingerprint_file(path)
    if result:
        fp, duration = result
        fp_cache[key] = {"fp": fp, "duration": duration}
        save_acoustid_fp_cache(fp_cache)
    return result


# ── AcoustID lookup ───────────────────────────────────────────────────────────

def load_lookup_cache() -> Dict[str, object]:
    if LOOKUP_CACHE_FILE.exists():
        with LOOKUP_CACHE_FILE.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_lookup_cache(cache: Dict[str, object]) -> None:
    with LOOKUP_CACHE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(cache, fh)


_lookup_save_counter = 0
_LOOKUP_SAVE_INTERVAL = 200


def acoustid_lookup(fingerprint: str, duration: int, lookup_cache: Dict[str, object], api_key: str = "", force_save: bool = False) -> Optional[dict]:
    """Look up a fingerprint on AcoustID. Returns the raw API response."""
    global _lookup_save_counter
    cache_key = fingerprint[:200]  # key by prefix — fingerprints are huge
    if cache_key in lookup_cache:
        return lookup_cache[cache_key]

    fields = {
        "client": (None, api_key or ACOUSTID_API_KEY),
        "format": (None, "json"),
        "duration": (None, str(duration)),
        "fingerprint": (None, fingerprint),
        "meta": (None, "recordings releases"),
    }
    try:
        resp = requests.post(
            "https://api.acoustid.org/v2/lookup",
            files=fields,
            timeout=20,
        )
        if not resp.ok:
            body = resp.json() if resp.content else {}
            err = body.get("error", {})
            print(f"  [!] AcoustID {resp.status_code}: code={err.get('code')} {err.get('message')}")
            return None
        data = resp.json()
        lookup_cache[cache_key] = data
        _lookup_save_counter += 1
        if _lookup_save_counter % _LOOKUP_SAVE_INTERVAL == 0 or force_save:
            save_lookup_cache(lookup_cache)
        time.sleep(0.35)  # AcoustID rate limit: ~3 req/s
        return data
    except Exception as exc:
        print(f"  [!] AcoustID error: {exc}")
        return None


def best_identification(api_response: dict) -> Optional[Tuple[str, str, str, float]]:
    """
    Extract the best (title, artist, album, score) from an AcoustID response.
    Returns None if nothing passes MIN_SCORE.
    """
    if not api_response or api_response.get("status") != "ok":
        return None

    best_title = ""
    best_artist = ""
    best_album = ""
    best_score = 0.0

    for result in api_response.get("results", []):
        score = float(result.get("score", 0))
        if score < MIN_SCORE:
            continue
        for rec in result.get("recordings", []):
            title = rec.get("title", "").strip()
            artists = rec.get("artists", [])
            artist = artists[0].get("name", "").strip() if artists else ""
            # Extract album/release title
            releases = rec.get("releases", [])
            album = releases[0].get("title", "").strip() if releases else ""
            if title and score > best_score:
                best_score = score
                best_title = title
                best_artist = artist
                best_album = album

    if best_title:
        return (best_title, best_artist, best_album, best_score)
    return None


# ── Metadata reading ──────────────────────────────────────────────────────────

def _tag_value(tags: dict, keys: List[str]) -> str:
    for key in keys:
        raw = tags.get(key)
        if raw is None:
            continue
        if isinstance(raw, list):
            val = str(raw[0] or "").strip()
        else:
            val = str(raw).strip()
        if val:
            return val
    return ""


def read_metadata(path: Path) -> Dict[str, str]:
    """Return embedded title, artist, album from file tags."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return {}
    if not audio:
        return {}
    tags = getattr(audio, "tags", {}) or {}
    return {
        "title": _tag_value(tags, ["\xa9nam", "TIT2", "title"]),
        "artist": _tag_value(tags, ["\xa9ART", "TPE1", "artist"]),
        "album": _tag_value(tags, ["\xa9alb", "TALB", "album"]),
    }


# ── Comparison ────────────────────────────────────────────────────────────────

def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def is_mismatch(meta_title: str, meta_album: str, id_title: str, id_artist: str, id_album: str, meta_artist: str) -> Tuple[bool, str]:
    """
    Return (is_mismatch, reason).
    Compares normalized strings to tolerate punctuation and case differences.
    Checks both title match AND album match — intra-album swaps are the most
    common wrong-audio scenario (track 3 has track 7's audio, both same artist).
    """
    title_match = normalize(meta_title) == normalize(id_title)
    album_match = normalize(meta_album) == normalize(id_album) if id_album else True

    if not title_match:
        return (True, f"title mismatch: tag={meta_title!r}  audio={id_title!r}")
    if not album_match and id_album:
        return (True, f"album mismatch: tag={meta_album!r}  audio={id_album!r}")
    return (False, "")


# ── Album scanning ────────────────────────────────────────────────────────────

def scan_album(
    album_path: Path,
    fp_cache: Dict[str, dict],
    lookup_cache: Dict[str, object],
    api_key: str = "",
) -> dict:
    """
    Scan all audio files in album_path. Returns a dict with:
      - tracks: per-file results
      - suspected_swaps: files where audio != metadata
      - acoustic_duplicates: sets of files with identical fingerprints
    """
    files = sorted(
        f for f in album_path.iterdir()
        if f.is_file()
        and f.suffix.lower() in AUDIO_EXTS
        and not f.name.startswith("._")
    )

    track_results = []
    fp_to_files: Dict[str, List[Path]] = defaultdict(list)

    for fpath in files:
        print(f"  {fpath.name}")

        meta = read_metadata(fpath)
        fp_result = get_fingerprint(fpath, fp_cache)

        result = {
            "file": fpath.name,
            "path": str(fpath),
            "metadata": meta,
            "fingerprint_available": fp_result is not None,
            "acoustid": None,
            "status": "unknown",
        }

        if fp_result is None:
            result["status"] = "no_fingerprint"
            track_results.append(result)
            continue

        fp, duration = fp_result
        fp_to_files[fp].append(fpath)

        api_resp = acoustid_lookup(fp, duration, lookup_cache, api_key=api_key)
        identification = best_identification(api_resp) if api_resp else None

        if identification is None:
            result["status"] = "unidentified"
            result["acoustid"] = {"score": None, "title": None, "artist": None, "album": None}
        else:
            id_title, id_artist, id_album, id_score = identification
            result["acoustid"] = {
                "title": id_title,
                "artist": id_artist,
                "album": id_album,
                "score": round(id_score, 3),
            }
            mismatch, reason = is_mismatch(
                meta.get("title", ""),
                meta.get("album", ""),
                id_title,
                id_artist,
                id_album,
                meta.get("artist", ""),
            )
            result["status"] = "MISMATCH" if mismatch else "ok"
            if mismatch:
                result["mismatch_reason"] = reason

        track_results.append(result)

    # Intra-album acoustic duplicates
    acoustic_dupes = [
        [str(p) for p in paths]
        for paths in fp_to_files.values()
        if len(paths) > 1
    ]

    suspected_swaps = [r for r in track_results if r["status"] == "MISMATCH"]

    return {
        "album": str(album_path),
        "tracks": track_results,
        "suspected_swaps": suspected_swaps,
        "acoustic_duplicates": acoustic_dupes,
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_album_report(album_result: dict) -> None:
    album = Path(album_result["album"])
    swaps = album_result["suspected_swaps"]
    dupes = album_result["acoustic_duplicates"]

    print(f"\n{'='*60}")
    print(f"Album: {album.parent.name} / {album.name}")
    print(f"{'='*60}")

    ok = sum(1 for t in album_result["tracks"] if t["status"] == "ok")
    unidentified = sum(1 for t in album_result["tracks"] if t["status"] == "unidentified")
    no_fp = sum(1 for t in album_result["tracks"] if t["status"] == "no_fingerprint")

    print(f"  Tracks scanned : {len(album_result['tracks'])}")
    print(f"  OK             : {ok}")
    print(f"  Suspected swaps: {len(swaps)}")
    print(f"  Unidentified   : {unidentified}")
    print(f"  No fingerprint : {no_fp}")

    if swaps:
        print("\n  SUSPECTED SWAPS:")
        for t in swaps:
            meta_title = t["metadata"].get("title", "(no title tag)")
            meta_album = t["metadata"].get("album", "(no album tag)")
            id_title = t["acoustid"]["title"] if t["acoustid"] else "?"
            id_artist = t["acoustid"]["artist"] if t["acoustid"] else "?"
            id_album = t["acoustid"].get("album", "?") if t["acoustid"] else "?"
            score = t["acoustid"]["score"] if t["acoustid"] else "?"
            reason = t.get("mismatch_reason", "")
            print(f"    [{score}] {t['file']}")
            print(f"           Metadata says : {meta_title}")
            print(f"           Metadata album: {meta_album}")
            print(f"           AcoustID says : {id_title} — {id_artist}")
            print(f"           AcoustID album: {id_album}")
            if reason:
                print(f"           Reason: {reason}")

    if dupes:
        print("\n  ACOUSTIC DUPLICATES (same audio, different files):")
        for dupe_set in dupes:
            for p in dupe_set:
                print(f"    - {Path(p).name}")
            print()


def write_report(all_results: List[dict], out_path: Path) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(all_results, fh, indent=2, ensure_ascii=False)
    print(f"\nJSON report: {out_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def resolve_album_paths(args: argparse.Namespace) -> List[Path]:
    if args.albums:
        paths = []
        for album_arg in args.albums:
            p = FORIPOD_ROOT / album_arg
            if not p.is_dir():
                print(f"[!] Album path not found: {p}")
            else:
                paths.append(p)
        return paths

    # No albums specified — scan everything
    return sorted(
        p for artist in FORIPOD_ROOT.iterdir()
        if artist.is_dir() and not artist.name.startswith(".")
        for p in artist.iterdir()
        if p.is_dir()
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect tracks where audio content doesn't match embedded metadata."
    )
    parser.add_argument(
        "--albums",
        nargs="+",
        metavar="ARTIST/ALBUM",
        help='Albums to check, e.g. "Ninajirachi/I Love My Computer". '
             "Omit to scan all albums (slow).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUTPUT_DIR / "track_swap_report.json",
        help="Output JSON report path.",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="AcoustID API key. Can also be set via ACOUSTID_API_KEY env var.",
    )
    args = parser.parse_args()

    import os
    api_key = args.api_key or os.environ.get("ACOUSTID_API_KEY", "")
    if not api_key:
        print("ERROR: No AcoustID API key provided.")
        print("  Get a free key at: https://acoustid.org/api-key")
        print("  Then run: python3 main/track_swap_detector.py --api-key YOUR_KEY ...")
        return

    album_paths = resolve_album_paths(args)
    if not album_paths:
        print("No albums to scan.")
        return

    print(f"Loading fingerprint cache ({ACOUSTID_FP_CACHE_FILE.name})...")
    fp_cache = load_acoustid_fp_cache()
    print(f"  {len(fp_cache)} cached fingerprints")

    print(f"Loading AcoustID lookup cache...")
    lookup_cache = load_lookup_cache()
    print(f"  {len(lookup_cache)} cached lookups")

    all_results = []

    for album_path in album_paths:
        print(f"\nScanning: {album_path.parent.name} / {album_path.name}")
        result = scan_album(album_path, fp_cache, lookup_cache, api_key=api_key)
        all_results.append(result)
        print_album_report(result)

    # Final save of caches
    save_lookup_cache(lookup_cache)
    write_report(all_results, args.out)

    # Final summary
    total_swaps = sum(len(r["suspected_swaps"]) for r in all_results)
    total_dupes = sum(len(r["acoustic_duplicates"]) for r in all_results)
    print(f"\n{'='*60}")
    print(f"SUMMARY: {len(all_results)} album(s) scanned")
    print(f"  Suspected swaps     : {total_swaps}")
    print(f"  Acoustic dupe sets  : {total_dupes}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
