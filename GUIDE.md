# Гайд по использованию ShortFarm

Самый частый сценарий:

```bash
source .venv/bin/activate
shortfarm split video.mp4
```

Если не хотите активировать окружение, можно запускать так:

```bash
./run split video.mp4
```

## Первый запуск

Перейдите в корень проекта, где лежит `pyproject.toml`:

```bash
cd /home/user/data/development/my-projects/shortsfarm
```

Если вы случайно находитесь внутри папки `shortfarm/`, поднимитесь на уровень выше:

```bash
cd ..
```

Создайте окружение, установите программу и проверьте готовность:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
shortfarm init
shortfarm doctor
```

После `source .venv/bin/activate` команда доступна просто как `shortfarm`.

Данные, база и готовые файлы по умолчанию лежат в папке `shortfarm-data` внутри проекта.

## Быстро нарезать одно видео

Нарезать видео по 60 секунд:

```bash
shortfarm split video.mp4
```

Указать другую длину куска:

```bash
shortfarm split video.mp4 --seconds 30
```

Посмотреть план без нарезки и без записи в базу:

```bash
shortfarm split video.mp4 --dry-run
```

Готовые файлы появятся в:

```text
shortfarm-data/output/split/<имя_видео>/<timestamp>/
```

## Пропустить части видео при нарезке

Пропустить начало до `00:07:30`:

```bash
shortfarm split video.mp4 --skip start-00:07:30
```

Пропустить всё после `01:37:00`:

```bash
shortfarm split video.mp4 --skip 01:37:00-end
```

Пропустить середину:

```bash
shortfarm split video.mp4 --skip 00:20:00-00:25:00
```

Можно указать несколько `--skip`:

```bash
shortfarm split video.mp4 --skip start-00:07:30 --skip 01:37:00-end
```

Можно использовать обёртку `skip(...)`:

```bash
shortfarm split video.mp4 --skip "skip(start-00:07:30, 01:37:00-end)"
```

Поддерживаемые форматы времени:

```text
SS
MM:SS
HH:MM:SS
start
end
```

## Нарезать папку с видео

Нарезать все видеофайлы в папке:

```bash
shortfarm split-folder ./videos
```

Нарезать по 30 секунд:

```bash
shortfarm split-folder ./videos --seconds 30
```

С пропуском диапазонов:

```bash
shortfarm split-folder ./videos --skip start-00:07:30 --skip 01:37:00-end
```

Только посмотреть план:

```bash
shortfarm split-folder ./videos --dry-run
```

Если одно видео в папке упадёт с ошибкой, ShortFarm покажет ошибку и продолжит остальные файлы.

## Ручной просмотр через mpv

Открыть конкретное видео на ручную разметку:

```bash
shortfarm review video.mp4
```

Открыть следующее необработанное видео:

```bash
shortfarm review-next
```

Повторно открыть уже просмотренное или пропущенное видео:

```bash
shortfarm review video.mp4 --force
```

## Горячие клавиши в mpv

Во время просмотра:

- `i` - поставить начало фрагмента.
- `o` - поставить конец фрагмента и сохранить метку.
- `s` - быстро сохранить клип на 60 секунд от текущей позиции.
- `u` - отменить последнюю метку текущей сессии.
- `d` - завершить просмотр; видео получит статус `reviewed`.
- `n` - пропустить видео; оно получит статус `skipped`.
- `q` - выйти без финального решения; видео вернётся в `inbox`.

Обычный порядок:

```text
1. Нажмите i в начале интересного момента.
2. Нажмите o в конце интересного момента.
3. Повторите для всех нужных фрагментов.
4. Нажмите d, чтобы завершить просмотр.
```

## Проверить состояние

Показать общую сводку:

```bash
shortfarm status
```

Показать подробную сводку:

```bash
shortfarm status --details
```

Показать состояние конкретного видео:

```bash
shortfarm status video.mp4
```

В сводке видны статусы видео, очередь клипов, количество готовых сегментов и последние ошибки.

## Посмотреть очередь

Показать очередь клипов и последние сегменты:

```bash
shortfarm queue
```

Показать только failed-клипы:

```bash
shortfarm queue --failed
```

Показать готовые клипы:

```bash
shortfarm queue --done
```

Показать очередь по одному видео:

```bash
shortfarm queue --video video.mp4
```

## Рендер клипов после ручной разметки

Если вы размечали видео через `mpv`, клипы попадают в очередь. Нарендерить несколько клипов:

```bash
shortfarm render --limit 5
```

Нарендерить всю очередь:

```bash
shortfarm render-all
```

Проверить результат:

```bash
shortfarm queue --done
```

## Повторить failed-клипы

Вернуть failed-клипы в очередь:

```bash
shortfarm retry-failed
```

Вернуть один failed-клип:

```bash
shortfarm retry-failed --clip-id 7
```

После этого снова запустите рендер:

```bash
shortfarm render --limit 5
```

## Безопасная очистка temp-файлов

Удалить только временные файлы failed/rendering-клипов:

```bash
shortfarm clean
```

Готовые output-файлы эта команда не удаляет.

## Запуск без активации окружения

Вместо `source .venv/bin/activate` можно использовать `./run`:

```bash
./run doctor
./run split video.mp4
./run split-folder ./videos
./run review video.mp4
./run status
```

Если `.venv/bin/shortfarm` ещё нет, `./run` покажет команды установки.

## Advanced/debug команды

Обычно они не нужны. Они оставлены для совместимости и диагностики:

```bash
shortfarm debug add video.mp4
shortfarm debug inbox
shortfarm debug jobs
shortfarm debug clips
shortfarm debug marks 12
shortfarm debug segments 12
shortfarm debug review-id 12
shortfarm debug split-id 12
```

Старые alias вроде `shortfarm add`, `shortfarm inbox`, `shortfarm clips`, `shortfarm marks`, `shortfarm segments` тоже сохранены, но для обычной работы лучше использовать новый интерфейс: `split`, `review`, `status`, `queue`.
