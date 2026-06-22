from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api import router as api_router

WEB_DIR = Path(__file__).resolve().parent
FAVICON_PATH = WEB_DIR / "static" / "favicon.svg"
ASSET_VERSION = max(
    (WEB_DIR / "static" / "app.js").stat().st_mtime_ns,
    (WEB_DIR / "static" / "style.css").stat().st_mtime_ns,
)


def create_app() -> FastAPI:
    app = FastAPI(title="ShortsFarm Web")
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        context = {"request": request, "asset_version": ASSET_VERSION}
        headers = {"Cache-Control": "no-store"}
        try:
            return templates.TemplateResponse(
                request=request,
                name="index.html",
                context=context,
                headers=headers,
            )
        except TypeError:
            return templates.TemplateResponse(
                "index.html",
                context,
                headers=headers,
            )

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(FAVICON_PATH, media_type="image/svg+xml")

    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
