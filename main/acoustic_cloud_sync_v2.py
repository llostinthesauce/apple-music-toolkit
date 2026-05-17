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
LOOKUP_CACHE = Path(__file__).parent.parent / ".acoustic_lookup_cache.json"
CLOUD_XML = Path("~/Desktop/amCloud.xml").expanduser()
LOG_FILE = Path(__file__).parent.parent / "output" / "acoustic_sync_v2_log.txt"

# AcoustID API Key — register at https://acoustid.org/new-application
API_KEY = os.environ.get("ACOUSTID_API_KEY", "")
if not API_KEY:
    raise SystemExit("Missing AcoustID key. Set ACOUSTID_API_KEY in your environment.")

def normalize_string(s):
    if not s: return ""
    import re
    return re.sub(r"[^a-zA-Z0-9]", "", str(s)).lower()

def lookup_fingerprint(fingerprint, duration=120):
    """Lookup fingerprint on AcoustID using Multipart POST."""
    url = "https://api.acoustid.org/v2/lookup"
    
    # If fingerprint is extremely long, truncate it slightly to stay within reasonable limits
    # AcoustID usually only needs the first part of the fingerprint for identification.
    if len(fingerprint) > 10000:
        fingerprint = fingerprint[:10000]

    fields = {
        "client": (None, API_KEY),
        "format": (None, "json"),
        "duration": (None, str(int(duration))),
        "fingerprint": (None, fingerprint),
        "meta": (None, "recordings releases")
    }
    try:
        response = requests.post(url, files=fields, timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return None

def main():
    if not CACHE_FILE.exists():
        print("Error: Fingerprint cache not found.")
        return

    print("Loading Cloud Blueprint and Local Fingerprints...")
    with open(CLOUD_XML, 'rb') as f:
        cloud_data = plistlib.load(f)
    with open(CACHE_FILE, 'r') as f:
        local_fps = json.load(f)

    lookup_cache = {}
    if LOOKUP_CACHE.exists():
        with open(LOOKUP_CACHE, 'r') as f:
            lookup_cache = json.load(f)

    # 1. Map Cloud tracks for fast lookup
    cloud_tracks = cloud_data.get('Tracks', {})
    cloud_set = {} # (artist, name) -> Location
    for tid, t in cloud_tracks.items():
        name = normalize_string(t.get('Name'))
        artist = normalize_string(t.get('Artist'))
        loc = t.get('Location')
        if name and artist and loc:
            cloud_set[(artist, name)] = loc

    print(f"Starting Smart Sync for {len(local_fps)} files...")
    
    recovered = 0
    
    with open(LOG_FILE, 'w') as log:
        log.write("DEEP SYNC V2 LOG\n" + "="*30 + "\n")

        for path, fp in local_fps.items():
            if not os.path.exists(path): continue
            
            p = Path(path)
            # STRATEGY A: Metadata match first (Fast)
            # If the filename already contains artist/title, match it directly to Cloud
            matched_loc = None
            norm_path = normalize_string(p.name)
            
            for (c_artist, c_name), c_loc in cloud_set.items():
                if c_artist in norm_path and c_name in norm_path:
                    # Potential match found by name
                    target_path = Path(urllib.parse.unquote(c_loc.replace('file://', '')))
                    if not target_path.exists():
                        matched_loc = target_path
                        break
            
            # STRATEGY B: Acoustic match (If strategy A failed)
            if not matched_loc:
                results = lookup_cache.get(fp)
                if not results:
                    results = lookup_fingerprint(fp)
                    if results:
                        lookup_cache[fp] = results
                        with open(LOOKUP_CACHE, 'w') as f:
                            json.dump(lookup_cache, f)
                        time.sleep(0.3)
                
                if results and results.get('status') == 'ok':
                    for res in results.get('results', []):
                        for rec in res.get('recordings', []):
                            r_title = normalize_string(rec.get('title'))
                            for art in rec.get('artists', []):
                                r_artist = normalize_string(art.get('name'))
                                if (r_artist, r_title) in cloud_set:
                                    target_path = Path(urllib.parse.unquote(cloud_set[(r_artist, r_title)].replace('file://', '')))
                                    if not target_path.exists():
                                        matched_loc = target_path
                                        break
                            if matched_loc: break
                        if matched_loc: break

            # Apply the move if matched
            if matched_loc:
                try:
                    matched_loc.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(path, str(matched_loc))
                    log.write(f"[FOUND] {p.name} -> {matched_loc}\n")
                    recovered += 1
                    if recovered % 10 == 0:
                        print(f"  Progress: {recovered} tracks recovered...")
                except Exception as e:
                    log.write(f"[ERR] Move failed: {e}\n")

    print(f"\nDeep Sync Complete! Recovered {recovered} tracks.")
    print(f"Log: {LOG_FILE}")

if __name__ == "__main__":
    main()
