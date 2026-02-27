import os
import argparse
from pathlib import Path
from collections import defaultdict
import mutagen
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
from mutagen.id3 import TPE2, APIC

# Configuration
AUDIO_EXTS = {".m4a", ".mp3", ".mp4"}

def get_audio_obj(path):
    try:
        return mutagen.File(path)
    except:
        return None

def unify_album_artist(tracks):
    if not tracks: return None
    
    aa_counts = defaultdict(int)
    for t_path in tracks:
        audio = get_audio_obj(t_path)
        if not audio: continue
        
        aa = ""
        if isinstance(audio, MP4):
            aa = audio.tags.get("aART", [""])[0]
        elif isinstance(audio, MP3):
            aa = str(audio.get("TPE2", ""))
        
        if aa: aa_counts[aa] += 1
    
    if not aa_counts:
        audio = get_audio_obj(tracks[0])
        if not audio: return None
        if isinstance(audio, MP4):
            winner = audio.tags.get("\xa9ART", [""])[0]
        elif isinstance(audio, MP3):
            winner = str(audio.get("TPE1", ""))
        else: return None
    else:
        winner = max(aa_counts, key=aa_counts.get)

    if not winner: return None

    updated = 0
    for t_path in tracks:
        audio = get_audio_obj(t_path)
        if not audio: continue
        
        needs_save = False
        if isinstance(audio, MP4):
            current = audio.tags.get("aART", [""])[0]
            if current != winner:
                audio.tags["aART"] = [winner]
                needs_save = True
        elif isinstance(audio, MP3):
            current = str(audio.get("TPE2", ""))
            if current != winner:
                audio.tags.add(TPE2(encoding=3, text=winner))
                needs_save = True
        
        if needs_save:
            audio.save()
            updated += 1
    return updated, winner

def check_art(path):
    audio = get_audio_obj(path)
    if not audio: return False
    if isinstance(audio, MP4):
        return "covr" in (audio.tags or {})
    elif isinstance(audio, MP3):
        return any(isinstance(f, APIC) for f in (audio.tags or {}).values())
    return False

def main():
    parser = argparse.ArgumentParser(description="Pristine library pass: Unify Album Artist, audit art, and clean folders.")
    parser.add_argument("--root", required=True, help="Root music library folder")
    parser.add_argument("--unify", action="store_true", help="Perform Album Artist unification")
    parser.add_argument("--audit-art", action="store_true", help="Perform artwork audit")
    parser.add_argument("--clean-empty", action="store_true", help="Remove empty directories")
    args = parser.parse_args()

    lib_root = Path(args.root)
    if not lib_root.exists():
        print(f"Error: Root {lib_root} not found.")
        return

    print(f"Scanning library at {lib_root}...")
    
    album_map = defaultdict(list)
    all_files = list(lib_root.glob("**/*"))
    for path in all_files:
        if path.suffix.lower() in AUDIO_EXTS and not path.name.startswith("._"):
            album_map[path.parent].append(path)

    total_albums = len(album_map)
    print(f"Found {total_albums} album folders.")

    if args.unify:
        print("\n>>> Unifying Album Artists...")
        unified_count = 0
        for i, (album_path, tracks) in enumerate(album_map.items()):
            res = unify_album_artist(tracks)
            if res and res[0] > 0:
                unified_count += 1
            if (i+1) % 100 == 0: print(f"  Processed {i+1}/{total_albums} albums...")
        print(f"  Done. Unified {unified_count} albums.")

    if args.audit_art:
        print("\n>>> Auditing Embedded Artwork...")
        missing_art_albums = []
        for i, (album_path, tracks) in enumerate(album_map.items()):
            if not check_art(tracks[0]):
                missing_art_albums.append(album_path)
        
        print(f"  Found {len(missing_art_albums)} albums missing embedded art.")
        if missing_art_albums:
            out_file = Path("output/missing_art_report.txt")
            out_file.parent.mkdir(parents=True, exist_ok=True)
            with open(out_file, "w") as f:
                for d in sorted(missing_art_albums):
                    f.write(f"{d.relative_to(lib_root)}\n")
            print(f"  Report saved to {out_file}")

    if args.clean_empty:
        print("\n>>> Cleaning Empty Directories...")
        removed = 0
        for root, dirs, files in os.walk(lib_root, topdown=False):
            for name in dirs:
                full_path = Path(root) / name
                try:
                    if not any(full_path.iterdir()):
                        full_path.rmdir()
                        removed += 1
                except: pass
        print(f"  Removed {removed} empty directories.")

if __name__ == "__main__":
    main()
