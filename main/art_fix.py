"""
art_fix.py — Normalize and embed album artwork across the music library.

For each album, checks if all tracks have identical embedded artwork. If inconsistent
or missing (or --force), fetches high-res art from iTunes Search API (primary) or
MusicBrainz/CoverArtArchive (fallback) and embeds it into every track in the album.

Usage:
    python3 art_fix.py [--root PATH] [--dry-run] [--force] [--resume]

    --root     Music root dir (default: MUSIC_ROOT or ~/Music)
    --dry-run  Show what would change, no writes
    --force    Re-fetch art even for albums that already have consistent art
    --resume   Skip albums already in the progress log (useful after interruptions)
"""

import argparse
import hashlib
import json
import re
import time
import urllib.parse
from pathlib import Path

import requests
from mutagen.mp4 import MP4, MP4Cover
import os

LIBRARY_ROOT = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))
PROGRESS_FILE = Path(__file__).parent.parent / "art_fix_progress.json"

# Albums that are intentionally artwork-free
SKIP_ALBUMS = {("Death Grips", "No Love Deep Web")}

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
MB_SEARCH_URL = "https://musicbrainz.org/ws/2/release/"
CAA_URL = "https://coverartarchive.org/release/"
USER_AGENT = "foriPodArtFix/1.0"

