import os
import subprocess
from pathlib import Path
import re

# Local library root
LIB_ROOT = Path("/Users/corbinshanks/Music/foriPod")
PLAYLIST_OUT = LIB_ROOT / "Playlists"

def run_applescript(script):
    process = subprocess.Popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate()
    return stdout.strip(), stderr

def main():
    PLAYLIST_OUT.mkdir(parents=True, exist_ok=True)
    
    print("Fetching user playlists...")
    # Get user playlist names
    script_playlists = 'tell application "Music" to get name of every playlist whose special kind is none'
    stdout, stderr = run_applescript(script_playlists)
    
    if not stdout:
        print("No user playlists found.")
        return
        
    # Split by comma and space (Music App default return)
    playlist_names = [p.strip() for p in stdout.split(',')]
    print(f"Found {len(playlist_names)} playlists.")

    for name in playlist_names:
        print(f"Exporting '{name}'...")
        # Get location of all tracks in the playlist
        script_tracks = f'''
        tell application "Music"
            set track_locations to ""
            set p to playlist "{name}"
            set t_count to count tracks of p
            repeat with i from 1 to t_count
                try
                    set loc to location of track i of p
                    set track_locations to track_locations & (POSIX path of loc) & "\n"
                on error
                    -- Skip tracks without location
                end try
            end repeat
            return track_locations
        end tell
        '''
        stdout, stderr = run_applescript(script_tracks)
        
        if not stdout:
            print(f"  No tracks found for '{name}'.")
            continue
            
        locations = [l.strip() for l in stdout.split('\n') if l.strip()]
        
        # Clean up safe filename
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
        m3u_file = PLAYLIST_OUT / f"{safe_name}.m3u"
        
        m3u_lines = ["#EXTM3U"]
        for loc in locations:
            try:
                # Convert to relative path from LIB_ROOT
                p = Path(loc)
                rel_p = p.relative_to(LIB_ROOT)
                m3u_lines.append(str(rel_p))
            except ValueError:
                # Track is outside library root, skip or use absolute
                pass
                
        with open(m3u_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(m3u_lines))
            f.write("\n")
            
        print(f"  Saved {len(m3u_lines)-1} tracks to {m3u_file.name}")

    print("\nPlaylist export complete!")

if __name__ == "__main__":
    main()
