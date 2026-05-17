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
    
    if not dump_file.exists():
        print("Failed to dump data.")
        return

    lines = []
    with open(dump_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    # Group by Artist
    artist_albums = defaultdict(set)
    for line in lines:
        parts = line.split('|')
        if len(parts) >= 3:
            aa = parts[1].lower().strip()
            alb = parts[2].strip()
            artist_albums[aa].add(alb)

    merge_candidates = []
    
    # Regex to find "fluff" in album names
    fluff_pattern = re.compile(r'\s*[\(\[].*?(bonus|deluxe|remaster|edition|version|mix|pt\.|part|disc|cd).*?[\)\]]', re.IGNORECASE)
    
    for artist, albums in artist_albums.items():
        if len(albums) < 2: continue
        
        albums = list(albums)
        for i in range(len(albums)):
            for j in range(i+1, len(albums)):
                a1 = albums[i]
                a2 = albums[j]
                
                # Check if one is just the other plus some fluff
                c1 = fluff_pattern.sub('', a1).strip()
                c2 = fluff_pattern.sub('', a2).strip()
                
                # Also check exact prefix match (e.g. "Album" and "Album _ Bonus")
                if c1 == c2 and c1 != "":
                    base = c1
                    longest = a1 if len(a1) > len(a2) else a2
                    shortest = a2 if len(a1) > len(a2) else a1
                    
                    merge_candidates.append({
                        'artist': artist,
                        'base': base,
                        'albums': [a1, a2],
                        'target': shortest
                    })

    if not merge_candidates:
        print("No obvious album name variations found to merge.")
        dump_file.unlink()
        return

    print(f"\nFound {len(merge_candidates)} potential album merges:")
    for c in merge_candidates:
        print(f" - Artist: {c['artist'].title()}")
        print(f"   Merge: '{c['albums'][0]}' AND '{c['albums'][1]}'")
        print(f"   Target -> '{c['target']}'")
        
    print("\nIf you want to merge these, let me know and I will write a script to unify their names.")
    dump_file.unlink()

if __name__ == "__main__":
    main()
