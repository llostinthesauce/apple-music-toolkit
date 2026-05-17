import os
import plistlib
import subprocess
import urllib.parse
from pathlib import Path

CLOUD_XML = Path("~/Desktop/amCloud.xml").expanduser()

def run_applescript(script):
    process = subprocess.Popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = process.communicate()
    return out.strip(), err.strip()

def main():
    print("Loading Cloud Blueprint to find target locations...")
    with open(CLOUD_XML, 'rb') as f:
        cloud_data = plistlib.load(f)
    
    cloud_tracks = cloud_data.get('Tracks', {})
    
    print("Purging 'Dead' entries from Music (tracks with missing files)...")
    # This script deletes ONLY tracks that Music app can't find on disk
    purge_script = '''
    tell application "Music"
        set deletedCount to 0
        set deadTracks to (every file track of library playlist 1 whose location is missing value)
        repeat with aTrack in deadTracks
            delete aTrack
            set deletedCount + 1
        end repeat
        return deletedCount
    end tell
    '''
    deleted, err = run_applescript(purge_script)
    print(f"Removed {deleted} broken entries from Music app.")

    print("Re-scanning music folders to pick up moved files...")
    # We add the root folder back - Music app is smart enough to only add new/moved files
    root_folder = os.environ.get("MUSIC_ROOT", str(Path.home() / "Music"))
    add_script = f'tell application "Music" to add POSIX file "{root_folder}"'
    run_applescript(add_script)
    
    print("\nRefresh Triggered! The Music app is now re-indexing your files.")
    print("This may take a few minutes to complete in the background of the Music app.")

if __name__ == "__main__":
    main()
