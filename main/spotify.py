"""
Import Apple Music XML exports into Spotify playlists and/or Liked Songs.
"""

import argparse
import json
import plistlib
import random
import re
import requests
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
GLOBAL_REQUEST_COUNT = 0


class HardRateLimitError(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Spotify rate limit reached. Retry after {retry_after_seconds} seconds.")


class SearchError(Exception):
    """Raised when a search fails due to an API error (e.g. 403 soft-ban, network failure).
    Distinct from a genuine 'not found' (empty results), so we don't cache the failure."""


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
    scope = "playlist-modify-public playlist-modify-private playlist-read-private playlist-read-collaborative user-library-modify user-library-read user-read-private user-read-email"
    print(f"Authenticating with Spotify (Scopes: {scope})...")
    try:
        sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                scope=scope,
                cache_path=".spotify_auth_cache",
                open_browser=True,
                show_dialog=True
            ),
            retries=5,
            status_retries=5,
            backoff_factor=2.0,
            requests_timeout=10,
        )
        current_user = sp.current_user()
    except Exception as exc:
        print(f"Spotify authentication failed: {exc}", file=sys.stderr)
        print(
            "Set SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, and SPOTIPY_REDIRECT_URI.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Authenticated as: {current_user.get('display_name')} (ID: {current_user['id']})")
    return sp, current_user["id"]


def first_track_uri(search_result: dict[str, Any]) -> str | None:
    items = search_result.get("tracks", {}).get("items", [])
    if not items:
        return None
    return items[0].get("uri")


def safe_search(sp: spotipy.Spotify, query: str) -> str | None:
    """Returns a Spotify track URI if found, None if genuinely not found (empty results).
    Raises SearchError on API errors so callers can avoid caching a false negative.
    Raises HardRateLimitError when Spotify demands a very long wait."""
    global GLOBAL_REQUEST_COUNT
    max_retries = 3
    for attempt in range(max_retries):
        try:
            GLOBAL_REQUEST_COUNT += 1
            # Every 50 requests, take a breather to let the rate limit bucket refill
            if GLOBAL_REQUEST_COUNT % 50 == 0:
                print("--- Cooldown breather (15s) ---")
                time.sleep(15)

            # Add small random jitter to search
            time.sleep(random.uniform(0.3, 0.7))

            return first_track_uri(sp.search(q=query, type="track", limit=1))
        except spotipy.exceptions.SpotifyException as exc:
            status = getattr(exc, "http_status", None)
            if status == 429:
                headers = getattr(exc, "headers", None) or {}
                retry_after_raw = headers.get("Retry-After")
                retry_after = int(retry_after_raw) if retry_after_raw else 15

                if retry_after >= 600:
                    raise HardRateLimitError(retry_after)

                print(f"Rate limited (429) during search. Waiting {retry_after + 2}s...")
                time.sleep(retry_after + 2)
                continue
            if status == 403:
                # Soft-ban from abuse detection — back off and retry
                wait = 60 * (attempt + 1)
                print(f"Soft-ban (403) on search attempt {attempt + 1}/{max_retries}. Waiting {wait}s...")
                time.sleep(wait)
                continue
            # Any other Spotify error (500, etc.) — raise so caller skips caching
            raise SearchError(f"Spotify API error {status}: {exc}")
        except Exception as exc:
            print(f"Network error during search: {exc}. Retrying in 5s...")
            time.sleep(5)
            continue
    # All retries exhausted (only reachable after 429/403/network retries)
    raise SearchError(f"Search failed after {max_retries} retries for query: {query!r}")


