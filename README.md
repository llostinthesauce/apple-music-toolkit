# Apple Music Toolkit (AMT)

A comprehensive, modular suite of Python utilities designed to manage, synchronize, and migrate local music libraries bridging the gap between Apple Music (iCloud) and offline ecosystems (e.g., iPods, Navidrome servers, Spotify).

## Overview

The toolkit is designed to be completely standalone. Rather than expecting users to memorize command-line parameters for 10 different scripts, the suite features a unified, interactive terminal interface. 

To launch the suite, simply execute the `amt.sh` wrapper script:

```bash
./amt.sh
```

This will launch a guided ASCII menu that dynamically prompts the user for required file paths, format preferences, and advanced configurations, automatically constructing and executing the underlying Python commands. All generated files natively route to the `outputs/` directory.

## Core Capabilities & Phases

The interactive menu is structurally organized into three logical phases of digital library maintenance:

### Phase 1: Local Library Maintenance & Tagging
This phase focuses on grooming the physical files on your hard drive to ensure maximum compatibility with offline media players and third-party servers.

1. **Fetch Missing Album Art** (`fetch_album_art.py`)
   Scans directories for missing `AlbumArt.jpg` files, fetches the highest quality front covers from MusicBrainz or CoverArtArchive APIs, and embeds them directly into the audio files.
2. **Tag Metadata from Folders** (`tag_from_folders.py`)
   A forceful metadata recovery tool that writes `Artist`, `Album`, and `Title` internal ID3/M4A tags based strictly on your folder structure hierarchy (e.g., `Artist/Album/Song.ext`).
3. **Merge Staging to Main Lib** (`merge_staging.py`)
   Moves newly sorted music from a temporary "staging" folder into your main collection using rapid atomic filesystem operations, seamlessly resolving conflicts.
4. **FLAC to AAC/ALAC Lossless Converter** (`convert_lossless.py`)
   Recursively transcodes heavyweight lossless files (`.flac`, `.wav`, `.aiff`) to universally compatible, smaller footprint formats (`.m4a`, `.mp3`). Supports custom bit depths (16-bit/24-bit) and sample rates (44.1kHz), making it ideal for standardizing high-resolution audio for legacy Apple devices while preserving all original metadata. *(Requires `ffmpeg`)*.
5. **Acoustic Duplicate Finder** (`find_audio_duplicates.py`)
   Identifies duplicate audio tracks by analyzing their actual audio waveforms via Chromaprint/AcoustID audio fingerprinting, bypassing the need for identical filenames or metadata. Allows the user to automatically prune the lower-quality duplicates to reclaim disk space. *(Requires `acoustid` package and internet access)*.
6. **Fetch & Embed Lyrics** (`fetch_lyrics.py`)
   Queries the LRCLIB public API for synced/unsynced lyrics based on internal ID3 metadata, then embeds the text permanently into the `USLT` tag for native playback display.

### Phase 2: Syncing & Cross-Checking Gap Analysis
This phase bridges the gap between your physical local files and your cloud streaming databases.

7. **Cross-Check Local vs. Cloud XML** (`cross_check.py`)
   Compares an Apple Music XML library export against a local Navidrome or iTunes XML database to identify gaps. Automatically outputs a ranked discrepancy text file into the `outputs/` directory.
8. **Convert Apple Music XML to M3U Playlists** (`convert.py`)
   A fuzzy-matching engine that ingests Apple Music XML playlists, maps the tracks against your physical local directory structure, and generates universally compatible `.m3u` playlist files in the `outputs/m3u_playlists/` directory.

### Phase 3: Platform Migration (Spotify)
This phase is designed for migrating curated libraries out of the Apple ecosystem and into Spotify.

9. **Import XML/Playlists to Spotify** (`import_to_spotify.py`)
   Performs a complete migration of an Apple Music XML library into a Spotify account, mapping tracks and recreating playlists natively via the Spotify API. 
   *(Note: Requires configuring `SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET` environment variables via a free Spotify Developer Application).*
10. **Extract M3U from Library XML** (`extract_m3u_from_library.py`)
    Extracts raw `.m3u` playlists straight from the XML source without altering them. These lists retain the raw `file:///` location paths exactly as Apple Music stores them. Dumps to the `outputs/m3u_raw/` directory.

## Requirements & Installation

- **Python 3.9+**
- System-level dependencies: `ffmpeg`, `chromaprint` (available via Homebrew on macOS or `apt` on Linux)
- Python packages (install via `pip3 install -r requirements.txt`):
  - `mutagen`
  - `spotipy`
  - `pydub`
  - `requests`
  - `pyacoustid`
