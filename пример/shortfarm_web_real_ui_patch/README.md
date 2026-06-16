# ShortFarm Web UI patch под текущую программу

Это не новая программа и не замена `db.py/services.py/render.py`.
Это web-слой для уже существующего ShortFarm.

## Куда копировать

Скопируй папку:

```text
shortfarm/web/
```

в свой проект рядом с `shortfarm/cli.py`, `shortfarm/db.py`, `shortfarm/services.py`.

## Что использует API

- `split_video_file()` и `split_video_folder()` из `services.py`
- `db.list_jobs()`, `db.list_videos_with_counts()`, `db.list_clips()`
- `render_queued()` и `retry_failed_clips()` из `render.py`
- существующую SQLite БД и `SHORTFARM_HOME`

## Запуск

```bash
python -m pip install -e .
shortfarm init
shortfarm web
```

Открыть:

```text
http://127.0.0.1:8000
```

## Важно

- UI основан на твоём файле `shortfarm_ui.html`.
- Таблицы и счётчики теперь берутся из API, а не из demo HTML.
- Split остаётся синхронным, потому что в текущем проекте нет отдельного background worker.
- MPV-код не трогается и не удаляется.
