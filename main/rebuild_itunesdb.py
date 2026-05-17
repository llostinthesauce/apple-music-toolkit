"""
rebuild_itunesdb.py — Rebuild iTunesDB for iPod Video (5th/5.5 gen).

Scans iPod_Control/Music/, reads metadata from each file with mutagen,
and writes a valid binary iTunesDB. Backs up the existing one first.
No files are moved or deleted.

Usage:
    python3 main/rebuild_itunesdb.py
    python3 main/rebuild_itunesdb.py --ipod /Volumes/IPOD --dry-run
"""

import argparse
import random
import shutil
import struct
import time
from pathlib import Path
from typing import List, Optional

import mutagen
import os

# ── Constants ─────────────────────────────────────────────────────────────────

# Mac epoch: seconds between 1904-01-01 and 1970-01-01
MAC_EPOCH_OFFSET = 2082844800
AUDIO_EXTS = {".m4a", ".mp3", ".aac", ".m4p", ".aiff", ".wav"}

# iTunesDB version for iPod Video 5th/5.5 gen
ITUNESDB_VERSION = 0x13


# ── Timestamp helpers ─────────────────────────────────────────────────────────

def mac_now() -> int:
    return int(time.time()) + MAC_EPOCH_OFFSET


def unix_to_mac(ts: float) -> int:
    return int(ts) + MAC_EPOCH_OFFSET


# ── mhod string sub-record ────────────────────────────────────────────────────
#
# mhod type IDs for strings:
#   1 = title, 2 = location, 3 = album, 4 = artist
#   5 = genre, 12 = composer, 22 = album_artist

def mhod_string(type_id: int, text: str) -> bytes:
    encoded = text.encode("utf-16-le")
    enc_len = len(encoded)
    header_len = 24
    # payload: string_encoding(4) + unknown(4) + string_len(4) + string_data
    total_len = header_len + 4 + 4 + 4 + enc_len

    buf = bytearray(header_len)
    struct.pack_into("<4s", buf, 0, b"mhod")
    struct.pack_into("<I",  buf, 4, header_len)
    struct.pack_into("<I",  buf, 8, total_len)
    struct.pack_into("<I",  buf, 12, type_id)
    # offsets 16, 20: unknown, leave as 0

    payload = struct.pack("<III", 1, 0, enc_len) + encoded  # 1 = UTF-16 LE
    return bytes(buf) + payload


# ── mhit track record ─────────────────────────────────────────────────────────

def _file_type(path: Path) -> bytes:
    ext = path.suffix.lower()
    if ext in (".m4a", ".aac", ".m4p"):
        return b"M4A "
    if ext == ".mp3":
        return b"MPEG"
    if ext == ".aiff":
        return b"AIFF"
    return b"    "