# Strip common edition qualifiers before searching
QUALIFIER_RE = re.compile(
    r"\s*[\(\[](deluxe|remaster(?:ed)?|anniversary|edition|explicit|clean|bonus|"
    r"expanded|super deluxe|special edition|collector|limited|re-?issue|180g|"
    r"digipak|\d{4} remaster).*?[\)\]]",
    re.IGNORECASE,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def strip_qualifiers(s: str) -> str:
    return QUALIFIER_RE.sub("", s).strip()


def art_hash(covr_data: bytes) -> str:
    return hashlib.md5(covr_data).hexdigest()


def get_album_art_state(album_dir: Path) -> tuple[str | None, list[Path]]:
    """
    Returns (common_hash_or_None, list_of_tracks).
    common_hash is None if art is missing or inconsistent across tracks.
    """
    tracks = sorted(
        t for t in album_dir.glob("*.m4a") if not t.name.startswith("._")
    )
    if not tracks:
        return None, []

    hashes = []
    for track in tracks:
        try:
            tags = MP4(track)
            covr = tags.get("covr", [])
            hashes.append(art_hash(bytes(covr[0])) if covr else None)
        except Exception:
            hashes.append(None)

    unique = set(hashes)
    if len(unique) == 1 and None not in unique:
        return hashes[0], tracks  # consistent, non-empty art
    return None, tracks  # missing or inconsistent


# ── Art fetching ───────────────────────────────────────────────────────────────


def fetch_itunes_art(artist: str, album: str, session: requests.Session) -> bytes | None:
    """Try iTunes Search API. Returns raw image bytes or None."""
    for album_term in [album, strip_qualifiers(album)]:
        term = f"{artist} {album_term}"
        try:
            r = session.get(
                ITUNES_SEARCH_URL,
                params={"term": term, "entity": "album", "limit": 5, "media": "music"},
                timeout=10,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            for result in results:
                url = result.get("artworkUrl100", "")
                if not url:
                    continue
                # Bump to highest available resolution
                img_url = url.replace("100x100bb", "3000x3000bb")
                img_r = session.get(img_url, timeout=15)
                img_r.raise_for_status()
                return img_r.content
        except Exception as e:
            print(f"    [iTunes error] {e}")
        time.sleep(2.0)  # iTunes asks for ~20 req/min
    return None


def fetch_musicbrainz_art(artist: str, album: str, session: requests.Session) -> bytes | None:
    """Fallback: MusicBrainz MBID → CoverArtArchive image bytes."""
    headers = {"User-Agent": USER_AGENT}
    for album_term in [album, strip_qualifiers(album)]:
        try:
            r = session.get(
                MB_SEARCH_URL,
                params={
                    "query": f'artist:"{artist}" AND release:"{album_term}"',
                    "fmt": "json",
                    "limit": 1,
                },
                headers=headers,
                timeout=10,
            )
            r.raise_for_status()
            releases = r.json().get("releases", [])
            time.sleep(1.1)  # MusicBrainz rate limit: 1 req/sec
            if not releases:
                continue
            mbid = releases[0]["id"]

            caa_r = session.get(f"{CAA_URL}{mbid}", headers=headers, timeout=10)
            if caa_r.status_code == 404:
                continue
            caa_r.raise_for_status()
            images = caa_r.json().get("images", [])
            for img in images:
                if img.get("front") and img.get("image"):
                    img_r = session.get(img["image"], headers=headers, timeout=15)
                    img_r.raise_for_status()
                    return img_r.content
        except Exception as e:
            print(f"    [MB/CAA error] {e}")
            time.sleep(1.1)
    return None


def fetch_art(artist: str, album: str, session: requests.Session) -> bytes | None:
    print(f"    Fetching iTunes art...")
    img = fetch_itunes_art(artist, album, session)
    if img:
        print(f"    Found via iTunes ({len(img):,} bytes)")
        return img
    print(f"    iTunes miss — trying MusicBrainz...")
    img = fetch_musicbrainz_art(artist, album, session)
    if img:
        print(f"    Found via MusicBrainz ({len(img):,} bytes)")
    return img


# ── Embedding ─────────────────────────────────────────────────────────────────


def embed_art_into_tracks(tracks: list[Path], img_bytes: bytes, dry_run: bool) -> int:
    """Embed identical artwork into every track. Returns count of files written."""
    # Detect format by magic bytes
    fmt = MP4Cover.FORMAT_JPEG
    if img_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        fmt = MP4Cover.FORMAT_PNG

    count = 0
    for track in tracks:
        if dry_run:
            print(f"    [DRY RUN] Would embed into {track.name}")
            count += 1
            continue
        try:
            tags = MP4(track)
            tags["covr"] = [MP4Cover(img_bytes, imageformat=fmt)]
            tags.save()
            print(f"    Embedded → {track.name}")
            count += 1
        except Exception as e:
            print(f"    [EMBED ERROR] {track.name}: {e}")
    return count


# ── Main ──────────────────────────────────────────────────────────────────────


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {}


def save_progress(progress: dict) -> None:
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Fix album artwork across music library.")
    parser.add_argument("--root", type=Path, default=LIBRARY_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-fetch art even if already consistent")
    parser.add_argument("--resume", action="store_true", help="Skip albums already in progress log")
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    if not root.exists():
        print(f"Error: {root} not found")
        return

    progress = load_progress() if args.resume else {}
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    stats = {"checked": 0, "consistent": 0, "fixed": 0, "fetched": 0, "missed": 0, "skipped": 0}

    for artist_dir in sorted(root.iterdir()):
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        artist = artist_dir.name

        for album_dir in sorted(artist_dir.iterdir()):
            if not album_dir.is_dir() or album_dir.name.startswith("."):
                continue
            album = album_dir.name
            key = f"{artist}|{album}"
            stats["checked"] += 1

            # Skip intentionally artless albums
            if (artist, album) in SKIP_ALBUMS:
                print(f"[SKIP] {artist} / {album} (intentionally artless)")
                stats["skipped"] += 1
                continue

            # Skip if already processed in a previous run
            if args.resume and progress.get(key) == "ok":
                stats["consistent"] += 1
                continue

            art_state, tracks = get_album_art_state(album_dir)
            if not tracks:
                continue

            if art_state is not None and not args.force:
                # Already consistent — nothing to do
                stats["consistent"] += 1
                if args.resume:
                    progress[key] = "ok"
                continue

            reason = "inconsistent/missing" if art_state is None else "force refresh"
            print(f"\n[{reason.upper()}] {artist} / {album} ({len(tracks)} tracks)")

            if args.dry_run:
                print(f"  [DRY RUN] Would fetch and embed art")
                stats["fetched"] += 1
                continue

            img = fetch_art(artist, album, session)
            if not img:
                print(f"  [MISS] No art found for {artist} / {album}")
                stats["missed"] += 1
                progress[key] = "miss"
                save_progress(progress)
                continue

            embedded = embed_art_into_tracks(tracks, img, dry_run=False)
            stats["fixed"] += 1
            stats["fetched"] += 1
            progress[key] = "ok"
            save_progress(progress)
            print(f"  Done — {embedded} tracks updated")

    print("\n── Summary ──────────────────────────────────────")
    print(f"  Albums checked:    {stats['checked']}")
    print(f"  Already consistent:{stats['consistent']}")
    print(f"  Fixed:             {stats['fixed']}")
    print(f"  Art fetch misses:  {stats['missed']}")
    print(f"  Skipped:           {stats['skipped']}")


if __name__ == "__main__":
    main()
