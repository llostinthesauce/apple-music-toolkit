#!/usr/bin/env bash

# Elegant CLI Colors
GOLD='\033[1;33m'
SILVER='\033[0;37m'
BLACK='\033[0;30m'
NC='\033[0m'

function show_header() {
    clear
    echo -e "${SILVER}"
    cat << "EOF"
    ___    __  __  ______
   /   |  /  |/  |/_  __/
  / /| | / /|_/ /  / /   
 / ___ |/ /  / /  / /    
/_/  |_/_/  /_/  /_/     
                         
EOF
    echo -e "${NC}      apple music toolkit\n"
    echo "================================================================"
    echo "             Simplicity is the ultimate sophistication.          "
    echo "================================================================"
    echo ""
    mkdir -p output
}

function show_menu() {
    echo -e "${GOLD}The Foundation${NC}"
    echo "  1) Align      - Perfect your file names and track numbering from XML"
    echo "  2) Polish     - Unify Album Artists and audit beautiful artwork"
    echo "  3) Audit      - Ensure library wholeness and find corruption"
    echo ""
    echo -e "${GOLD}The Legacy${NC}"
    echo "  4) History    - Restore your lifetime of play counts and ratings"
    echo "  5) Playlists  - Rebuild your structure with high-fidelity matching"
    echo "  6) Build Plst - Rebuild playlists from XML export"
    echo "  7) Export     - Share your playlists with Navidrome and the world"
    echo ""
    echo -e "${GOLD}The Craft${NC}"
    echo "  8) Fill       - Complete your collection by downloading missing tracks"
    echo "  9) Art        - Sourcing the highest quality visual covers"
    echo " 10) Art Fix    - Normalize and embed album artwork across the library"
    echo " 11) Lyrics     - Embedding the poetry into your files"
    echo " 12) Transcode  - Moving between formats without losing a single bit"
    echo ""
    echo -e "${GOLD}The Bridge${NC}"
    echo " 13) Spotify    - Sync your curated world to the cloud"
    echo ""
    echo -e "${GOLD}The Guardian${NC}"
    echo " 14) Validate   - Full library integrity check (saves report)"
    echo " 15) Fix        - Apply fixes from last validation (dry-run first)"
    echo " 16) Scan Vol   - Analyze physical loudness of tracks"
    echo ""
    echo -e "${SILVER}  0) Exit${NC}"
    echo ""
}

function setup_env() {
    if [ ! -f ".env" ]; then
        echo -e "${GOLD}Welcome! Let's set up your Apple Music Toolkit environment.${NC}"
        read -p "Enter your Music Library root [~/Music]: " user_music_root
        user_music_root=${user_music_root:-~/Music}

        read -p "Enter your Apple Music XML export path [~/Desktop/Library.xml]: " user_xml
        user_xml=${user_xml:-~/Desktop/Library.xml}

        read -p "Enter your iPod mount point [/Volumes/IPOD]: " user_ipod
        user_ipod=${user_ipod:-/Volumes/IPOD}

        echo "MUSIC_ROOT=\"$user_music_root\"" > .env
        echo "XML_DEFAULT=\"$user_xml\"" >> .env
        echo "IPOD_MOUNT=\"$user_ipod\"" >> .env
        echo -e "${GOLD}Configuration saved to .env. You can edit this file anytime.${NC}\n"
        echo -e "${GOLD}Tip: See .env.example for additional configurable variables.${NC}\n"
        sleep 1
    fi

    # Export vars in .env to the shell environment for Python to read
    set -a
    source .env
    set +a

    # Detect Python: prefer venv, fall back to system python3
    if [ -f ".venv/bin/python3" ]; then
        PY=".venv/bin/python3"
    elif command -v python3 &> /dev/null; then
        PY="python3"
    else
        echo -e "${GOLD}Error: python3 not found. Install Python 3 and create a venv.${NC}"
        exit 1
    fi
}

setup_env

while true; do
    show_header
    show_menu
    read -p "What would you like to do? [0-16]: " choice

    case $choice in
        1)
            $PY main/align.py
            ;;
        2)
            $PY main/polish.py
            ;;
        3)
            $PY main/audit.py
            ;;
        4)
            $PY main/history.py
            ;;
        5)
            $PY main/playlists.py
            ;;
        6)
            $PY main/build_playlists.py
            ;;
        7)
            $PY main/export.py
            ;;
        8)
            $PY main/fill.py
            ;;
        9)
            $PY main/art.py
            ;;
        10)
            $PY main/art_fix.py
            ;;
        11)
            $PY main/lyrics.py
            ;;
        12)
            $PY main/transcode.py
            ;;
        13)
            $PY main/spotify.py
            ;;
        14)
            $PY main/validate.py
            ;;
        15)
            $PY main/fix.py
            ;;
        16)
            $PY main/loudness_scan.py
            ;;
        0)
            echo "Stay hungry. Stay foolish. Goodbye!"
            exit 0 
            ;;
        *) 
            echo -e "Invalid selection."
            sleep 1
            continue
            ;;
    esac

    echo -e "\nTask complete."
    read -n 1 -s -r -p "Press any key to return..."
done
