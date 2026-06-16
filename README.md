# ShortsFarm

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
.venv/bin/shortsfarm doctor
```

By default ShortsFarm stores its SQLite DB and generated files in `./shortsfarm-data`. To use another location:

```bash
export SHORTSFARM_HOME=/path/to/shortsfarm-data
```

## Main Workflow

```bash
.venv/bin/shortsfarm init
.venv/bin/shortsfarm add /path/to/video.mp4
.venv/bin/shortsfarm inbox
.venv/bin/shortsfarm review inbox
.venv/bin/shortsfarm clips --status queued
.venv/bin/shortsfarm render --limit 5
.venv/bin/shortsfarm clips --status done
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
.venv/bin/shortsfarm review open-id <video_id>
.venv/bin/shortsfarm review open-id <video_id> --force
.venv/bin/shortsfarm review open /path/to/video.mp4
.venv/bin/shortsfarm review reset <video_id>
.venv/bin/shortsfarm marks <video_id>
.venv/bin/shortsfarm skip <video_id>
.venv/bin/shortsfarm clips --status failed
.venv/bin/shortsfarm retry-failed
.venv/bin/shortsfarm retry-failed --clip-id <clip_id>
.venv/bin/shortsfarm render-all
```

The legacy split/cut flow is also available:

```bash
.venv/bin/shortsfarm split <video_id> --seconds 60 --mode fast
.venv/bin/shortsfarm cut
.venv/bin/shortsfarm cut-all
```

## Tests

```bash
.venv/bin/pytest
```
