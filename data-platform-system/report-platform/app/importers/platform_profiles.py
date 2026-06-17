from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import settings


@dataclass(frozen=True)
class PlatformProfile:
    code: str
    name: str
    header_scan_rows: int = 1
    date_fields: tuple[str, ...] = ()
    store_code_fields: tuple[str, ...] = ()
    store_name_fields: tuple[str, ...] = ()
    province_fields: tuple[str, ...] = ()
    city_fields: tuple[str, ...] = ()
    region_fields: tuple[str, ...] = ()
    ignored_fields: tuple[str, ...] = ()
    field_aliases: dict[str, str] = field(default_factory=dict)


DEFAULT_PROFILE = PlatformProfile(
    code="default",
    name="默认平台",
    header_scan_rows=1,
    date_fields=("日期", "统计日期", "营业日期", "date", "metric_date"),
    store_code_fields=("门店ID", "门店编码", "store_id", "store_code"),
    store_name_fields=("门店名", "门店名称", "store_name"),
    province_fields=("省份", "所在省份"),
    city_fields=("城市", "所在城市"),
    region_fields=("区域", "所在区域"),
    ignored_fields=(),
)


def load_platform_profile(platform_code: str, path: Path | None = None) -> PlatformProfile:
    config_path = path or settings.platform_profile_path
    if not config_path.exists():
        return DEFAULT_PROFILE
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    platforms = raw.get("platforms", {})
    payload = platforms.get(platform_code, {})
    return PlatformProfile(
        code=platform_code,
        name=payload.get("name", platform_code),
        header_scan_rows=int(payload.get("header_scan_rows", DEFAULT_PROFILE.header_scan_rows)),
        date_fields=tuple(payload.get("date_fields", DEFAULT_PROFILE.date_fields)),
        store_code_fields=tuple(payload.get("store_code_fields", DEFAULT_PROFILE.store_code_fields)),
        store_name_fields=tuple(payload.get("store_name_fields", DEFAULT_PROFILE.store_name_fields)),
        province_fields=tuple(payload.get("province_fields", DEFAULT_PROFILE.province_fields)),
        city_fields=tuple(payload.get("city_fields", DEFAULT_PROFILE.city_fields)),
        region_fields=tuple(payload.get("region_fields", DEFAULT_PROFILE.region_fields)),
        ignored_fields=tuple(payload.get("ignored_fields", DEFAULT_PROFILE.ignored_fields)),
        field_aliases=dict(payload.get("field_aliases", {})),
    )
