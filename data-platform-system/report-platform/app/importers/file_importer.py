from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from ..models import AreaAssignment, FieldMapping, ImportBatch, MetricValue, RawImportRow, Store, StoreAssignment, TextMetricValue
from .assignment_keys import assignment_key, normalize_store_name
from .platform_profiles import PlatformProfile, load_platform_profile


def preview_file(
    db: Session,
    batch: ImportBatch,
    path: Path,
    *,
    date_field: str | None = None,
    store_code_field: str | None = None,
    store_name_field: str | None = None,
) -> dict[str, Any]:
    profile = load_platform_profile(batch.platform_code)
    df = _read_table(path, profile)
    mappings = _load_mappings(db, batch.platform_code)
    mapping_by_source = _mapping_lookup(mappings, profile)
    selected_date_field, selected_store_code_field, selected_store_name_field = _resolve_key_fields(
        df,
        profile=profile,
        date_field=date_field,
        store_code_field=store_code_field,
        store_name_field=store_name_field,
    )

    if not selected_date_field:
        raise ValueError("无法识别日期字段，请在导入时指定 date_field。")
    if not selected_store_code_field and not selected_store_name_field:
        raise ValueError("无法识别门店字段，请在导入时指定 store_code_field 或 store_name_field。")

    mapped_fields = [field for field in df.columns if _canonical_field(str(field), profile) in mapping_by_source]
    ignored_fields = set(profile.ignored_fields)
    unmapped_fields = [
        str(field)
        for field in df.columns
        if _canonical_field(str(field), profile) not in mapping_by_source and str(field) not in ignored_fields
    ]
    metric_value_count = 0
    duplicate_metric_values = 0
    warnings = 0
    sample_warnings = []
    stores = set()
    for index, row in df.iterrows():
        raw_data = {str(key): _clean_raw_value(value) for key, value in row.to_dict().items()}
        metric_date = _parse_date(raw_data.get(selected_date_field))
        store_code = str(raw_data.get(selected_store_code_field) or raw_data.get(selected_store_name_field) or "").strip()
        store_name = str(raw_data.get(selected_store_name_field) or store_code).strip()
        if not metric_date or not store_code:
            warnings += 1
            if len(sample_warnings) < 20:
                sample_warnings.append({"row_number": int(index) + 1, "warning": "缺少日期或门店"})
            continue
        stores.add(store_code)
        for source_field, mapping in mapping_by_source.items():
            if source_field not in raw_data:
                continue
            if mapping.data_type == "text":
                value = _clean_text_metric(mapping.metric_code, raw_data[source_field])
            else:
                value = _parse_decimal(raw_data[source_field], blank_as_zero=True)
                if value is None:
                    continue
            metric_value_count += 1
            dimension_hash = _dimension_hash({})
            existing = (
                _find_active_text_metric_value(db, batch, metric_date, store_code, mapping.metric_code, dimension_hash)
                if mapping.data_type == "text"
                else _find_active_metric_value(db, batch, metric_date, store_code, mapping.metric_code, dimension_hash)
            )
            if existing:
                duplicate_metric_values += 1

    return {
        "batch_id": batch.id,
        "status": batch.status,
        "rows": len(df),
        "store_count": len(stores),
        "columns": [str(column) for column in df.columns],
        "platform_profile": {
            "code": profile.code,
            "name": profile.name,
            "header_scan_rows": profile.header_scan_rows,
        },
        "key_fields": {
            "date_field": selected_date_field,
            "store_code_field": selected_store_code_field,
            "store_name_field": selected_store_name_field,
        },
        "mapped_fields": [str(field) for field in mapped_fields],
        "unmapped_fields": unmapped_fields,
        "metric_value_count": metric_value_count,
        "duplicate_metric_values": duplicate_metric_values,
        "warnings": warnings,
        "sample_warnings": sample_warnings,
        "duplicate_policy": batch.duplicate_policy,
    }


