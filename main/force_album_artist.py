import subprocess

def run_applescript(script):
    process = subprocess.Popen(['osascript', '-e', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = process.communicate()
    return stdout.strip(), stderr

def main():
    print("Fetching tracks with blank Album Artist from Apple Music...")
    
    script_get = '''
    tell application "Music"
        set targetTracks to (every track of playlist "Music" whose album artist is "")
        set out to ""
        repeat with t in targetTracks
            set pid to persistent ID of t
            set a to artist of t
            set out to out & pid & "|" & a & "\\n"
        end repeat
        return out
    end tell
    '''
    
    stdout, stderr = run_applescript(script_get)
    if stderr and not stdout:
        print(f"Error fetching: {stderr}")
        return
        
    lines = [l.strip() for l in stdout.split('\\n') if l.strip()]
    if not lines and "\\n" not in stdout:
        # fallback if \\n isn't literal
        lines = [l.strip() for l in stdout.split('\n') if l.strip()]

    print(f"Found {len(lines)} tracks to fix.")

    batch_size = 50
    updated = 0
    
    print("Pushing Album Artist tags into Apple Music database...")
    for i in range(0, len(lines), batch_size):
        batch = lines[i:i+batch_size]
        script_set = ['tell application "Music"']
        for line in batch:
            parts = line.split('|', 1)
            if len(parts) == 2:
                pid, artist = parts
                if artist: 
                    safe_artist = artist.replace('"', '\\"')
                    script_set.append('try')
                    script_set.append(f'    set album artist of (some track whose persistent ID is "{pid}") to "{safe_artist}"')
                    script_set.append('end try')
                    updated += 1
        script_set.append('end tell')
        
        run_applescript("\n".join(script_set))
        print(f"  Batch {i//batch_size + 1}/{(len(lines)-1)//batch_size + 1} processed...", end="\r")

    print(f"\nSuccessfully forced Apple Music to recognize Album Artist for {updated} tracks!")

if __name__ == "__main__":
    main()
