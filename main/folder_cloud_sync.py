import json
import os
import plistlib
import shutil
import urllib.parse
from pathlib import Path

# Paths
CLOUD_XML = Path("~/Desktop/amCloud.xml").expanduser()
LOG_FILE = Path(__file__).parent.parent / "output" / "folder_sync_log.txt"

def normalize_string(s):
    if not s: return ""
    import re
    return re.sub(r"[^a-z0-9]", "", str(s).lower())

def main():
    print("Loading Cloud Blueprint...")
    with open(CLOUD_XML, 'rb') as f:
        cloud_data = plistlib.load(f)
    
    cloud_blueprint = {} 
    for tid, t in cloud_data.get('Tracks', {}).items():
        name = t.get('Name')
        artist = t.get('Artist')
        album = t.get('Album', 'Unknown Album')
        loc = t.get('Location')
        if not name or not artist: continue
        
        if not loc:
            loc = str(Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music"))) / artist / album / f"{name}.m4a")
        else:
            loc = urllib.parse.unquote(loc.replace('file://', ''))

        key = (normalize_string(artist), normalize_string(name))
        cloud_blueprint[key] = Path(loc)

    root = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))
    print(f"Scanning disk: {root}...")
    
    recovered = 0
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    with open(LOG_FILE, 'w') as log:
        log.write("FOLDER SYNC LOG\n" + "="*30 + "\n")

        for path in root.glob("**/*.m4a"):
            if path.name.startswith("._"): continue
            
            p_full_path = normalize_string(str(path)) # Normalize the WHOLE path
            p_filename = normalize_string(path.name)
            
            matched_target = None
            for (c_artist, c_name), target_path in cloud_blueprint.items():
                # NEW LOGIC: Artist must be in the FOLDER path, Title must be in the FILENAME
                if c_artist in p_full_path and c_name in p_filename:
                    if path.resolve() == target_path.resolve():
                        continue
                    if not target_path.exists():
                        matched_target = target_path
                        break
            
            if matched_target:
                try:
                    matched_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(matched_target))
                    log.write(f"[FOUND] {path.name}\n        -> {matched_target}\n\n")
                    recovered += 1
                    if recovered % 50 == 0:
                        print(f"  Progress: {recovered} tracks aligned...")
                except Exception as e:
                    log.write(f"[ERR] {e}\n")
            
    print(f"\nComplete! Aligned {recovered} tracks.")

if __name__ == "__main__":
    main()