def to_uri(track_id_or_uri: str) -> str:
    if track_id_or_uri.startswith("spotify:track:"):
        return track_id_or_uri
    return f"spotify:track:{track_id_or_uri}"


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
        val = cache[key]
        return to_uri(val) if val else None

    clean_artist = strip_featured(artist)
    clean_title = strip_featured(title)
    
    # Prioritize specific queries to avoid multiple hits
    queries = [
        f'track:"{title}" artist:"{artist}" album:"{album}"',
        f'track:"{title}" artist:"{artist}"',
        f"{clean_title} {clean_artist}",
    ]

    seen: set[str] = set()
    track_uri: str | None = None
    had_error = False
    for i, query in enumerate(queries):
        normalized_query = normalize_value(query)
        if not normalized_query or normalized_query in seen:
            continue
        seen.add(normalized_query)

        try:
            track_uri = safe_search(sp, normalized_query)
        except SearchError as exc:
            print(f"Search error (will not cache result): {exc}")
            had_error = True
            break

        if track_uri:
            break

        # Incremental sleep between query variations
        time.sleep(1.0 + (i * 0.5))

    # Only cache confirmed results — never cache API errors as "not found"
    if not had_error:
        cache[key] = track_uri
    # Base delay between tracks
    time.sleep(0.8 + random.uniform(0.1, 0.3))
    return track_uri


