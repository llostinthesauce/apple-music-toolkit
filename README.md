# Apple Music Toolkit (AMT)

A collection of Python and AppleScript utilities for managing, cleaning, and exporting Apple Music library data.

## Features & Utilities

### Metadata Management
* **`main/auto_merge_albums.py`**: Automatically merges album variations (e.g., removing "[Deluxe Edition]" or "(Bonus Track Version)") to unify tracks under a single album identifier.
* **`main/find_album_merges.py`**: Dry-run utility to identify and list potential album merges without modifying the database.
* **`main/fix_compilations.py`**: Identifies split compilations and soundtracks, automatically applying the "Compilation" flag and setting the Album Artist to "Various Artists".
* **`main/force_album_artist.py`**: Finds tracks with a blank "Album Artist" field and populates it with the "Artist" value.
* **`main/refresh_album.py`**: Force-refreshes an album in the Music app by purging existing library entries and re-scanning the physical folder from disk. Useful for fixing "ghost" tracks or metadata mismatches.
* **`main/enrich_metadata.py`**: Reads an exported Apple Music XML file and updates the current library's Genre and Year metadata to match.

### Spotify Integration
* **`main/spotify.py`**: Imports an Apple Music XML export directly into Spotify. Supports migrating playlists and saving tracks to "Liked Songs" while handling API rate limiting and local caching.

### Library Maintenance (via `amt.sh`)
* **Align**: Corrects track numbering and renames files to a standard format.
* **Polish**: Audits embedded artwork and album artist tags.
* **Audit**: Scans for missing tracks, gaps, and file corruption based on local metadata.
* **Wholeness**: (`main/check_wholeness.py`) Scans your library and cross-references albums against the MusicBrainz API to identify missing tracks and duplicates.
* **History**: Restores Play Counts and Star Ratings from XML backups.

## Usage

Many core features can be accessed via the interactive shell script:

```bash
./amt.sh
```

Individual Python utilities can be run directly:

```bash
python main/spotify.py --source "Library.xml" --library --playlists
python main/auto_merge_albums.py
```

## Requirements

Ensure dependencies are installed before running the Python scripts:

```bash
pip install -r requirements.txt
```

For Spotify integration, you must configure a `.env` file with your Spotify API credentials (`SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI`).
---

*Part of [@llostinthesauce](https://github.com/llostinthesauce)'s portfolio*
