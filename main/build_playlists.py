"""
build_playlists.py — Rebuild playlists from an Apple Music XML export.

Scans the local music library to build a tag index, then fuzzy-matches each
XML playlist track against local files by title + artist. Outputs one M3U8 file
per playlist, ready to import into Apple Music (File → Import).

Usage:
    python3 build_playlists.py [--xml PATH] [--root PATH] [--out DIR] [--threshold N]

    --xml        Apple Music library XML (default: hardcoded path below)
    --root       Local music root (default: hardcoded path below)
    --out        Output directory for .m3u8 files (default: ./playlists_out/)
    --threshold  Minimum fuzzy match score 0-100 (default: 82)
    --rebuild-index  Force rescan of local files (ignores cached index)

Outputs:
    playlists_out/<playlist_name>.m3u8   — import into Apple Music
    playlists_out/match_report.csv       — review low-confidence matches and misses
    playlists_out/local_index.json       — cached tag scan (reuse on next run)
"""

import argparse
import csv
import json
import plistlib
import re
import unicodedata
from pathlib import Path

from mutagen.mp4 import MP4
from rapidfuzz import fuzz, process
import os

XML_DEFAULT = Path(os.environ.get("XML_DEFAULT", str(Path.home() / "Desktop" / "Library.xml")))
LIBRARY_ROOT = Path(os.environ.get("MUSIC_ROOT", str(Path.home() / "Music")))
OUTPUT_DIR = Path(os.environ.get("PLAYLISTS_OUT", os.path.join(SCRIPT_DIR, "output", "playlists")))

# Playlists to exclude by name (Apple system playlists)
SYSTEM_PLAYLIST_NAMES = {
    "Library", "Music", "Movies", "TV Shows", "Podcasts",
    "Audiobooks", "Genius", "Purchased",
}

FEAT_RE = re.compile(r"\s*[\(\[](feat\.|ft\.|featuring)\s.*?[\)\]]", re.IGNORECASE)
EDITION_RE = re.compile(
    r"\s*[\(\[](deluxe|remaster(?:ed)?|anniversary|edition|explicit|clean|bonus|"
    r"expanded|super deluxe|special edition|collector|limited|re-?issue|\d{4} remaster"
    r"|live|original|single|EP).*?[\)\]]",
    re.IGNORECASE,
)
# Strip trailing " - Single", " - EP", " (Deluxe)", etc.
SUFFIX_RE = re.compile(
    r"\s*[-–—]\s*(single|ep|deluxe(?:\s+version)?|remaster(?:ed)?|"
    r"live|original|explicit|clean|bonus track|expanded)\s*$",
    re.IGNORECASE,
)
PUNC_RE = re.compile(r"[^\w\s]")

# Smart quotes → ASCII
SMART_QUOTE_MAP = str.maketrans("\u2018\u2019\u201c\u201d", "''\"\"")
# Common censored → uncensored
CENSOR_MAP = {
    "f*****g": "fucking", "f***": "fuck", "s**t": "shit",
    "n****": "nigga", "b****": "bitch", "d***": "dick",
    "a**": "ass", "p***y": "pussy",
}

def normalize(s: str) -> str:
    """Lowercase, strip diacritics, remove feat. clauses, editions, and punctuation."""
    s = s.translate(SMART_QUOTE_MAP)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = FEAT_RE.sub("", s)
    s = EDITION_RE.sub("", s)
    s = SUFFIX_RE.sub("", s)
    s = PUNC_RE.sub("", s)
    s = s.lower().strip()
    # Replace censored words with uncensored
    for censored, clean in CENSOR_MAP.items():
        s = s.replace(censored, clean)
    return s