def mhit(
    track_id: int,
    file_path: Path,
    ipod_root: Path,
    title: str,
    artist: str,
    album: str,
    genre: str,
    track_number: int,
    num_tracks: int,
    disc_number: int,
    num_discs: int,
    year: int,
    duration_ms: int,
    bitrate: int,
    sample_rate: int,
    file_size: int,
) -> bytes:
    # Build location path using colon separators (:iPod_Control:Music:F00:AATD.m4a)
    rel = file_path.relative_to(ipod_root)
    location = ":" + ":".join(rel.parts)

    # Build mhod string children
    strings = [
        (1, title),
        (2, location),
        (3, album),
        (4, artist),
        (5, genre),
    ]
    children = bytearray()
    mhod_count = 0
    for type_id, text in strings:
        if text:
            children += mhod_string(type_id, text)
            mhod_count += 1

    # mhit header is 244 bytes for version 0x13 (5th gen)
    header_len = 244
    total_len = header_len + len(children)
    db_id = random.getrandbits(64)
    mtime = unix_to_mac(file_path.stat().st_mtime) if file_path.exists() else mac_now()

    buf = bytearray(header_len)  # zero-initialized
    struct.pack_into("<4s", buf, 0,   b"mhit")
    struct.pack_into("<I",  buf, 4,   header_len)
    struct.pack_into("<I",  buf, 8,   total_len)
    struct.pack_into("<I",  buf, 12,  mhod_count)
    struct.pack_into("<I",  buf, 16,  track_id)
    struct.pack_into("<I",  buf, 20,  1)              # visible = 1
    struct.pack_into("<4s", buf, 24,  _file_type(file_path))
    # 28-31: vbr(2), compilation(1), rating(1) — leave 0
    struct.pack_into("<I",  buf, 32,  mtime)          # last_modified
    struct.pack_into("<I",  buf, 36,  file_size)
    struct.pack_into("<I",  buf, 40,  duration_ms)
    struct.pack_into("<I",  buf, 44,  track_number)
    struct.pack_into("<I",  buf, 48,  num_tracks)
    struct.pack_into("<I",  buf, 52,  year)
    struct.pack_into("<I",  buf, 56,  bitrate)
    struct.pack_into("<I",  buf, 60,  sample_rate)
    # 64-91: volume_adj, start/stop time, sound_check, play counts — leave 0
    struct.pack_into("<I",  buf, 92,  disc_number)
    struct.pack_into("<I",  buf, 96,  num_discs)
    # 100: user_id — leave 0
    struct.pack_into("<I",  buf, 104, mac_now())      # date_added
    # 108: bookmark_time — leave 0
    struct.pack_into("<Q",  buf, 112, db_id)          # unique 64-bit id
    # 120: checked byte — 0 = track is enabled
    struct.pack_into("<I",  buf, 136, sample_rate * 0x10000)  # sample_rate_x
    struct.pack_into("<I",  buf, 144, mac_now())      # last_modified copy
    struct.pack_into("<Q",  buf, 176, db_id)          # db_id2 (must match)
    struct.pack_into("<I",  buf, 200, 0x01)           # media_type: audio

    return bytes(buf) + bytes(children)


# ── mhlt track list ───────────────────────────────────────────────────────────

def mhlt(tracks_data: List[bytes]) -> bytes:
    # mhlt: magic(4) + header_len(4) + num_tracks(4) + unused(12) = 24 bytes
    # No total_len field — unlike most other mh* records.
    header_len = 24
    buf = bytearray(header_len)
    struct.pack_into("<4s", buf, 0, b"mhlt")
    struct.pack_into("<I",  buf, 4, header_len)
    struct.pack_into("<I",  buf, 8, len(tracks_data))
    return bytes(buf) + b"".join(tracks_data)


# ── mhip playlist item ────────────────────────────────────────────────────────

def mhip(track_id: int) -> bytes:
    header_len = 76
    buf = bytearray(header_len)
    struct.pack_into("<4s", buf, 0,  b"mhip")
    struct.pack_into("<I",  buf, 4,  header_len)
    struct.pack_into("<I",  buf, 8,  header_len)  # total_len = header (no children)
    # 12: num_mhod = 0
    # 16: podcast_grouping_flag = 0
    # 20: group_id = 0
    struct.pack_into("<I",  buf, 24, track_id)
    struct.pack_into("<I",  buf, 28, mac_now())
    return bytes(buf)


# ── mhyp master playlist ──────────────────────────────────────────────────────

def mhyp_master(track_ids: List[int]) -> bytes:
    header_len = 108
    name_mhod = mhod_string(1, "Library")
    items = b"".join(mhip(tid) for tid in track_ids)
    total_len = header_len + len(name_mhod) + len(items)

    buf = bytearray(header_len)
    struct.pack_into("<4s", buf, 0,  b"mhyp")
    struct.pack_into("<I",  buf, 4,  header_len)
    struct.pack_into("<I",  buf, 8,  total_len)
    struct.pack_into("<I",  buf, 12, len(track_ids))  # num_songs
    struct.pack_into("<I",  buf, 16, 1)               # num_mhod (just the name)
    struct.pack_into("<I",  buf, 20, 1)               # is_master = 1
    struct.pack_into("<I",  buf, 28, mac_now())
    struct.pack_into("<Q",  buf, 32, random.getrandbits(64))  # playlist_id
    struct.pack_into("<I",  buf, 44, 1)               # sort_order: manual
    return bytes(buf) + name_mhod + items


