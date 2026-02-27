import plistlib
import mutagen
from pathlib import Path
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
import shutil
import re
import os
import argparse

# Default patterns to ignore
AUDIO_EXTS = {".m4a", ".mp3", ".mp4"}

def normalize(s):
    return re.sub(r'[^\w]', '', str(s)).lower().strip()

def main():
    parser = argparse.ArgumentParser(description="Canonicalize music library using Apple Music XML data.")
    parser.add_argument("--xml", required=True, help="Path to Apple Music Library XML")
    parser.add_argument("--root", required=True, help="Root directory of the music library")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run without renaming or tagging")
    args = parser.parse_args()

    xml_path = Path(args.xml)
    lib_root = Path(args.root)

    if not xml_path.exists():
        print(f"Error: XML not found at {xml_path}")
        return
    if not lib_root.exists():
        print(f"Error: Library root not found at {lib_root}")
        return

    print(f"Loading XML data from {xml_path}...")
    with open(xml_path, 'rb') as f:
        data = plistlib.load(f)
    
    xml_tracks = data.get('Tracks', {})
    # Map (Artist, Album, Name) -> (Track Num, Track Total)
    track_map = {}
    for tid, info in xml_tracks.items():
        name = normalize(info.get('Name', ''))
        artist = normalize(info.get('Artist', ''))
        album = normalize(info.get('Album', ''))
        num = info.get('Track Number', 0)
        total = info.get('Track Count', 0)
        if name and artist and num:
            track_map[(artist, album, name)] = (num, total)

    print(f"Parsed {len(track_map)} tracks with metadata from XML.")
    print(f"Scanning library at {lib_root}...")
    
    all_audio = []
    for path in lib_root.glob("**/*"):
        if path.suffix.lower() in AUDIO_EXTS and not path.name.startswith("._"):
            all_audio.append(path)

    print(f"Found {len(all_audio)} audio files to process.")

    fixed_count = 0
    temp_records = []
    
    # Phase 1: Tagging and Temporary Renaming (Unique IDs)
    for i, path in enumerate(all_audio):
        try:
            audio = mutagen.File(path)
            if not audio: continue
            
            if isinstance(audio, MP4):
                tags = audio.tags or {}
                name = tags.get("\xa9nam", [""])[0]
                artist = tags.get("\xa9ART", [""])[0]
                album = tags.get("\xa9alb", [""])[0]
            elif isinstance(audio, MP3):
                tags = audio.tags or {}
                name = str(tags.get("TIT2", ""))
                artist = str(tags.get("TPE1", ""))
                album = str(tags.get("TALB", ""))
            else: continue

            key = (normalize(artist), normalize(album), normalize(name))
            if key in track_map:
                num, total = track_map[key]
                
                if not args.dry_run:
                    # Update Tags
                    if isinstance(audio, MP4):
                        audio.tags["trkn"] = [(num, total)]
                        audio.save()
                    elif isinstance(audio, MP3):
                        from mutagen.id3 import TRCK
                        audio.tags.add(TRCK(encoding=3, text=f"{num}/{total}" if total else str(num)))
                        audio.save()
                
                clean_title = name.replace("/", "_").replace(":", "_").strip()
                temp_name = f"TEMP_CANON_{i:05d}_{num:02d}{path.suffix}"
                temp_path = path.parent / temp_name
                
                if not args.dry_run:
                    os.rename(path, temp_path)
                
                temp_records.append({
                    'current_path': temp_path if not args.dry_run else path,
                    'final_name': f"{num:02d} {clean_title}{path.suffix}"
                })
                fixed_count += 1
        except Exception as e:
            print(f"Error processing {path.name}: {e}")
            pass
            
    # Phase 2: Final Renaming to "XX Title.ext"
    if not args.dry_run:
        print(f"Phase 1 complete. Renaming files to final format...")
        for record in temp_records:
            try:
                target = record['current_path'].parent / record['final_name']
                if target.exists() and target != record['current_path']:
                    target = target.parent / f"{target.stem}_dup{target.suffix}"
                os.rename(record['current_path'], target)
            except Exception as e:
                print(f"Error renaming {record['current_path'].name}: {e}")
                pass

    print(f"\nSummary:")
    if args.dry_run:
        print(f"  [DRY RUN] Would have canonicalized {fixed_count} tracks.")
    else:
        print(f"  Successfully canonicalized {fixed_count} tracks on disk.")

if __name__ == "__main__":
    main()
