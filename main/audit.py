import os
import argparse
from pathlib import Path
import mutagen
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
from collections import defaultdict

AUDIO_EXTS = {".m4a", ".mp3", ".mp4"}

def main():
    parser = argparse.ArgumentParser(description="Comprehensive library integrity and wholeness audit.")
    parser.add_argument("--root", required=True, help="Root music library folder")
    args = parser.parse_args()

    lib_root = Path(args.root)
    if not lib_root.exists():
        print(f"Error: {lib_root} not found.")
        return

    print(f"Starting Library Integrity Audit on {lib_root}...")
    
    stats = {
        "total_files": 0,
        "missing_tags": 0,
        "naming_violations": 0,
        "gap_violations": 0,
        "corrupt_files": 0
    }
    
    albums = defaultdict(list)
    report = []

    # 1. Scan Files
    for path in lib_root.glob("**/*"):
        if path.suffix.lower() in AUDIO_EXTS and not path.name.startswith("._"):
            stats["total_files"] += 1
            try:
                audio = mutagen.File(path)
                if audio:
                    albums[path.parent].append((path, audio))
                    
                    # Check for basic tags
                    has_tags = True
                    if isinstance(audio, MP4):
                        if not all(k in audio.tags for k in ["\xa9nam", "\xa9ART", "\xa9alb"]):
                            has_tags = False
                    elif isinstance(audio, MP3):
                        if not all(k in audio.tags for k in ["TIT2", "TPE1", "TALB"]):
                            has_tags = False
                    
                    if not has_tags:
                        stats["missing_tags"] += 1
                        report.append(f"Missing Basic Tags: {path.relative_to(lib_root)}")

                    # Check naming convention (01 Title.ext)
                    if not re.match(r"^\d{2}\s", path.name):
                        stats["naming_violations"] += 1
                        report.append(f"Naming Violation (Missing XX prefix): {path.name}")

                else:
                    stats["corrupt_files"] += 1
                    report.append(f"Corrupt/Unreadable: {path.relative_to(lib_root)}")
            except:
                stats["corrupt_files"] += 1
                report.append(f"Error reading: {path.relative_to(lib_root)}")

    # 2. Check Album Wholeness
    for folder, tracks in albums.items():
        track_nums = []
        for path, audio in tracks:
            try:
                if isinstance(audio, MP4):
                    num = audio.tags.get("trkn", [(0, 0)])[0][0]
                else:
                    num = int(str(audio.get("TRCK", "0")).split("/")[0])
                if num > 0: track_nums.append(num)
            except: pass
        
        if track_nums:
            max_num = max(track_nums)
            if max_num > len(track_nums):
                missing = set(range(1, max_num + 1)) - set(track_nums)
                if missing:
                    stats["gap_violations"] += 1
                    report.append(f"Missing Tracks in Album: {folder.relative_to(lib_root)} (Missing numbers: {sorted(list(missing))})")

    print("
" + "="*40)
    print("FINAL INTEGRITY REPORT")
    print("="*40)
    print(f"Total Audio Files:    {stats['total_files']}")
    print(f"Naming Violations:    {stats['naming_violations']}")
    print(f"Missing Tags:         {stats['missing_tags']}")
    print(f"Album Gaps:           {stats['gap_violations']}")
    print(f"Corrupt Files:        {stats['corrupt_files']}")
    print("="*40)

    out_file = Path("output/integrity_audit_full.txt")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        f.write("
".join(report))
    
    print(f"
Detailed issues saved to {out_file}")

import re
if __name__ == "__main__":
    main()
