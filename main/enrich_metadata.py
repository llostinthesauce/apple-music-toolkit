import plistlib
import subprocess
from pathlib import Path
import re

XML_PATH = Path("iCloudLibraryforSpotifyImport.xml")

def run_applescript(script):
    process = subprocess.Popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate()
    return stdout.strip(), stderr

def normalize(s):
    return re.sub(r'[^\w]', '', str(s)).lower().strip()

def main():
    if not XML_PATH.exists():
        print(f"Error: XML not found at {XML_PATH}")
        return

    print(f"Loading XML from {XML_PATH}...")
    with open(XML_PATH, 'rb') as f:
        lib_data = plistlib.load(f)

    tracks_data = lib_data.get('Tracks', {})
    
    metadata_map = {}
    for tid, track in tracks_data.items():
        artist = normalize(track.get('Artist', ''))
        album = normalize(track.get('Album', ''))
        name = normalize(track.get('Name', ''))
        
        genre = track.get('Genre', '')
        year = track.get('Year', 0)
        
        if genre or year:
            key = (artist, album, name)
            metadata_map[key] = {
                'genre': genre,
                'year': year
            }

    print(f"Parsed Genre/Year data for {len(metadata_map)} tracks from XML.")

    dump_file = Path("metadata_enrich_dump.txt")
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
        set line_text to (item i of allPIDs & "|" & item i of allNames & "|" & item i of allArtists & "|" & item i of allAlbums & "\\n")
        write line_text to file_ref as «class utf8»
    end repeat
    close access file_ref
    '''
    print("Dumping current Music library state...")
    run_applescript(script_dump_fast)

    lines = []
    if dump_file.exists():
        with open(dump_file, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if l.strip()]

    print("Matching tracks and preparing updates...")
    update_batches = []
    current_batch = []
    batch_size = 50
    
    match_count = 0
    for line in lines:
        parts = line.strip().split('|')
        if len(parts) < 4: continue
        
        pid, name, artist, album = parts[0], parts[1], parts[2], parts[3]
        key = (normalize(artist), normalize(album), normalize(name))
        
        if key in metadata_map:
            meta = metadata_map[key]
            if meta['genre'] or meta['year']:
                cmd = 'try\\n'
                cmd += f'    tell (some track whose persistent ID is "{pid}")\\n'
                if meta['genre']:
                    safe_genre = meta['genre'].replace('"', '\\"')
                    cmd += f'        set genre to "{safe_genre}"\\n'
                if meta['year'] > 0:
                    cmd += f'        set year to {meta["year"]}\\n'
                cmd += '    end tell\\n'
                cmd += 'end try'
                
                # Unescape for actual apple script string
                cmd = cmd.replace("\\n", "\n")
                
                current_batch.append(cmd)
                match_count += 1
                
                if len(current_batch) >= batch_size:
                    update_batches.append(current_batch)
                    current_batch = []

    if current_batch:
        update_batches.append(current_batch)

    print(f"Matched {match_count} tracks.")
    print(f"Updating Genre/Year for {match_count} tracks in {len(update_batches)} batches...")
    
    for i, batch in enumerate(update_batches):
        script = 'tell application "Music"\n' + "\n".join(batch) + '\nend tell'
        run_applescript(script)
        if (i + 1) % 10 == 0 or (i + 1) == len(update_batches):
            print(f"  Batch {i+1}/{len(update_batches)} applied...", end="\r")

    print("\nGenre/Year enrichment complete!")
    if dump_file.exists():
        dump_file.unlink()

if __name__ == "__main__":
    main()