def commit_file_to_metric_values(
    db: Session,
    batch: ImportBatch,
    path: Path,
    *,
    date_field: str | None = None,
    store_code_field: str | None = None,
    store_name_field: str | None = None,
) -> dict[str, int]:
    profile = load_platform_profile(batch.platform_code)
    df = _read_table(path, profile)
    mappings = _load_mappings(db, batch.platform_code)
    mapping_by_source = _mapping_lookup(mappings, profile)
    selected_date_field, selected_store_code_field, selected_store_name_field = _resolve_key_fields(
        df,
        profile=profile,
        date_field=date_field,
        store_code_field=store_code_field,
        store_name_field=store_name_field,
    )

    if not selected_date_field:
        raise ValueError("无法识别日期字段，请在导入时指定 date_field。")
    if not selected_store_code_field and not selected_store_name_field:
        raise ValueError("无法识别门店字段，请在导入时指定 store_code_field 或 store_name_field。")

    inserted = 0
    skipped = 0
    warnings = 0
    seen_store_codes: set[str] = set()
    for index, row in df.iterrows():
        raw_data = {str(key): _clean_raw_value(value) for key, value in row.to_dict().items()}
        metric_date = _parse_date(raw_data.get(selected_date_field))
        store_code = str(raw_data.get(selected_store_code_field) or raw_data.get(selected_store_name_field) or "").strip()
        store_name = str(raw_data.get(selected_store_name_field) or store_code).strip()
        province = _first_raw_value(raw_data, profile.province_fields)
        city = _first_raw_value(raw_data, profile.city_fields)
        region = _first_raw_value(raw_data, profile.region_fields)
        row_warning = None
        if not metric_date or not store_code:
            row_warning = "缺少日期或门店，未写入指标明细。"
            warnings += 1

        db.add(
            RawImportRow(
                batch_id=batch.id,
                row_number=int(index) + 1,
                raw_data=raw_data,
                normalized_keys={"metric_date": str(metric_date) if metric_date else None, "store_code": store_code},
                warning=row_warning,
            )
        )
        if row_warning:
            continue

        if store_code not in seen_store_codes:
            _upsert_store(
                db,
                platform_code=batch.platform_code,
                store_code=store_code,
                store_name=store_name,
                province=province,
                city=city,
                region=region,
            )
            seen_store_codes.add(store_code)
        for source_field, mapping in mapping_by_source.items():
            if source_field not in raw_data:
                continue
            dimension_hash = _dimension_hash({})
            if mapping.data_type == "text":
                value = _clean_text_metric(mapping.metric_code, raw_data[source_field])
                result = _upsert_text_metric_value(
                    db,
                    batch=batch,
                    metric_date=metric_date,
                    store_code=store_code,
                    metric_code=mapping.metric_code,
                    value=value,
                    dimension_hash=dimension_hash,
                )
            else:
                value = _parse_decimal(raw_data[source_field], blank_as_zero=True)
                if value is None:
                    continue
                result = _upsert_number_metric_value(
                    db,
                    batch=batch,
                    metric_date=metric_date,
                    store_code=store_code,
                    metric_code=mapping.metric_code,
                    value=value,
                    dimension_hash=dimension_hash,
                )
            if result:
                inserted += 1
            else:
                skipped += 1

    batch.row_count = len(df)
    batch.warning_count = warnings
    batch.status = "imported"
    db.commit()
    return {"rows": len(df), "inserted_metric_values": inserted, "skipped_duplicates": skipped, "warnings": warnings}


def _load_mappings(db: Session, platform_code: str) -> list[FieldMapping]:
    return db.query(FieldMapping).filter(FieldMapping.platform_code == platform_code, FieldMapping.enabled.is_(True)).all()


def _resolve_key_fields(
    df: pd.DataFrame,
    *,
    profile: PlatformProfile,
    date_field: str | None,
    store_code_field: str | None,
    store_name_field: str | None,
) -> tuple[str | None, str | None, str | None]:
    return (
        date_field or _first_existing(df, profile.date_fields),
        store_code_field or _first_existing(df, profile.store_code_fields),
        store_name_field or _first_existing(df, profile.store_name_fields),
    )


def _find_active_metric_values(
    db: Session,
    batch: ImportBatch,
    metric_date: date,
    store_code: str,
    metric_code: str,
    dimension_hash: str,
) -> list[MetricValue]:
    return (
        db.query(MetricValue)
        .filter(
            MetricValue.metric_date == metric_date,
            MetricValue.platform_code == batch.platform_code,
            MetricValue.store_code == store_code,
            MetricValue.metric_code == metric_code,
            MetricValue.dimension_hash == dimension_hash,
            MetricValue.is_active.is_(True),
        )
        .order_by(MetricValue.version.desc())
        .all()
    )


def _find_active_metric_value(
    db: Session,
    batch: ImportBatch,
    metric_date: date,
    store_code: str,
    metric_code: str,
    dimension_hash: str,
) -> MetricValue | None:
    values = _find_active_metric_values(db, batch, metric_date, store_code, metric_code, dimension_hash)
    return values[0] if values else None