def safe_filename(name: str) -> str:
    """Make a playlist name safe for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name.strip(". ")[:120]


# e.g. "01 The Genesis.m4a" → "The Genesis"
# e.g. "1-05 Title.m4a" → "Title"
FILENAME_TITLE_RE = re.compile(r"^\d+-?\d+\s+(.+)\.m4a$", re.IGNORECASE)

def title_from_filename(path: Path) -> str:
    m = FILENAME_TITLE_RE.match(path.name)
    return m.group(1) if m else path.stem


# ── Local index ───────────────────────────────────────────────────────────────


def build_local_index(root: Path, cache_path: Path) -> list[dict]:
    """
    Scan all .m4a files under root, read title/artist/album tags.
    Caches result to cache_path as JSON.
    """
    print(f"Scanning local library at {root}...")
    index = []
    count = 0
    for m4a in sorted(root.rglob("*.m4a")):
        if m4a.name.startswith("._"):
            continue
        try:
            tags = MP4(m4a)
            title = (tags.get("©nam") or [""])[0] or title_from_filename(m4a)
            artist = (tags.get("©ART") or [""])[0]
            album_artist = (tags.get("aART") or [""])[0]
            album = (tags.get("©alb") or [""])[0]
            duration_ms = int(tags.info.length * 1000) if tags.info else 0
            index.append({
                "path": str(m4a),
                "title": title,
                "artist": artist,
                "album_artist": album_artist,
                "album": album,
                "duration_ms": duration_ms,
                "norm_key": normalize(title) + "|" + normalize(artist or album_artist),
            })
            count += 1
            if count % 500 == 0:
                print(f"  ...scanned {count} files")
        except Exception as e:
            print(f"  [WARN] Could not read {m4a.name}: {e}")

    print(f"  Total: {count} tracks indexed")
    with open(cache_path, "w") as f:
        json.dump(index, f)
    return index


def load_local_index(root: Path, cache_path: Path, force_rebuild: bool) -> list[dict]:
    if not force_rebuild and cache_path.exists():
        print(f"Loading cached local index from {cache_path.name}...")
        with open(cache_path) as f:
            idx = json.load(f)
        print(f"  {len(idx)} tracks loaded from cache")
        return idx
    return build_local_index(root, cache_path)


# ── Matching ──────────────────────────────────────────────────────────────────


def make_lookup_keys(index: list[dict]) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Build lookup dicts: by norm_key, and by artist."""
    lookup: dict[str, list[dict]] = {}
    artist_index: dict[str, list[dict]] = {}
    for track in index:
        k = track["norm_key"]
        lookup.setdefault(k, []).append(track)
        a = normalize(track["artist"] or track["album_artist"] or "")
        if a:
            artist_index.setdefault(a, []).append(track)
    return lookup, artist_index


def find_match(
    xml_title: str,
    xml_artist: str,
    xml_album: str,
    lookup: dict,
    artist_index: dict,
    index: list[dict],
    norm_keys: list[str],
    threshold: int,
) -> tuple[dict | None, int, str]:
    """
    Returns (matched_track, score, method).
    method: 'exact', 'fuzzy', 'title_only', 'artist_fuzzy', or 'miss'
    """
    norm_title = normalize(xml_title)
    norm_artist = normalize(xml_artist)
    norm_album = normalize(xml_album)

    # 1. Exact title+artist match
    exact_key = norm_title + "|" + norm_artist
    if exact_key in lookup:
        candidates = lookup[exact_key]
        for c in candidates:
            if normalize(c["album"]) == norm_album:
                return c, 100, "exact"
        return candidates[0], 100, "exact"

    # 2. Fuzzy match on "title artist" concatenation
    query = f"{norm_title} {norm_artist}"
    result = process.extractOne(
        query,
        norm_keys,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
    )
    if result:
        matched_key, score, _ = result
        candidates = lookup.get(matched_key, [])
        if candidates:
            return candidates[0], score, "fuzzy"

    # 3. Match by artist first, then fuzzy title within artist's tracks
    artist_candidates = artist_index.get(norm_artist, [])
    if not artist_candidates:
        # Try fuzzy matching the artist name
        artist_names = list(artist_index.keys())
        artist_result = process.extractOne(
            norm_artist,
            artist_names,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=80,
        )
        if artist_result:
            artist_candidates = artist_index.get(artist_result[0], [])

    if artist_candidates:
        # Build title-only norm keys for this artist's tracks
        art_keys = [normalize(t["title"]) for t in artist_candidates]
        title_result = process.extractOne(
            norm_title,
            art_keys,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold - 10,
        )
        if title_result:
            _, score, idx = title_result
            return artist_candidates[idx], score, "artist_fuzzy"

    # 4. Try matching title only (some tracks have artist discrepancies)
    title_keys = [k.split("|")[0] for k in norm_keys]
    result2 = process.extractOne(
        norm_title,
        title_keys,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold + 5,
    )
    if result2:
        _, score, idx_pos = result2
        return index[idx_pos], score, "title_only"

    return None, 0, "miss"


# ── Playlist export ───────────────────────────────────────────────────────────


