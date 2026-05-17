import os
import argparse
import subprocess
import urllib.parse
from pathlib import Path

def run_applescript(script):
    process = subprocess.Popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = process.communicate()
    return out.strip(), err.strip()

def main():
    parser = argparse.ArgumentParser(description="Force refresh an album in Music by purging and re-adding.")
    parser.add_argument("--album", required=True, help="Exact album name in Music library")
    parser.add_argument("--artist", required=True, help="Exact artist name in Music library")
    parser.add_argument("--root", default=os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")), help="Root of your music library")
    args = parser.parse_args()

    print(f"--- Force Refresh: {args.artist} - {args.album} ---")

    # 1. Purge from Music App
    print("Purging entries from Music app database...")
    purge_script = f'''
    tell application "Music"
        set deletedCount to 0
        set badTracks to (every file track of library playlist 1 whose album is "{args.album}" and artist is "{args.artist}")
        repeat with aTrack in badTracks
            delete aTrack
            set deletedCount to deletedCount + 1
        end repeat
        return deletedCount
    end tell
    '''
    count, err = run_applescript(purge_script)
    if err:
        print(f"Error during purge: {err}")
    else:
        print(f"Successfully deleted {count} library entries.")

    # 2. Find Folder on Disk
    print("Locating folder on disk...")
    lib_path = Path(args.root)
    # Search for the folder matching the artist/album structure
    # We use a glob to handle cases where the folder name might slightly differ
    found_folder = None
    for p in lib_path.glob(f"**/{args.artist}/{args.album}"):
        if p.is_dir():
            found_folder = p
            break
    
    if not found_folder:
        # Try a more relaxed search if the specific path wasn't found
        for p in lib_path.glob(f"**/*{args.album}*"):
            if p.is_dir() and args.artist.lower() in str(p).lower():
                found_folder = p
                break

    if found_folder:
        print(f"Found folder: {found_folder}")
        # 3. Re-add to Music
        print("Re-adding folder to Music library...")
        add_script = f'tell application "Music" to add POSIX file "{found_folder}"'
        out, err = run_applescript(add_script)
        if err:
            print(f"Error during re-add: {err}")
        else:
            print("Successfully re-added album. Check your Music app!")
    else:
        print(f"Error: Could not find folder for '{args.album}' under {args.root}")

if __name__ == "__main__":
    main()
