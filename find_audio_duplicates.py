#!/usr/bin/env python3
"""
Find Audio Duplicates via AcoustID Fingerprinting

Scans a directory recursively and generates acoustic fingerprints for all 
detected audio files (regardless of format/bitrate/metadata). 
Outputs a report grouping identical audio files together so you can 
safely delete the lower quality duplicates.

Requires: acoustid, chromaprint (installed via system package manager)
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict

try:
    import acoustid
except ImportError:
    print("Error: Missing required packages.")
    print("Run: pip3 install pyacoustid")
    print("And install the 'chromaprint' system package via Homebrew/apt.")
    sys.exit(1)

# Most standard audio extensions
AUDIO_EXT = {".mp3", ".m4a", ".flac", ".aac", ".ogg", ".wav", ".aiff", ".wma", ".alac"}

def scan_directory(root_dir: Path) -> dict:
    """Recursively scan for audio files and group by AcoustID fingerprint."""
    fingerprint_groups = defaultdict(list)
    total_files = 0
    failed_files = 0

    print(f"Scanning {root_dir}...")
    
    for file_path in root_dir.rglob("*"):
        if file_path.name.startswith("._") or file_path.suffix.lower() not in AUDIO_EXT:
            continue
            
        total_files += 1
        print(f"  Fingerprinting: {file_path.name}", end="\r")
        
        try:
            # Generate the acoustic fingerprint
            # Using 120 seconds of audio to build a robust fingerprint
            duration, fp = acoustid.fingerprint_file(str(file_path), maxlength=120)
            
            # The AcoustID module returns bytes natively, so we decode to string for dict keys
            fp_hash = fp.decode('utf-8')
            fingerprint_groups[fp_hash].append(file_path)
            
        except acoustid.FingerprintGenerationError as e:
            # Often means it's not a valid audio file or chromaprint crashed on edge case
            failed_files += 1
            pass
            
    print(f"\n\nScan complete. Processed {total_files} audio files. ({failed_files} failed)")
    return fingerprint_groups

def report_duplicates(fingerprint_groups: dict, auto_delete: bool):
    """Filter groupings to those with > 1 file and report/delete."""
    
    duplicates = {fp: paths for fp, paths in fingerprint_groups.items() if len(paths) > 1}
    
    if not duplicates:
        print("No acoustic duplicates found!")
        return

    print(f"\nFound {len(duplicates)} sets of duplicated audio.\n")
    
    total_wasted_bytes = 0
    
    for fp, paths in duplicates.items():
        print("Duplicate Group:")
        
        # Sort paths by file size (largest to smallest) assuming largest = highest quality
        paths.sort(key=lambda p: p.stat().st_size, reverse=True)
        
        # The "keeper" is the largest file
        keeper = paths[0]
        keeper_mb = keeper.stat().st_size / (1024 * 1024)
        print(f"  🌟 KEEPER: {keeper.name} ({keeper_mb:.1f} MB)")
        print(f"             {keeper.parent}")
        
        for duplicate in paths[1:]:
            dup_size = duplicate.stat().st_size
            dup_mb = dup_size / (1024 * 1024)
            total_wasted_bytes += dup_size
            
            print(f"  🗑  DUP:    {duplicate.name} ({dup_mb:.1f} MB)")
            print(f"             {duplicate.parent}")
            
            if auto_delete:
                try:
                    duplicate.unlink()
                    print("             [DELETED]")
                except OSError as e:
                    print(f"             [ERROR DELETING: {e}]")
                    
        print("-" * 50)
        
    saved_mb = total_wasted_bytes / (1024 * 1024)
    print(f"\nTotal duplicate overhead: {saved_mb:.1f} MB")
    
    if not auto_delete:
        print("\nRun this command again with --delete to automatically keep the largest")
        print("file in each group and delete the smaller duplicates.")

def main():
    parser = argparse.ArgumentParser(description="Find true audio duplicates using Acoustic Fingerprinting")
    parser.add_argument("--root", required=True, type=Path, help="Root directory to scan")
    parser.add_argument("--delete", action="store_true", help="Automatically delete the smaller duplicates in each group")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Error: {args.root} is not a directory.")
        sys.exit(1)

    fp_groups = scan_directory(args.root)
    report_duplicates(fp_groups, args.delete)

if __name__ == "__main__":
    main()