def write_m3u8(playlist_name: str, entries: list[dict], out_dir: Path) -> Path:
    """Write a .m3u8 file for Apple Music import."""
    filename = safe_filename(playlist_name) + ".m3u8"
    out_path = out_dir / filename
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for entry in entries:
            duration_sec = entry.get("duration_ms", 0) // 1000
            display = f"{entry.get('artist', '')} - {entry.get('title', '')}"
            f.write(f"#EXTINF:{duration_sec},{display}\n")
            f.write(entry["path"] + "\n")
    return out_path


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Build M3U8 playlists from Apple Music XML.")
    parser.add_argument("--xml", type=Path, default=XML_DEFAULT)
    parser.add_argument("--root", type=Path, default=LIBRARY_ROOT)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--threshold", type=int, default=82)
    parser.add_argument("--rebuild-index", action="store_true")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    cache_path = args.out / "local_index.json"

    # Load local library index
    local_index = load_local_index(args.root, cache_path, args.rebuild_index)
    lookup, artist_index = make_lookup_keys(local_index)
    norm_keys = [t["norm_key"] for t in local_index]

    # Parse Apple Music XML
    print(f"\nParsing XML: {args.xml}...")
    with open(args.xml, "rb") as f:
        lib = plistlib.load(f)

    xml_tracks: dict[str, dict] = lib.get("Tracks", {})
    xml_playlists: list[dict] = lib.get("Playlists", [])

    # Filter to user playlists
    user_playlists = [
        p for p in xml_playlists
        if not p.get("Master")
        and not p.get("Distinguished Kind")
        and p.get("Name", "") not in SYSTEM_PLAYLIST_NAMES
    ]
    print(f"  {len(xml_tracks)} tracks, {len(user_playlists)} user playlists")

    # Deduplicate playlist names by appending a counter
    name_counts: dict[str, int] = {}
    report_rows = []

    stats = {
        "playlists": len(user_playlists),
        "tracks_total": 0,
        "matched_exact": 0,
        "matched_fuzzy": 0,
        "matched_artist": 0,
        "matched_title": 0,
        "missed": 0,
    }

    for pl in user_playlists:
        pl_name = pl.get("Name", "Untitled")
        name_counts[pl_name] = name_counts.get(pl_name, 0) + 1
        if name_counts[pl_name] > 1:
            pl_name = f"{pl_name} ({name_counts[pl_name]})"

        items = pl.get("Playlist Items", [])
        print(f"\nPlaylist: '{pl_name}' ({len(items)} tracks)")

        matched_entries = []
        for item in items:
            tid = str(item.get("Track ID", ""))
            xml_track = xml_tracks.get(tid, {})
            if not xml_track:
                continue

            xml_title = xml_track.get("Name", "")
            xml_artist = xml_track.get("Artist", "") or xml_track.get("Album Artist", "")
            xml_album = xml_track.get("Album", "")
            xml_duration = xml_track.get("Total Time", 0)

            stats["tracks_total"] += 1

            matched, score, method = find_match(
                xml_title, xml_artist, xml_album, lookup, artist_index, local_index, norm_keys, args.threshold
            )

            if matched:
                if method == "exact":
                    stats["matched_exact"] += 1
                elif method == "fuzzy":
                    stats["matched_fuzzy"] += 1
                elif method == "artist_fuzzy":
                    stats["matched_artist"] += 1
                else:
                    stats["matched_title"] += 1
                matched_entries.append({
                    "path": matched["path"],
                    "title": matched["title"],
                    "artist": matched["artist"],
                    "duration_ms": matched["duration_ms"],
                })
                status = "ok"
            else:
                stats["missed"] += 1
                status = "miss"

            report_rows.append({
                "playlist": pl_name,
                "xml_title": xml_title,
                "xml_artist": xml_artist,
                "xml_album": xml_album,
                "matched_path": matched["path"] if matched else "",
                "matched_title": matched["title"] if matched else "",
                "matched_artist": matched["artist"] if matched else "",
                "score": score,
                "method": method,
                "status": status,
            })

        if matched_entries:
            out_path = write_m3u8(pl_name, matched_entries, args.out)
            print(f"  Wrote {len(matched_entries)}/{len(items)} tracks → {out_path.name}")
        else:
            print(f"  [WARN] No matches found, playlist skipped")

    # Write match report
    report_path = args.out / "match_report.csv"
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "playlist", "xml_title", "xml_artist", "xml_album",
            "matched_path", "matched_title", "matched_artist", "score", "method", "status"
        ])
        writer.writeheader()
        writer.writerows(report_rows)

    print(f"\n── Summary ────────────────────────────────────────")
    print(f"  Playlists:      {stats['playlists']}")
    print(f"  Tracks total:   {stats['tracks_total']}")
    print(f"  Exact matches:  {stats['matched_exact']}")
    print(f"  Fuzzy matches:  {stats['matched_fuzzy']}")
    print(f"  Artist+fuzzy:   {stats['matched_artist']}")
    print(f"  Title only:     {stats['matched_title']}")
    print(f"  Misses:         {stats['missed']}")
    total_matched = stats["matched_exact"] + stats["matched_fuzzy"] + stats["matched_artist"] + stats["matched_title"]
    match_rate = total_matched / max(stats["tracks_total"], 1) * 100
    print(f"  Match rate:     {match_rate:.1f}%")
    print(f"\n  M3U8 files → {args.out}/")
    print(f"  Review report → {report_path}")
    print(f"\nImport into Apple Music: File → Import → select any .m3u8 file")


if __name__ == "__main__":
    main()
