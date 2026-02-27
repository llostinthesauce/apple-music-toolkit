import plistlib
import subprocess
from pathlib import Path
import time
import argparse

def run_applescript(script):
    process = subprocess.Popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate()
    return stdout.strip(), stderr

def main():
    parser = argparse.ArgumentParser(description="Migrate play counts and ratings from Apple Music XML to local library.")
    parser.add_argument("--xml", required=True, help="Path to Apple Music Library XML")
    parser.add_argument("--dry-run", action="store_true", help="Perform a dry run")
    args = parser.parse_args()

    xml_path = Path(args.xml)
    if not xml_path.exists():
        print(f"Error: XML not found at {xml_path}")
        return

    print(f"Loading XML from {xml_path}...")
    with open(xml_path, 'rb') as f:
        lib_data = plistlib.load(f)

    tracks_data = lib_data.get('Tracks', {})
    
    metadata_map = {}
    for tid, track in tracks_data.items():
        artist = str(track.get('Artist', '')).lower().strip()
        album = str(track.get('Album', '')).lower().strip()
        name = str(track.get('Name', '')).lower().strip()
        
        key = (artist, album, name)
        metadata_map[key] = {
            'played_count': track.get('Play Count', 0),
            'skipped_count': track.get('Skip Count', 0),
            'rating': track.get('Rating', 0),
        }

    print(f"Parsed metadata for {len(metadata_map)} tracks from XML.")

    dump_file = Path("current_library_metadata_dump.txt")
    script_dump_fast = f'''
    set output_file to "{dump_file.absolute()}"
    tell application "Music"
        set allPIDs to persistent ID of tracks of playlist "Music"
        set allNames to name of tracks of playlist "Music"
        set allArtists to artist of tracks of playlist "Music"
        set allAlbums to album of tracks of playlist "Music"
    end tell
    
    set file_ref to open for access POSIX file output_file with write permission
    set eof file_ref to 0
    repeat with i from 1 to count of allPIDs
        set line_text to (item i of allPIDs & "|" & item i of allNames & "|" & item i of allArtists & "|" & item i of allAlbums & "\n")
        write line_text to file_ref as «class utf8»
    end repeat
    close access file_ref
    '''
    print("Dumping current Music library state...")
    run_applescript(script_dump_fast)

    if not dump_file.exists():
        print("Error: Failed to dump current library state.")
        return

    print("Matching tracks and preparing updates...")
    update_batches = []
    current_batch = []
    batch_size = 50
    
    match_count = 0
    with open(dump_file, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('|')
            if len(parts) < 4: continue
            
            pid, name, artist, album = parts[0], parts[1], parts[2], parts[3]
            key = (artist.lower().strip(), album.lower().strip(), name.lower().strip())
            
            if key in metadata_map:
                meta = metadata_map[key]
                if meta['played_count'] > 0 or meta['skipped_count'] > 0 or meta['rating'] > 0:
                    cmd = 'try\n'
                    cmd += f'    tell (some track whose persistent ID is "{pid}")\n'
                    if meta['played_count'] > 0:
                        cmd += f'        set played count to {meta["played_count"]}\n'
                    if meta['skipped_count'] > 0:
                        cmd += f'        set skipped count to {meta["skipped_count"]}\n'
                    if meta['rating'] > 0:
                        cmd += f'        set rating to {meta["rating"]}\n'
                    cmd += '    end tell\n'
                    cmd += 'end try'
                    
                    current_batch.append(cmd)
                    match_count += 1
                    
                    if len(current_batch) >= batch_size:
                        update_batches.append(current_batch)
                        current_batch = []

    if current_batch:
        update_batches.append(current_batch)

    print(f"Matched {match_count} tracks.")
    if args.dry_run:
        print(f"[DRY RUN] Would have updated {match_count} tracks in {len(update_batches)} batches.")
    else:
        print(f"Updating {match_count} tracks in {len(update_batches)} batches...")
        for i, batch in enumerate(update_batches):
            script = 'tell application "Music"\n' + "\n".join(batch) + '\nend tell'
            run_applescript(script)
            if (i + 1) % 10 == 0 or (i + 1) == len(update_batches):
                print(f"  Batch {i+1}/{len(update_batches)} applied...")

    print("\nMetadata migration complete!")
    if dump_file.exists():
        dump_file.unlink()

if __name__ == "__main__":
    main()
