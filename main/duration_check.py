"""
Duration-based wrong-audio detector.
Compares each track's actual duration against MusicBrainz expected durations.
Flags mismatches > 8 seconds. Much faster than AcoustID fingerprinting.
"""
import json, os
import urllib.request
import urllib.parse
import time
import sys

MUSICBRAINZ_DELAY = 1.2  # rate limit ~1 req/sec
OUTPUT_DIR = os.environ.get('OUTPUT_DIR', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output'))
DURATION_TOLERANCE = 8   # seconds

def mb_get(url, retries=2):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "foriPod/1.0"})
            r = urllib.request.urlopen(req, timeout=15)
            time.sleep(MUSICBRAINZ_DELAY)
            return json.loads(r.read())
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                print(f"  [!] MusicBrainz error: {e}", file=sys.stderr)
    return None

# Load scan results
SCAN_FILE = os.environ.get('SCAN_RESULTS', os.path.join(OUTPUT_DIR, 'scan_results.json'))
with open(SCAN_FILE) as f:
    content = f.read()
idx = content.index('{"total":')
scan_data = json.loads(content[idx:])

# Group tracks by album
from collections import defaultdict
albums = defaultdict(list)
for r in scan_data['results']:
    parts = r['path'].split('/')
    if len(parts) >= 3:
        album_key = '/'.join(parts[:2])  # Artist/Album
        albums[album_key].append(r)

# Sort albums
album_keys = sorted(albums.keys())
total = len(album_keys)
print(f"Checking {total} albums against MusicBrainz...", file=sys.stderr)

results = []
checked = 0
mismatches = 0

for album_key in album_keys:
    tracks = sorted(albums[album_key], key=lambda r: r['path'])
    artist, album_name = album_key.split('/', 1)
    checked += 1
    
    if checked % 50 == 0:
        print(f"  {checked}/{total} albums, {mismatches} mismatches found...", file=sys.stderr)
    
    # Search MusicBrainz for this release
    query = f'release:"{album_name}" AND artist:"{artist}"'
    url = f'https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(query)}&fmt=json&limit=3'
    
    data = mb_get(url)
    if not data or not data.get('releases'):
        continue
    
    # Get the first matching release's tracklist
    release_id = data['releases'][0]['id']
    url2 = f'https://musicbrainz.org/ws/2/release/{release_id}?inc=recordings&fmt=json'
    
    rel_data = mb_get(url2)
    if not rel_data:
        continue
    
    # Extract expected durations
    mb_tracks = []
    for media in rel_data.get('media', []):
        for t in media.get('tracks', []):
            dur = t.get('length', 0) // 1000 if t.get('length') else 0
            mb_tracks.append({
                'title': t['title'],
                'position': int(t.get('position', 0)),
                'duration_s': dur,
            })
    
    if not mb_tracks:
        continue
    
    # Compare each file
    album_mismatches = []
    for i, r in enumerate(tracks):
        actual_s = r['duration_m'] * 60 + r['duration_s']
        
        # Match by position (track number)
        mb_track = mb_tracks[i] if i < len(mb_tracks) else None
        
        if mb_track and mb_track['duration_s'] > 0:
            diff = abs(actual_s - mb_track['duration_s'])
            if diff > DURATION_TOLERANCE:
                album_mismatches.append({
                    'file': r['path'].split('/')[-1],
                    'path': r['path'],
                    'actual_s': actual_s,
                    'expected_s': mb_track['duration_s'],
                    'diff_s': diff,
                    'mb_title': mb_track['title'],
                    'position': i + 1,
                })
                mismatches += 1
    
    if album_mismatches:
        results.append({
            'album': album_key,
            'mb_release_id': release_id,
            'mb_album': rel_data.get('title', ''),
            'total_tracks': len(tracks),
            'mismatches': album_mismatches,
        })

# Save results
output = {
    'total_albums_checked': checked,
    'total_mismatches': mismatches,
    'albums_with_mismatches': len(results),
    'results': results,
}

out_path = os.path.join(OUTPUT_DIR, 'duration_mismatches.json')
with open(out_path, 'w') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\nDone! {len(results)} albums with duration mismatches ({mismatches} tracks total)", file=sys.stderr)
print(f"Saved to: {out_path}", file=sys.stderr)

# Quick summary
for r in results:
    print(f"\n  {r['album']} — {len(r['mismatches'])} wrong:")
    for m in r['mismatches']:
        print(f"    {m['file']}: actual {m['actual_s']//60}:{m['actual_s']%60:02d}  expected {m['expected_s']//60}:{m['expected_s']%60:02d}  ({m['diff_s']}s diff)")