def chunked(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def import_library(
    sp: spotipy.Spotify,
    tracks_dict: dict[int, dict[str, Any]],
    track_cache: dict[str, str | None],
    dry_run: bool = False,
):
    print(f"\nImporting library ({len(tracks_dict)} tracks) to Spotify Liked Songs (via /me/library)...")
    matched_uris: list[str] = []
    misses = 0
    
    for index, track in enumerate(tracks_dict.values(), start=1):
        try:
            track_uri = find_track_on_spotify(
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
        if track_uri:
            matched_uris.append(track_uri)
            print(f"[{index}/{len(tracks_dict)}] Found: {display}")
        else:
            misses += 1
            print(f"[{index}/{len(tracks_dict)}] Missing: {display}")
        
        # Save to Library in real-time batches of 50 (2026 Unified Endpoint)
        if len(matched_uris) >= 50:
            print(f"\n>>> Saving batch of {len(matched_uris)} tracks to Liked Songs...")
            if not dry_run:
                try:
                    # PUT /me/library is the unified endpoint as of Feb 2026
                    sp._put("me/library", payload={"uris": matched_uris})
                    time.sleep(2.0)
                except Exception as exc:
                    print(f"Error saving batch to library: {exc}")
            matched_uris = [] # Clear the batch

        if index % 10 == 0:
            save_track_cache(track_cache)

    # Final cleanup
    if matched_uris:
        if not dry_run:
            try:
                sp._put("me/library", payload={"uris": matched_uris})
            except Exception as exc:
                print(f"Error saving final library batch: {exc}")

    print(f"Library import complete. Misses: {misses}.")
    save_track_cache(track_cache)


def get_existing_playlists(sp: spotipy.Spotify, current_user_id: str) -> dict[str, str]:
    print("Fetching existing playlists from Spotify...")
    playlists = {}
    try:
        results = sp.current_user_playlists(limit=50)
        page = 1
        while results:
            print(f"  Processing playlist page {page}...")
            for item in results["items"]:
                if not item: continue
                name = item.get("name")
                if name:
                    # Only track playlists owned by the current user to avoid 403 on modification
                    if item.get("owner", {}).get("id") == current_user_id:
                        playlists[normalize_value(name)] = item["id"]
            
            if results["next"]:
                results = sp.next(results)
                page += 1
            else:
                results = None
    except Exception as exc:
        print(f"Warning: Error fetching some playlists: {exc}. Proceeding with what we found.")
        
    print(f"Successfully mapped {len(playlists)} playlists owned by you.")
    return playlists


def import_playlists(
    sp: spotipy.Spotify,
    user_id: str,
    playlists: list[dict[str, Any]],
    tracks: dict[int, dict[str, Any]],
    track_cache: dict[str, str | None],
    dry_run: bool = False,
):
    existing_playlists = get_existing_playlists(sp, user_id)
    print(f"\nImporting playlists ({len(playlists)} total)...")
    permission_denied = False
    for playlist in playlists:
        if permission_denied:
            break
        name = playlist["name"]
        source_track_ids = playlist["track_ids"]
        print(f"\nPlaylist: {name} ({len(source_track_ids)} tracks)")
        
        if name in existing_playlists:
            spotify_playlist_id = existing_playlists[name]
            print(f"Found existing playlist '{name}' (ID: {spotify_playlist_id}).")
        else:
            spotify_playlist_id = None
            if not dry_run:
                try:
                    print(f"Creating new playlist '{name}'...")
                    # Use /me/playlists — Spotify deprecated POST /users/{id}/playlists
                    created_playlist = sp._post("me/playlists", payload={"name": name, "public": True, "collaborative": False})
                    spotify_playlist_id = created_playlist["id"]
                    existing_playlists[name] = spotify_playlist_id
                    time.sleep(10.0) # Increased sleep to avoid anti-abuse ML bots
                except spotipy.exceptions.SpotifyException as exc:
                    print(f"Could not create playlist '{name}': {exc}")
                    if getattr(exc, "http_status", None) == 429:
                        print("Rate limit hit creating playlist. Backing off 60s...")
                        time.sleep(60)
                    continue
                except Exception as exc:
                    print(f"Could not create playlist '{name}': {exc}")
                    continue

        matched_ids: list[str] = []
        missing = 0
        for i, source_track_id in enumerate(source_track_ids, start=1):
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
            
            # Add to Playlist in real-time batches of 50
            if len(matched_ids) >= 50:
                print(f"Adding batch of {len(matched_ids)} tracks to '{name}'...")
                if not dry_run and spotify_playlist_id:
                    try:
                        # NEW for Feb 2026: Use /items endpoint instead of /tracks
                        # Increased wait to satisfy anti-pattern detection
                        sp._post(f"playlists/{spotify_playlist_id}/items", payload={"uris": matched_ids})
                        print("Batch committed.")
                        time.sleep(5.0)
                    except spotipy.exceptions.SpotifyException as exc:
                        if getattr(exc, "http_status", None) == 403:
                            print(f"Access denied to playlist '{name}' (ID: {spotify_playlist_id}). Trying fresh PUBLIC fallback...")
                            fallback_name = f"{name} (Imported 2026)"
                            new_pl = sp._post("me/playlists", payload={"name": fallback_name, "public": True})
                            spotify_playlist_id = new_pl["id"]
                            existing_playlists[name] = spotify_playlist_id
                            print(f"Resuming into new playlist: {fallback_name}")
                            # Use new /items endpoint for fallback as well
                            sp._post(f"playlists/{spotify_playlist_id}/items", payload={"uris": matched_ids})
                        else:
                            print(f"Error adding tracks to '{name}': {exc}")
                    except Exception as exc:
                        print(f"Unexpected error on playlist '{name}': {exc}")
                matched_ids = [] # Clear the batch
            
            if i % 10 == 0:
                save_track_cache(track_cache)

        # Final cleanup for the current playlist
        if matched_ids:
            print(f"Adding final {len(matched_ids)} tracks to '{name}'...")
            if not dry_run and spotify_playlist_id:
                try:
                    sp._post(f"playlists/{spotify_playlist_id}/items", payload={"uris": matched_ids})
                    print("Final batch committed.")
                    time.sleep(5.0)
                except spotipy.exceptions.SpotifyException as exc:
                    if getattr(exc, "http_status", None) == 403:
                         new_pl = sp._post("me/playlists", payload={"name": f"{name} (Imported 2026)", "public": True})
                         sp._post(f"playlists/{new_pl['id']}/items", payload={"uris": matched_ids})
                    else:
                        print(f"Error adding final batch to '{name}': {exc}")
                except Exception as exc:
                    print(f"Error adding final batch to '{name}': {exc}")

        save_track_cache(track_cache)
        print(f"Finished playlist '{name}' ({missing} missing).")


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
