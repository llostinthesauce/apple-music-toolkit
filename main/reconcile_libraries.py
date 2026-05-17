import plistlib
import os
import argparse
from pathlib import Path
from collections import defaultdict

def normalize_string(s):
    if not s: return ""
    import re
    return re.sub(r"[^a-zA-Z0-9]", "", str(s)).lower()

def get_track_fingerprint(track):
    """Create a unique-ish string for a track based on metadata."""
    artist = normalize_string(track.get('Artist', 'Unknown'))
    album = normalize_string(track.get('Album', 'Unknown'))
    name = normalize_string(track.get('Name', 'Unknown'))
    # Optional: include track number for better precision
    num = track.get('Track Number', 0)
    return f"{artist}|{album}|{name}|{num}"

def load_xml(path):
    print(f"Loading {path}...")
    with open(path, 'rb') as f:
        return plistlib.load(f)

def reconcile(cloud_xml_path, local_xml_path):
    cloud_data = load_xml(cloud_xml_path)
    local_data = load_xml(local_xml_path)

    cloud_tracks = cloud_data.get('Tracks', {})
    local_tracks = local_data.get('Tracks', {})

    cloud_map = {}
    local_map = {}

    print("Fingerprinting Cloud library...")
    for tid, track in cloud_tracks.items():
        fp = get_track_fingerprint(track)
        cloud_map[fp] = track

    print("Fingerprinting Local library...")
    for tid, track in local_tracks.items():
        fp = get_track_fingerprint(track)
        local_map[fp] = track

    cloud_fps = set(cloud_map.keys())
    local_fps = set(local_map.keys())

    missing_in_local = cloud_fps - local_fps
    extra_in_local = local_fps - cloud_fps
    matched = cloud_fps & local_fps

    # Output report
    report_path = Path(__file__).parent.parent / "output" / "library_reconciliation.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with open(report_path, "w") as f:
        f.write(f"Library Reconciliation Report\n")
        f.write(f"Cloud XML: {cloud_xml_path}\n")
        f.write(f"Local XML: {local_xml_path}\n")
        f.write("="*50 + "\n\n")

        f.write(f"SUMMARY:\n")
        f.write(f"- Cloud Tracks: {len(cloud_fps)}\n")
        f.write(f"- Local Tracks: {len(local_fps)}\n")
        f.write(f"- Matched:      {len(matched)}\n")
        f.write(f"- Missing Local: {len(missing_in_local)} (In Cloud, not in library)\n")
        f.write(f"- Extra Local:   {len(extra_in_local)} (In library, not in Cloud)\n\n")

        if missing_in_local:
            f.write("MISSING IN LOCAL (FORIPOD):\n")
            # Group by artist for readability
            by_artist = defaultdict(list)
            for fp in missing_in_local:
                t = cloud_map[fp]
                by_artist[t.get('Artist', 'Unknown')].append(f"{t.get('Album', 'Unknown')} - {t.get('Name', 'Unknown')}")
            
            for artist in sorted(by_artist.keys()):
                f.write(f"\n[ ] {artist}:\n")
                for track_info in sorted(by_artist[artist]):
                    f.write(f"    - {track_info}\n")

        if extra_in_local:
            f.write("\n" + "="*50 + "\n")
            f.write("EXTRA IN LOCAL (NOT IN CLOUD):\n")
            by_artist_extra = defaultdict(list)
            for fp in extra_in_local:
                t = local_map[fp]
                by_artist_extra[t.get('Artist', 'Unknown')].append(f"{t.get('Album', 'Unknown')} - {t.get('Name', 'Unknown')}")
            
            for artist in sorted(by_artist_extra.keys()):
                f.write(f"\n[+] {artist}:\n")
                for track_info in sorted(by_artist_extra[artist]):
                    f.write(f"    - {track_info}\n")

    print(f"\nReconciliation complete!")
    print(f"Found {len(missing_in_local)} missing and {len(extra_in_local)} extra tracks.")
    print(f"Detailed report saved to: {report_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile Cloud vs Local music libraries.")
    parser.add_argument("--cloud", required=True, help="Path to Apple Music Cloud XML")
    parser.add_argument("--local", required=True, help="Path to library Local XML")
    args = parser.parse_args()

    reconcile(args.cloud, args.local)
