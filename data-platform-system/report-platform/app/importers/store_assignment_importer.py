from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import AreaAssignment, MetricValue, Store, StoreAssignment
from .assignment_keys import assignment_key, clean_text
from .store_matching import normalize_store_type, sync_stores_from_area_assignments


AREA_SHEET = "区域配置"
STORE_SHEET = "门店配置"

AREA_COLUMNS = {
    "province": ("所在省份", "省份"),
    "city": ("所在城市", "城市"),
    "store_name": ("门店名", "门店名称", "店铺名称"),
    "store_type": ("门店性质", "门店类型", "性质"),
    "region": ("大区", "区域"),
    "owner": ("负责人", "门店负责人", "运营负责人"),
}
STORE_COLUMNS = {
    "platform_code": ("平台", "platform", "platform_code", "平台代码"),
    "store_name": ("门店名", "门店名称", "店铺名称"),
    "province": ("所在省份", "省份"),
    "city": ("所在城市", "城市"),
}

PLATFORM_ALIASES = {
    "美团": "meituan",
    "meituan": "meituan",
    "点评": "meituan",
    "抖音": "douyin",
    "douyin": "douyin",
}


def import_store_assignments(db: Session, path: Path) -> dict[str, int]:
    _ensure_area_assignment_schema(db)
    sheets = _read_workbook(path)
    area_df = _sheet(sheets, AREA_SHEET)
    area_columns = _resolve_columns(area_df, AREA_COLUMNS, AREA_SHEET, optional={"store_name", "store_type"})

    area_lookup, area_stats = _import_areas(db, area_df, area_columns)
    store_stats = {"upserted": 0, "updated_stores": 0, "warnings": 0}
    # 门店配置 sheet 暂不启用。
    # 现在以平台报表导入的门店名称/省份/城市为准，再通过区域配置按“所在省份+所在城市”
    # 自动匹配大区和负责人。保留 STORE_COLUMNS / _import_stores 代码，后续如需精确到门店覆盖时再开启。
    updated_stores = sync_stores_from_area_assignments(db)
    db.commit()

    return {
        "area_rows": len(area_df),
        "store_rows": 0,
        "upserted_areas": area_stats["upserted"],
        "upserted_assignments": store_stats["upserted"],
        "updated_stores": store_stats["updated_stores"] + updated_stores,
        "warnings": area_stats["warnings"] + store_stats["warnings"],
    }


def _import_areas(db: Session, df: pd.DataFrame, columns: dict[str, str]) -> tuple[dict[tuple[str, str, str, str], tuple[str, str]], dict[str, int]]:
    lookup: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    warnings = 0
    for _, row in df.iterrows():
        province = clean_text(row.get(columns["province"]))
        city = clean_text(row.get(columns["city"]))
        store_name = clean_text(row.get(columns["store_name"])) if "store_name" in columns else ""
        store_type = normalize_store_type(row.get(columns["store_type"])) if "store_type" in columns else "all"
        region = clean_text(row.get(columns["region"]))
        owner = clean_text(row.get(columns["owner"]))
        if not province or not city or not region or not owner:
            warnings += 1
            continue
        lookup[(*assignment_key(province, city, store_name), store_type)] = (region, owner)
    upserted = 0
    for key, (region, owner) in lookup.items():
        assignment = (
            db.query(AreaAssignment)
            .filter(AreaAssignment.city.in_(_city_candidates(key[1])))
            .all()
        )
        existing = next(
            (
                item
                for item in assignment
                if (*assignment_key(item.province, item.city, item.store_name), normalize_store_type(item.store_type)) == key
            ),
            None,
        )
        if existing:
            existing.region = region
            existing.owner = owner
            existing.enabled = True
        else:
            province, city, store_name, store_type = key
            db.add(AreaAssignment(province=province, city=city, store_name=store_name, store_type=store_type, region=region, owner=owner))
        upserted += 1
    return lookup, {"upserted": upserted, "warnings": warnings}