def _find_active_text_metric_values(
    db: Session,
    batch: ImportBatch,
    metric_date: date,
    store_code: str,
    metric_code: str,
    dimension_hash: str,
) -> list[TextMetricValue]:
    return (
        db.query(TextMetricValue)
        .filter(
            TextMetricValue.metric_date == metric_date,
            TextMetricValue.platform_code == batch.platform_code,
            TextMetricValue.store_code == store_code,
            TextMetricValue.metric_code == metric_code,
            TextMetricValue.dimension_hash == dimension_hash,
            TextMetricValue.is_active.is_(True),
        )
        .order_by(TextMetricValue.version.desc())
        .all()
    )


def _find_active_text_metric_value(
    db: Session,
    batch: ImportBatch,
    metric_date: date,
    store_code: str,
    metric_code: str,
    dimension_hash: str,
) -> TextMetricValue | None:
    values = _find_active_text_metric_values(db, batch, metric_date, store_code, metric_code, dimension_hash)
    return values[0] if values else None


def _upsert_number_metric_value(
    db: Session,
    *,
    batch: ImportBatch,
    metric_date: date,
    store_code: str,
    metric_code: str,
    value: Decimal,
    dimension_hash: str,
) -> bool:
    existing_values = _find_active_metric_values(db, batch, metric_date, store_code, metric_code, dimension_hash)
    existing = existing_values[0] if existing_values else None
    if existing and batch.duplicate_policy == "skip":
        return False
    if existing and batch.duplicate_policy == "overwrite":
        existing.value = value
        existing.batch_id = batch.id
        return True
    if existing and batch.duplicate_policy == "version":
        for existing_value in existing_values:
            existing_value.is_active = False
        version = existing.version + 1
    elif existing:
        version = existing.version + 1
    else:
        version = 1
    db.add(
        MetricValue(
            batch_id=batch.id,
            metric_date=metric_date,
            platform_code=batch.platform_code,
            store_code=store_code,
            metric_code=metric_code,
            value=value,
            dimensions={},
            dimension_hash=dimension_hash,
            version=version,
            is_active=True,
        )
    )
    return True


def _upsert_text_metric_value(
    db: Session,
    *,
    batch: ImportBatch,
    metric_date: date,
    store_code: str,
    metric_code: str,
    value: str,
    dimension_hash: str,
) -> bool:
    existing_values = _find_active_text_metric_values(db, batch, metric_date, store_code, metric_code, dimension_hash)
    existing = existing_values[0] if existing_values else None
    if existing and batch.duplicate_policy == "skip":
        return False
    if existing and batch.duplicate_policy == "overwrite":
        existing.value = value
        existing.batch_id = batch.id
        return True
    if existing and batch.duplicate_policy == "version":
        for existing_value in existing_values:
            existing_value.is_active = False
        version = existing.version + 1
    elif existing:
        version = existing.version + 1
    else:
        version = 1
    db.add(
        TextMetricValue(
            batch_id=batch.id,
            metric_date=metric_date,
            platform_code=batch.platform_code,
            store_code=store_code,
            metric_code=metric_code,
            value=value,
            dimensions={},
            dimension_hash=dimension_hash,
            version=version,
            is_active=True,
        )
    )
    return True


def _read_table(path: Path, profile: PlatformProfile) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return _read_excel_with_profile(path, profile)
    if suffix == ".csv":
        return _normalize_columns(pd.read_csv(path), profile)
    raise ValueError(f"不支持的文件格式：{suffix}")


def _read_excel_with_profile(path: Path, profile: PlatformProfile) -> pd.DataFrame:
    best_df: pd.DataFrame | None = None
    best_score = -1
    for header_row in range(max(profile.header_scan_rows, 1)):
        df = pd.read_excel(path, header=header_row)
        normalized = _normalize_columns(df, profile)
        score = _header_score(normalized, profile)
        if score > best_score:
            best_score = score
            best_df = normalized
    if best_df is None:
        raise ValueError("无法读取 Excel 文件。")
    return best_df


