import subprocess
from collections import defaultdict
import re
from pathlib import Path

def run_applescript(script):
    process = subprocess.Popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate()
    return stdout.strip(), stderr

def main():
    print("Fetching compilation data from Apple Music (fast dump)...")
    
    dump_file = Path("comp_merge_dump.txt")
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
                set a to artist of t
                set alb to album of t
                set isComp to compilation of t
                if alb is not "" then
                    set line_text to (pid & "|" & aa & "|" & a & "|" & alb & "|" & isComp & "\\n")
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

    # Group by Album
    album_tracks = defaultdict(list)
    for line in lines:
        parts = line.split('|')
        if len(parts) >= 5:
            pid, aa, a, alb, is_comp = parts[0], parts[1], parts[2], parts[3], parts[4]
            album_tracks[alb].append({
                'pid': pid,
                'aa': aa,
                'a': a,
                'comp': is_comp == 'true'
            })

    tracks_to_fix = []
    albums_fixed = []
    
    for alb, tracks in album_tracks.items():
        if len(tracks) < 2: continue
        
        # A compilation usually has different track artists and is not marked as a compilation,
        # OR it has different album artists breaking it apart.
        album_artists = set(t['aa'] for t in tracks if t['aa'])
        track_artists = set(t['a'] for t in tracks if t['a'])
        
        is_soundtrack = "soundtrack" in alb.lower() or "motion picture" in alb.lower()
        
        needs_fix = False
        # If there are multiple distinct track artists and it's NOT marked as a compilation
        if len(track_artists) > 1 and any(not t['comp'] for t in tracks):
            # And it's either a soundtrack or lacks a unifying album artist
            if is_soundtrack or len(album_artists) != 1:
                needs_fix = True
                
        # If there are multiple album artists, it's definitely split
        if len(album_artists) > 1:
            needs_fix = True
            
        if needs_fix:
            albums_fixed.append(alb)
            for t in tracks:
                tracks_to_fix.append(t['pid'])

    if not tracks_to_fix:
        print("No compilations need grouping.")
        dump_file.unlink()
        return

    print(f"\\nFound {len(albums_fixed)} split compilations/soundtracks.")
    for a in albums_fixed[:10]:
        print(f" - {a}")
    if len(albums_fixed) > 10: print(" ...")

    updated = 0
    batch_size = 50
    print(f"\\nApplying 'Compilation' flag and 'Various Artists' to {len(tracks_to_fix)} tracks...")
    
    for i in range(0, len(tracks_to_fix), batch_size):
        batch = tracks_to_fix[i:i+batch_size]
        script_set = ['tell application "Music"']
        for pid in batch:
            script_set.append('try')
            script_set.append(f'    set targetTrack to (some track whose persistent ID is "{pid}")')
            script_set.append(f'    set compilation of targetTrack to true')
            script_set.append(f'    set album artist of targetTrack to "Various Artists"')
            script_set.append('end try')
        script_set.append('end tell')
        
        run_applescript("\\n".join(script_set))
        updated += len(batch)
        print(f"  Batch {i//batch_size + 1} processed...", end="\\r")

    print(f"\\n\\nSuccessfully grouped {updated} compilation/soundtrack tracks!")
    dump_file.unlink()

if __name__ == "__main__":
    main()
