# Команда `shortfarm web`

В твоём текущем `cli.py` эта команда уже есть. Если в рабочем проекте её нет, добавь перед debug-группой:

```python
@app.command("web")
def web_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Host for local web UI"),
    port: int = typer.Option(8000, "--port", "-p", help="Port for local web UI"),
    open_browser: bool = typer.Option(False, "--open-browser", help="Open browser automatically"),
) -> None:
    """Start the local FastAPI web interface."""
    try:
        ensure_dirs()
        db.init_db()
        import webbrowser
        import uvicorn
        url = f"http://{host}:{port}"
        typer.echo(f"Open {url}")
        if open_browser:
            webbrowser.open(url)
        uvicorn.run(
            "shortfarm.web.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=False,
        )
    except Exception as exc:
        die(str(exc))
```
