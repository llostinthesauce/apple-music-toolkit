import json
import os
import plistlib
import requests
import time
from pathlib import Path
from collections import defaultdict

# Paths
CACHE_FILE = Path(__file__).parent.parent / ".audio_fingerprints.json"
LOOKUP_CACHE = Path(__file__).parent.parent / ".acoustic_lookup_cache.json"
CLOUD_XML = Path("~/Desktop/amCloud.xml").expanduser()
LOG_FILE = Path(__file__).parent.parent / "output" / "acoustic_cloud_sync_log.txt"

# AcoustID API Key — register at https://acoustid.org/new-application
API_KEY = os.environ.get("ACOUSTID_API_KEY", "")
if not API_KEY:
    raise SystemExit("Missing AcoustID key. Set ACOUSTID_API_KEY in your environment.")

def normalize_string(s):
    if not s: return ""
    import re
    return re.sub(r"[^a-zA-Z0-9]", "", str(s)).lower()

def lookup_fingerprint(fingerprint, duration=120):
    """Lookup fingerprint on AcoustID using Multipart POST to avoid URL limits."""
    url = "https://api.acoustid.org/v2/lookup"
    
    # By passing data through the 'files' parameter (even as strings), 
    # requests is forced to use multipart/form-data, which never appends to the URL.
    fields = {
        "client": (None, API_KEY),
        "format": (None, "json"),
        "duration": (None, str(int(duration))),
        "fingerprint": (None, fingerprint),
        "meta": (None, "recordings releases")
    }
    try:
        response = requests.post(url, files=fields, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        if "414" in str(e):
            print("  [!] Fingerprint STILL too long for this API endpoint. Skipping track.")
        else:
            print(f"Lookup error: {e}")
        return None

def main():
    if not CACHE_FILE.exists():
        print("Error: Fingerprint cache not found.")
        return

    print("Loading Cloud XML and Local Fingerprints...")
    with open(CLOUD_XML, 'rb') as f:
        cloud_data = plistlib.load(f)
    with open(CACHE_FILE, 'r') as f:
        local_fps = json.load(f)

    # Load lookup cache
    lookup_cache = {}
    if LOOKUP_CACHE.exists():
        with open(LOOKUP_CACHE, 'r') as f:
            lookup_cache = json.load(f)

    print(f"Starting AcoustID Identification for {len(local_fps)} local files...")
    
    # Identify Cloud tracks for comparison
    cloud_tracks = cloud_data.get('Tracks', {})
    cloud_set = set()
    for tid, t in cloud_tracks.items():
        name = normalize_string(t.get('Name'))
        artist = normalize_string(t.get('Artist'))
        if name and artist:
            cloud_set.add((artist, name))

    matches_found = 0
    
    # We only want to lookup fingerprints that aren't already identified as cloud matches
    # or aren't in our lookup cache.
    for path, fp in local_fps.items():
        if not os.path.exists(path): continue
        
        # Check if already identified
        if fp in lookup_cache:
            results = lookup_cache[fp]
        else:
            print(f"Identifying: {Path(path).name}")
            results = lookup_fingerprint(fp)
            if results:
                lookup_cache[fp] = results
                with open(LOOKUP_CACHE, 'w') as f:
                    json.dump(lookup_cache, f)
                time.sleep(0.5) # Avoid hammering API
            else:
                continue

        # Check if identification matches any Cloud tracks
        if results.get('status') == 'ok' and results.get('results'):
            for res in results['results']:
                for recording in res.get('recordings', []):
                    rec_title = normalize_string(recording.get('title'))
                    for artist_info in recording.get('artists', []):
                        rec_artist = normalize_string(artist_info.get('name'))
                        
                        if (rec_artist, rec_title) in cloud_set:
                            # WE HAVE A MATCH! 
                            # Local file 'path' matches Cloud track (rec_artist, rec_title)
                            # Now we need to find the target location from the XML
                            
                            target_loc = None
                            for tid, t in cloud_tracks.items():
                                if normalize_string(t.get('Name')) == rec_title and normalize_string(t.get('Artist')) == rec_artist:
                                    target_loc = t.get('Location')
                                    break
                            
                            if target_loc:
                                import urllib.parse
                                target_path = Path(urllib.parse.unquote(target_loc.replace('file://', '')))
                                if not target_path.exists():
                                    print(f"  [FOUND] Local file: {Path(path).name}")
                                    print(f"          Target:     {target_path}")
                                    
                                    # Perform the move
                                    try:
                                        target_path.parent.mkdir(parents=True, exist_ok=True)
                                        import shutil
                                        shutil.move(path, str(target_path))
                                        matches_found += 1
                                        # Update our local record so we don't try to move it again
                                        local_fps[str(target_path)] = local_fps.pop(path)
                                    except Exception as e:
                                        print(f"          Error moving: {e}")
                                    break # Found recording match
                if matches_found > 0: break # Move to next file

    print(f"\nDeep Acoustic Sync Complete!")
    print(f"Total tracks recovered and matched: {matches_found}")

if __name__ == "__main__":
    main()
