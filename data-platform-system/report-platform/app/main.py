from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import settings
from .db import create_all
from .routers import health, imports, metrics, reports, store_config, summary


app = FastAPI(title=settings.app_name)
STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def on_startup() -> None:
    create_all()


app.include_router(health.router)
app.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
app.include_router(imports.router, prefix="/imports", tags=["imports"])
app.include_router(summary.router, prefix="/summary", tags=["summary"])
app.include_router(reports.router, prefix="/reports", tags=["reports"])
app.include_router(store_config.router, prefix="/store-config", tags=["store-config"])
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
