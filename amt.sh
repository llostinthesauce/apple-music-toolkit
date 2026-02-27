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
    echo "  6) Export     - Share your playlists with Navidrome and the world"
    echo ""
    echo -e "${GOLD}The Craft${NC}"
    echo "  7) Fill       - Complete your collection by downloading missing tracks"
    echo "  8) Art        - Sourcing the highest quality visual covers"
    echo "  9) Lyrics     - Embedding the poetry into your files"
    echo " 10) Transcode  - Moving between formats without losing a single bit"
    echo ""
    echo -e "${GOLD}The Bridge${NC}"
    echo " 11) Spotify    - Sync your curated world to the cloud"
    echo ""
    echo -e "${SILVER}  0) Exit${NC}"
    echo ""
}

while true; do
    show_header
    show_menu
    read -p "What would you like to do? [0-11]: " choice

    case $choice in
        1)
            python3 main/align.py
            ;;
        2)
            python3 main/polish.py
            ;;
        3)
            python3 main/audit.py
            ;;
        4)
            python3 main/history.py
            ;;
        5)
            python3 main/playlists.py
            ;;
        6)
            python3 main/export.py
            ;;
        7)
            python3 main/fill.py
            ;;
        8)
            python3 main/art.py
            ;;
        9)
            python3 main/lyrics.py
            ;;
        10)
            python3 main/transcode.py
            ;;
        11)
            python3 main/spotify.py
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
