# Apple Music Toolkit — Developer Reference

## Environment
- **venv:** `.venv/bin/python3` (mutagen, rapidfuzz, requests)
- **Config:** `.env` (copy from `.env.example` — read by `amt.sh` and scripts)
- **Output:** `./output/` (runtime reports, logs, scan results)

## Common Patterns

### Scan → Detect → Fix Pipeline
```bash
python3 main/scan_library.py                    # ffprobe everything → stdout
python3 main/validate.py --dry-run              # find issues
python3 main/fix.py --dry-run                   # preview fixes
python3 main/art_fix.py                         # fetch artwork
```

### Download → Convert → Import
```bash
# 1. Download FLACs to $STAGING_DIR/Artist/Album/
# 2. Convert:
python3 main/download_pipeline.py --staging $STAGING_DIR
# 3. If tags/art need fixing:
.venv/bin/python3 main/fixup_art.py
```

### Playlist Rebuild
```bash
.venv/bin/python3 main/build_playlists.py \
  --xml $XML_DEFAULT \
  --root $MUSIC_ROOT \
  --out playlists_out/ --threshold 80
```

### iPod Sync
- `build_sync_library.py` — hard-link safe tracks, split large tracks at silence points
- Config: `main/sync_config.json` (copy from `sync_config.example.json`)

### ALAC Format
- Always: `-vn -map 0:a -af aformat=sample_fmts=s16p -c:a alac -ar 44100`
- Mutagen: `MP4(path)`, tags: `\xa9nam`, `\xa9ART`, `\xa9alb`, `aART`, `trkn`, `disk`, `covr`
