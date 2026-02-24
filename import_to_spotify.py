"""
Import Apple Music XML exports into Spotify playlists and/or Liked Songs.
"""

import argparse
import json
import plistlib
import re
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=True)

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    print("Error: 'spotipy' not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


SYSTEM_PLAYLIST_KEYS = {
    "Master",
    "Music Videos",
    "Movies",
    "TV Shows",
    "Podcasts",
    "Audiobooks",
    "Voice Memos",
    "Purchased",
    "Downloaded",
}
FEAT_PATTERN = re.compile(r"\s*[\(\[\-]\s*(feat\.?|ft\.?).*?$", re.IGNORECASE)
CACHE_PATH = Path(".spotify_track_cache.json")


class HardRateLimitError(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Spotify rate limit reached. Retry after {retry_after_seconds} seconds.")


def normalize_value(value: str) -> str:
    return " ".join((value or "").strip().split())


def strip_featured(value: str) -> str:
    cleaned = FEAT_PATTERN.sub("", normalize_value(value))
    return normalize_value(cleaned)


def cache_key(artist: str, title: str, album: str) -> str:
    return f"{artist.lower()}\x1f{title.lower()}\x1f{album.lower()}"


def load_track_cache(path: Path = CACHE_PATH) -> dict[str, str | None]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str | None] = {}
    for key, value in data.items():
        if isinstance(key, str) and (isinstance(value, str) or value is None):
            out[key] = value
    return out


def save_track_cache(cache: dict[str, str | None], path: Path = CACHE_PATH):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True))
    tmp.replace(path)


def parse_library(xml_path: Path) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    print(f"Parsing Apple Music XML: {xml_path}")
    with open(xml_path, "rb") as file_handle:
        data = plistlib.load(file_handle)

    tracks: dict[int, dict[str, Any]] = {}
    for track_id_raw, info in data.get("Tracks", {}).items():
        if info.get("Has Video"):
            continue
        track_id = int(track_id_raw)
        tracks[track_id] = {
            "title": normalize_value(info.get("Name", "")),
            "artist": normalize_value(info.get("Artist", "")),
            "album": normalize_value(info.get("Album", "")),
            "duration_ms": int(info.get("Total Time", 0) or 0),
        }

    playlists: list[dict[str, Any]] = []
    for playlist in data.get("Playlists", []):
        if (
            playlist.get("Master")
            or playlist.get("Distinguished Kind")
            or playlist.get("Name") in SYSTEM_PLAYLIST_KEYS
        ):
            continue

        name = normalize_value(playlist.get("Name", "Untitled Playlist"))
        track_ids = [
            int(item["Track ID"])
            for item in playlist.get("Playlist Items", [])
            if "Track ID" in item
        ]
        if track_ids:
            playlists.append({"name": name, "track_ids": track_ids})

    print(f"Found {len(tracks)} tracks and {len(playlists)} playlists")
    return tracks, playlists


def get_spotify_client() -> tuple[spotipy.Spotify, str]:
    print("Authenticating with Spotify...")
    scope = "playlist-modify-public playlist-modify-private user-library-modify"
    try:
        sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(scope=scope),
            retries=10,
            status_retries=10,
            backoff_factor=0.5,
        )
        current_user = sp.current_user()
    except Exception as exc:
        print(f"Spotify authentication failed: {exc}", file=sys.stderr)
        print(
            "Set SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, and SPOTIPY_REDIRECT_URI.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Authenticated as {current_user.get('display_name') or current_user['id']}")
    return sp, current_user["id"]


def first_track_id(search_result: dict[str, Any]) -> str | None:
    items = search_result.get("tracks", {}).get("items", [])
    if not items:
        return None
    return items[0].get("id")


def safe_search(sp: spotipy.Spotify, query: str) -> str | None:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return first_track_id(sp.search(q=query, type="track", limit=1))
        except spotipy.exceptions.SpotifyException as exc:
            if getattr(exc, "http_status", None) == 429:
                headers = getattr(exc, "headers", None) or {}
                retry_after_raw = headers.get("Retry-After")
                retry_after = int(retry_after_raw) if retry_after_raw else 5
                
                if retry_after >= 600:
                    raise HardRateLimitError(retry_after)
                    
                print(f"Rate limited during search. Waiting {retry_after}s...")
                time.sleep(retry_after + 1)
                continue
            return None
        except Exception:
            return None
    return None


