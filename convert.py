"""
convert.py — Apple Music XML library → local M3U playlist files.

WHAT IT DOES
------------
Reads an Apple Music XML library export (File → Library → Export Library…),
matches every track in every user playlist against a local iTunes-structured
music folder (Artist/Album/Title.ext), and writes one .m3u file per playlist.

Also produces two gap-analysis files:
  - unmatched_tracks.txt    flat list of every unmatched track with playlist context
  - missing_by_artist.txt   deduplicated missing tracks grouped Artist → Album → Song,
                            for easy identification of what you still need to acquire

MATCHING STRATEGY
-----------------
1. Primary:  (artist_norm, album_norm, title_norm) exact lookup against folder index
2. Fallback: title-only scan across the entire index (disabled by default; enable
             via match_track(..., title_fallback=True) if you want more lenient matching)

Normalization strips accents, lowercases, removes punctuation, and collapses
whitespace — handles common differences between Apple Music metadata and local
filenames. Leading track numbers (e.g. "01 Song Name.mp3") are also stripped.

USAGE
-----
    python3 convert.py \\
        --source ~/AppleMusicLibrary.xml \\
        --local  ~/Music/iTunes/iTunes\\ Media/Music \\
        --output ~/Playlists

OUTPUT
------
    ~/Playlists/
      Playlist Name.m3u       (one per playlist with at least one matched track)
      unmatched_tracks.txt
      missing_by_artist.txt

DEPENDENCIES
------------
    pip install -r requirements.txt   (mutagen, for tag-based future fallback)
    Python 3.9+
"""

import unicodedata
import re
import plistlib
import argparse
import sys
from pathlib import Path


def normalize(text: str | None) -> str:
    """Lowercase, strip accents, remove punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


_SYSTEM_PLAYLIST_KEYS = {"Master", "Music Videos", "Movies", "TV Shows",
                          "Podcasts", "Audiobooks", "Voice Memos",
                          "Purchased", "Downloaded"}

def parse_library(xml_path) -> tuple[dict, list]:
    """
    Parse an Apple Music XML library export.
    Returns:
        tracks: dict of {track_id: {title, artist, album, duration_ms}}
        playlists: list of {name, track_ids}
    """
    with open(xml_path, "rb") as f:
        data = plistlib.load(f)

    tracks = {}
    for track_id_str, info in data.get("Tracks", {}).items():
        track_id = int(track_id_str)
        tracks[track_id] = {
            "title": info.get("Name", ""),
            "artist": info.get("Artist", ""),
            "album": info.get("Album", ""),
            "duration_ms": info.get("Total Time", 0),
        }

    playlists = []
    for pl in data.get("Playlists", []):
        if pl.get("Master") or pl.get("Distinguished Kind") or pl.get("Name") in _SYSTEM_PLAYLIST_KEYS:
            continue
        name = pl.get("Name", "Untitled Playlist")
        track_ids = [
            item["Track ID"]
            for item in pl.get("Playlist Items", [])
            if "Track ID" in item
        ]
        playlists.append({"name": name, "track_ids": track_ids})

    return tracks, playlists


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".aac", ".ogg", ".wav", ".aiff", ".wma"}

def build_local_index(music_root: Path) -> dict:
    """
    Walk music library recursively.
    Supports: Artist/Album/Title.ext OR Artist/Title.ext OR any depth.
    Returns dict: (artist_norm, album_norm, title_norm) -> Path
    """
    index = {}
    # Recursively find all files with supported extensions
    for track_file in music_root.rglob("*"):
        if track_file.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        
        # Skip hidden macOS metadata files (resource forks)
        if track_file.name.startswith(".") or track_file.name.startswith("._"):
            continue
        
        # Determine Artist and Album based on path relative to music_root
        try:
            relative_path = track_file.relative_to(music_root)
        except ValueError:
            continue
            
        parts = relative_path.parts
        
        artist_norm = ""
        album_norm = ""
        
        if len(parts) >= 3:
            # Standard structure: Artist/Album/Title.ext
            artist_norm = normalize(parts[0])
            album_norm = normalize(parts[1])
        elif len(parts) == 2:
            # Shallow structure: Artist/Title.ext
            artist_norm = normalize(parts[0])
            album_norm = ""
        
        # Strip leading track numbers (e.g. "01 ", "12 ") and replace
        # underscores used as filename-safe substitutes for ?, :, etc.
        stem = re.sub(r"^\d+\s+", "", track_file.stem).replace("_", " ")
        title_norm = normalize(stem)
        
        key = (artist_norm, album_norm, title_norm)
        index[key] = track_file.resolve()
    return index


def match_track(track: dict, index: dict, title_fallback: bool = True):
    """
    Look up a track in the local index.
    1. Primary: (artist, album, title) normalized key.
    2. Secondary: (artist, "", title) normalized key (for shallow folders).
    3. Fuzzy: Try title match if artist is very similar.
    Returns absolute Path or None.
    """
    artist_n = normalize(track.get("artist", ""))
    album_n = normalize(track.get("album", ""))
    title_n = normalize(track.get("title", ""))

    # Try Artist/Album/Title
    key = (artist_n, album_n, title_n)
    if key in index:
        return index[key]

    # Try Artist/Title (ignoring album)
    key_no_album = (artist_n, "", title_n)
    if key_no_album in index:
        return index[key_no_album]

    if title_fallback and title_n:
        # Check for title match where artist is identical or very similar
        for (a, al, t), path in index.items():
            if t == title_n:
                # Basic fuzzy artist check: is the artist string part of our index artist?
                # (Handles "Artist" vs "Artist feat. Someone")
                if artist_n in a or a in artist_n:
                    return path

    return None


def _safe_filename(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores."""
    return re.sub(r'[<>:"/\\|?*]', "_", name)

