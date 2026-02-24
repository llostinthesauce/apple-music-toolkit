#!/usr/bin/env bash

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

function show_header() {
    clear
    echo -e "${CYAN}"
    cat << "EOF"
    ___    __  __  ______
   /   |  /  |/  |/_  __/
  / /| | / /|_/ /  / /   
 / ___ |/ /  / /  / /    
/_/  |_/_/  /_/  /_/     
                         
EOF
    echo -e "${NC}      apple music toolkit\n"
    echo "================================================================"
    echo "                 Welcome down the rabbit hole.                  "
    echo "================================================================"
    echo ""
    mkdir -p outputs/m3u_playlists outputs/m3u_raw
}

function show_menu() {
    echo -e "${YELLOW}Phase 1: Library Maintenance & Tagging${NC}"
    echo "  1) Fetch Missing Album Art"
    echo "  2) Tag Metadata from Folders"
    echo "  3) Merge Staging to Main Lib"
    echo "  4) FLAC to AAC Lossless Converter"
    echo "  5) Find Audio Duplicates (Acoustic Match)"
    echo "  6) Fetch & Embed Lyrics (LRCLIB)"
    echo ""
    echo -e "${YELLOW}Phase 2: Syncing & Cross-checking${NC}"
    echo "  7) Cross-check Local against Cloud XML"
    echo "  8) Convert Apple Music XML to M3U Playlists (for synced devices)"
    echo ""
    echo -e "${YELLOW}Phase 3: Spotify Importing${NC}"
    echo "  9) Import XML/Playlists to Spotify"
    echo " 10) Extract M3U from Library XML (raw paths, no local scanning)"
    echo ""
    echo -e "${RED}  0) Exit${NC}"
    echo ""
}

