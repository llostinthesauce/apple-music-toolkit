import json, subprocess, os, tempfile, shutil

SCAN_FILE = os.environ.get('SCAN_RESULTS', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'output', 'scan_results.json'))
with open(SCAN_FILE) as f:
    content = f.read()
idx = content.index('{"total":')
data = json.loads(content[idx:])

root = os.environ.get('MUSIC_ROOT', os.path.expanduser('~/Music'))
oversized = [a for a in data['art_oversized'] if max(a['art_w'], a['art_h']) >= 1000]

print(f"Compressing {len(oversized)} tracks with art >= 1000px -> 600x600")
print()

success = 0
skipped = 0
failed = 0
errors = []

for i, item in enumerate(oversized):
    path = os.path.join(root, item['path'])
    if not os.path.exists(path):
        failed += 1
        continue

    try:
        tmp_art = tempfile.mktemp(suffix='.jpg')
        tmp_resized = tempfile.mktemp(suffix='.jpg')
        tmp_output = tempfile.mktemp(suffix='.m4a')

        # Extract cover art
        subprocess.run([
            'ffmpeg', '-y', '-v', 'quiet',
            '-i', path, '-an', '-vcodec', 'copy', tmp_art
        ], check=True, timeout=30)

        if os.path.getsize(tmp_art) < 100:
            os.unlink(tmp_art)
            skipped += 1
            continue

        # Resize with sips (macOS native)
        subprocess.run([
            'sips', '-Z', '600', tmp_art, '--out', tmp_resized
        ], check=True, timeout=30)

        # Replace art: copy audio, embed resized art
        subprocess.run([
            'ffmpeg', '-y', '-v', 'quiet',
            '-i', path, '-i', tmp_resized,
            '-map', '0:a', '-map', '1:v',
            '-c:a', 'copy', '-c:v', 'copy',
            '-disposition:v:0', 'attached_pic',
            tmp_output
        ], check=True, timeout=60)

        shutil.move(tmp_output, path)

        for f in [tmp_art, tmp_resized]:
            if os.path.exists(f):
                os.unlink(f)

        success += 1
        if success % 100 == 0:
            print(f"  {success}/{len(oversized)}...")

    except Exception as e:
        failed += 1
        errors.append(f"{item['path']}: {e}")
        for f in [tmp_art, tmp_resized, tmp_output]:
            if os.path.exists(f):
                os.unlink(f)

print(f"\nDone! Success: {success}, Skipped: {skipped}, Failed: {failed}")
if errors:
    print("Errors:")
    for e in errors[:10]:
        print(f"  {e}")
    if len(errors) > 10:
        print(f"  ... and {len(errors)-10} more")
