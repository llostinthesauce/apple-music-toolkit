# Track Numbering Fix & Missing Track Audit — Design

Date: 2026-02-25

## Problem

- 56% of ~8,321 audio files in `foriPod` are missing `trkn` metadata (track number/total)
- Some albums have incomplete track sets (files missing from disk)
- Existing `tag_from_folders.py` fixed artist/album/title but not track numbers
- Library path: `~/Music/foriPod/Media.localized/Music/Music/`

## Solution: Two New Scripts

### Script 1: `fix_track_numbers.py`

Writes `trkn` metadata to files missing it.

**Phase 1 (offline):** Parse leading digits from filename (`01 Track.m4a` → trkn=1).
**Phase 2 (MusicBrainz):** For albums with trkn total=0, query MB API to fill in canonical track count. Cached to `~/.cache/amt_mb_cache.json`. Rate-limited to 1 req/sec.

CLI: `python3 fix_track_numbers.py --root /path/to/Music [--dry-run] [--skip-mb]`

### Script 2: `audit_missing_tracks.py`

Identifies albums with missing tracks and outputs reports.

**Method C (offline):** Flag albums where `trkn` total > 0 but file count < total.
**Method A (MusicBrainz):** Query canonical tracklist per album, fuzzy-match against files on disk to find missing track titles/numbers.

Output: `output/missing_tracks.csv` + `output/missing_tracks.json`

CLI: `python3 audit_missing_tracks.py --root /path/to/Music --output output/ [--skip-mb]`

## What Is Not Changed

- `tag_from_folders.py` — untouched, already handled artist/album/title
- `music_audit.py` — untouched, different scope (activity-based spot checks)
