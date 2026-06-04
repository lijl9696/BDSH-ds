from __future__ import annotations

from fastapi import APIRouter
import yaml

from ..config import settings


router = APIRouter()


@router.get("/presets")
def list_report_presets() -> dict:
    if not settings.preset_path.exists():
        return {"reports": []}
    return yaml.safe_load(settings.preset_path.read_text(encoding="utf-8")) or {"reports": []}