def _import_stores(
    db: Session,
    df: pd.DataFrame,
    columns: dict[str, str],
    area_lookup: dict[tuple[str, str, str, str], tuple[str, str]],
) -> dict[str, int]:
    upserted = 0
    updated_stores = 0
    warnings = 0
    for _, row in df.iterrows():
        platform_code = _clean_platform(row.get(columns["platform_code"]))
        store_name = clean_text(row.get(columns["store_name"]))
        province = clean_text(row.get(columns["province"]))
        city = clean_text(row.get(columns["city"]))
        area = area_lookup.get((*assignment_key(province, city, store_name), "all")) or area_lookup.get((*assignment_key(province, city, ""), "all"))
        if not platform_code or not store_name or not province or not city or not area:
            warnings += 1
            continue
        region, owner = area
        assignment = (
            db.query(StoreAssignment)
            .filter(StoreAssignment.platform_code == platform_code, StoreAssignment.store_name == store_name)
            .first()
        )
        if assignment:
            assignment.province = province
            assignment.city = city
            assignment.region = region
            assignment.owner = owner
        else:
            db.add(
                StoreAssignment(
                    platform_code=platform_code,
                    store_name=store_name,
                    province=province,
                    city=city,
                    region=region,
                    owner=owner,
                )
            )
        upserted += 1
        updated_stores += _sync_stores(db, platform_code, store_name, province, city, region, owner)
    return {"upserted": upserted, "updated_stores": updated_stores, "warnings": warnings}


def _sync_stores(
    db: Session,
    platform_code: str,
    store_name: str,
    province: str,
    city: str,
    region: str,
    owner: str,
) -> int:
    platform_store_codes = (
        db.query(MetricValue.store_code)
        .filter(MetricValue.platform_code == platform_code, MetricValue.is_active.is_(True))
        .distinct()
        .subquery()
    )
    stores = (
        db.query(Store)
        .filter(Store.city.in_(_city_candidates(city)), Store.store_code.in_(db.query(platform_store_codes.c.store_code)))
        .all()
    )
    updated = 0
    for store in stores:
        if assignment_key(store.province, store.city, store.name) != assignment_key(province, city, store_name):
            continue
        store.region = region
        store.owner = owner
        updated += 1
    return updated


def _read_workbook(path: Path) -> dict[str, pd.DataFrame]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, sheet_name=None)
    if suffix == ".csv":
        return {AREA_SHEET: pd.read_csv(path)}
    raise ValueError("门店配置请上传包含“区域配置”sheet 的 Excel 文件。")


def _sheet(sheets: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    if name not in sheets:
        raise ValueError(f"门店配置缺少 sheet：{name}")
    return sheets[name]


def _resolve_columns(
    df: pd.DataFrame,
    required: dict[str, tuple[str, ...]],
    sheet_name: str,
    *,
    optional: set[str] | None = None,
) -> dict[str, str]:
    optional = optional or set()
    available = {str(column).strip(): str(column) for column in df.columns}
    resolved: dict[str, str] = {}
    for key, candidates in required.items():
        for candidate in candidates:
            if candidate in available:
                resolved[key] = available[candidate]
                break
        if key not in resolved and key not in optional:
            raise ValueError(f"{sheet_name} 缺少必要字段：{candidates[0]}")
    return resolved


def _ensure_area_assignment_schema(db: Session) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    db.execute(text("ALTER TABLE area_assignments ADD COLUMN IF NOT EXISTS store_name VARCHAR(255) NOT NULL DEFAULT ''"))
    db.execute(text("ALTER TABLE area_assignments ADD COLUMN IF NOT EXISTS store_type VARCHAR(32) NOT NULL DEFAULT 'all'"))
    db.execute(text("ALTER TABLE area_assignments ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE"))
    db.execute(text("ALTER TABLE area_assignments DROP CONSTRAINT IF EXISTS uq_area_assignment_province_city"))
    db.execute(text("ALTER TABLE area_assignments DROP CONSTRAINT IF EXISTS uq_area_assignment_province_city_store"))
    db.execute(text("DROP INDEX IF EXISTS uq_area_assignment_province_city_store"))
    db.execute(
        text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_area_assignment_province_city_store_type') THEN "
            "ALTER TABLE area_assignments ADD CONSTRAINT uq_area_assignment_province_city_store_type UNIQUE (province, city, store_name, store_type); "
            "END IF; "
            "END $$;"
        )
    )


def _clean_text(value: Any) -> str:
    return clean_text(value)


def _clean_platform(value: Any) -> str:
    text = clean_text(value)
    return PLATFORM_ALIASES.get(text, text)


def _city_candidates(city: str) -> list[str]:
    text = clean_text(city)
    if not text:
        return [""]
    candidates = {text}
    if text.endswith("市"):
        candidates.add(text[:-1])
    else:
        candidates.add(f"{text}市")
    return list(candidates)
