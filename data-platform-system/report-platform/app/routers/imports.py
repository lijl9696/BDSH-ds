from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..importers.file_importer import commit_file_to_metric_values, preview_file
from ..models import ImportBatch, ImportFile
from ..schemas import ImportPreviewResponse


router = APIRouter()


@router.post("/files")
def upload_import_file(
    platform_code: str = Form(...),
    period_start: date = Form(...),
    period_end: date = Form(...),
    duplicate_policy: str = Form("skip"),
    date_field: str | None = Form(None),
    store_code_field: str | None = Form(None),
    store_name_field: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    content = file.file.read()
    digest = hashlib.sha256(content).hexdigest()
    existing_file = (
        db.query(ImportFile)
        .join(ImportBatch, ImportBatch.id == ImportFile.batch_id)
        .filter(
            ImportFile.sha256 == digest,
            ImportBatch.platform_code == platform_code,
            ImportBatch.status == "imported",
        )
        .order_by(ImportFile.id.desc())
        .first()
    )
    if existing_file:
        raise HTTPException(status_code=409, detail=f"这个文件已经导入过，批次 ID：{existing_file.batch_id}。无需重复入库。")

    upload_dir = settings.upload_dir / platform_code / str(period_start)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "upload.bin").name
    storage_path = upload_dir / f"{digest[:12]}_{safe_name}"
    storage_path.write_bytes(content)

    batch = ImportBatch(
        platform_code=platform_code,
        period_start=period_start,
        period_end=period_end,
        duplicate_policy=duplicate_policy,
        import_options={
            "date_field": date_field,
            "store_code_field": store_code_field,
            "store_name_field": store_name_field,
        },
        status="uploaded",
    )
    db.add(batch)
    db.flush()
    db.add(
        ImportFile(
            batch_id=batch.id,
            filename=safe_name,
            storage_path=str(storage_path),
            sha256=digest,
        )
    )
    db.commit()
    return {
        "batch_id": batch.id,
        "status": batch.status,
        "sha256": digest,
        "next_step": f"调用 /imports/{batch.id}/preview 预览，确认后调用 /imports/{batch.id}/commit 入库。",
    }


@router.get("/{batch_id}/preview", response_model=ImportPreviewResponse)
def preview_import(batch_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    batch, import_file = _get_batch_and_file(db, batch_id)
    options = batch.import_options or {}
    try:
        return preview_file(
            db,
            batch,
            Path(import_file.storage_path),
            date_field=options.get("date_field"),
            store_code_field=options.get("store_code_field"),
            store_name_field=options.get("store_name_field"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{batch_id}/commit")
def commit_import(batch_id: int, duplicate_policy: str | None = Form(None), db: Session = Depends(get_db)) -> dict[str, object]:
    batch, import_file = _get_batch_and_file(db, batch_id)
    if batch.status == "imported":
        raise HTTPException(status_code=409, detail="该批次已经入库，不能重复提交。")
    if duplicate_policy:
        batch.duplicate_policy = duplicate_policy
    options = batch.import_options or {}
    try:
        stats = commit_file_to_metric_values(
            db,
            batch,
            Path(import_file.storage_path),
            date_field=options.get("date_field"),
            store_code_field=options.get("store_code_field"),
            store_name_field=options.get("store_name_field"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"batch_id": batch.id, "status": batch.status, "stats": stats}


def _get_batch_and_file(db: Session, batch_id: int) -> tuple[ImportBatch, ImportFile]:
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    import_file = db.query(ImportFile).filter(ImportFile.batch_id == batch.id).order_by(ImportFile.id.desc()).first()
    if not import_file:
        raise HTTPException(status_code=404, detail="导入文件不存在")
    return batch, import_file
