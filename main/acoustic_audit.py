import os
import subprocess
import json
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

CACHE_FILE = Path(__file__).parent.parent / ".audio_fingerprints.json"

def get_fingerprint(fpath):
    """Run fpcalc to get the audio fingerprint."""
    try:
        # -raw provides the fingerprint, -length 120 scans first 2 mins
        cmd = ['fpcalc', '-raw', '-length', '120', str(fpath)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        fingerprint = None
        for line in result.stdout.split('\n'):
            if line.startswith('FINGERPRINT='):
                fingerprint = line.split('=')[1]
                break
        return fingerprint
    except Exception as e:
        return None

def main():
    parser = argparse.ArgumentParser(description="Acoustic fingerprint audit of music library.")
    parser.add_argument("--root", required=True, help="Path to music library")
    args = parser.parse_args()

    lib_root = Path(args.root)
    
    # Load existing cache
    cache = {}
    if CACHE_FILE.exists():
        print(f"Loading existing fingerprints from {CACHE_FILE}...")
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)

    # Find all audio files
    files = []
    for ext in [".m4a", ".mp3", ".wav", ".flac"]:
        files.extend(list(lib_root.glob(f"**/*{ext}")))
    
    # Filter out hidden files
    files = [f for f in files if not f.name.startswith("._")]
    
    to_scan = [f for f in files if str(f) not in cache]
    
    print(f"Found {len(files)} files total. {len(to_scan)} need scanning.")

    if to_scan:
        print("Starting fingerprinting... this will take a while.")
        
        # Process in batches to save progress periodically
        batch_size = 100
        for i in range(0, len(to_scan), batch_size):
            batch = to_scan[i:i+batch_size]
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(lambda x: (str(x), get_fingerprint(x)), batch))
            
            for path, fp in results:
                if fp:
                    cache[path] = fp
            
            # Save progress
            with open(CACHE_FILE, 'w') as f:
                json.dump(cache, f)
            
            print(f"Progress: {min(i + batch_size, len(to_scan))}/{len(to_scan)} scanned...")

    # Analyze for duplicates
    print("\n--- Duplicate Analysis (Acoustically Identical) ---")
    fp_map = defaultdict(list)
    for path, fp in cache.items():
        # Only check files that still exist at the mapped path
        if os.path.exists(path):
            fp_map[fp].append(path)

    duplicates = {fp: paths for fp, paths in fp_map.items() if len(paths) > 1}
    
    report_path = Path(__file__).parent.parent / "output" / "acoustic_duplicates.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with open(report_path, "w") as f:
        f.write(f"Acoustic Duplicate Report\n")
        f.write("="*40 + "\n")
        if not duplicates:
            f.write("No identical audio waves found.\n")
        else:
            for fp, paths in duplicates.items():
                f.write(f"\n[Duplicate Set]\n")
                for p in paths:
                    f.write(f"  - {p}\n")

    print(f"Found {len(duplicates)} sets of acoustically identical files.")
    print(f"Detailed report saved to: {report_path}")

if __name__ == "__main__":
    main()
