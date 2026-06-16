# ShortFarm

Local CLI workflow for reviewing long videos in `mpv`, marking useful fragments, and rendering them into short clips with `ffmpeg`.

The implementation follows `ТЗ проекта.pdf`; the `пример/` directory is kept only as the original reference draft.

## Requirements

- Python 3.11+
- `ffmpeg` and `ffprobe`
- `mpv`

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/shortfarm doctor
```

By default ShortFarm stores its SQLite DB and generated files in `./shortfarm-data`. To use another location:

```bash
export SHORTFARM_HOME=/path/to/shortfarm-data
```

## Main Workflow

```bash
.venv/bin/shortfarm init
.venv/bin/shortfarm add /path/to/video.mp4
.venv/bin/shortfarm inbox
.venv/bin/shortfarm review inbox
.venv/bin/shortfarm clips --status queued
.venv/bin/shortfarm render --limit 5
.venv/bin/shortfarm clips --status done
```

## MPV Hotkeys

- `i` - set clip start (`set_in`)
- `o` - set clip end and save mark (`mark`)
- `s` - quick 60-second clip from current position (`quick_clip`)
- `u` - undo the latest mark in the current session (`undo`)
- `d` - finish review and mark video as `reviewed` (`done`)
- `n` - skip video and mark it as `skipped` (`skip`)
- `q` - quit without final decision; marks are preserved and video returns to `inbox` (`quit`)

## Useful Commands

```bash
.venv/bin/shortfarm review open-id <video_id>
.venv/bin/shortfarm review open-id <video_id> --force
.venv/bin/shortfarm review open /path/to/video.mp4
.venv/bin/shortfarm review reset <video_id>
.venv/bin/shortfarm marks <video_id>
.venv/bin/shortfarm skip <video_id>
.venv/bin/shortfarm clips --status failed
.venv/bin/shortfarm retry-failed
.venv/bin/shortfarm retry-failed --clip-id <clip_id>
.venv/bin/shortfarm render-all
```

The legacy split/cut flow is also available:

```bash
.venv/bin/shortfarm split <video_id> --seconds 60 --mode fast
.venv/bin/shortfarm cut
.venv/bin/shortfarm cut-all
```

## Tests

```bash
.venv/bin/pytest
```
