#!/usr/bin/env python3
"""Fixup: embed cover art from staging FLAC albums into already-converted m4as."""
import os, sys, subprocess
from mutagen.mp4 import MP4, MP4Cover

STAGING = os.environ.get('STAGING_DIR', os.path.expanduser('~/Downloads/_staging'))
LIBRARY = os.environ.get('MUSIC_ROOT', os.path.expanduser('~/Music'))

def find_cover(album_dir):
    for f in os.listdir(album_dir):
        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
            return os.path.join(album_dir, f)
    return None

def find_m4as(lib_dir):
    m4as = []
    for f in sorted(os.listdir(lib_dir)):
        if f.lower().endswith('.m4a'):
            m4as.append(os.path.join(lib_dir, f))
    return m4as

def embed_art(m4a_path, art_path):
    tmp = art_path + '.resized.jpg'
    subprocess.run(['sips', '-Z', '600', art_path, '--out', tmp],
                   check=True, capture_output=True)
    with open(tmp, 'rb') as f:
        art_data = f.read()
    audio = MP4(m4a_path)
    audio['covr'] = [MP4Cover(art_data, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()
    os.remove(tmp)

def main():
    total = 0
    for artist in sorted(os.listdir(STAGING)):
        apath = os.path.join(STAGING, artist)
        if not os.path.isdir(apath) or artist.startswith('.'):
            continue
        for album in sorted(os.listdir(apath)):
            apath2 = os.path.join(apath, album)
            if not os.path.isdir(apath2):
                continue
            # Find cover art
            cover = find_cover(apath2)
            if not cover:
                print(f"  {artist}/{album}: no cover art found")
                continue
            # Find matching m4as in library
            lib_target = os.path.join(LIBRARY, artist, album)
            if not os.path.isdir(lib_target):
                print(f"  {artist}/{album}: no library dir at {lib_target}")
                continue
            m4as = find_m4as(lib_target)
            if not m4as:
                print(f"  {artist}/{album}: no m4as found")
                continue
            # Check if first track already has art
            first = MP4(m4as[0])
            if 'covr' in first:
                print(f"  {artist}/{album}: ART EXISTS ({len(m4as)} tracks) — skip")
                continue
            # Embed art
            for m in m4as:
                try:
                    embed_art(m, cover)
                except Exception as e:
                    print(f"    [!] {os.path.basename(m)}: {e}")
            album_total = len(m4as)
            total += album_total
            print(f"  {artist}/{album}: embedded art in {album_total} tracks")

    print(f"\nDone: {total} tracks updated")

if __name__ == '__main__':
    main()
