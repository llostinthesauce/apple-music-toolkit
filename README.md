# Apple Music Toolkit

Python and AppleScript utilities for managing, cleaning, and exporting an ALAC-based Apple Music library — including cloud reconciliation, acoustic fingerprinting, album wholeness checks against MusicBrainz/Tidal, artwork repair, and iPod sync.

## Quick Start

```bash
git clone https://github.com/llostinthesauce/apple-music-toolkit.git
cd apple-music-toolkit
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # then edit .env with your paths
cp sync_config.example.json sync_config.json   # only if using iPod sync
./amt.sh                                   # interactive menu
```

## Requirements

```bash
pip install -r requirements.txt
```

Key Python deps: `mutagen`, `rapidfuzz`, `requests`. Built-in: `json`, `plistlib`, `subprocess`, `urllib`.

External tools:
- `ffmpeg`, `ffprobe` — audio analysis and transcoding
- `fpcalc` (Chromaprint) — required by acoustic-fingerprint scripts
- `sips` — macOS-only, artwork resizing
- `Apple Music.app` — for XML library export and AppleScript control

## Environment Variables

Set via `.env` (copy from `.env.example`):

| Variable | Purpose | Default |
|---|---|---|
| `MUSIC_ROOT` | Library root (`Artist/Album/Track` structure) | `~/Music` |
| `XML_DEFAULT` | Apple Music XML export path | `~/Desktop/Library.xml` |
| `IPOD_MOUNT` | iPod mount point | `/Volumes/iPod` |
| `ACOUSTID_API_KEY` | Required for fingerprint scripts — register at [acoustid.org](https://acoustid.org/new-application) | _(unset)_ |
| `OUTPUT_DIR` | Report output directory | `./output/` |
| `STAGING_DIR` | Download pipeline staging | `~/Downloads/_staging` |
| `LOUDNESS_REPORT` | Loudness scan CSV path | `./output/loudness_report.csv` |
| `SCAN_RESULTS` | Override scan results location | `./output/scan_results.json` |
| `PLAYLISTS_OUT` | Override playlist export directory | `./output/playlists/` |
| `WHOLENESS_FILE` | MusicBrainz wholeness export path | _(unset)_ |

## Script Index

### Library Detection & Audit
| Script | What it does |
|---|---|
| `scan_library.py` | ffprobe all tracks to JSON (size, bitrate, duration, art dimensions) |
| `validate.py` | Full library validation: dupes, missing art, metadata issues |
| `audit.py` | Scan for missing tracks, gaps, and file corruption |
| `dedupe.py` | Find and handle duplicate tracks |
| `duration_check.py` | Compare actual track lengths against MusicBrainz |
| `loudness_scan.py` | Volume analysis across library (ffmpeg `volumedetect`) |
| `sync_quick_check.py` | Quick local-vs-cloud XML comparison |

### Acoustic Fingerprinting (AcoustID)
| Script | What it does |
|---|---|
| `acoustic_audit.py` | Build fingerprint cache for the library (uses `fpcalc`) |
| `track_swap_detector.py` | Identify mis-tagged files by fingerprint vs. metadata |
| `acoustic_cloud_sync.py` | Match local fingerprints to cloud XML and relocate files |
| `acoustic_cloud_sync_v2.py` | Iteration of `acoustic_cloud_sync` with truncated-fingerprint handling |

### Album Wholeness
| Script | What it does |
|---|---|
| `check_wholeness.py` | Cross-reference albums against MusicBrainz for missing tracks |
| `wholeness_tidal.py` | Same check against Tidal tracklists via the triton API (supersedes `check_wholeness.py`) |
| `apply_wholeness.py` | Apply fixes from a wholeness report — quarantines flagged tracks |
| `build_download_list.py` | Generate a download manifest from diff + wholeness reports |
| `fill.py` | Download missing tracks from the triton (Tidal) API into staging |

### Cloud Reconciliation
| Script | What it does |
|---|---|
| `cloud_diff.py` | Diff local library against cloud XML |
| `reconcile_libraries.py` | Compare two Music XML exports side-by-side |
| `deep_reconcile.py` | Fingerprint-aware reconciliation pass |
| `final_cloud_sync.py` | Last-pass cloud sync after fingerprint matching |
| `folder_cloud_sync.py` | Sync local folder structure to match cloud XML |
| `apply_sync_fixes.py` | Apply moves logged from a prior sync run |
| `undo_sync.py` | Reverse the last folder sync from its log |

### Download, Convert & Import
| Script | What it does |
|---|---|
| `download_pipeline.py` | FLAC → ALAC conversion, tagging, artwork embedding |
| `tag_staging.py` | Write tags on files in staging before import |
| `tag.py` | Write artist/album/title tags from folder structure (offline) |
| `import_to_music.py` | Auto-import tracks into Apple Music |
| `transcode.py` | Batch transcode ALAC → AAC for iPod sync |
| `ingest.py` | Ingest new music into the main library |

### Metadata & Organization
| Script | What it does |
|---|---|
| `fix.py` | General fix utility (dupes, art, metadata) |
| `fix_compilations.py` | Repair album-artist tags on compilation albums |
| `fix_track_numbers.py` | Correct track numbering from filenames + MusicBrainz |
| `force_album_artist.py` | Populate blank album-artist tag from artist tag |
| `enrich_metadata.py` | Sync genre/year from XML export back to files |
| `strip_meta_prefixes.py` | Remove `(feat. ...)` and edition suffixes from album-name tags |
| `strip_prefixes.py` | Remove leading track-number prefixes from filenames |
| `align.py` | Canonicalize library files using Apple Music XML data |
| `auto_merge_albums.py` | Merge album variations (deluxe/standard editions) |
| `find_album_merges.py` | Identify potential album merges (dry-run) |

### Artwork
| Script | What it does |
|---|---|
| `art_fix.py` | Fetch missing artwork from iTunes API + MusicBrainz |
| `art.py` | Artwork utilities and embedding helpers |
| `compress_art.py` | Resize oversized artwork to 600x600 |
| `fixup_art.py` | Embed cover art from staging into existing m4a files |

### Playlists
| Script | What it does |
|---|---|
| `build_playlists.py` | Rebuild `.m3u8` playlists from Music XML via fuzzy matching |
| `playlists.py` | Rebuild playlists directly inside Music via AppleScript |
| `export.py` | Export current playlists to `.m3u` files |
| `spotify.py` | Migrate playlists to Spotify (Spotipy-based) |

### iPod Sync
| Script | What it does |
|---|---|
| `build_sync_library.py` | Build hard-linked + silence-split library for iPod sync |
| `compress_large_tracks.py` | Identify and shrink tracks above size thresholds |
| `rebuild_itunesdb.py` | Rebuild a binary `iTunesDB` for iPod 5th/5.5-gen Video |

### Utilities
| Script | What it does |
|---|---|
| `purge_junk.py` | Remove orphaned files and empty folders |
| `polish.py` | Full cleanup pass (artwork, tags, compilations) |
| `refresh_library.py` | Force Music to re-scan the library |
| `refresh_album.py` | Purge and re-add a single album in Music |
| `lyrics.py` | Fetch lyrics from online sources |
| `history.py` | Restore play counts and ratings from XML backups |

### Shared Modules
| File | What it does |
|---|---|
| `engine.py` | MusicBrainz API client with rate limiting + disk cache; imported by other scripts |

## License

MIT — see [LICENSE](LICENSE).
