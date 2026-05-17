import os
import argparse
import requests
import time
from pathlib import Path
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
import mutagen
from collections import defaultdict

AUDIO_EXTS = {".m4a", ".mp3"}
MUSICBRAINZ_API_BASE = "https://musicbrainz.org/ws/2/"
USER_AGENT = "MusicWholenessChecker/1.0.0 ( mailto:corbin@example.com )"

import re

def normalize_string(s):
    """Normalize string for fuzzy-ish matching."""
    if not s: return ""
    # Remove punctuation, spaces, and lowercase
    return re.sub(r"[^a-zA-Z0-9]", "", s).lower()

def get_musicbrainz_tracks(artist, album):
    """Fetch tracklist for an album from MusicBrainz."""
    headers = {"User-Agent": USER_AGENT}
    params = {
        "query": f'artist:"{artist}" AND release:"{album}"',
        "fmt": "json"
    }
    
    try:
        # Search for the release
        response = requests.get(f"{MUSICBRAINZ_API_BASE}release", params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if not data.get("releases"):
            return None
            
        # Get the first/best match
        release_id = data["releases"][0]["id"]
        
        # Get detailed release info including media/tracks
        time.sleep(1) # Respect Rate Limit (1 req/sec)
        response = requests.get(f"{MUSICBRAINZ_API_BASE}release/{release_id}", params={"inc": "recordings", "fmt": "json"}, headers=headers)
        response.raise_for_status()
        release_info = response.json()
        
        expected_tracks = []
        for medium in release_info.get("media", []):
            for track in medium.get("tracks", []):
                title = track.get("title")
                pos = track.get("position")
                expected_tracks.append({"title": title, "position": pos})
        
        return expected_tracks
    except Exception as e:
        print(f"Error fetching from MusicBrainz for {artist} - {album}: {e}")
        return None

def scan_and_check(root_path):
    lib_root = Path(root_path)
    albums = defaultdict(list)
    
    print(f"Scanning library: {lib_root}")
    for path in lib_root.glob("**/*"):
        if path.suffix.lower() in AUDIO_EXTS and not path.name.startswith("._"):
            try:
                audio = mutagen.File(path)
                if audio:
                    artist = ""
                    album = ""
                    title = ""
                    
                    if isinstance(audio, MP4):
                        artist = audio.tags.get("\xa9ART", [""])[0]
                        album = audio.tags.get("\xa9alb", [""])[0]
                        title = audio.tags.get("\xa9nam", [""])[0]
                    elif isinstance(audio, MP3):
                        artist = str(audio.get("TPE1", ""))
                        album = str(audio.get("TALB", ""))
                        title = str(audio.get("TIT2", ""))
                    
                    if artist and album:
                        albums[(artist, album)].append({
                            "path": path,
                            "title": title
                        })
            except Exception as e:
                print(f"Error reading {path}: {e}")

    print(f"Found {len(albums)} unique albums. Checking against MusicBrainz...")
    
    report_file = Path(__file__).parent.parent / "output" / "wholeness_report.txt"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(report_file, "w") as f:
        f.write(f"Wholeness Audit - {time.ctime()}\n")
        f.write(f"Root: {lib_root}\n")
        f.write("="*40 + "\n")
        
        for (artist, album), local_tracks in albums.items():
            print(f"\nChecking: {artist} - {album}")
            f.write(f"\nChecking: {artist} - {album}\n")
            expected = get_musicbrainz_tracks(artist, album)
            
            if not expected:
                msg = "  [!] Could not find album on MusicBrainz."
                print(msg)
                f.write(msg + "\n")
                continue
                
            # Check for duplicates (local)
            titles_seen = defaultdict(int)
            for t in local_tracks:
                titles_seen[normalize_string(t["title"])] += 1
                
            duplicates = [t["title"] for t in local_tracks if titles_seen[normalize_string(t["title"])] > 1]
            duplicates = list(set(duplicates))
            
            if duplicates:
                msg = f"  [D] Possible Duplicates found locally: {duplicates}"
                print(msg)
                f.write(msg + "\n")
                
            # Check for missing
            local_normalized = {normalize_string(t["title"]) for t in local_tracks}
            missing = []
            for ext in expected:
                if normalize_string(ext["title"]) not in local_normalized:
                    missing.append(f"{ext['position']}. {ext['title']}")
                    
            if missing:
                msg = "  [M] Missing tracks (according to MusicBrainz):"
                print(msg)
                f.write(msg + "\n")
                for m in missing:
                    print(f"      - {m}")
                    f.write(f"      - {m}\n")
            else:
                if not duplicates:
                    msg = "  [✓] Album is complete and has no duplicates."
                    print(msg)
                    f.write(msg + "\n")
    
    print(f"\nDetailed report saved to: {report_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan library and check against MusicBrainz API.")
    parser.add_argument("--root", required=True, help="Root folder of the library")
    args = parser.parse_args()
    
    scan_and_check(args.root)
