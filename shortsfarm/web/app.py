from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db
from .api import router as api_router
from .files_api import router as files_api_router
from .media_api import router as media_api_router
from .pipeline_api import router as pipeline_api_router
from .studio_api import router as studio_api_router
from .tags_api import router as tags_api_router

WEB_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEB_DIR.parents[1]
STUDIO_DIST = PROJECT_ROOT / "frontend" / "dist"
FAVICON_PATH = WEB_DIR / "static" / "favicon.svg"
def asset_version() -> int:
    return max(
        (WEB_DIR / "static" / "app.js").stat().st_mtime_ns,
        (WEB_DIR / "static" / "js" / "features" / "files.js").stat().st_mtime_ns,
        (WEB_DIR / "static" / "js" / "features" / "tags.js").stat().st_mtime_ns,
        (WEB_DIR / "static" / "style.css").stat().st_mtime_ns,
        (WEB_DIR / "static" / "vendor" / "tabler-icons" / "tabler-icons.min.css").stat().st_mtime_ns,
    )


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


def create_app() -> FastAPI:
    app = FastAPI(title="ShortsFarm Web")
    app.mount("/static", NoCacheStaticFiles(directory=str(WEB_DIR / "static")), name="static")
    if (STUDIO_DIST / "assets").is_dir():
        app.mount(
            "/studio/assets",
            StaticFiles(directory=str(STUDIO_DIST / "assets")),
            name="studio-assets",
        )
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

    @app.on_event("startup")
    def recover_remotion_jobs() -> None:
        db.init_db()
        db.fail_interrupted_remotion_render_jobs()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        context = {
            "request": request,
            "asset_version": asset_version(),
            "studio_built": (STUDIO_DIST / "assets" / "studio.js").is_file(),
        }
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
    app.include_router(files_api_router, prefix="/api")
    app.include_router(tags_api_router, prefix="/api")
    app.include_router(media_api_router, prefix="/api/media")
    app.include_router(pipeline_api_router, prefix="/api/shorts-pipeline")
    app.include_router(studio_api_router, prefix="/api/studio")

    def spa_response(title: str = "ShortsFarm Studio") -> FileResponse | HTMLResponse:
        index_path = STUDIO_DIST / "index.html"
        if index_path.is_file():
            return FileResponse(index_path, media_type="text/html")
        return HTMLResponse(
            f"""
            <!doctype html>
            <html lang="ru"><meta charset="utf-8">
            <title>{title}</title>
            <body style="font-family:system-ui;background:#101216;color:#eee;padding:40px">
              <h1>{title} ещё не собран</h1>
              <p>Выполните в корне проекта:</p>
              <pre style="padding:16px;background:#191d24;border-radius:8px">npm --prefix frontend install
npm --prefix frontend run build</pre>
              <p><a href="/" style="color:#8ab4ff">Вернуться в основную панель</a></p>
            </body></html>
            """,
            status_code=503,
        )

    @app.get("/studio", include_in_schema=False)
    def studio_index() -> Response:
        return spa_response("ShortsFarm Studio")

    @app.get("/studio/{path:path}", include_in_schema=False)
    def studio_spa(path: str) -> Response:
        return spa_response("ShortsFarm Studio")

    @app.get("/player", include_in_schema=False)
    def player_index() -> Response:
        return spa_response("ShortsFarm Video Player")

    @app.get("/player/{path:path}", include_in_schema=False)
    def player_spa(path: str) -> Response:
        return spa_response("ShortsFarm Video Player")
    return app


app = create_app()
