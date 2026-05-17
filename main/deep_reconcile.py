import plistlib
import json
import os
from pathlib import Path
from collections import defaultdict

CACHE_FILE = Path(__file__).parent.parent / ".audio_fingerprints.json"
CLOUD_XML = Path("~/Desktop/amCloud.xml").expanduser()
REPORT_PATH = Path(__file__).parent.parent / "output" / "deep_sync_report.txt"

def normalize_string(s):
    if not s: return ""
    import re
    return re.sub(r"[^a-zA-Z0-9]", "", str(s)).lower()

def main():
    if not CACHE_FILE.exists():
        print("Error: Fingerprint cache not found. Run acoustic_audit.py first.")
        return

    print("Loading Cloud XML and Fingerprint Cache...")
    with open(CLOUD_XML, 'rb') as f:
        cloud_data = plistlib.load(f)
    with open(CACHE_FILE, 'r') as f:
        fingerprints = json.load(f)

    # 1. Map fingerprints to local paths
    # Some fingerprints might have multiple paths (duplicates)
    fp_to_local = defaultdict(list)
    for path, fp in fingerprints.items():
        if os.path.exists(path):
            fp_to_local[fp].append(path)

    cloud_tracks = cloud_data.get('Tracks', {})
    
    matched = []
    recoverable = [] # Exists on disk but metadata/filename is wrong
    missing = []    # Not found on disk at all

    print(f"Analyzing {len(cloud_tracks)} Cloud tracks...")

    for tid, t in cloud_tracks.items():
        name = t.get('Name', 'Unknown')
        artist = t.get('Artist', 'Unknown')
        album = t.get('Album', 'Unknown')
        
        # Look for the file where Apple thinks it should be
        loc = t.get('Location')
        local_path = None
        if loc:
            import urllib.parse
            local_path = urllib.parse.unquote(loc.replace('file://', ''))
        
        if local_path and os.path.exists(local_path):
            matched.append((name, artist, local_path))
            continue

        # IF NOT AT LOCATION: Check by metadata fingerprint (Strict)
        # (This handles if you moved the file but the XML is old)
        found_by_fp = False
        # We'll use the acoustic fingerprints to find it
        # Note: Ideally we'd have Cloud fingerprints, but since we don't, 
        # we check if any "extra" local file has metadata matching this cloud track.
        
        # For now, we search our local scan for a metadata match
        for path, fp in fingerprints.items():
            # This is a bit slow but thorough for a one-time report
            if (normalize_string(name) in normalize_string(path) and 
                normalize_string(artist) in normalize_string(path)):
                recoverable.append({
                    'name': name,
                    'artist': artist,
                    'album': album,
                    'found_at': path,
                    'reason': 'Filename/Path mismatch'
                })
                found_by_fp = True
                break
        
        if not found_by_fp:
            missing.append((name, artist, album))

    # Write Report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, 'w') as f:
        f.write("DEEP SYNC AUDIT REPORT\n")
        f.write("="*50 + "\n\n")
        f.write(f"SUMMARY:\n")
        f.write(f"- Cloud Tracks:  {len(cloud_tracks)}\n")
        f.write(f"- Perfect Match: {len(matched)}\n")
        f.write(f"- Recoverable:   {len(recoverable)} (Found on disk, but paths differ)\n")
        f.write(f"- Truly Missing: {len(missing)}\n\n")

        if recoverable:
            f.write("RECOVERABLE TRACKS (Move/Rename these to match Cloud):\n")
            for item in recoverable:
                f.write(f"[!] {item['artist']} - {item['name']}\n")
                f.write(f"    Current: {item['found_at']}\n")
                f.write(f"    Target:  {item['album']}\n\n")

        if missing:
            f.write("\n" + "="*50 + "\n")
            f.write("TRULY MISSING (Not found on disk):\n")
            by_artist = defaultdict(list)
            for n, art, alb in missing:
                by_artist[art].append(f"{alb} - {n}")
            
            for art in sorted(by_artist.keys()):
                f.write(f"\n[ ] {art}:\n")
                for track in sorted(by_artist[art]):
                    f.write(f"    - {track}\n")

    print(f"Audit Complete! Report saved to: {REPORT_PATH}")

if __name__ == "__main__":
    main()