# ── mhlp playlist list ────────────────────────────────────────────────────────

def mhlp(track_ids: List[int]) -> bytes:
    # mhlp: magic(4) + header_len(4) + num_playlists(4) + unused(12) = 24 bytes
    header_len = 24
    master = mhyp_master(track_ids)
    buf = bytearray(header_len)
    struct.pack_into("<4s", buf, 0, b"mhlp")
    struct.pack_into("<I",  buf, 4, header_len)
    struct.pack_into("<I",  buf, 8, 1)  # one playlist (the master)
    return bytes(buf) + master


# ── mhsd dataset records ──────────────────────────────────────────────────────

def mhsd(type_id: int, child: bytes) -> bytes:
    header_len = 96
    total_len = header_len + len(child)
    buf = bytearray(header_len)
    struct.pack_into("<4s", buf, 0,  b"mhsd")
    struct.pack_into("<I",  buf, 4,  header_len)
    struct.pack_into("<I",  buf, 8,  total_len)
    struct.pack_into("<I",  buf, 12, type_id)   # 1 = track list, 2 = playlist list
    return bytes(buf) + child


# ── mhbd root record ──────────────────────────────────────────────────────────

def mhbd(tracks_mhsd: bytes, playlists_mhsd: bytes) -> bytes:
    header_len = 104
    children = tracks_mhsd + playlists_mhsd
    total_len = header_len + len(children)

    buf = bytearray(header_len)
    struct.pack_into("<4s", buf, 0,  b"mhbd")
    struct.pack_into("<I",  buf, 4,  header_len)
    struct.pack_into("<I",  buf, 8,  total_len)
    struct.pack_into("<I",  buf, 12, 1)               # unknown = 1
    struct.pack_into("<I",  buf, 16, ITUNESDB_VERSION)
    struct.pack_into("<I",  buf, 20, 2)               # num_children: 2 mhsd records
    struct.pack_into("<Q",  buf, 24, random.getrandbits(64))  # database id
    struct.pack_into("<I",  buf, 32, 2)               # unknown = 2
    return bytes(buf) + children


# ── Metadata reading ──────────────────────────────────────────────────────────

def _tag(tags: dict, *keys) -> str:
    for key in keys:
        val = tags.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            return str(val[0]).strip() if val else ""
        return str(val).strip()
    return ""


def _int_from_tag(tags: dict, *keys) -> int:
    raw = _tag(tags, *keys)
    try:
        return int(raw.split("/")[0]) if raw else 0
    except (ValueError, AttributeError):
        return 0


