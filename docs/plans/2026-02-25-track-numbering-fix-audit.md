# Track Numbering Fix & Missing Track Audit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix missing `trkn` metadata across 8,321 audio files and produce CSV+JSON reports of albums with missing tracks.

**Architecture:** Two standalone scripts in `apple-music-toolkit/`. `fix_track_numbers.py` writes `trkn` from filenames (Phase 1, offline) then fills in track totals via MusicBrainz (Phase 2, online). `audit_missing_tracks.py` identifies missing tracks using on-disk trkn gaps (Method C) plus MusicBrainz canonical tracklist comparison (Method A). Both share a MusicBrainz helper module.

**Tech Stack:** Python 3.12, mutagen (already in requirements), requests (already in requirements), MusicBrainz JSON API (no key required, 1 req/sec limit)

**Library root:** `~/Music/foriPod/Media.localized/Music/Music/`

---

### Task 1: Create shared MusicBrainz helper module

**Files:**
- Create: `apple-music-toolkit/mb_client.py`

This module handles all MusicBrainz API calls, rate limiting, and disk caching. Both scripts import from it.

**Step 1: Write `mb_client.py`**

```python
"""
mb_client.py — MusicBrainz API client with rate limiting and disk cache.

API docs: https://musicbrainz.org/doc/MusicBrainz_API
Rate limit: 1 request/second (enforced by this module)
Cache: ~/.cache/amt_mb_cache.json — persists across runs
"""

import json
import re
import time
import unicodedata
from pathlib import Path

import requests

CACHE_PATH = Path.home() / ".cache" / "amt_mb_cache.json"
MB_BASE = "https://musicbrainz.org/ws/2"
HEADERS = {"User-Agent": "apple-music-toolkit/1.0 (local-use)"}
MIN_INTERVAL = 1.05  # seconds between requests (MusicBrainz policy: 1/sec)

_cache: dict = {}
_last_request_time: float = 0.0


def _load_cache() -> None:
    global _cache
    if CACHE_PATH.exists():
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}


def _save_cache() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _rate_limited_get(url: str, params: dict) -> dict | None:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        _last_request_time = time.time()
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        _last_request_time = time.time()
        return None


def normalize(text: str) -> str:
    """Lowercase, strip accents, remove punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def lookup_album(artist: str, album: str) -> dict | None:
    """
    Query MusicBrainz for a release matching artist+album.

    Returns dict with keys:
        title     str   canonical album title
        tracks    list  [{"num": int, "title": str}, ...]
        count     int   total track count

    Returns None if no confident match found (score < 85).
    """
    _load_cache()
    cache_key = f"{normalize(artist)}||{normalize(album)}"
    if cache_key in _cache:
        return _cache[cache_key]

    query = f'artist:"{artist}" AND release:"{album}"'
    data = _rate_limited_get(
        f"{MB_BASE}/release",
        {"query": query, "fmt": "json", "limit": 5, "inc": "recordings"},
    )

    result = None
    if data and data.get("releases"):
        for release in data["releases"]:
            score = int(release.get("score", 0))
            if score < 85:
                continue
            # Extract tracks from media
            tracks = []
            for medium in release.get("media", []):
                for track in medium.get("tracks", []):
                    num = track.get("number") or track.get("position", 0)
                    try:
                        num = int(num)
                    except (ValueError, TypeError):
                        num = 0
                    title = (track.get("recording") or {}).get("title") or track.get("title", "")
                    tracks.append({"num": num, "title": title})
            if tracks:
                result = {
                    "title": release.get("title", album),
                    "tracks": sorted(tracks, key=lambda t: t["num"]),
                    "count": len(tracks),
                }
                break

    _cache[cache_key] = result
    _save_cache()
    return result
```

**Step 2: Verify the module loads and can make a test query**

```bash
cd /Users/corbinshanks/Documents/gh-coding/musicmasters/apple-music-toolkit
python3 -c "
from mb_client import lookup_album
result = lookup_album('Arctic Monkeys', 'AM')
if result:
    print(f'Found: {result[\"title\"]} — {result[\"count\"]} tracks')
    for t in result['tracks'][:3]:
        print(f'  {t[\"num\"]}. {t[\"title\"]}')
else:
    print('No result')
"
```

Expected output: `Found: AM — 12 tracks` followed by the first 3 track titles.

**Step 3: Commit**

```bash
cd /Users/corbinshanks/Documents/gh-coding/musicmasters/apple-music-toolkit
git add mb_client.py
git commit -m "feat: add MusicBrainz client with rate limiting and disk cache"
```

---

### Task 2: Write `fix_track_numbers.py`

**Files:**
- Create: `apple-music-toolkit/fix_track_numbers.py`

**Step 1: Write the script**

