# ShortsFarm Windows installer

Этот каталог содержит online-установщик для Windows. Он не вшивает весь проект в
`exe`: установщик скачивает ZIP проекта с GitHub, ставит зависимости из
интернета и создаёт локальный запуск через браузер.

## Что устанавливается

- ShortsFarm source из GitHub ZIP.
- Python 3.12 и виртуальное окружение `.venv`.
- Node.js LTS, `frontend/node_modules`, Remotion packages и собранный `frontend/dist`.
- FFmpeg/FFprobe, mpv и Google Chrome для Remotion/Chromium.
- Ярлыки в Start Menu и на Desktop.

По умолчанию:

```text
App:  %LOCALAPPDATA%\ShortsFarm\app
Data: %LOCALAPPDATA%\ShortsFarm\data
URL:  http://127.0.0.1:8000
```

`SHORTSFARM_HOME` и `SHORTSFARM_CHROMIUM` сохраняются в user environment.

## Быстрая установка без setup.exe

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\installer\windows\install.ps1
```

Можно переопределить источник ZIP:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\installer\windows\install.ps1 `
  -RepositoryZipUrl "https://github.com/username8448/shortsfarm/archive/refs/heads/main.zip"
```

## Сборка setup.exe

1. Установить Inno Setup 6.
2. В корне репозитория выполнить:

```powershell
iscc installer\windows\ShortsFarm.iss
```

Готовый файл появится в:

```text
installer\windows\output\ShortsFarmSetup.exe
```

## Запуск после установки

Ярлык `ShortsFarm` запускает:

```text
shortsfarm web --host 127.0.0.1 --port 8000 --open-browser
```

Если сервер уже запущен, ярлык просто откроет браузер на локальной панели.

## Обновление

Установщик создаёт ярлык `Update ShortsFarm`. Он повторно запускает bootstrapper,
скачивает свежий ZIP и переустанавливает app-код и зависимости. Папка данных
`%LOCALAPPDATA%\ShortsFarm\data` не удаляется.

## Примечания по Windows

- Установка рассчитана на 64-bit Windows 10/11.
- Основной путь установки не требует admin-прав.
- Для установки системных пакетов используется `winget`. Если `winget` отсутствует,
  Python и Node.js имеют fallback-download, а FFmpeg/mpv/Chrome нужно поставить
  вручную или установить `winget`.
- Remotion требует Chrome/Chromium; путь сохраняется в `SHORTSFARM_CHROMIUM`.
