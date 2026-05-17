import os, subprocess, json, sys

root = os.environ.get('MUSIC_ROOT', os.path.expanduser('~/Music'))

results = []
art_oversized = []
total = 0
scanned = 0

# Count total
for dirpath, dirnames, filenames in os.walk(root):
    for f in filenames:
        if f.endswith('.m4a'):
            total += 1

print(f"Scanning {total} tracks...", file=sys.stderr)

for dirpath, dirnames, filenames in os.walk(root):
    for f in filenames:
        if not f.endswith('.m4a'):
            continue
        path = os.path.join(dirpath, f)
        file_size = os.path.getsize(path)
        scanned += 1
        if scanned % 500 == 0:
            print(f"  {scanned}/{total}...", file=sys.stderr)

        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', path],
                capture_output=True, text=True, timeout=15
            )
            info = json.loads(result.stdout)

            fmt = info.get('format', {})
            duration = float(fmt.get('duration', 0))
            bitrate = int(fmt.get('bit_rate', 0)) // 1000 if fmt.get('bit_rate') else 0

            art_w = art_h = 0
            for stream in info.get('streams', []):
                if stream.get('codec_type') == 'video':
                    art_w = stream.get('width', 0)
                    art_h = stream.get('height', 0)
                    break

            rel = path.replace(root + '/', '')
            size_mb = file_size / 1_000_000
            mins = int(duration // 60) if duration else 0
            secs = int(duration % 60) if duration else 0
            max_art_dim = max(art_w, art_h)

            # Risk: buffer starvation
            # Photo (32MB RAM, ~25MB buffer): files > 80MB on bad chain = risk
            # Video (64MB RAM, ~50MB buffer): files > 150MB = risk
            # Large art (>1000px) eats buffer when decoded

            risk_photo = 'OK'
            risk_video = 'OK'

            if size_mb > 120 or (size_mb > 60 and max_art_dim > 1000):
                risk_photo = 'HIGH'
            elif size_mb > 80 or (size_mb > 50 and max_art_dim > 1000):
                risk_photo = 'MED'

            if size_mb > 250 or (size_mb > 150 and max_art_dim > 1000):
                risk_video = 'HIGH'
            elif size_mb > 180 or (size_mb > 120 and max_art_dim > 1000):
                risk_video = 'MED'

            results.append({
                'path': rel,
                'size_mb': round(size_mb, 1),
                'duration_m': mins,
                'duration_s': secs,
                'bitrate': bitrate,
                'art_w': art_w,
                'art_h': art_h,
                'risk_photo': risk_photo,
                'risk_video': risk_video,
            })

            if max_art_dim > 1000:
                art_oversized.append({
                    'path': rel,
                    'art_w': art_w,
                    'art_h': art_h,
                    'size_mb': round(size_mb, 1),
                })

        except Exception as e:
            pass

print(json.dumps({
    'total': total,
    'scanned': len(results),
    'art_oversized_count': len(art_oversized),
    'art_oversized': art_oversized,
    'results': results,
}))