while true; do
    show_header
    show_menu
    read -p "Select an option [0-10]: " choice

    case $choice in
        1)
            echo -e "\n${CYAN}>>> Fetch Missing Album Art (fetch_album_art.py) <<<${NC}"
            echo "Scans a directory for folders lacking cover art and downloads it automatically"
            echo "from MusicBrainz or the CoverArtArchive."
            read -p "Enter root music directory to scan (e.g. ~/Music/foriPod): " root_dir
            if [ -n "$root_dir" ]; then
                python3 fetch_album_art.py --root "$root_dir"
            fi
            ;;
        2)
            echo -e "\n${CYAN}>>> Tag Metadata from Folders (tag_from_folders.py) <<<${NC}"
            echo "Assuming your music is stored hierarchically (Artist/Album/Song.mp3),"
            echo "this script will fix missing internal ID3/mp4 tags to match the folders."
            read -p "Enter root music directory to scan (e.g. ~/Music/foriPod): " root_dir
            if [ -n "$root_dir" ]; then
                python3 tag_from_folders.py --root "$root_dir"
            fi
            ;;
        3)
            echo -e "\n${CYAN}>>> Merge Staging to Main Lib (merge_staging.py) <<<${NC}"
            echo "Instantly moves files from a staging directory into your main library"
            echo "using the proper Artist/Album structure."
            read -p "Enter source staging folder path: " s_dir
            read -p "Enter destination main library path: " d_dir
            if [ -n "$s_dir" ] && [ -n "$d_dir" ]; then
                python3 merge_staging.py --source "$s_dir" --dest "$d_dir"
            fi
            ;;
        4)
            echo -e "\n${CYAN}>>> FLAC/WAV Lossless Audio Converter (convert_lossless.py) <<<${NC}"
            echo "Transcodes heavyweight lossless files down to a format of your choice"
            echo "while perfectly preserving their ID3/M4A metadata. Requires 'ffmpeg'."
            
            read -p "Enter root directory to scan (e.g. ~/Music/foriPod): " root_dir
            if [ -z "$root_dir" ]; then
                echo -e "${RED}Directory required. Returning to menu.${NC}"
                sleep 1; continue
            fi
            
            echo -e "\n${YELLOW}Output Format Preferences:${NC}"
            echo "  1) AAC (.m4a)   - Best for modern Apple devices"
            echo "  2) MP3 (.mp3)   - Best for maximum legacy compatibility"
            echo "  3) ALAC (.m4a)  - Apple Lossless (keeps original full quality)"
            read -p "Select format [1-3] (Default: 3): " f_choice
            
            format="alac"
            if [ "$f_choice" == "1" ]; then format="aac"; fi
            if [ "$f_choice" == "2" ]; then format="mp3"; fi
            
            if [ "$format" != "alac" ]; then
                echo -e "\n${YELLOW}Bitrate Settings:${NC}"
                echo "  1) 320k (Maximum Quality)"
                echo "  2) 256k (iTunes Plus Standard)"
                echo "  3) 192k (Good Quality, Space Saving)"
                read -p "Select bitrate [1-3] (Default: 2): " b_choice
                
                bitrate="256k"
                if [ "$b_choice" == "1" ]; then bitrate="320k"; fi
                if [ "$b_choice" == "3" ]; then bitrate="192k"; fi
                args="--root \"$root_dir\" --format \"$format\" --bitrate \"$bitrate\""
            else
                echo -e "\n${YELLOW}ALAC Defaults (44.1kHz, 16-bit) selected.${NC}"
                args="--root \"$root_dir\" --format \"alac\" --sample-rate \"44100\" --bit-depth \"16\""
            fi
            
            echo -e "\n${YELLOW}Advanced:${NC}"
            if [ "$format" != "alac" ]; then
                read -p "Custom Sample Rate override? (Leave blank to keep original, or enter e.g. 44100): " s_rate
                if [ -n "$s_rate" ]; then args="$args --sample-rate \"$s_rate\""; fi
            fi
            
            read -p "Delete original lossless files after successful conversion? (y/n): " do_delete
            if [[ "$do_delete" =~ ^[Yy]$ ]]; then args="$args --delete"; fi
            
            eval python3 convert_lossless.py $args
            ;;
        5)
            echo -e "\n${CYAN}>>> Find Audio Duplicates (find_audio_duplicates.py) <<<${NC}"
            echo "Identifies cloned audio files by listening to their acoustic fingerprint"
            echo "using Chromaprint/AcoustID. Needs internet access."
            read -p "Enter root directory to scan: " root_dir
            read -p "Auto-delete smaller bitrate duplicates? (y/n): " do_delete
            
            args="--root \"$root_dir\""
            if [[ "$do_delete" =~ ^[Yy]$ ]]; then args="$args --delete"; fi
            
            if [ -n "$root_dir" ]; then
                eval python3 find_audio_duplicates.py $args
            fi
            ;;
        6)
            echo -e "\n${CYAN}>>> Fetch & Embed Lyrics (fetch_lyrics.py) <<<${NC}"
            echo "Pulls lyrics from LRCLIB and permanently embeds them in the ID3 tags of"
            echo "your tracks so they render natively on iPods and Apple Music."
            read -p "Enter root directory to scan: " root_dir
            read -p "Overwrite files that already have lyrics? (y/n): " do_overwrite
            
            args="--root \"$root_dir\""
            if [[ "$do_overwrite" =~ ^[Yy]$ ]]; then args="$args --overwrite"; fi
            
            if [ -n "$root_dir" ]; then
                eval python3 fetch_lyrics.py $args
            fi
            ;;
        7)
            echo -e "\n${CYAN}>>> Cross-check Local against Cloud XML (cross_check.py) <<<${NC}"
            echo "Identifies what is missing from your local library in comparison to what"
            echo "is stored in your Apple Music/iCloud library."
            read -p "Enter path to Apple Music Cloud XML: " c_xml
            read -p "Enter path to Local Music Database XML: " l_xml
            
            o_txt="outputs/missing_analysis.txt"
            echo -e "${GREEN}Outputs will be routed to: $o_txt${NC}"
            
            if [ -n "$c_xml" ] && [ -n "$l_xml" ]; then
                python3 cross_check.py --cloud "$c_xml" --local "$l_xml" --output "$o_txt"
            fi
            ;;
        8)
            echo -e "\n${CYAN}>>> Convert Apple Music XML to M3U Playlists (convert.py) <<<${NC}"
            echo "Converts an Apple Music XML library into standard .m3u playlist files by"
            echo "resolving the tracks against files on your local drive using fuzzy-matching."
            read -p "Enter path to Apple Music XML: " xml_file
            read -p "Enter path to local music root folder (for matching): " local_folder
            read -p "Enter remote server path prefix (e.g. /mnt/music/foriPod) [Leave blank to use local paths]: " prefix_path
            
            out_dir="outputs/m3u_playlists"
            echo -e "${GREEN}Outputs will be routed to: $out_dir${NC}"
            
            if [ -n "$xml_file" ] && [ -n "$local_folder" ]; then
                cmd="python3 convert.py --source \"$xml_file\" --local \"$local_folder\" --output \"$out_dir\""
                if [ -n "$prefix_path" ]; then
                    cmd="$cmd --prefix \"$prefix_path\""
                fi
                eval $cmd
            fi
            ;;
        9)
            echo -e "\n${CYAN}>>> Import XML/Playlists to Spotify (import_to_spotify.py) <<<${NC}"
            echo "Automatically recreates your Apple Music library and playlists in a Spotify account."
            echo -e "${YELLOW}Please ensure SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET are set via env variables.${NC}"
            read -p "Enter path to Apple Music XML: " xml_file
            read -p "Sync Playlists? (y/n): " do_playlists
            read -p "Sync Liked Songs Library? (y/n): " do_library
            
            args="--source \"$xml_file\""
            if [[ "$do_playlists" =~ ^[Yy]$ ]]; then args="$args --playlists"; fi
            if [[ "$do_library" =~ ^[Yy]$ ]]; then args="$args --library"; fi
            
            if [ -n "$xml_file" ]; then
                eval python3 import_to_spotify.py $args
            fi
            ;;
        10)
            echo -e "\n${CYAN}>>> Extract M3U from Library XML (extract_m3u_from_library.py) <<<${NC}"
            echo "Extracts raw .m3u files directly from Apple Music. Crucially, the playlist paths"
            echo "will just point to where Apple Music stored them rather than matching local files."
            read -p "Enter path to Apple Music XML: " xml_file
            
            out_dir="outputs/m3u_raw"
            echo -e "${GREEN}Outputs will be routed to: $out_dir${NC}"
            
            if [ -n "$xml_file" ]; then
                python3 extract_m3u_from_library.py --source "$xml_file" --output "$out_dir"
            fi
            ;;
        0) 
            echo "Exiting AMT. Goodbye!"
            exit 0 
            ;;
        *) 
            echo -e "${RED}Invalid option. Try again.${NC}"
            sleep 1
            continue
            ;;
    esac

    echo -e "\n${GREEN}Process Complete.${NC}"
    read -n 1 -s -r -p "Press any key to return to the menu..."
done
