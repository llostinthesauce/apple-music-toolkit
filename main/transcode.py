#!/usr/bin/env python3
"""
Convert FLAC/WAV/AIFF to AAC/MP3/ALAC
Recursively scans a directory for lossless audio files and converts them 
to target format while preserving basic ID3/Metadata tags.

Requires: pydub, mutagen, ffmpeg (installed via homebrew/apt)
"""

import argparse
import os
import sys
from pathlib import Path

try:
    from pydub import AudioSegment
    import mutagen
    from mutagen.flac import FLAC
    from mutagen.wave import WAVE
    from mutagen.aiff import AIFF
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.id3 import ID3NoHeaderError, ID3, TIT2, TPE1, TALB, TDRC, TRCK, APIC
except ImportError:
    print("Error: Missing required packages.")
    print("Run: pip3 install pydub mutagen")
    sys.exit(1)


LOSSLESS_EXTENSIONS = {".flac", ".wav", ".aiff", ".aif"}

def get_metadata(file_path: Path) -> dict:
    """Extract standard metadata from lossless files using Mutagen."""
    tags = {
        "title": None,
        "artist": None,
        "album": None,
        "track": None,
        "date": None,
        "cover": None
    }
    
    ext = file_path.suffix.lower()
    audio = None
    
    try:
        if ext == ".flac":
            audio = FLAC(file_path)
            tags["title"] = audio.get("title", [None])[0]
            tags["artist"] = audio.get("artist", [None])[0]
            tags["album"] = audio.get("album", [None])[0]
            tags["track"] = audio.get("tracknumber", [None])[0]
            tags["date"] = audio.get("date", [None])[0]
            if audio.pictures:
                tags["cover"] = audio.pictures[0].data
        elif ext == ".wav":
            audio = WAVE(file_path) # WAV tags are often messy, we'll extract what we can
            if audio.tags:
                tags["title"] = audio.tags.get("TIT2", [None])[0]
                tags["artist"] = audio.tags.get("TPE1", [None])[0]
                tags["album"] = audio.tags.get("TALB", [None])[0]
    except Exception as e:
        print(f"  Warning: Could not read tags for {file_path.name} ({e})")
        
    return tags

def apply_m4a_metadata(m4a_path: Path, tags: dict):
    """Apply previously extracted metadata to the new M4A file via Mutagen MP4."""
    try:
        audio = MP4(m4a_path)
        
        if tags["title"]: audio["\xa9nam"] = tags["title"]
        if tags["artist"]: audio["\xa9ART"] = tags["artist"]
        if tags["album"]: audio["\xa9alb"] = tags["album"]
        if tags["date"]: audio["\xa9day"] = tags["date"]
        
        # Handle track number '1' or '1/12'
        if tags["track"]:
            track_str = str(tags["track"]).split('/')[0]
            if track_str.isdigit():
                audio["trkn"] = [(int(track_str), 0)]
                
        if tags["cover"]:
            audio["covr"] = [MP4Cover(tags["cover"], imageformat=MP4Cover.FORMAT_JPEG)]
            
        audio.save()
    except Exception as e:
        print(f"  Warning: Could not write tags to {m4a_path.name} ({e})")

def apply_mp3_metadata(mp3_path: Path, tags: dict):
    """Apply previously extracted metadata to the new MP3 file via Mutagen ID3."""
    try:
        try:
            audio = ID3(mp3_path)
        except ID3NoHeaderError:
            audio = ID3()
            
        if tags["title"]: audio.add(TIT2(encoding=3, text=tags["title"]))
        if tags["artist"]: audio.add(TPE1(encoding=3, text=tags["artist"]))
        if tags["album"]: audio.add(TALB(encoding=3, text=tags["album"]))
        if tags["date"]: audio.add(TDRC(encoding=3, text=str(tags["date"])))
        
        if tags["track"]:
            track_str = str(tags["track"]).split('/')[0]
            audio.add(TRCK(encoding=3, text=track_str))
            
        if tags["cover"]:
            audio.add(APIC(
                encoding=3,
                mime='image/jpeg',
                type=3, desc='Cover',
                data=tags["cover"]
            ))
            
        audio.save(mp3_path, v2_version=3)
    except Exception as e:
        print(f"  Warning: Could not write tags to {mp3_path.name} ({e})")

def convert_file(file_path: Path, out_format: str, bitrate: str, sample_rate: str, bit_depth: str, delete_original: bool):
    """Convert a single audio file via pydub and FFmpeg."""
    if out_format == "aac":
        ext = ".m4a"
        export_kwargs = {"format": "mp4", "codec": "aac", "parameters": ["-strict", "experimental"]}
        if bitrate: export_kwargs["bitrate"] = bitrate
    elif out_format == "mp3":
        ext = ".mp3"
        export_kwargs = {"format": "mp3", "codec": "libmp3lame"}
        if bitrate: export_kwargs["bitrate"] = bitrate
    elif out_format == "alac":
        ext = ".m4a"
        export_kwargs = {"format": "mp4", "codec": "alac"}
    else:
        print(f"Unknown format: {out_format}")
        return

    out_path = file_path.with_suffix(ext)
    print(f"Converting: {file_path.name} -> {ext}")
    
    if out_path.exists():
        print(f"  Skipping: {out_path.name} already exists.")
        return
        
    # Extract tags first
    tags = get_metadata(file_path)
    
    try:
        # Load lossless audio
        audio = AudioSegment.from_file(file_path)
        
        if sample_rate and sample_rate.isdigit():
            audio = audio.set_frame_rate(int(sample_rate))
            
        if bit_depth and bit_depth.isdigit():
            audio = audio.set_sample_width(int(bit_depth) // 8)
            
        # Export
        audio.export(out_path, **export_kwargs)
        
        # Rewrite the metadata to the new file
        if out_format in ("aac", "alac"):
            apply_m4a_metadata(out_path, tags)
        elif out_format == "mp3":
            apply_mp3_metadata(out_path, tags)
            
        print("  Convert & Tag SUCCESS")
        
        if delete_original:
            file_path.unlink()
            print("  Deleted original file.")
            
    except Exception as e:
        print(f"  Error converting {file_path.name}: {e}")
        if out_path.exists():
            out_path.unlink() # Clean up failed partial exports

def main():
    parser = argparse.ArgumentParser(description="Recursively convert lossless audio (FLAC/WAV/AIFF) to AAC/MP3/ALAC")
    parser.add_argument("--root", required=True, type=Path, help="Root directory to scan")
    parser.add_argument("--format", default="aac", choices=["aac", "mp3", "alac"], help="Output format codec")
    parser.add_argument("--bitrate", default="256k", help="Bitrate for lossy formats (e.g. 256k, 320k)")
    parser.add_argument("--sample-rate", default="", help="Optional Sample rate overriding (e.g. 44100)")
    parser.add_argument("--bit-depth", default="", help="Optional Bit depth overriding (e.g. 16, 24)")
    parser.add_argument("--delete", action="store_true", help="Delete original lossless files after successful conversion")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Error: Does not exist or is not a directory: {args.root}")
        sys.exit(1)

    print(f"Scanning {args.root} for lossless files...")
    
    found_count = 0
    for file_path in args.root.rglob("*"):
        if file_path.suffix.lower() in LOSSLESS_EXTENSIONS and not file_path.name.startswith("._"):
            found_count += 1
            convert_file(file_path, args.format, args.bitrate, args.sample_rate, args.bit_depth, args.delete)
            
    print(f"\nDone. Processed {found_count} lossless files.")

if __name__ == "__main__":
    main()
