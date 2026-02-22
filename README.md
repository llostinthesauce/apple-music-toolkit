# Playlist Toolset

A collection of Python scripts for managing and synchronizing a local music library (e.g., for an iPod) with an Apple Music/iCloud library.

## Scripts

### 1. `convert.py`
Matches Apple Music XML playlists against a local music folder and generates `.m3u` playlist files.
- Supports recursive scanning of your local library.
- Uses fuzzy matching to handle minor metadata differences (e.g., "Remastered").
- **Usage:**
  ```bash
  python3 convert.py --source AppleMusic.xml --local /path/to/music --output ./playlists
  ```

### 2. `cross_check.py`
Identifies the gap between your cloud library and your local collection.
- Compares two XML exports (Cloud vs. Local).
- Outputs a ranked list of missing albums.
- **Usage:**
  ```bash
  python3 cross_check.py --cloud Cloud.xml --local Local.xml --output missing.txt
  ```

### 3. `merge_staging.py`
Moves music from a "staging" folder into your main library using atomic moves (`os.rename`).
- Fast and requires zero additional disk space.
- **Usage:**
  ```bash
  python3 merge_staging.py --source ~/staging --dest /path/to/music
  ```

### 4. `tag_from_folders.py`
Writes Artist, Album, and Title metadata tags to `.m4a` files based on their folder structure.
- **Usage:**
  ```bash
  python3 tag_from_folders.py --root /path/to/music
  ```

### 5. `fetch_album_art.py`
Scans folders for missing `AlbumArt.jpg`, fetches the front cover from MusicBrainz/CoverArtArchive, and embeds it into the audio files.
- **Usage:**
  ```bash
  python3 fetch_album_art.py --root /path/to/music
  ```

## Requirements
- Python 3.9+
- `plistlib` (standard library)
- `mutagen` (for `tag_from_folders.py`, `fetch_album_art.py`)
- `requests` (for `fetch_album_art.py`)

## Installation
```bash
pip install -r requirements.txt
```

---
*Created for the MusicMasters project.*
