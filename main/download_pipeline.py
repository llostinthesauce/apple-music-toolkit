#!/usr/bin/env python3
"""
Download pipeline — converts downloaded FLACs to tagged ALAC in library.
Dry-run mode: shows what would happen without touching files.

Flow:
  1. User downloads FLACs from squid.wtf → ~/Downloads/_staging/Artist/Album/
  2. This script picks them up, converts FLAC→ALAC, tags, art, moves to library

Usage:
  python3 download_pipeline.py --staging ~/Downloads/_staging --dry-run
  python3 download_pipeline.py --staging ~/Downloads/_staging   # real run
  python3 download_pipeline.py --queue                          # show download queue
"""
import json, os, sys, subprocess, shutil, argparse, re

LIBRARY_ROOT = os.environ.get('MUSIC_ROOT', os.path.expanduser('~/Music'))
STAGING_DEFAULT = os.environ.get('STAGING_DIR', os.path.expanduser('~/Downloads/_staging'))
QUEUE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output', 'download_queue.json')

# ── Download Queue ──────────────────────────────────────────────────────────

def get_queue():
    """Albums confirmed for re-download."""
    return [
        # Single-track fixes
        {"artist": "The Avalanches", "album": "Wildflower",
         "scope": "Track 02 only (replace Because I'm Me)",
         "url_hint": "https://tidal.squid.wtf or https://qobuz.squid.wtf → search 'The Avalanches Wildflower'"},

        {"artist": "beabadoobee", "album": "Patched Up",
         "scope": "Track 02 only (replace Tired — plays Slowdive)",
         "url_hint": "https://qobuz.squid.wtf → search 'beabadoobee Patched Up'"},

        # Full album re-downloads (wrong audio)
        {"artist": "Bladee", "album": "Cold Visions",
         "scope": "Full album — 2 tracks wrong, 2 missing",
         "url_hint": "Tidal or Qobuz search 'Bladee Cold Visions'"},

        {"artist": "Bladee", "album": "Working on Dying",
         "scope": "Full album — only 1 stray track on disk",
         "url_hint": "Tidal/Qobuz 'Bladee Working on Dying'"},

        {"artist": "Bladee", "album": "Rainworld",
         "scope": "Full album — only 3/9 tracks on disk",
         "url_hint": "Qobuz (Tidal might not have this mixtape) 'Bladee Rainworld'"},

        {"artist": "Ecco2k", "album": "D&G",
         "scope": "Full album — only 1/12 tracks on disk",
         "url_hint": "Drain Gang compilation — check SoundCloud or Qobuz"},

        {"artist": "Childish Gambino", "album": "Camp",
         "scope": "Full album — only 1/13 tracks on disk",
         "url_hint": "Tidal/Qobuz 'Childish Gambino Camp'"},

        # Missing entirely
        {"artist": "Childish Gambino", "album": "3.15.20",
         "scope": "Full album — missing entirely",
         "url_hint": "Tidal/Qobuz 'Childish Gambino 3.15.20'"},

        {"artist": "Kendrick Lamar", "album": "DAMN.",
         "scope": "Full album — missing entirely",
         "url_hint": "Tidal/Qobuz 'Kendrick Lamar DAMN'"},

        {"artist": "N.E.R.D", "album": "In Search Of",
         "scope": "Full album — missing entirely",
         "url_hint": "Tidal/Qobuz 'NERD In Search Of' — note: folder is 'N.E.R.D_'"},

        {"artist": "Gorillaz", "album": "Electrospective",
         "scope": "Full album — missing entirely",
         "url_hint": "Qobuz (compilation, might be CD-only)"},

        {"artist": "black midi", "album": "Cavalcovers",
         "scope": "Full album — missing entirely",
         "url_hint": "Qobuz or Bandcamp 'black midi Cavalcovers' — covers EP"},
    ]

def save_queue():
    with open(QUEUE_FILE, 'w') as f:
        json.dump(get_queue(), f, indent=2)

# ── Pipeline ────────────────────────────────────────────────────────────────

def find_flacs(staging_dir):
    """Find all FLAC files in staging directory."""
    flacs = []
    for root, dirs, files in os.walk(staging_dir):
        for f in files:
            if f.lower().endswith('.flac'):
                flacs.append(os.path.join(root, f))
    return flacs

def flac_to_alac(src, dst, dry_run=False):
    """Convert FLAC to ALAC m4a. No video streams, 44.1kHz 16-bit."""
    print(f"  CONVERT: {os.path.basename(src)}")
    if dry_run:
        return True

    cmd = [
        'ffmpeg', '-y', '-v', 'quiet',
        '-i', src,
        '-vn', '-map', '0:a',
        '-af', 'aformat=sample_fmts=s16p',
        '-c:a', 'alac', '-ar', '44100',
        dst
    ]
    try:
        subprocess.run(cmd, check=True, timeout=120, capture_output=True)
        return True
    except Exception as e:
        print(f"    [!] ffmpeg error: {e}")
        return False