```python
"""
fix_track_numbers.py — write trkn (track number/total) metadata from filenames.

PHASE 1 (offline, --skip-mb to stop here):
  Parses leading digits from filename: "01 Track Name.m4a" → trkn=(1, 0)
  Skips files that already have trkn[0] > 0.
  Writes total as 0 when unknown.

PHASE 2 (MusicBrainz, requires internet):
  For albums where any track has trkn total == 0, queries MusicBrainz for
  canonical track count. If matched (score >= 85), writes total into all
  tracks in that album.

Usage:
    python3 fix_track_numbers.py --root ~/Music/foriPod/Media.localized/Music/Music [--dry-run] [--skip-mb]
"""

import argparse
import re
from collections import defaultdict
from pathlib import Path

import mutagen
from mutagen.mp4 import MP4, MP4Tags
from mutagen.mp3 import MP3
from mutagen.id3 import TRCK

AUDIO_EXTS = {".m4a", ".mp4", ".mp3"}
TRACK_RE = re.compile(r"^(\d+)")


def parse_num_from_filename(stem: str) -> int | None:
    """Extract leading track number from filename stem. Returns None if not found."""
    m = TRACK_RE.match(stem)
    return int(m.group(1)) if m else None


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
    trck = str(audio.get("TRCK", ""))
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
    """Fill in track totals from MusicBrainz for albums that still need it."""
    from mb_client import lookup_album

    targets = [(a, alb) for (a, alb), needed in needs_total.items() if needed]
    print(f"\nPhase 2: querying MusicBrainz for {len(targets)} albums...")

    updated = skipped = no_match = 0
    for artist, album in targets:
        result = lookup_album(artist, album)
        if not result:
            print(f"  NO MATCH: {artist} / {album}")
            no_match += 1
            continue

        total = result["count"]
        print(f"  FOUND ({total} tracks): {artist} / {album}")

        for path in albums[(artist, album)]:
            try:
                if path.suffix.lower() in {".m4a", ".mp4"}:
                    audio = MP4(path)
                    num, existing_total = get_trkn_m4a(audio.tags)
                    if existing_total == total:
                        skipped += 1
                        continue
                    set_trkn_m4a(path, num if num > 0 else (parse_num_from_filename(path.stem) or 0), total, dry_run)
                else:
                    audio = MP3(path)
                    num, existing_total = get_trkn_mp3(audio)
                    if existing_total == total:
                        skipped += 1
                        continue
                    set_trkn_mp3(path, num if num > 0 else (parse_num_from_filename(path.stem) or 0), total, dry_run)
                updated += 1
            except Exception as e:
                print(f"    ERROR {path.name}: {e}")

    verb = "Would update" if dry_run else "Updated"
    print(f"\nPhase 2 done. {verb} totals: {updated}  Already correct: {skipped}  No MB match: {no_match}")


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
```

**Step 2: Dry-run to verify Phase 1 output looks correct**

```bash
cd /Users/corbinshanks/Documents/gh-coding/musicmasters/apple-music-toolkit
python3 fix_track_numbers.py \
  --root ~/Music/foriPod/Media.localized/Music/Music \
  --dry-run --skip-mb 2>&1 | head -40
```

Expected: lines like `WOULD SET trkn=(1,0): Artist/Album/01 Track.m4a` for files missing track numbers.

**Step 3: Run Phase 1 for real (offline only first)**

```bash
python3 fix_track_numbers.py \
  --root ~/Music/foriPod/Media.localized/Music/Music \
  --skip-mb 2>&1 | tee /tmp/fix_phase1.log
tail -5 /tmp/fix_phase1.log
```

Expected: summary line like `Fixed: 4726  Skipped: 3595  Errors: 0`

**Step 4: Run Phase 2 (MusicBrainz totals) — this will take time**

```bash
python3 fix_track_numbers.py \
  --root ~/Music/foriPod/Media.localized/Music/Music \
  2>&1 | tee /tmp/fix_phase2.log
tail -10 /tmp/fix_phase2.log
```

Expected: per-album MusicBrainz lookups, summary of updated totals. Takes ~10-20 min for ~600 albums at 1 req/sec.

**Step 5: Commit**

```bash
cd /Users/corbinshanks/Documents/gh-coding/musicmasters/apple-music-toolkit
git add fix_track_numbers.py
git commit -m "feat: add fix_track_numbers script (filename parse + MusicBrainz total fill)"
```

---

### Task 3: Write `audit_missing_tracks.py`

**Files:**
- Create: `apple-music-toolkit/audit_missing_tracks.py`

**Step 1: Write the script**

