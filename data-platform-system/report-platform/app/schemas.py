from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


Aggregation = Literal[
    "sum",
    "avg",
    "weighted_avg",
    "max",
    "min",
    "count",
    "count_distinct",
    "latest",
    "first",
    "ratio",
    "formula",
]


class MetricCreate(BaseModel):
    code: str
    name: str
    value_type: str = "number"
    unit: str | None = None
    aggregation: Aggregation = "sum"
    description: str | None = None


class MetricRead(MetricCreate):
    id: int
    enabled: bool

    class Config:
        from_attributes = True


class FieldMappingCreate(BaseModel):
    platform_code: str
    source_field: str
    metric_code: str
    data_type: str = "number"
    clean_rule: dict[str, Any] = Field(default_factory=dict)


class FieldMappingRead(FieldMappingCreate):
    id: int
    enabled: bool

    class Config:
        from_attributes = True


class FieldMappingBulkUpsert(BaseModel):
    mappings: list[FieldMappingCreate]


class SummaryRequest(BaseModel):
    start_date: date
    end_date: date
    time_grain: Literal["day", "week", "month", "quarter", "year"] = "month"
    metrics: list[str]
    platforms: list[str] | None = None
    stores: list[str] | None = None
    provinces: list[str] | None = None
    regions: list[str] | None = None
    owners: list[str] | None = None
    group_by: list[Literal["time", "platform", "store", "province", "region", "city", "owner"]] = Field(default_factory=list)
    include_inactive_versions: bool = False


class SummaryRow(BaseModel):
    dimensions: dict[str, Any]
    values: dict[str, Decimal | int | float | None]


class SummaryResponse(BaseModel):
    rows: list[SummaryRow]
    warnings: list[str] = Field(default_factory=list)


class ImportPreviewResponse(BaseModel):
    batch_id: int
    status: str
    rows: int
    store_count: int
    columns: list[str]
    platform_profile: dict[str, Any]
    key_fields: dict[str, str | None]
    mapped_fields: list[str]
    unmapped_fields: list[str]
    metric_value_count: int
    duplicate_metric_values: int
    warnings: int
    sample_warnings: list[dict[str, Any]]
    duplicate_policy: str
