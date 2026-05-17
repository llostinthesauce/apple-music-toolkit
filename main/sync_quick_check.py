import json
import os
import plistlib
import urllib.parse
from pathlib import Path

CLOUD_XML = Path("~/Desktop/amCloud.xml").expanduser()
LOCAL_XML = Path("~/Desktop/Library.xml").expanduser()

def normalize_string(s):
    if not s: return ""
    import re
    return re.sub(r"[^a-zA-Z0-9]", "", str(s)).lower()

def main():
    print("Loading Cloud Blueprint...")
    with open(CLOUD_XML, 'rb') as f:
        cloud_data = plistlib.load(f)
    
    cloud_tracks = cloud_data.get('Tracks', {})
    cloud_map = {}
    
    print(f"Total tracks in XML: {len(cloud_tracks)}")
    
    for tid, t in cloud_tracks.items():
        name = t.get('Name')
        artist = t.get('Artist')
        album = t.get('Album', 'Unknown Album')
        
        # If no location exists, we infer what the path SHOULD be 
        # based on Apple standard: Music/Artist/Album/XX Name.m4a
        loc = t.get('Location')
        if not loc:
            # Construct a theoretical path for matching purposes
            # We will use this to "fix" local files that match the metadata
            theoretical_path = str(Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music"))) / artist / album / f"{name}.m4a")
            loc = theoretical_path # Store it so we have a target
            
        norm_name = normalize_string(name)
        norm_artist = normalize_string(artist)
        if norm_name and norm_artist:
            cloud_map[(norm_artist, norm_name)] = loc

    print(f"Cloud mapping built with {len(cloud_map)} tracks.")

    # Check music folder directly for things that exist but might not be in Music app
    root = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))
    found = 0
    total_files = 0
    
    print(f"Scanning disk: {root}...")
    for path in root.glob("**/*.m4a"):
        total_files += 1
        p_name = normalize_string(path.name)
        
        # Simple name match check
        for (c_artist, c_name), c_loc in cloud_map.items():
            if c_artist in p_name and c_name in p_name:
                target_path = Path(urllib.parse.unquote(c_loc.replace('file://', '')))
                if path.resolve() != target_path.resolve() and not target_path.exists():
                    print(f"MATCH: {path.name}")
                    print(f"  -> {target_path}")
                    found += 1
                    break
    
    print(f"\nDone. Found {found} obvious matches out of {total_files} files.")

if __name__ == "__main__":
    main()
