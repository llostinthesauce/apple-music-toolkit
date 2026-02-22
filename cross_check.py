"""
cross_check.py — diff your iCloud/streaming library against your owned local library.

WHAT IT DOES
------------
Compares two Apple Music XML exports:
  - Your full iCloud/streaming library (everything you've ever listened to or added)
  - Your local/owned library (tracks actually downloaded to your device or iPod)

Finds every track you have in iCloud that you do NOT own locally, and writes a
prioritized acquisition list + full track breakdown to a text file.

HOW TO EXPORT THE XMLs
-----------------------
In Apple Music (macOS):  File → Library → Export Library…

  iCloud library:  export while connected to iCloud (shows full streaming catalog)
  Local library:   switch to "Downloaded Music" or your iPod sync source, then export

MATCHING STRATEGY (multi-tier to avoid false misses)
------------------------------------------------------
1. Exact:    (artist_norm, title_norm) direct set lookup — fastest path
2. Fuzzy:    title exact + artist similarity ≥ 0.6 via SequenceMatcher
             (handles "feat." variants, minor name differences)
3. Partial:  title containment + artist similarity — catches edge cases where
             remaster/live suffixes weren't fully stripped by normalization

Normalization: Unicode NFD decomposition → strip combining marks → lowercase →
strip punctuation → collapse whitespace. Also strips common suffixes like
"(Remastered)", "(Live)", "(Bonus Track)" etc. from titles before comparing.

USAGE
-----
    python3 cross_check.py \\
        --cloud iCloudLibrary.xml \\
        --local LocaliPodLibrary.xml \\
        --output output/missing_from_local.txt

OUTPUT
------
    missing_from_local.txt contains two sections:
      1. TOP MISSING ALBUMS — ranked by track count (best candidates to buy first)
      2. FULL MISSING TRACK LIST — every missing track, by artist → album → song

DEPENDENCIES
------------
    Python 3.9+ stdlib only (plistlib, difflib, collections, re, unicodedata)
"""

