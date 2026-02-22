"""
fetch_album_art.py — scan for missing AlbumArt.jpg and fetch from CoverArtArchive (MusicBrainz).

Requires:
    pip install mutagen requests

Usage:
    python3 fetch_album_art.py --root /path/to/music [--dry-run]
"""

import argparse
import os
import requests
import time
from pathlib import Path
from mutagen.mp4 import MP4, MP4Cover

# MB API requires a User-Agent
USER_AGENT = "MusicMasters/1.0 (https://github.com/corbinshanks/musicmasters)"
AUDIO_EXTS = {".m4a", ".mp4"}
MB_SEARCH_URL = "https://musicbrainz.org/ws/2/release/"
CAA_URL = "https://coverartarchive.org/release/"

def search_mbid(artist, album):
    """Search MusicBrainz for a release MBID."""
    params = {
        "query": f'artist:"{artist}" AND release:"{album}"',
        "fmt": "json",
        "limit": 1
    }
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(MB_SEARCH_URL, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        releases = data.get("releases", [])
        if releases:
            return releases[0]["id"]
    except Exception as e:
        print(f"  [ERROR MB] {e}")
    return None

def fetch_caa_image_url(mbid):
    """Get the front cover image URL from CoverArtArchive."""
    url = f"{CAA_URL}{mbid}"
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        for image in data.get("images", []):
            if image.get("front") and image.get("image"):
                return image["image"]
    except Exception as e:
        print(f"  [ERROR CAA] {e}")
    return None

def download_image(url, album_dir):
    """Download image and return its path."""
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(url, headers=headers, stream=True)
        response.raise_for_status()
        
        # Detect extension
        ext = ".jpg"
        content_type = response.headers.get("Content-Type", "")
        if "png" in content_type.lower() or url.lower().endswith(".png"):
            ext = ".png"
        
        target_path = album_dir / f"AlbumArt{ext}"
        with open(target_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return target_path
    except Exception as e:
        print(f"  [ERROR DL] {e}")
    return None

def embed_art(audio_path, image_path):
    """Embed image into .m4a/.mp4 file."""
    try:
        audio = MP4(audio_path)
        with open(image_path, "rb") as f:
            image_data = f.read()
        
        # Determine format
        cover_format = MP4Cover.FORMAT_JPEG
        if str(image_path).lower().endswith(".png"):
            cover_format = MP4Cover.FORMAT_PNG
            
        audio["covr"] = [MP4Cover(image_data, imageformat=cover_format)]
        audio.save()
        return True
    except Exception as e:
        print(f"  [ERROR EMBED] {audio_path.name}: {e}")
    return False

def main():
    parser = argparse.ArgumentParser(description="Fetch and embed missing album art.")
    parser.add_argument("--root", required=True, type=Path, help="Root music folder (Artist/Album)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no downloads or writes")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if AlbumArt.jpg exists")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.exists():
        print(f"Error: {root} not found")
        return

    processed = fetched = embedded = 0

    # MusicBrainz asks for max 1 request per second
    RATE_LIMIT_DELAY = 1.1

    for artist_dir in sorted(root.iterdir()):
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        artist = artist_dir.name

        for album_dir in sorted(artist_dir.iterdir()):
            if not album_dir.is_dir() or album_dir.name.startswith("."):
                continue
            album = album_dir.name
            
            # Check for existing art (jpg or png)
            existing_art = list(album_dir.glob("AlbumArt.*"))
            if existing_art and not args.force:
                continue

            print(f"Checking {artist} - {album}...")
            
            if args.dry_run:
                print(f"  [DRY RUN] Would search MB for {artist} - {album}")
                continue

            mbid = search_mbid(artist, album)
            time.sleep(RATE_LIMIT_DELAY) # Respect MB rate limit
            
            if not mbid:
                print(f"  [SKIP] No MBID found for {artist} - {album}")
                continue

            image_url = fetch_caa_image_url(mbid)
            if not image_url:
                print(f"  [SKIP] No cover art found for MBID {mbid}")
                continue

            print(f"  Found art: {image_url}")
            art_path = download_image(image_url, album_dir)
            if art_path:
                fetched += 1
                # Embed in all audio files in the folder
                for track in sorted(album_dir.rglob("*")):
                    if track.suffix.lower() not in AUDIO_EXTS:
                        continue
                    if track.name.startswith("._"):
                        continue
                    if embed_art(track, art_path):
                        embedded += 1
                        print(f"    Embedded: {track.name}")

            processed += 1

    print("\nDone.")
    print(f"  Albums processed: {processed}")
    print(f"  Artwork fetched:  {fetched}")
    print(f"  Files embedded:   {embedded}")

if __name__ == "__main__":
    main()
