from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..aggregation import validate_aggregation
from ..db import get_db
from ..models import FieldMapping, Metric
from ..schemas import FieldMappingBulkUpsert, FieldMappingCreate, FieldMappingRead, MetricCreate, MetricRead


router = APIRouter()


@router.get("", response_model=list[MetricRead])
def list_metrics(db: Session = Depends(get_db)) -> list[Metric]:
    return db.query(Metric).order_by(Metric.code).all()


@router.post("", response_model=MetricRead)
def create_metric(payload: MetricCreate, db: Session = Depends(get_db)) -> Metric:
    validate_aggregation(payload.aggregation)
    metric = Metric(**payload.model_dump(), enabled=True)
    db.add(metric)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="指标编码已存在") from exc
    db.refresh(metric)
    return metric


@router.get("/field-mappings", response_model=list[FieldMappingRead])
def list_field_mappings(db: Session = Depends(get_db)) -> list[FieldMapping]:
    return db.query(FieldMapping).order_by(FieldMapping.platform_code, FieldMapping.source_field).all()


@router.post("/field-mappings", response_model=FieldMappingRead)
def create_field_mapping(payload: FieldMappingCreate, db: Session = Depends(get_db)) -> FieldMapping:
    mapping = FieldMapping(**payload.model_dump(), enabled=True)
    db.add(mapping)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="字段映射已存在或指标不存在") from exc
    db.refresh(mapping)
    return mapping


@router.post("/field-mappings/bulk", response_model=list[FieldMappingRead])
def bulk_upsert_field_mappings(payload: FieldMappingBulkUpsert, db: Session = Depends(get_db)) -> list[FieldMapping]:
    results: list[FieldMapping] = []
    for item in payload.mappings:
        existing = (
            db.query(FieldMapping)
            .filter(FieldMapping.platform_code == item.platform_code, FieldMapping.source_field == item.source_field)
            .first()
        )
        if existing:
            existing.metric_code = item.metric_code
            existing.data_type = item.data_type
            existing.clean_rule = item.clean_rule
            existing.enabled = True
            results.append(existing)
        else:
            mapping = FieldMapping(**item.model_dump(), enabled=True)
            db.add(mapping)
            results.append(mapping)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="批量字段映射失败，请确认指标编码已存在") from exc
    for item in results:
        db.refresh(item)
    return results
