"""
merge_staging.py — merge a staging music folder into foriPod with zero extra disk space.

Uses os.rename() (atomic inode move) instead of copy+delete, so merging 40GB
takes seconds and requires NO free disk space beyond what's already there.

Handles the "Artist already exists" case by merging at the Album level:
  staging/Radiohead/Kid A/     →  foriPod/Radiohead/Kid A/   (new album added)
  staging/Radiohead/OK Computer → skipped if already exists at dest

Usage:
    python3 merge_staging.py --source SOURCE --dest DEST [--dry-run]
"""

import argparse
import os
import shutil
from pathlib import Path


def merge(source: Path, dest: Path, dry_run: bool):
    moved = skipped = failed = 0

    for src_file in sorted(source.rglob("*")):
        if not src_file.is_file():
            continue

        # Compute destination path, preserving relative structure
        rel = src_file.relative_to(source)
        dst_file = dest / rel

        if dst_file.exists():
            print(f"  SKIP (exists)  {rel}")
            skipped += 1
            continue

        if dry_run:
            print(f"  WOULD MOVE     {rel}")
            moved += 1
            continue

        # Create destination directory if needed (no space cost)
        dst_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            # os.rename is atomic on same filesystem — zero disk space needed
            os.rename(src_file, dst_file)
            print(f"  MOVED          {rel}")
            moved += 1
        except OSError as e:
            # Cross-device move (shouldn't happen on same drive) — fall back to copy+delete
            print(f"  COPY+DEL       {rel}  (cross-device fallback)")
            try:
                shutil.copy2(src_file, dst_file)
                src_file.unlink()
                moved += 1
            except Exception as e2:
                print(f"  FAILED         {rel} — {e2}")
                failed += 1

    # Clean up empty directories left behind in source
    if not dry_run:
        for src_dir in sorted(source.rglob("*"), reverse=True):
            if src_dir.is_dir():
                try:
                    src_dir.rmdir()  # only removes if empty
                except OSError:
                    pass

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Done.")
    print(f"  Moved:   {moved}")
    print(f"  Skipped: {skipped}")
    if failed:
        print(f"  Failed:  {failed}")


def main():
    parser = argparse.ArgumentParser(description="Merge staging music folder into foriPod (zero extra disk space).")
    parser.add_argument("--source", required=True, type=Path, help="Source folder (staging)")
    parser.add_argument("--dest",   required=True, type=Path, help="Destination folder (foriPod/Music)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    dest   = args.dest.expanduser().resolve()

    if not source.exists():
        print(f"Error: source not found: {source}")
        return
    dest.mkdir(parents=True, exist_ok=True)

    print(f"Source: {source}")
    print(f"Dest:   {dest}")
    if args.dry_run:
        print("[DRY RUN] No files will be moved.\n")

    merge(source, dest, args.dry_run)


if __name__ == "__main__":
    main()