def _first_existing(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    columns = {str(column).strip(): str(column) for column in df.columns}
    for candidate in candidates:
        normalized = str(candidate).strip()
        if normalized in columns:
            return columns[normalized]
    return None


def _normalize_columns(df: pd.DataFrame, profile: PlatformProfile) -> pd.DataFrame:
    renamed = {}
    seen = set()
    for column in df.columns:
        clean = str(column).strip()
        if clean.startswith("Unnamed:"):
            clean = ""
        canonical = _canonical_field(clean, profile)
        if not canonical:
            canonical = clean
        next_name = canonical
        index = 2
        while next_name in seen:
            next_name = f"{canonical}_{index}"
            index += 1
        seen.add(next_name)
        renamed[column] = next_name
    return df.rename(columns=renamed)


def _canonical_field(field_name: str, profile: PlatformProfile) -> str:
    clean = str(field_name).strip()
    return profile.field_aliases.get(clean, clean)


def _mapping_lookup(mappings: list[FieldMapping], profile: PlatformProfile) -> dict[str, FieldMapping]:
    lookup = {}
    for mapping in mappings:
        lookup[_canonical_field(mapping.source_field, profile)] = mapping
    return lookup


def _header_score(df: pd.DataFrame, profile: PlatformProfile) -> int:
    columns = {str(column).strip() for column in df.columns}
    key_hits = sum(1 for field in profile.date_fields + profile.store_code_fields + profile.store_name_fields if field in columns)
    alias_hits = sum(1 for alias in profile.field_aliases.values() if alias in columns)
    non_empty_columns = sum(1 for column in columns if column)
    return key_hits * 10 + alias_hits + non_empty_columns


def _parse_date(value: Any) -> date | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    range_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})\s*[~至-]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})", text)
    if range_match:
        text = range_match.group(1)
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _parse_decimal(value: Any, *, blank_as_zero: bool = False) -> Decimal | None:
    if value is None or pd.isna(value):
        return Decimal("0") if blank_as_zero else None
    text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "").replace("元", "").replace("%", "")
    if not text or text in {"-", "--", "—", "nan", "NaN", "None"}:
        return Decimal("0") if blank_as_zero else None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _clean_text_metric(metric_code: str, value: Any) -> str:
    text = "" if value is None or pd.isna(value) else str(value).strip()
    if metric_code == "meituan_business_medal":
        if "金" in text:
            return "金牌"
        if "银" in text:
            return "银牌"
        if "铜" in text:
            return "铜牌"
        return "无"
    return text or "无"


def _clean_raw_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _first_raw_value(raw_data: dict[str, Any], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        value = raw_data.get(candidate)
        if value not in (None, ""):
            text = str(value).strip()
            if text and text != "-":
                return text
    return None


def _dimension_hash(dimensions: dict[str, Any]) -> str:
    if not dimensions:
        return "default"
    text = repr(sorted(dimensions.items()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _upsert_store(
    db: Session,
    *,
    platform_code: str,
    store_code: str,
    store_name: str,
    province: str | None = None,
    city: str | None = None,
    region: str | None = None,
) -> None:
    store = db.query(Store).filter(Store.store_code == store_code).first()
    if store:
        if store_name:
            store.name = store_name
        if province:
            store.province = province
        if city:
            store.city = city
        if region and not store.region:
            store.region = region
        _apply_store_assignment(db, platform_code=platform_code, store=store)
        return
    store = Store(store_code=store_code, name=store_name or store_code, province=province, city=city, region=region)
    _apply_store_assignment(db, platform_code=platform_code, store=store)
    db.add(store)


def _apply_store_assignment(db: Session, *, platform_code: str, store: Store) -> None:
    if not store.province or not store.city:
        return
    store_key = assignment_key(store.province, store.city, store.name)
    city_key = assignment_key(store.province, store.city, "")
    if store.name:
        store_area_assignment = next(
            (
                item
                for item in _area_candidates(db, store.city)
                if normalize_store_name(item.store_name)
                and assignment_key(item.province, item.city, item.store_name) == store_key
            )
            ,
            None,
        )
        if store_area_assignment:
            store.region = store_area_assignment.region
            store.owner = store_area_assignment.owner
            return
    area_assignment = next(
        (
            item
            for item in _area_candidates(db, store.city)
            if not normalize_store_name(item.store_name)
            and assignment_key(item.province, item.city, item.store_name) == city_key
        )
        ,
        None,
    )
    if area_assignment:
        store.region = area_assignment.region
        store.owner = area_assignment.owner
        return
    if not store.name:
        return
    store_assignment = (
        db.query(StoreAssignment)
        .filter(
            StoreAssignment.platform_code == platform_code,
            StoreAssignment.store_name == store.name,
            StoreAssignment.province == store.province,
            StoreAssignment.city == store.city,
        )
        .first()
    )
    if store_assignment:
        store.region = store_assignment.region
        store.owner = store_assignment.owner


def _area_candidates(db: Session, city: str | None) -> list[AreaAssignment]:
    text = "" if city is None else str(city).strip()
    if not text:
        return []
    candidates = {text}
    if text.endswith("市"):
        candidates.add(text[:-1])
    else:
        candidates.add(f"{text}市")
    return db.query(AreaAssignment).filter(AreaAssignment.city.in_(candidates)).all()