def write_m3u(output_dir: Path, playlist_name: str, tracks: list, local_root: Path = None, prefix: str = None) -> Path:
    """
    Write a .m3u playlist file.
    tracks: list of {path, artist, title, duration_ms}
    Returns the path of the written file.
    """
    filename = _safe_filename(playlist_name) + ".m3u"
    out_path = output_dir / filename
    lines = ["#EXTM3U"]
    for t in tracks:
        duration_sec = t["duration_ms"] // 1000
        lines.append(f'#EXTINF:{duration_sec},{t["artist"]} - {t["title"]}')
        
        path_str = str(t["path"])
        if local_root and prefix:
            try:
                rel_path = t["path"].relative_to(local_root.resolve())
                # Convert path separators to forward slash for Linux/Unix compatibility
                path_str = f"{prefix.rstrip('/')}/{rel_path.as_posix()}"
            except ValueError:
                pass # Fallback to original path if not relative
                
        lines.append(path_str)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def write_missing_by_artist(output_dir: Path, misses: list) -> Path:
    """
    Write missing_by_artist.txt grouped by artist → album → songs.
    misses: list of {playlist, artist, album, title}
    """
    out_path = output_dir / "missing_by_artist.txt"
    if not misses:
        out_path.write_text("No unmatched tracks.\n", encoding="utf-8")
        return out_path

    # Deduplicate by (artist, album, title) — same song may appear in multiple playlists
    seen = set()
    unique = []
    for m in misses:
        key = (m["artist"], m["album"], m["title"])
        if key not in seen:
            seen.add(key)
            unique.append(m)

    # Build nested structure: artist -> album -> [titles]
    from collections import defaultdict
    tree: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for m in unique:
        artist = m["artist"] or "Unknown Artist"
        album = m["album"] or "Unknown Album"
        tree[artist][album].append(m["title"])

    lines = []
    for artist in sorted(tree, key=str.casefold):
        lines.append(f"{artist}")
        for album in sorted(tree[artist], key=str.casefold):
            lines.append(f"  {album}")
            for title in sorted(tree[artist][album], key=str.casefold):
                lines.append(f"    - {title}")
        lines.append("")  # blank line between artists

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def write_report(output_dir: Path, misses: list) -> Path:
    """
    Write unmatched_tracks.txt.
    misses: list of {playlist, artist, title}
    """
    out_path = output_dir / "unmatched_tracks.txt"
    if not misses:
        out_path.write_text("No unmatched tracks.\n", encoding="utf-8")
        return out_path
    lines = [f'{m["playlist"]} | {m["artist"]} - {m["title"]}' for m in misses]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Convert Apple Music XML playlists to local M3U files."
    )
    parser.add_argument("--source", required=True, type=Path,
                        help="Path to Apple Music XML library export")
    parser.add_argument("--local", required=True, type=Path,
                        help="Path to local iTunes-structured music folder")
    parser.add_argument("--output", required=True, type=Path,
                        help="Directory to write M3U files and report into")
    parser.add_argument("--prefix", required=False, type=str,
                        help="Remote path prefix to replace the local root (e.g., /mnt/music/foriPod)")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Error: source file not found: {args.source}", file=sys.stderr)
        sys.exit(1)
    if not args.local.is_dir():
        print(f"Error: local library folder not found: {args.local}", file=sys.stderr)
        sys.exit(1)

    args.output.mkdir(parents=True, exist_ok=True)

    print("Parsing Apple Music library...")
    tracks, playlists = parse_library(args.source)
    print(f"  Found {len(tracks)} tracks, {len(playlists)} playlists")

    print("Indexing local library...")
    index = build_local_index(args.local)
    print(f"  Indexed {len(index)} local tracks")

    misses = []
    for playlist in playlists:
        matched = []
        for track_id in playlist["track_ids"]:
            track = tracks.get(track_id)
            if not track:
                continue
            local_path = match_track(track, index)
            if local_path:
                matched.append({
                    "path": local_path,
                    "artist": track["artist"],
                    "title": track["title"],
                    "duration_ms": track["duration_ms"],
                })
            else:
                misses.append({
                    "playlist": playlist["name"],
                    "artist": track["artist"],
                    "album": track["album"],
                    "title": track["title"],
                })
        if matched:
            write_m3u(args.output, playlist["name"], matched, local_root=args.local, prefix=args.prefix)
        print(f"  {playlist['name']}: {len(matched)}/{len(playlist['track_ids'])} matched")

    write_report(args.output, misses)
    write_missing_by_artist(args.output, misses)
    print(f"\nDone. Output written to: {args.output}")
    print(f"Unmatched tracks: {len(misses)} (see unmatched_tracks.txt and missing_by_artist.txt)")

if __name__ == "__main__":
    main()
