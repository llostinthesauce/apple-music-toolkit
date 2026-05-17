#!/usr/bin/env python3
"""
Automated Lyrics Fetcher

Recursively scans a directory of audio files, attempts to extract the 
Artist/Title metadata, queries the free LRCLIB API for synced/plain lyrics,
and permanently embeds them into the files' ID3/MP4 metadata tags (USLT).
"""

import argparse
import sys
import time
from pathlib import Path

try:
    import requests
    import mutagen
    from mutagen.id3 import ID3NoHeaderError, ID3, USLT, error
    from mutagen.mp4 import MP4
    from mutagen.flac import FLAC
except ImportError:
    print("Error: Missing required packages.")
    print("Run: pip3 install requests mutagen")
    sys.exit(1)

# Only scanning formats Mutagen handles cleanly
SUPPORTED_EXTS = {".mp3", ".m4a", ".flac"}

def fetch_lrclib_lyrics(artist: str, track_name: str, album_name: str, duration_sec: int) -> str | None:
    """Hits the lrclib.net API for lyrics."""
    url = "https://lrclib.net/api/get"
    params = {
        "artist_name": artist,
        "track_name": track_name,
        "album_name": album_name,
        "duration": duration_sec
    }
    
    # Strip blanks
    params = {k: v for k, v in params.items() if v}

    try:
        response = requests.get(url, params=params, headers={"User-Agent": "AppleMusicToolkit/1.0"})
        if response.status_code == 200:
            data = response.json()
            # Prefer plainLyrics for standard media players over syncedLyrics
            return data.get("plainLyrics") or data.get("syncedLyrics")
            
        elif response.status_code == 404:
            return None # Expected if not found
            
        elif response.status_code == 429:
            print("  Rate limited by LRCLIB! Sleeping 10 seconds...")
            time.sleep(10)
            return fetch_lrclib_lyrics(artist, track_name, album_name, duration_sec)
            
    except requests.RequestException:
        pass
        
    return None

def process_file(file_path: Path, overwrite: bool) -> bool:
    """Extract tags, fetch lyrics, embed if found. Returns True on success."""
    ext = file_path.suffix.lower()
    artist, title, album, duration = "", "", "", 0

    try:
        audio = mutagen.File(file_path)
        if not audio:
            return False
            
        duration = int(audio.info.length) if hasattr(audio.info, "length") else 0

        if ext == ".mp3":
            # MP3 / ID3
            if audio.tags is None:
                audio.add_tags()
            
            if not overwrite and any(isinstance(frame, USLT) for frame in audio.tags.values()):
                print(f"  Skipped {file_path.name} (Lyrics present)")
                return False
                
            title = audio.tags.get("TIT2", [""])[0]
            artist = audio.tags.get("TPE1", [""])[0]
            album = audio.tags.get("TALB", [""])[0]

        elif ext == ".m4a":
            # AAC / M4A
            if not overwrite and "\xa9lyr" in audio.tags:
                print(f"  Skipped {file_path.name} (Lyrics present)")
                return False
                
            title = audio.tags.get("\xa9nam", [""])[0]
            artist = audio.tags.get("\xa9ART", [""])[0]
            album = audio.tags.get("\xa9alb", [""])[0]

        elif ext == ".flac":
            # FLAC
            if not overwrite and "lyrics" in audio.tags:
                print(f"  Skipped {file_path.name} (Lyrics present)")
                return False
                
            title = audio.tags.get("title", [""])[0]
            artist = audio.tags.get("artist", [""])[0]
            album = audio.tags.get("album", [""])[0]

        # Bail if no standard metadata
        if not artist or not title:
            print(f"  Skipped {file_path.name} (Missing 'Title' or 'Artist' ID3 tags)")
            return False

        # Attempt API Fetch
        lyrics = fetch_lrclib_lyrics(artist, title, album, duration)
        
        if not lyrics:
            print(f"  Not Found: '{title}' by {artist}")
            return False

        # Embed lyrics back into the file
        if ext == ".mp3":
            audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
            audio.save()
        elif ext == ".m4a":
            audio.tags["\xa9lyr"] = [lyrics]
            audio.save()
        elif ext == ".flac":
            audio.tags["lyrics"] = [lyrics]
            audio.save()

        print(f"  SUCCESS: {title} by {artist}")
        # Small delay to respect LRCLIB public API
        time.sleep(1)
        return True

    except Exception as e:
        print(f"  Error processing {file_path.name}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Fetch and embed lyrics to M4A/MP3 tags from LRCLIB")
    parser.add_argument("--root", required=True, type=Path, help="Root directory to scan")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing lyrics tags")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Error: Does not exist or is not a directory: {args.root}")
        sys.exit(1)

    print(f"Scanning {args.root} for audio files...")
    
    success_count = 0
    total_count = 0
    
    for file_path in args.root.rglob("*"):
        if file_path.suffix.lower() in SUPPORTED_EXTS and not file_path.name.startswith("._"):
            total_count += 1
            if process_file(file_path, args.overwrite):
                success_count += 1
            
    print(f"\nDone. Successfully added lyrics to {success_count} / {total_count} files.")

if __name__ == "__main__":
    main()
