import plistlib, os, subprocess, urllib.parse, csv, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'output')
os.makedirs(OUTPUT_DIR, exist_ok=True)

xml_path = os.environ.get('XML_DEFAULT', os.path.expanduser('~/Desktop/Library.xml'))
output_path = os.environ.get('LOUDNESS_REPORT', os.path.join(OUTPUT_DIR, 'loudness_report.csv'))
log_path = os.environ.get('LOUDNESS_LOG', os.path.join(OUTPUT_DIR, 'loudness_scan.log'))

# Helper to check which tracks are already done
done_paths = set()
if os.path.exists(output_path):
    with open(output_path, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if row and len(row) > 5:
                done_paths.add(row[5]) # The Path column

def get_mean_vol(fpath):
    try:
        cmd = ['ffmpeg', '-i', fpath, '-af', 'volumedetect', '-vn', '-sn', '-dn', '-f', 'null', '/dev/null']
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        for line in res.stderr.split('\n'):
            if 'mean_volume' in line:
                return float(line.split(': ')[1].split(' ')[0])
    except Exception as e:
        with open(log_path, 'a') as l:
            l.write(f"Error on {fpath}: {e}\n")
        return None
    return None

try:
    with open(xml_path, 'rb') as f: plist = plistlib.load(f)
except Exception as e:
    print(f"Error loading XML: {e}")
    sys.exit(1)

to_process = []
for tid, t in plist.get('Tracks', {}).items():
    loc = t.get('Location')
    if loc:
        p = urllib.parse.unquote(loc.replace('file://', ''))
        if os.path.exists(p) and p not in done_paths:
            to_process.append({'name': t.get('Name', 'Unknown'), 'artist': t.get('Artist', 'Unknown'), 'album': t.get('Album', 'Unknown'), 'norm': t.get('Normalization', 0), 'path': p})

total = len(to_process)
print(f"Resuming scan of {total} remaining tracks (Already done: {len(done_paths)})")

if total == 0:
    print("No remaining tracks to process.")
    sys.exit(0)

# Open in append mode
with open(output_path, 'a', newline='') as f:
    writer = csv.writer(f)
    if os.path.getsize(output_path) == 0:
        writer.writerow(['Name', 'Artist', 'Album', 'Apple Norm', 'Physical Mean (dB)', 'Path'])
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        for i, track in enumerate(to_process):
            mean_vol = get_mean_vol(track['path'])
            writer.writerow([track['name'], track['artist'], track['album'], track['norm'], mean_vol, track['path']])
            if (i + 1) % 50 == 0 or (i + 1) == total:
                print(f"Progress: {i+1}/{total} remaining tracks...", flush=True)
                f.flush()

print(f"Scan complete!")