def find_track_on_spotify(
    sp: spotipy.Spotify,
    artist: str,
    title: str,
    album: str,
    cache: dict[str, str | None],
) -> str | None:
    artist = normalize_value(artist)
    title = normalize_value(title)
    album = normalize_value(album)
    key = cache_key(artist, title, album)
    if key in cache:
        return cache[key]

    clean_artist = strip_featured(artist)
    clean_title = strip_featured(title)
    clean_album = strip_featured(album)
    queries = [
        f'track:"{title}" artist:"{artist}" album:"{album}"',
        f'track:"{title}" artist:"{artist}"',
        f'track:"{clean_title}" artist:"{clean_artist}"',
        f"{clean_title} {clean_artist}",
    ]

    seen: set[str] = set()
    track_id: str | None = None
    for query in queries:
        normalized_query = normalize_value(query)
        if not normalized_query or normalized_query in seen:
            continue
        seen.add(normalized_query)
        track_id = safe_search(sp, normalized_query)
        if track_id:
            break
        time.sleep(0.08)

    cache[key] = track_id
    time.sleep(0.12) # Add base delay to prevent 429s during heavy searches
    return track_id


def chunked(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def import_library(
    sp: spotipy.Spotify,
    tracks_dict: dict[int, dict[str, Any]],
    track_cache: dict[str, str | None],
    dry_run: bool = False,
):
    print(f"\nImporting library ({len(tracks_dict)} tracks) to Liked Songs...")
    matched_track_ids: list[str] = []
    misses = 0

    for index, track in enumerate(tracks_dict.values(), start=1):
        try:
            track_id = find_track_on_spotify(
                sp,
                artist=track["artist"],
                title=track["title"],
                album=track["album"],
                cache=track_cache,
            )
        except HardRateLimitError as exc:
            save_track_cache(track_cache)
            print(f"Stopping import due to hard rate limit. Retry after {exc.retry_after_seconds} seconds.")
            return
        display = f"{track['artist']} - {track['title']}"
        if track_id:
            matched_track_ids.append(track_id)
            print(f"[{index}/{len(tracks_dict)}] Found: {display}")
        else:
            misses += 1
            print(f"[{index}/{len(tracks_dict)}] Missing: {display}")
        if index % 200 == 0:
            save_track_cache(track_cache)

    if not matched_track_ids:
        print("No tracks matched for library import.")
        return

    unique_ids = list(dict.fromkeys(matched_track_ids))
    if dry_run:
        save_track_cache(track_cache)
        print(
            f"Dry run: would add {len(unique_ids)} tracks to Liked Songs "
            f"(matched {len(unique_ids)}, missing {misses})."
        )
        return

    for chunk in chunked(unique_ids, 50):
        try:
            sp.current_user_saved_tracks_add(tracks=chunk)
        except Exception as exc:
            print(f"Error adding library chunk: {exc}")

    print(f"Library import complete. Matched {len(unique_ids)} tracks, missing {misses}.")
    save_track_cache(track_cache)


def import_playlists(
    sp: spotipy.Spotify,
    user_id: str,
    playlists: list[dict[str, Any]],
    tracks: dict[int, dict[str, Any]],
    track_cache: dict[str, str | None],
    dry_run: bool = False,
):
    print(f"\nImporting playlists ({len(playlists)} total)...")
    permission_denied = False
    for playlist in playlists:
        if permission_denied:
            break
        name = playlist["name"]
        source_track_ids = playlist["track_ids"]
        print(f"\nPlaylist: {name} ({len(source_track_ids)} tracks)")
        spotify_playlist_id: str | None = None
        if not dry_run:
            try:
                created_playlist = sp.user_playlist_create(
                    user=user_id,
                    name=name,
                    public=False,
                    collaborative=False,
                )
                spotify_playlist_id = created_playlist["id"]
                time.sleep(1.5) # Sleep to avoid playlist creation anti-abuse ML bots triggering
            except spotipy.exceptions.SpotifyException as exc:
                print(f"Could not create playlist '{name}': {exc}")
                if getattr(exc, "http_status", None) == 403:
                    permission_denied = True
                    save_track_cache(track_cache)
                    print(
                        "Stopping playlist import due to Spotify 403 permission error. "
                        "Delete '.cache', re-auth, and ensure your Spotify account is added "
                        "as a user in your app settings if the app is in development mode."
                    )
                elif getattr(exc, "http_status", None) == 429:
                    print("Rate limit hit forming playlist. Backing off 10s...")
                    time.sleep(10)
                continue
            except Exception as exc:
                print(f"Could not create playlist '{name}': {exc}")
                continue

        matched_ids: list[str] = []
        missing = 0
        for source_track_id in source_track_ids:
            track = tracks.get(source_track_id)
            if not track:
                continue
            try:
                match = find_track_on_spotify(
                    sp,
                    artist=track["artist"],
                    title=track["title"],
                    album=track["album"],
                    cache=track_cache,
                )
            except HardRateLimitError as exc:
                save_track_cache(track_cache)
                print(
                    f"Stopping playlist import due to hard rate limit. "
                    f"Retry after {exc.retry_after_seconds} seconds."
                )
                return
            if match:
                matched_ids.append(match)
            else:
                missing += 1
        save_track_cache(track_cache)

        if not matched_ids:
            print("No matches for this playlist.")
            continue

        if dry_run:
            print(f"Dry run: would add {len(matched_ids)} tracks to '{name}' ({missing} missing).")
            continue

        for chunk in chunked(matched_ids, 50): # Kept low to avoid rate limits
            try:
                sp.playlist_add_items(spotify_playlist_id, chunk)
                time.sleep(0.5)
            except Exception as exc:
                print(f"Error adding playlist chunk for '{name}': {exc}")
                if "429" in str(exc):
                    time.sleep(5)
                    try:
                        sp.playlist_add_items(spotify_playlist_id, chunk)
                    except:
                        pass

        print(f"Added {len(matched_ids)} tracks to '{name}' ({missing} missing).")


def filter_playlists(playlists: list[dict[str, Any]], selected_names: list[str] | None) -> list[dict[str, Any]]:
    if not selected_names:
        return playlists
    wanted = {normalize_value(name).casefold() for name in selected_names}
    filtered = [pl for pl in playlists if normalize_value(pl["name"]).casefold() in wanted]
    return filtered


def main():
    parser = argparse.ArgumentParser(description="Import Apple Music XML to Spotify.")
    parser.add_argument("--source", required=True, type=Path, help="Apple Music XML export path")
    parser.add_argument("--playlists", action="store_true", help="Import playlists from XML")
    parser.add_argument("--library", action="store_true", help="Import all tracks to Liked Songs")
    parser.add_argument("--dry-run", action="store_true", help="Match/search only; do not write to Spotify")
    parser.add_argument(
        "--playlist",
        action="append",
        help="Import only specific playlist name(s). Repeat flag for multiple.",
    )
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Source file does not exist: {args.source}", file=sys.stderr)
        sys.exit(1)
    if not args.playlists and not args.library:
        print("Nothing to do: specify at least one of --playlists or --library")
        sys.exit(0)

    spotify_client, user_id = get_spotify_client()
    tracks, playlists = parse_library(args.source)
    track_cache = load_track_cache()
    playlists = filter_playlists(playlists, args.playlist)
    if args.playlist:
        print(f"Selected {len(playlists)} playlist(s) by --playlist filter.")
    print(f"Loaded {len(track_cache)} cached track matches from {CACHE_PATH}.")

    if args.library:
        import_library(spotify_client, tracks, track_cache, dry_run=args.dry_run)
    if args.playlists:
        import_playlists(
            spotify_client,
            user_id,
            playlists,
            tracks,
            track_cache,
            dry_run=args.dry_run,
        )

    save_track_cache(track_cache)
    print("\nImport complete.")


if __name__ == "__main__":
    main()
