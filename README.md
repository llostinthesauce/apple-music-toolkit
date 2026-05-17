# Apple Music Toolkit

Scripts and utilities for managing an ALAC music library and syncing to iPods.

## Quick Start
```bash
git clone <this-repo>
cd apple-music-toolkit
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # then edit .env with your paths
./amt.sh                # interactive menu
```

## Requirements
```bash
pip install -r requirements.txt
```
Key deps: mutagen, rapidfuzz, requests. Built-in: json, plistlib, subprocess, urllib.

External tools: ffmpeg, ffprobe, sips (macOS), Apple Music.app (for XML export).

## Environment Variables

Set via `.env` file (copy `.env.example`):

| Variable | Purpose | Default |
|---|---|---|
| `MUSIC_ROOT` | Library root (Artist/Album/ track structure) | `~/Music` |
| `XML_DEFAULT` | Apple Music XML export | `~/Desktop/Library.xml` |
| `IPOD_MOUNT` | iPod mount point | `/Volumes/iPod` |
| `OUTPUT_DIR` | Report output directory | `./output/` |
| `STAGING_DIR` | Download pipeline staging | `~/Downloads/_staging` |
| `LOUDNESS_REPORT` | Loudness scan CSV | `./output/loudness_report.csv` |

## Script Index

### Library Detection & Auditing
| Script | What it does |
|---|---|
| `scan_library.py` | ffprobe all tracks to JSON (size, bitrate, duration, art dimensions) |
| `track_swap_detector.py` | AcoustID fingerprinting to find wrong audio files |
| `duration_check.py` | MusicBrainz duration comparison vs actual track lengths |
| `validate.py` | Full library validation: dupes, missing art, metadata issues |
| `audit.py` | Scans for missing tracks, gaps, file corruption |
| `check_wholeness.py` | Cross-reference albums against MusicBrainz for missing tracks |
| `loudness_scan.py` | Volume analysis across library (ffmpeg volumedetect) |

### Download & Import
| Script | What it does |
|---|---|
| `download_pipeline.py` | FLAC to ALAC conversion, tagging, artwork embedding |
| `tag_staging.py` | Tag files in staging before import |
| `import_to_music.py` | Auto-import tracks into Apple Music |
| `transcode.py` | Batch transcode ALAC to AAC for iPod sync |

### Playlists
| Script | What it does |
|---|---|
| `build_playlists.py` | Rebuild .m3u8 playlists from Music XML via fuzzy matching |
| `playlists.py` | Rebuild playlists directly in Music via AppleScript |
| `export.py` | Export current playlists to .m3u files |

### Artwork
| Script | What it does |
|---|---|
| `art_fix.py` | Fetch missing artwork from iTunes API + MusicBrainz |
| `art.py` | Artwork utilities |
| `compress_art.py` | Resize oversized artwork to 600x600 |
| `fixup_art.py` | Embed cover art from staging into existing m4a files |

### Metadata & Organization
| Script | What it does |
|---|---|
| `fix_compilations.py` | Fix album artist tags on compilation albums |
| `fix_track_numbers.py` | Correct track numbering |
| `fix.py` | General fix utility (dupes, art, metadata) |
| `force_album_artist.py` | Populate blank album artist from artist tag |
| `enrich_metadata.py` | Sync genre/year from XML export |
| `strip_meta_prefixes.py` | Remove `(feat. ...)` and edition suffixes from album names |
| `auto_merge_albums.py` | Merge album variations (deluxe/standard editions) |
| `find_album_merges.py` | Identify potential album merges (dry-run) |

### iPod Sync
| Script | What it does |
|---|---|
| `build_sync_library.py` | Build hard-linked + silence-split library for iPod sync |
| `compress_large_tracks.py` | Identify tracks above size thresholds |

### Cloud & Reconciliation
| Script | What it does |
|---|---|
| `cloud_diff.py` | Diff local vs cloud library |
| `reconcile_libraries.py` | Compare two Music XML exports |
| `acoustic_cloud_sync.py` | Match AcoustID fingerprints between local and cloud |
| `history.py` | Restore play counts and ratings from XML backups |

### Utilities
| Script | What it does |
|---|---|
| `ingest.py` | Ingest new music into library |
| `dedupe.py` | Find and handle duplicate tracks |
| `purge_junk.py` | Remove orphaned files and empty folders |
| `polish.py` | Full cleanup pass (artwork, tags, compilations) |
| `refresh_library.py` | Force Music to re-scan library |
| `lyrics.py` | Fetch lyrics from online sources |
| `spotify.py` | Migrate playlists to Spotify |