def embed_art(m4a_path, art_path, dry_run=False):
    """Embed cover art into m4a file."""
    if not os.path.exists(art_path):
        return
    print(f"    Art: {os.path.basename(art_path)}")
    if dry_run:
        return

    try:
        from mutagen.mp4 import MP4, MP4Cover
        with open(art_path, 'rb') as f:
            art_data = f.read()

        # Resize to 600x600 first
        tmp = art_path + '.resized.jpg'
        subprocess.run(['sips', '-Z', '600', art_path, '--out', tmp],
                       check=True, capture_output=True)

        with open(tmp, 'rb') as f:
            art_data_resized = f.read()

        audio = MP4(m4a_path)
        audio["covr"] = [MP4Cover(art_data_resized, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
        os.remove(tmp)
    except Exception as e:
        print(f"    [!] Art error: {e}")

def tag_from_source(m4a_path, src_flac_path, dry_run=False):
    """Copy tags from original FLAC to the new ALAC file."""
    if dry_run:
        return

    try:
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4

        src = FLAC(src_flac_path)
        dst = MP4(m4a_path)

        tag_map = {
            'title': '\xa9nam',
            'artist': '\xa9ART',
            'album': '\xa9alb',
            'albumartist': 'aART',
            'date': '\xa9day',
            'genre': '\xa9gen',
            'tracknumber': 'trkn',
            'discnumber': 'disk',
        }

        for flac_key, mp4_key in tag_map.items():
            val = src.get(flac_key)
            if val:
                if mp4_key in ('trkn', 'disk'):
                    # Format as [(num, total)]
                    num = int(val[0]) if val else 1
                    total = int(src.get('tracktotal', [str(num)])) if mp4_key == 'trkn' else int(src.get('disctotal', ['1'])[0])
                    dst[mp4_key] = [(num, total or 1)]
                else:
                    dst[mp4_key] = [str(val[0])]

        dst.save()
        return True
    except Exception as e:
        print(f"    [!] Tag error: {e}")
        return False

def process_album(staging_album_dir, target_dir, dry_run=False):
    """Process one album: FLAC→ALAC, tag, art, move to library."""
    flacs = find_flacs(staging_album_dir)
    if not flacs:
        print(f"  No FLACs found in {staging_album_dir}")
        return 0

    # Find cover art
    art_candidates = []
    for f in os.listdir(staging_album_dir):
        if f.lower().endswith(('.jpg', '.jpeg', '.png')):
            art_candidates.append(os.path.join(staging_album_dir, f))
    art_path = art_candidates[0] if art_candidates else None

    os.makedirs(target_dir, exist_ok=True)
    converted = 0

    for flac in sorted(flacs):
        base = os.path.splitext(os.path.basename(flac))[0]
        m4a_dst = os.path.join(target_dir, base + '.m4a')

        if os.path.exists(m4a_dst) and not dry_run:
            print(f"  SKIP (exists): {base}.m4a")
            continue

        success = flac_to_alac(flac, m4a_dst, dry_run)
        if success:
            tag_from_source(m4a_dst, flac, dry_run)
            if art_path:
                embed_art(m4a_dst, art_path, dry_run)
            converted += 1

    return converted

def find_staging_albums(staging_dir):
    """Find artist/album folders in staging that contain FLACs."""
    albums = []
    for artist in sorted(os.listdir(staging_dir)):
        artist_path = os.path.join(staging_dir, artist)
        if not os.path.isdir(artist_path) or artist.startswith('.'):
            continue
        for album in sorted(os.listdir(artist_path)):
            album_path = os.path.join(artist_path, album)
            if os.path.isdir(album_path):
                flacs = find_flacs(album_path)
                if flacs:
                    albums.append((artist, album, album_path, len(flacs)))
    return albums

# ── Entry Point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Download pipeline: FLAC → ALAC → library")
    parser.add_argument('--staging', default=STAGING_DEFAULT,
                        help=f'Staging directory (default: {STAGING_DEFAULT})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would happen without doing it')
    parser.add_argument('--queue', action='store_true',
                        help='Show download queue')
    args = parser.parse_args()

    if args.queue:
        queue = get_queue()
        print(f"\n{'='*60}")
        print(f"DOWNLOAD QUEUE — {len(queue)} items")
        print(f"{'='*60}\n")
        print("Download from: https://qobuz.squid.wtf (primary) or https://tidal.squid.wtf (backup)\n")
        print("Save FLACs to: ~/Downloads/_staging/Artist/Album/\n")

        for i, item in enumerate(queue, 1):
            print(f"  [{i}] {item['artist']} — {item['album']}")
            print(f"      Scope: {item['scope']}")
            print(f"      URL:   {item['url_hint']}")
            print()

        print(f"{'='*60}")
        print(f"After downloading: python3 download_pipeline.py --staging ~/Downloads/_staging")
        print(f"{'='*60}")
        save_queue()
        return

    staging = args.staging
    dry = args.dry_run

    print(f"\n{'='*60}")
    print(f"DOWNLOAD PIPELINE{' (DRY RUN)' if dry else ''}")
    print(f"{'='*60}")
    print(f"Staging: {staging}")
    print(f"Library: {LIBRARY_ROOT}")
    print()

    if not os.path.isdir(staging):
        print("Staging directory not found. Create it and download FLACs there first.")
        print(f"  mkdir -p {staging}")
        print(f"Then run --queue to see what to download.")
        return

    albums = find_staging_albums(staging)

    if not albums:
        print("No FLACs found in staging.")
        print("Run --queue to see what to download first.")
        return

    print(f"Found {len(albums)} album(s) to process:\n")
    total = 0

    for artist, album, src_path, flac_count in albums:
        print(f"  {artist} — {album}  ({flac_count} FLACs)")
        target = os.path.join(LIBRARY_ROOT, artist, album)
        converted = process_album(src_path, target, dry_run=dry)
        total += converted
        if converted > 0:
            print(f"  ✓ {converted} tracks → {target}")
        print()

    print(f"{'='*60}")
    print(f"{'Would process' if dry else 'Processed'} {total} tracks")
    if not dry:
        print(f"Open Apple Music to verify new files")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
