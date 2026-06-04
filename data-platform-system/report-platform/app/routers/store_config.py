from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..importers.store_assignment_importer import import_store_assignments


router = APIRouter()


@router.post("/upload")
def upload_store_config(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict[str, object]:
    content = file.file.read()
    digest = hashlib.sha256(content).hexdigest()
    upload_dir = settings.upload_dir / "store-config"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "store_config.xlsx").name
    storage_path = upload_dir / f"{digest[:12]}_{safe_name}"
    storage_path.write_bytes(content)
    try:
        stats = import_store_assignments(db, storage_path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "imported", "sha256": digest, "stats": stats}