def read_track_info(path: Path) -> Optional[dict]:
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None:
        return None

    tags = getattr(audio, "tags", {}) or {}
    info = getattr(audio, "info", None)

    title  = _tag(tags, "\xa9nam", "TIT2", "title") or path.stem
    artist = _tag(tags, "\xa9ART", "TPE1", "artist") or "Unknown Artist"
    album  = _tag(tags, "\xa9alb", "TALB", "album") or "Unknown Album"
    genre  = _tag(tags, "\xa9gen", "TCON", "genre") or ""

    # Track number — M4A uses trkn = [(num, total)], MP3 uses TRCK = "num/total"
    trk_raw = tags.get("trkn") or tags.get("TRCK") or tags.get("tracknumber")
    track_number, num_tracks = 0, 0
    if trk_raw:
        raw = trk_raw[0] if isinstance(trk_raw, list) else trk_raw
        if isinstance(raw, tuple):
            track_number = int(raw[0] or 0)
            num_tracks   = int(raw[1] or 0)
        else:
            parts = str(raw).split("/")
            track_number = int(parts[0]) if parts[0].strip().isdigit() else 0
            num_tracks   = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else 0

    # Disc number — same pattern
    dsk_raw = tags.get("disk") or tags.get("TPOS") or tags.get("discnumber")
    disc_number, num_discs = 0, 0
    if dsk_raw:
        raw = dsk_raw[0] if isinstance(dsk_raw, list) else dsk_raw
        if isinstance(raw, tuple):
            disc_number = int(raw[0] or 0)
            num_discs   = int(raw[1] or 0)
        else:
            parts = str(raw).split("/")
            disc_number = int(parts[0]) if parts[0].strip().isdigit() else 0

    # Year
    date = _tag(tags, "\xa9day", "TDRC", "TYER", "date", "year")
    year = int(date[:4]) if date and date[:4].isdigit() else 0

    duration_ms = int((getattr(info, "length", 0) or 0) * 1000)
    bitrate     = int(getattr(info, "bitrate",     0) or 0)
    sample_rate = int(getattr(info, "sample_rate", 44100) or 44100)
    file_size   = path.stat().st_size if path.exists() else 0

    return dict(
        title=title, artist=artist, album=album, genre=genre,
        track_number=track_number, num_tracks=num_tracks,
        disc_number=disc_number, num_discs=num_discs,
        year=year, duration_ms=duration_ms,
        bitrate=bitrate, sample_rate=sample_rate, file_size=file_size,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild iTunesDB for iPod Video 5th/5.5 gen from existing files."
    )
    parser.add_argument("--ipod",    default=os.environ.get("IPOD_MOUNT", "/Volumes/IPOD"), help="iPod mount point")
    parser.add_argument("--dry-run", action="store_true",     help="Scan only, no write")
    args = parser.parse_args()

    ipod_root = Path(args.ipod)
    music_dir = ipod_root / "iPod_Control" / "Music"
    db_path   = ipod_root / "iPod_Control" / "iTunes" / "iTunesDB"

    if not music_dir.exists():
        print(f"ERROR: {music_dir} not found. Is the iPod mounted?")
        return

    print(f"Scanning {music_dir} ...")
    files = sorted(
        f for f in music_dir.rglob("*")
        if f.is_file()
        and f.suffix.lower() in AUDIO_EXTS
        and not f.name.startswith("._")
    )
    print(f"  {len(files)} audio files found")

    tracks_data: List[bytes] = []
    track_ids:   List[int]   = []
    skipped = 0

    for i, path in enumerate(files, 1):
        info = read_track_info(path)
        if info is None:
            print(f"  [skip] {path.name}")
            skipped += 1
            continue

        track_id = i
        track_ids.append(track_id)
        tracks_data.append(mhit(
            track_id=track_id,
            file_path=path,
            ipod_root=ipod_root,
            **info,
        ))

        if i % 1000 == 0:
            print(f"  {i}/{len(files)} processed ...")

    print(f"\n  {len(tracks_data)} tracks indexed, {skipped} skipped")
    print("Building database ...")

    db = mhbd(
        mhsd(1, mhlt(tracks_data)),
        mhsd(2, mhlp(track_ids)),
    )

    size_mb = len(db) / 1_048_576
    print(f"  Database size: {size_mb:.1f} MB")

    if args.dry_run:
        print("[dry-run] Not writing.")
        return

    # Back up existing DB
    if db_path.exists() and db_path.stat().st_size > 100:
        bak = db_path.with_suffix(".bak2")
        shutil.copy2(db_path, bak)
        print(f"  Backed up existing DB → {bak.name}")

    db_path.write_bytes(db)
    print(f"\nWritten: {db_path}")
    print("Safely eject the iPod, then hold MENU at boot to enter Apple firmware.")


if __name__ == "__main__":
    main()
