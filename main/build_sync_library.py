#!/usr/bin/env python3
"""
Build a sync-optimized library for iPod syncing.
Hard-links all tracks from main library, splits tracks > threshold
into ≤target MB segments at silence points. Main library untouched.

Usage:
  python3 build_sync_library.py --ipod video   # Build for 5.5 gen
  python3 build_sync_library.py --ipod photo   # Build for 4th gen
  python3 build_sync_library.py --ipod video --dry-run
"""
import json
import os
import shutil
import subprocess
import sys
import argparse
import re
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "sync_config.json")
SCAN_FILE = os.path.join(SCRIPT_DIR, "scan_results.json")

def load_config(ipod_name):
    with open(CONFIG_FILE) as f:
        cfg = json.load(f)
    ipod = cfg['ipods'].get(ipod_name)
    if not ipod:
        print(f"Unknown iPod: {ipod_name}. Options: {list(cfg['ipods'].keys())}")
        sys.exit(1)
    return cfg['main_library'], ipod, cfg['split']

def load_scan():
    with open(SCAN_FILE) as f:
        content = f.read()
    idx = content.index('{"total":')
    return json.loads(content[idx:])

def fmt_time(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"

def detect_silence_points(filepath, silence_db, silence_dur):
    """Find silence points in a track using ffmpeg silencedetect."""
    cmd = [
        'ffmpeg', '-i', filepath,
        '-af', f'silencedetect=n={silence_db}dB:d={silence_dur}',
        '-f', 'null', '-'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    points = []
    for line in result.stderr.split('\n'):
        if 'silence_end' in line:
            m = re.search(r'silence_end:\s*([\d.]+)', line)
            if m:
                t = float(m.group(1))
                if t > 5 and t < 7200:  # skip first 5s and absurd times
                    points.append(t)
    return sorted(set(round(p) for p in points))

def pick_split_points(duration_s, silence_points, min_seg, max_seg, threshold_mb, target_mb, bitrate_kbps):
    """
    Choose split timestamps so each segment is ≤ target_mb.
    Prefers silence points. Falls back to fixed intervals if none found.
    """
    if not silence_points:
        # No silence found — split at fixed intervals
        seg_s = (target_mb * 8000) / max(bitrate_kbps, 500)
        seg_s = max(min_seg, min(seg_s, max_seg))
        points = []
        t = seg_s
        while t < duration_s - min_seg:
            points.append(int(t))
            t += seg_s
        return points

    # Greedy: pick silence points at ~target interval
    seg_s = (target_mb * 8000) / max(bitrate_kbps, 500)
    seg_s = max(min_seg, min(seg_s, max_seg))

    split_times = []
    last_split = 0
    for sp in silence_points:
        if sp - last_split >= seg_s and duration_s - sp >= min_seg:
            split_times.append(sp)
            last_split = sp

    # If no good silences found, fall back to fixed
    if not split_times:
        t = seg_s
        while t < duration_s - min_seg:
            split_times.append(int(t))
            t += seg_s

    return split_times

def split_track(src_path, dst_dir, track_name, split_times, dry_run=False):
    """Split a track into segments using ffmpeg -c copy. No re-encode."""
    base = os.path.splitext(track_name)[0]
    ext = os.path.splitext(track_name)[1]
    times_str = ','.join(str(t) for t in split_times)

    # Output pattern: "Track Name (Pt 1).m4a", "Track Name (Pt 2).m4a"
    out_pattern = os.path.join(dst_dir, f"{base} (Pt %d){ext}")

    if dry_run:
        parts = len(split_times) + 1
        print(f"      Would split into {parts} parts at: {', '.join(fmt_time(t) for t in split_times)}")
        return True

    cmd = [
        'ffmpeg', '-y', '-v', 'quiet',
        '-i', src_path,
        '-c', 'copy',
        '-f', 'segment',
        '-segment_times', times_str,
        '-reset_timestamps', '1',
        '-map', '0',
        out_pattern
    ]

    try:
        subprocess.run(cmd, check=True, timeout=300, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"      [!] ffmpeg error: {e.stderr.decode()[:200] if e.stderr else 'unknown'}")
        return False

def build_sync_library(ipod_name, dry_run=False):
    main_root, ipod_cfg, split_cfg = load_config(ipod_name)
    sync_root = ipod_cfg['sync_root']
    threshold_mb = ipod_cfg['split_threshold_mb']
    target_mb = ipod_cfg['split_target_mb']

    print(f"\n{'='*60}")
    print(f"BUILD SYNC LIBRARY: {ipod_cfg['name']}")
    print(f"{'='*60}")
    print(f"  Main library:   {main_root}")
    print(f"  Sync root:      {sync_root}")
    print(f"  Split threshold: >{threshold_mb}MB")
    print(f"  Target segment:  ≤{target_mb}MB")
    print(f"  Dry run:         {dry_run}")
    print()

    # Load scan data for track sizes
    print("Loading scan data...")
    scan = load_scan()
    track_sizes = {}
    for r in scan['results']:
        track_sizes[r['path']] = r

    # Walk main library
    total_linked = 0
    total_split = 0
    total_failed = 0
    artists_done = 0

    for artist in sorted(os.listdir(main_root)):
        artist_path = os.path.join(main_root, artist)
        if not os.path.isdir(artist_path) or artist.startswith('.'):
            continue

        for album in sorted(os.listdir(artist_path)):
            album_path = os.path.join(artist_path, album)
            if not os.path.isdir(album_path):
                continue

            for track_file in sorted(os.listdir(album_path)):
                if not track_file.endswith('.m4a'):
                    continue

                src_path = os.path.join(album_path, track_file)
                rel_path = os.path.join(artist, album)

                # Determine sync destination
                dst_dir = os.path.join(sync_root, rel_path)
                rel_key = f"{artist}/{album}/{track_file}"

                # Get file size from scan data
                scan_info = track_sizes.get(rel_key, {})
                size_mb = scan_info.get('size_mb', 0)

                if size_mb == 0:
                    # Fallback to actual file size
                    try:
                        size_mb = os.path.getsize(src_path) / 1_000_000
                    except:
                        continue

                os.makedirs(dst_dir, exist_ok=True)
                dst_path = os.path.join(dst_dir, track_file)

                if size_mb <= threshold_mb:
                    # Just hard-link — zero disk space
                    if dry_run:
                        total_linked += 1
                    else:
                        if os.path.exists(dst_path):
                            os.remove(dst_path)
                        try:
                            os.link(src_path, dst_path)
                            total_linked += 1
                        except OSError:
                            # Cross-device link not supported, fall back to copy
                            shutil.copy2(src_path, dst_path)
                            total_linked += 1
                else:
                    # Needs splitting
                    print(f"  SPLIT: {rel_key} ({size_mb:.0f}MB)")

                    # Get bitrate and duration from scan
                    bitrate = scan_info.get('bitrate', 900)
                    duration_s = scan_info.get('duration_m', 0) * 60 + scan_info.get('duration_s', 0)

                    # Detect silence points
                    silence = detect_silence_points(
                        src_path,
                        split_cfg['silence_db'],
                        split_cfg['silence_duration']
                    )

                    # Pick split points
                    points = pick_split_points(
                        duration_s, silence,
                        split_cfg['min_segment_seconds'],
                        split_cfg['max_segment_seconds'],
                        threshold_mb, target_mb, bitrate
                    )

                    if points:
                        success = split_track(src_path, dst_dir, track_file, points, dry_run)
                        if success:
                            total_split += 1
                            if not dry_run and len(points) > 0:
                                parts = len(points) + 1
                                print(f"      → {parts} segments at: {', '.join(fmt_time(t) for t in points)}")
                        else:
                            total_failed += 1
                            print(f"      [!] Split failed, falling back to linking full file")
                            if not dry_run:
                                os.link(src_path, dst_path)
                    else:
                        # No split points, just link
                        print(f"      → No split points found, linking full file")
                        if not dry_run:
                            os.link(src_path, dst_path)
                        total_linked += 1

        artists_done += 1
        if artists_done % 10 == 0 and not dry_run:
            print(f"  ... {artists_done} artists processed")

    print(f"\n{'='*60}")
    print(f"DONE: {ipod_cfg['name']}")
    print(f"  Hard-linked:  {total_linked} tracks")
    print(f"  Split:        {total_split} tracks")
    print(f"  Failed:       {total_failed} tracks")
    print(f"  Sync library: {sync_root}")
    if dry_run:
        print(f"  (DRY RUN — no files modified)")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Build sync-optimized library for iPod")
    parser.add_argument('--ipod', choices=['video', 'photo'], required=True,
                        help='Which iPod to build for')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without doing it')
    args = parser.parse_args()

    build_sync_library(args.ipod, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
