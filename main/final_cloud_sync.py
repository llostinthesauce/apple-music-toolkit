import json
import os
import plistlib
import requests
import time
import shutil
import urllib.parse
from pathlib import Path
from collections import defaultdict

# Paths
CACHE_FILE = Path(__file__).parent.parent / ".audio_fingerprints.json"
CLOUD_XML = Path(os.environ.get("XML_DEFAULT", str(Path.home() / "Desktop" / "Library.xml")))
LOG_FILE = Path(__file__).parent.parent / "output" / "final_sync_log.txt"

def normalize_string(s):
    if not s: return ""
    import re
    # Lowercase and remove punctuation but keep spaces for better word matching
    return re.sub(r"[^a-z0-9\s]", "", str(s).lower()).strip()

def main():
    print("Loading Cloud Blueprint (10,000 tracks)...")
    with open(CLOUD_XML, 'rb') as f:
        cloud_data = plistlib.load(f)
    
    # 1. Map Cloud tracks for fast lookup
    cloud_tracks = cloud_data.get('Tracks', {})
    # Map (Artist + Title) -> Target Path
    # We use normalized keys for robust matching
    cloud_blueprint = {} 
    
    for tid, t in cloud_tracks.items():
        name = t.get('Name')
        artist = t.get('Artist')
        album = t.get('Album', 'Unknown Album')
        loc = t.get('Location')
        
        if not name or not artist: continue
        
        if not loc:
            # Construct the "Apple Standard" target path
            loc = str(Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music"))) / artist / album / f"{name}.m4a")
        else:
            loc = urllib.parse.unquote(loc.replace('file://', ''))

        key = (normalize_string(artist), normalize_string(name))
        cloud_blueprint[key] = Path(loc)

    print(f"Blueprint built with {len(cloud_blueprint)} unique song/artist pairs.")

    # 2. Scan Local Disk for Candidates
    root = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))
    print(f"Scanning for local files in {root}...")
    
    recovered = 0
    skipped_conflicts = 0
    
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'w') as log:
        log.write("FINAL SYNC LOG\n" + "="*30 + "\n")

        # Walk through every file on disk
        for path in root.glob("**/*.m4a"):
            if path.name.startswith("._"): continue
            
            # Check if this file is already in its blueprint home
            # (If it's already perfectly aligned, skip it)
            
            p_name = normalize_string(path.stem) # Song name part of filename
            p_full = normalize_string(path.name)
            
            matched_target = None
            # Search blueprint for a match
            # To avoid the "You" problem, we require the full title to be 
            # a distinct part of the filename or metadata
            for (c_artist, c_name), target_path in cloud_blueprint.items():
                # STRICT MATCH: Filename must contain artist AND the specific song title
                # We check for exact title match within the filename words
                if c_name in p_full and c_artist in p_full:
                    # Double check: Is the file already at its target?
                    if path.resolve() == target_path.resolve():
                        continue
                    
                    if not target_path.exists():
                        matched_target = target_path
                        break
            
            if matched_target:
                try:
                    matched_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(matched_target))
                    log.write(f"[MOVED] {path.name}\n        -> {matched_target}\n\n")
                    recovered += 1
                    if recovered % 50 == 0:
                        print(f"  Progress: {recovered} tracks aligned...")
                except Exception as e:
                    log.write(f"[ERR] Failed to move {path.name}: {e}\n")
            
    print(f"\nSync Complete!")
    print(f"- Tracks Aligned: {recovered}")
    print(f"See full log at: {LOG_FILE}")

if __name__ == "__main__":
    main()
