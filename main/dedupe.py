import os
import re
from pathlib import Path
from collections import defaultdict
import mutagen
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3

# Configuration
LIB_ROOT = Path("/Users/corbinshanks/Music/foriPod/Media.localized/Music/Music")
AUDIO_EXTS = {".m4a", ".mp3", ".mp4"}

def normalize(s):
    return re.sub(r'[^\w]', '', str(s)).lower().strip()

def get_info(path):
    try:
        audio = mutagen.File(path)
        if isinstance(audio, MP4):
            tags = audio.tags or {}
            return {
                "name": tags.get("\xa9nam", [""])[0],
                "artist": tags.get("\xa9ART", [""])[0],
                "album": tags.get("\xa9alb", [""])[0],
                "duration": int(audio.info.length),
                "bitrate": int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
            }
        elif isinstance(audio, MP3):
            tags = audio.tags or {}
            return {
                "name": str(tags.get("TIT2", "")),
                "artist": str(tags.get("TPE1", "")),
                "album": str(tags.get("TALB", "")),
                "duration": int(audio.info.length),
                "bitrate": int(audio.info.bitrate) if audio.info.bitrate else 0
            }
    except:
        pass
    return None

def main():
    print(f"Starting Deep Duplicate Audit on {LIB_ROOT}...")
    
    all_tracks = []
    for path in LIB_ROOT.glob("**/*"):
        if path.suffix.lower() in AUDIO_EXTS and not path.name.startswith("._"):
            info = get_info(path)
            if info:
                info["path"] = path
                all_tracks.append(info)

    print(f"Auditing {len(all_tracks)} tracks...")

    dup_groups = defaultdict(list)
    for t in all_tracks:
        key = (normalize(t['name']), normalize(t['artist']))
        dup_groups[key].append(t)

    true_duplicates = []
    multi_release = []
    live_versions = []

    for key, tracks in dup_groups.items():
        if len(tracks) < 2:
            continue
            
        albums = set(normalize(t['album']) for t in tracks)
        durations = [t['duration'] for t in tracks]
        max_dur_diff = max(durations) - min(durations)

        if len(albums) > 1:
            multi_release.append(tracks)
        elif max_dur_diff > 10:
            live_versions.append(tracks)
        else:
            true_duplicates.append(tracks)

    print("\n--- DUPLICATE AUDIT RESULTS ---")
    print(f"Total Song Clusters with multiple copies: {len(true_duplicates) + len(multi_release) + len(live_versions)}")
    print(f"1. TRUE DUPLICATES (Same file/version): {len(true_duplicates)}")
    print(f"2. CROSS-ALBUM COPIES (Greatest Hits / Comps): {len(multi_release)}")
    print(f"3. VERSION VARIATIONS (Live / Remixes): {len(live_versions)}")

    if true_duplicates:
        print("\nTOP TRUE DUPLICATES (Safe to potentially delete):")
        for group in true_duplicates[:15]:
            t = group[0]
            print(f" - {t['artist']} - {t['name']} ({len(group)} copies in {t['album']})")
            for instance in group:
                print(f"    -> {instance['path'].name} ({instance['bitrate']}kbps)")

    if multi_release:
        print("\nCROSS-ALBUM SAMPLES (Same song, different releases):")
        for group in sorted(multi_release, key=len, reverse=True)[:10]:
            t = group[0]
            print(f" - {t['artist']} - {t['name']} ({len(group)} releases):")
            for instance in group:
                print(f"    -> Album: {instance['album']}")

if __name__ == "__main__":
    main()