import argparse
import plistlib
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize(text: str | None) -> str:
    """Lowercase, strip accents, remove punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_artist(artist: str) -> str:
    """Normalize artist, stripping common 'feat.' suffixes."""
    artist = re.sub(r"\s*(feat\.?|ft\.?|featuring|with)\s+.*", "", artist, flags=re.IGNORECASE)
    return normalize(artist)


def normalize_title(title: str) -> str:
    """
    Normalize title, stripping remaster/live/bonus suffixes that differ
    between streaming and local versions.
    """
    title = re.sub(
        r"\s*[\(\[](remaster(ed)?|remix|live|bonus( track)?|deluxe|re-?issue"
        r"|single version|radio edit|album version|\d{4}( remaster)?)[^\)\]]*[\)\]]",
        "", title, flags=re.IGNORECASE
    )
    return normalize(title)


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def parse_tracks(xml_path: Path) -> list[dict]:
    """Return list of {artist, album, title} dicts for all tracks in XML."""
    with open(xml_path, "rb") as f:
        data = plistlib.load(f)
    tracks = []
    for info in data.get("Tracks", {}).values():
        tracks.append({
            "artist": info.get("Artist", "") or "",
            "album":  info.get("Album",  "") or "",
            "title":  info.get("Name",   "") or "",
        })
    return tracks


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

def build_index(tracks: list[dict]) -> dict:
    """
    Build a lookup dict for fast matching.
    Keys:
      (artist_norm, title_norm)  — primary
      title_norm                 — fallback (maps to set of artist_norms)
    """
    primary = set()
    by_title: dict[str, set] = defaultdict(set)

    for t in tracks:
        an = normalize_artist(t["artist"])
        tn = normalize_title(t["title"])
        primary.add((an, tn))
        by_title[tn].add(an)

    return {"primary": primary, "by_title": by_title}


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def is_matched(track: dict, index: dict) -> bool:
    """
    Multi-tier match:
    1. Exact (artist_norm, title_norm) — fast path
    2. Title match + artist similarity >= 0.6  (handles 'feat.' variants,
       slight name differences)
    3. Partial title match: if one title contains the other and artist matches
    """
    an = normalize_artist(track["artist"])
    tn = normalize_title(track["title"])

    # Tier 1 — exact
    if (an, tn) in index["primary"]:
        return True

    # Tier 2 — title match, fuzzy artist
    if tn in index["by_title"]:
        for local_artist in index["by_title"][tn]:
            if similarity(an, local_artist) >= 0.6:
                return True

    # Tier 3 — partial title containment + artist match
    # e.g. "Goodnight Moon" vs "Goodnight Moon (Bonus Track)" already handled
    # by stripping, but catch edge cases where stripping was insufficient
    if tn and len(tn) >= 5:
        for local_artist in index["by_title"]:
            if local_artist == tn:
                continue  # already checked
            if (tn in local_artist or local_artist in tn):
                # check if artist is plausible
                for la in index["by_title"][local_artist]:
                    if similarity(an, la) >= 0.6:
                        return True

    return False


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_missing(out_path: Path, missing: list[dict]) -> None:
    # Deduplicate by (artist, album, title)
    seen = set()
    unique = []
    for m in missing:
        key = (m["artist"], m["album"], m["title"])
        if key not in seen:
            seen.add(key)
            unique.append(m)

    # Build ranked album list
    album_counts: dict[tuple, int] = defaultdict(int)
    for m in unique:
        album_counts[(m["artist"] or "Unknown Artist", m["album"] or "Unknown Album")] += 1
    ranked = sorted(album_counts.items(), key=lambda x: x[1], reverse=True)

    # Build tree: artist -> album -> [titles]
    tree: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for m in unique:
        artist = m["artist"] or "Unknown Artist"
        album  = m["album"]  or "Unknown Album"
        tree[artist][album].append(m["title"])

    lines = []

    # --- Section 1: ranked acquisition list ---
    lines.append("=" * 90)
    lines.append("TOP MISSING ALBUMS — ranked by track count (best candidates to buy/download)")
    lines.append("=" * 90)
    lines.append(f"{'#':<5} {'Tracks':<8} {'Artist':<35} Album")
    lines.append("-" * 90)
    for i, ((artist, album), n) in enumerate(ranked, 1):
        lines.append(f"{i:<5} {n:<8} {artist[:34]:<35} {album}")

    lines.append("")
    lines.append("")

    # --- Section 2: full breakdown by artist → album → song ---
    lines.append("=" * 90)
    lines.append(f"FULL MISSING TRACK LIST — {len(unique)} unique tracks, by artist → album → song")
    lines.append("=" * 90)
    lines.append("")
    for artist in sorted(tree, key=str.casefold):
        lines.append(artist)
        for album in sorted(tree[artist], key=str.casefold):
            lines.append(f"  {album}")
            for title in sorted(tree[artist][album], key=str.casefold):
                lines.append(f"    - {title}")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Written: {out_path}  ({len(unique)} unique missing tracks)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Cross-check iCloud vs local iPod library.")
    parser.add_argument("--cloud", required=True, type=Path, help="iCloud library XML")
    parser.add_argument("--local", required=True, type=Path, help="Local iPod library XML")
    parser.add_argument("--output", required=True, type=Path, help="Output .txt path")
    args = parser.parse_args()

    print("Parsing cloud library...")
    cloud_tracks = parse_tracks(args.cloud)
    print(f"  {len(cloud_tracks)} tracks")

    print("Parsing local library...")
    local_tracks = parse_tracks(args.local)
    print(f"  {len(local_tracks)} tracks")

    print("Building local index...")
    index = build_index(local_tracks)

    print("Matching...")
    missing = []
    matched = 0
    for t in cloud_tracks:
        if is_matched(t, index):
            matched += 1
        else:
            missing.append(t)

    print(f"  Matched: {matched} / {len(cloud_tracks)}")
    print(f"  Missing: {len(missing)}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_missing(args.output, missing)


if __name__ == "__main__":
    main()
