import os
import shutil
from pathlib import Path

LOG_FILE = Path(__file__).parent.parent / "output" / "folder_sync_log.txt"

def main():
    if not LOG_FILE.exists():
        print("Error: Sync log not found. Cannot undo.")
        return

    print("Reading log to reverse moves...")
    
    moves = []
    with open(LOG_FILE, 'r') as f:
        lines = f.readlines()
        
        current_source_name = None
        for line in lines:
            line = line.strip()
            if line.startswith("[FOUND]"):
                current_source_name = line.replace("[FOUND] ", "")
            elif line.startswith("->"):
                target_path = line.replace("-> ", "")
                if current_source_name and target_path:
                    # We don't have the EXACT original source path in the log,
                    # but we know it was somewhere under the music root.
                    # This is tricky - the log shows: [FOUND] filename -> new_full_path
                    # I need to confirm if we can find where they came from.
                    pass

    print("Wait, the log only shows the TARGET, not the ORIGINAL source path.")
    print("I need to find a better way to reverse this.")

if __name__ == "__main__":
    main()
