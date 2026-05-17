import plistlib
import os
import shutil
import urllib.parse
from pathlib import Path

# Paths
CACHE_FILE = Path(__file__).parent.parent / ".audio_fingerprints.json"
CLOUD_XML = Path("~/Desktop/amCloud.xml").expanduser()
LOG_FILE = Path(__file__).parent.parent / "output" / "sync_fix_log.txt"

def normalize_string(s):
    if not s: return ""
    import re
    return re.sub(r"[^a-zA-Z0-9]", "", str(s)).lower()

def main():
    print("Loading Cloud Blueprint...")
    with open(CLOUD_XML, 'rb') as f:
        cloud_data = plistlib.load(f)
    
    with open(CACHE_FILE, 'r') as f:
        fingerprints = json.load(f)

    cloud_tracks = cloud_data.get('Tracks', {})
    
    print(f"Applying fixes for {len(cloud_tracks)} Cloud tracks...")
    
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    fixes_applied = 0
    errors = 0

    with open(LOG_FILE, 'w') as log:
        log.write("SYNC FIX LOG\n" + "="*30 + "\n")

        for tid, t in cloud_tracks.items():
            loc = t.get('Location')
            if not loc: continue
            
            # Target path (where Apple wants it)
            target_path = Path(urllib.parse.unquote(loc.replace('file://', '')))
            
            # If it's already there, skip
            if target_path.exists():
                continue

            name = t.get('Name', 'Unknown')
            artist = t.get('Artist', 'Unknown')

            # Search fingerprints for this track (using metadata match from deep_reconcile logic)
            found_source = None
            for path, fp in fingerprints.items():
                if (normalize_string(name) in normalize_string(path) and 
                    normalize_string(artist) in normalize_string(path)):
                    if os.path.exists(path):
                        found_source = Path(path)
                        break
            
            if found_source:
                try:
                    # Create the target directory
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Move the file
                    # If target exists (rare conflict), don't overwrite
                    if not target_path.exists():
                        shutil.move(str(found_source), str(target_path))
                        log.write(f"[FIX] Moved: {found_source.name}\n      To:    {target_path}\n\n")
                        fixes_applied += 1
                    else:
                        log.write(f"[SKIP] Conflict: {target_path} already exists.\n")
                except Exception as e:
                    log.write(f"[ERR] Failed to move {found_source}: {e}\n")
                    errors += 1

    print(f"\nFixing Complete!")
    print(f"- Fixes Applied: {fixes_applied}")
    print(f"- Errors:        {errors}")
    print(f"Detailed log saved to: {LOG_FILE}")

import json
if __name__ == "__main__":
    main()