```python
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
    python3 audit_missing_tracks.py \
        --root ~/Music/foriPod/Media.localized/Music/Music \
        --output output/ \
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

AUDIO_EXTS = {".m4a", ".mp4", ".mp3"}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_track_num(stem: str) -> str:
    """Remove leading track number from filename stem for title matching."""
    return re.sub(r"^\d+[\s.\-]+", "", stem).strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


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
            trck = str(audio.get("TRCK", ""))
            parts = trck.split("/")
            return (int(parts[0]) if parts[0] else 0, int(parts[1]) if len(parts) > 1 and parts[1] else 0)
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

def method_a(albums: dict) -> list[dict]:
    """
    Query MusicBrainz for every album. Compare canonical tracklist against
    files on disk by fuzzy title matching. Returns missing tracks with titles.
    """
    from mb_client import lookup_album

    missing = []
    total_albums = len(albums)
    for i, ((artist, album), tracks) in enumerate(albums.items(), 1):
        print(f"  [{i}/{total_albums}] {artist} / {album}", end="  ")
        result = lookup_album(artist, album)
        if not result:
            print("no MB match")
            continue

        canonical = result["tracks"]
        # Build set of normalized on-disk title stems
        disk_titles = {normalize(strip_track_num(p.stem)) for p in tracks}

        for t in canonical:
            ct_norm = normalize(t["title"])
            # Exact match
            if ct_norm in disk_titles:
                continue
            # Fuzzy match: is there any disk title with similarity >= 0.80?
            matched = any(similarity(ct_norm, d) >= 0.80 for d in disk_titles)
            if not matched:
                missing.append({
                    "artist": artist,
                    "album": album,
                    "track_num": t["num"],
                    "track_title": t["title"],
                    "source": "musicbrainz",
                })

        gaps = len([t for t in canonical if normalize(t["title"]) not in disk_titles])
        print(f"{gaps} missing" if gaps else "complete")

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
    return sorted(by_key.values(), key=lambda r: (r["artist"].casefold(), r["album"].casefold(), r["track_num"]))


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
        artist = r["artist"]
        album = r["album"]
        tree.setdefault(artist, {}).setdefault(album, []).append({
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

    # Summary by artist
    by_artist: dict[str, int] = defaultdict(int)
    for r in all_missing:
        by_artist[r["artist"]] += 1
    print("\nTop 15 artists with most missing tracks:")
    for artist, count in sorted(by_artist.items(), key=lambda x: x[1], reverse=True)[:15]:
        print(f"  {count:>4}  {artist}")


if __name__ == "__main__":
    main()
```

**Step 2: Dry-run audit with Method C only (fast, offline)**

```bash
cd /Users/corbinshanks/Documents/gh-coding/musicmasters/apple-music-toolkit
python3 audit_missing_tracks.py \
  --root ~/Music/foriPod/Media.localized/Music/Music \
  --output output/ \
  --skip-mb 2>&1 | tail -20
```

Expected: Method C summary, CSV + JSON written to `output/`.

**Step 3: Spot-check output**

```bash
head -20 output/missing_tracks.csv
python3 -c "
import json
data = json.load(open('output/missing_tracks.json'))
artists = list(data.keys())[:5]
for a in artists:
    for alb, tracks in data[a].items():
        print(f'{a} / {alb}: {len(tracks)} missing')
"
```

**Step 4: Run full audit with MusicBrainz (takes time — ~10-30 min)**

```bash
python3 audit_missing_tracks.py \
  --root ~/Music/foriPod/Media.localized/Music/Music \
  --output output/ \
  2>&1 | tee /tmp/audit.log
tail -20 /tmp/audit.log
```

**Step 5: Commit**

```bash
cd /Users/corbinshanks/Documents/gh-coding/musicmasters/apple-music-toolkit
git add audit_missing_tracks.py output/
git commit -m "feat: add audit_missing_tracks script and initial missing tracks report"
```

---

### Task 4: Update requirements.txt and README

**Files:**
- Modify: `apple-music-toolkit/requirements.txt`
- Modify: `apple-music-toolkit/README.md`

**Step 1: Add `musicbrainzngs` to requirements if needed**

Check if `requests` alone is sufficient (it is — we use raw HTTP, not the `musicbrainzngs` wrapper). No changes to `requirements.txt` needed.

**Step 2: Add entries to README.md for the two new scripts**

Under the existing script list, add:

```markdown
### fix_track_numbers.py
Writes `trkn` (track number/total) metadata to files missing it.
- Phase 1: parses leading digits from filenames (offline, fast)
- Phase 2: queries MusicBrainz to fill in track totals (online, cached)

```
python3 fix_track_numbers.py --root ~/Music/path/to/Music [--dry-run] [--skip-mb]
```

### audit_missing_tracks.py
Identifies albums with missing tracks and writes CSV + JSON reports.
- Method C: detects gaps using on-disk trkn metadata (offline)
- Method A: queries MusicBrainz for canonical tracklists and fuzzy-matches against disk (online, cached)

```
python3 audit_missing_tracks.py --root ~/Music/path/to/Music --output output/ [--skip-mb]
```
```

**Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document fix_track_numbers and audit_missing_tracks scripts"
```
