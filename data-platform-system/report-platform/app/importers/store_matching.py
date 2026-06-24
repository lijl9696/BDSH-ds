from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..models import AreaAssignment, Store, StoreAssignment
from .assignment_keys import assignment_key, clean_text, normalize_store_name


STORE_TYPE_ALL = "all"
STORE_TYPE_DIRECT = "direct"
STORE_TYPE_FRANCHISE = "franchise"
STORE_TYPE_UNKNOWN = "unknown"

STORE_TYPE_LABELS = {
    STORE_TYPE_ALL: "全部",
    STORE_TYPE_DIRECT: "直营",
    STORE_TYPE_FRANCHISE: "加盟",
    STORE_TYPE_UNKNOWN: "未知",
}


@dataclass(frozen=True)
class AssignmentResult:
    status: str
    source: str
    confidence: int
    note: str = ""


def normalize_store_type(value: Any) -> str:
    text = clean_text(value).lower()
    if not text:
        return STORE_TYPE_ALL
    if text in {"all", "全部", "通用", "不限", "所有", "默认"}:
        return STORE_TYPE_ALL
    if text in {"direct", "直营", "直营店", "自营"}:
        return STORE_TYPE_DIRECT
    if text in {"franchise", "加盟", "加盟店"}:
        return STORE_TYPE_FRANCHISE
    if text in {"unknown", "未知", "待确认"}:
        return STORE_TYPE_UNKNOWN
    return text


def infer_store_type(store: Store) -> str:
    current = normalize_store_type(store.store_type)
    if current not in {STORE_TYPE_ALL, STORE_TYPE_UNKNOWN}:
        return current
    name = normalize_store_name(store.name)
    if "直营" in name:
        return STORE_TYPE_DIRECT
    return STORE_TYPE_UNKNOWN


def apply_store_assignment(db: Session, *, platform_code: str, store: Store, preserve_manual: bool = True) -> AssignmentResult:
    if preserve_manual and store.assignment_status == "confirmed" and store.assignment_source in {"manual_store", "manual_export"}:
        return AssignmentResult("confirmed", "manual_store", 100, "人工确认配置")
    if not store.province or not store.city:
        return _mark(store, "unconfigured", "missing_location", 0, "缺少省份或城市，无法匹配大区负责人")

    store_key = assignment_key(store.province, store.city, store.name)
    city_key = assignment_key(store.province, store.city, "")
    candidates = _area_candidates(db, store.city)

    store_area_assignment = next(
        (
            item
            for item in candidates
            if _is_enabled(item)
            and normalize_store_name(item.store_name)
            and assignment_key(item.province, item.city, item.store_name) == store_key
        ),
        None,
    )
    if store_area_assignment:
        _apply_area(store, store_area_assignment)
        return _mark(store, "confirmed", "store_name", 100, "按门店名称精确匹配")

    store_type = infer_store_type(store)
    city_assignments = [
        item
        for item in candidates
        if _is_enabled(item)
        and not normalize_store_name(item.store_name)
        and assignment_key(item.province, item.city, item.store_name) == city_key
    ]
    if city_assignments:
        exact_type = next((item for item in city_assignments if normalize_store_type(item.store_type) == store_type), None)
        if exact_type and store_type != STORE_TYPE_UNKNOWN:
            _apply_area(store, exact_type)
            return _mark(store, "auto", "city_type", 85, "按城市+门店性质自动匹配")

        all_type = [item for item in city_assignments if normalize_store_type(item.store_type) == STORE_TYPE_ALL]
        specific_types = {
            normalize_store_type(item.store_type)
            for item in city_assignments
            if normalize_store_type(item.store_type) not in {STORE_TYPE_ALL, STORE_TYPE_UNKNOWN}
        }
        if len(city_assignments) == 1 or (len(all_type) == 1 and not specific_types):
            selected = city_assignments[0] if len(city_assignments) == 1 else all_type[0]
            _apply_area(store, selected)
            return _mark(store, "auto", "city_default", 70, "按城市默认配置自动匹配")
        if store_type == STORE_TYPE_UNKNOWN and len(specific_types) == 1 and not all_type:
            selected_type = next(iter(specific_types))
            selected = next(item for item in city_assignments if normalize_store_type(item.store_type) == selected_type)
            store.store_type = selected_type
            _apply_area(store, selected)
            return _mark(store, "auto", "city_single_type", 65, "城市只有一种门店性质配置，自动套用")

        store.region = None
        store.owner = None
        return _mark(store, "review", "city_conflict", 30, "同城存在多种门店性质配置，请人工确认")

    if store.name:
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
            return _mark(store, "confirmed", "legacy_store", 95, "按旧门店配置精确匹配")

    return _mark(store, "unconfigured", "none", 0, "没有找到可用区域配置")


def sync_stores_from_area_assignments(db: Session) -> int:
    updated = 0
    for store in db.query(Store).all():
        before = (store.region, store.owner, store.store_type, store.assignment_status, store.assignment_source)
        apply_store_assignment(db, platform_code="", store=store, preserve_manual=True)
        after = (store.region, store.owner, store.store_type, store.assignment_status, store.assignment_source)
        if before != after:
            updated += 1
    return updated


def _area_candidates(db: Session, city: str | None) -> list[AreaAssignment]:
    text = clean_text(city)
    if not text:
        return []
    candidates = {text}
    if text.endswith("市"):
        candidates.add(text[:-1])
    else:
        candidates.add(f"{text}市")
    return db.query(AreaAssignment).filter(AreaAssignment.city.in_(candidates)).all()


def _is_enabled(assignment: AreaAssignment) -> bool:
    return getattr(assignment, "enabled", True) is not False


def _apply_area(store: Store, assignment: AreaAssignment) -> None:
    store.region = assignment.region
    store.owner = assignment.owner
    store_type = normalize_store_type(assignment.store_type)
    if store_type != STORE_TYPE_ALL:
        store.store_type = store_type


def _mark(store: Store, status: str, source: str, confidence: int, note: str) -> AssignmentResult:
    store.assignment_status = status
    store.assignment_source = source
    store.assignment_confidence = confidence
    store.assignment_note = note
    if not getattr(store, "store_type", None):
        store.store_type = STORE_TYPE_UNKNOWN
    return AssignmentResult(status, source, confidence, note)
