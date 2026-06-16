from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api import router as api_router

WEB_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    app = FastAPI(title="ShortFarm Web")
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
