import subprocess
from collections import defaultdict
import re
from pathlib import Path

def run_applescript(script):
    process = subprocess.Popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate()
    return stdout.strip(), stderr

def main():
    print("Fetching album data from Apple Music (fast dump)...")
    
    dump_file = Path("album_merge_dump.txt")
    script_dump = f'''
    set output_file to "{dump_file.absolute()}"
    tell application "Music"
        set allTracks to tracks of playlist "Music"
        set file_ref to open for access POSIX file output_file with write permission
        set eof file_ref to 0
        repeat with t in allTracks
            try
                set pid to persistent ID of t
                set aa to album artist of t
                if aa is "" then
                    set aa to artist of t
                end if
                set alb to album of t
                if alb is not "" then
                    set line_text to (pid & "|" & aa & "|" & alb & "\\n")
                    write line_text to file_ref as «class utf8»
                end if
            end try
        end repeat
        close access file_ref
    end tell
    '''
    
    run_applescript(script_dump)
    
    lines = []
    with open(dump_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    # Group tracks by (Artist, Album)
    album_tracks = defaultdict(list)
    artist_albums = defaultdict(set)
    for line in lines:
        parts = line.split('|')
        if len(parts) >= 3:
            pid = parts[0]
            aa = parts[1].lower().strip()
            alb = parts[2].strip()
            artist_albums[aa].add(alb)
            album_tracks[(aa, alb)].append(pid)

    merges = []
    fluff_pattern = re.compile(r'\s*[\(\[].*?(bonus|deluxe|remaster|edition|version|mix|pt\.|part|disc|cd).*?[\)\]]', re.IGNORECASE)
    
    for artist, albums in artist_albums.items():
        if len(albums) < 2: continue
        albums = list(albums)
        for i in range(len(albums)):
            for j in range(i+1, len(albums)):
                a1 = albums[i]
                a2 = albums[j]
                
                c1 = fluff_pattern.sub('', a1).strip()
                c2 = fluff_pattern.sub('', a2).strip()
                
                if c1 == c2 and c1 != "":
                    longest = a1 if len(a1) > len(a2) else a2
                    shortest = a2 if len(a1) > len(a2) else a1
                    
                    merges.append({
                        'artist': artist,
                        'source': longest,
                        'target': shortest,
                        'tracks': album_tracks[(artist, longest)]
                    })

    if not merges:
        print("No albums to merge.")
        dump_file.unlink()
        return

    print(f"\\nFound {len(merges)} variations to merge into base names.")
    
    updated = 0
    batch_size = 50
    
    for merge in merges:
        print(f"Merging: '{merge['source']}' -> '{merge['target']}' ({len(merge['tracks'])} tracks)")
        tracks = merge['tracks']
        
        for i in range(0, len(tracks), batch_size):
            batch = tracks[i:i+batch_size]
            script_set = ['tell application "Music"']
            safe_target = merge['target'].replace('"', '\\"')
            for pid in batch:
                script_set.append('try')
                script_set.append(f'    set album of (some track whose persistent ID is "{pid}") to "{safe_target}"')
                script_set.append('end try')
            script_set.append('end tell')
            
            run_applescript("\\n".join(script_set))
            updated += len(batch)
            print(f"  Batch {i//batch_size + 1} processed...", end="\\r")
        print()

    print(f"\\nSuccessfully unified {updated} tracks across {len(merges)} albums.")
    dump_file.unlink()

if __name__ == "__main__":
    main()
