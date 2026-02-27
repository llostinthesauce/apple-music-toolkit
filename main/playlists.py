import plistlib
import subprocess
from pathlib import Path
import re
import argparse

def run_applescript(script):
    process = subprocess.Popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate()
    return stdout.strip(), stderr

def main():
    parser = argparse.ArgumentParser(description="Rebuild playlists from Apple Music XML.")
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
    playlists_data = lib_data.get('Playlists', [])

    print(f"Parsed {len(tracks_data)} tracks and {len(playlists_data)} playlists from XML.")

    # Filter out system playlists
    user_playlists = []
    for p in playlists_data:
        if p.get('Master') or p.get('Distinguished Kind'):
            continue
        if p.get('Name') in ['Library', 'Music', 'Movies', 'TV Shows', 'Podcasts', 'Audiobooks', 'Genius']:
            continue
        user_playlists.append(p)

    print(f"Found {len(user_playlists)} user playlists to rebuild.")

    for p in user_playlists:
        name = p.get('Name', 'Untitled Playlist').replace('"', '\\"')
        items = p.get('Playlist Items', [])
        
        if args.dry_run:
            print(f"[DRY RUN] Would rebuild '{name}' with {len(items)} tracks.")
            continue

        print(f"\n>>> Rebuilding '{name}' ({len(items)} tracks)...")

        # Create playlist
        script_create = f'tell application "Music" to make new playlist with properties {{name:"{name}"}}'
        run_applescript(script_create)

        # Build track search and add script
        batch_size = 30
        for i in range(0, len(items), batch_size):
            batch_items = items[i:i+batch_size]
            script_lines = ['tell application "Music"']
            
            for item in batch_items:
                track_id = str(item.get('Track ID'))
                track_info = tracks_data.get(track_id)
                if not track_info:
                    continue
                
                t_name = track_info.get('Name', '').replace('"', '\\"')
                t_artist = track_info.get('Artist', '').replace('"', '\\"')
                t_album = track_info.get('Album', '').replace('"', '\\"')
                
                # Use AppleScript to find the track in the library and add to playlist
                script_lines.append('try')
                script_lines.append(f'    set t to (some track whose name is "{t_name}" and artist is "{t_artist}" and album is "{t_album}")')
                script_lines.append(f'    duplicate t to playlist "{name}"')
                script_lines.append('end try')
            
            script_lines.append('end tell')
            
            # Execute batch
            run_applescript("\n".join(script_lines))
            if (i + batch_size) % 150 == 0 or (i + batch_size) >= len(items):
                print(f"  Processed {min(i+batch_size, len(items))}/{len(items)} tracks...")

        print(f"  Done rebuilding '{name}'.")

    print("\nRebuild process complete!")

if __name__ == "__main__":
    main()
