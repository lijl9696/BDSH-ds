from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Platform(Base):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    province: Mapped[str | None] = mapped_column(String(128), index=True)
    city: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128), index=True)
    owner: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default=text("'active'"))
    aliases: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class StoreAssignment(Base):
    __tablename__ = "store_assignments"
    __table_args__ = (UniqueConstraint("platform_code", "store_name", name="uq_store_assignment_platform_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_code: Mapped[str] = mapped_column(String(64), index=True)
    province: Mapped[str | None] = mapped_column(String(128), index=True)
    city: Mapped[str | None] = mapped_column(String(128), index=True)
    region: Mapped[str] = mapped_column(String(128), index=True)
    owner: Mapped[str] = mapped_column(String(128), index=True)
    store_name: Mapped[str] = mapped_column(String(255), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AreaAssignment(Base):
    __tablename__ = "area_assignments"
    __table_args__ = (UniqueConstraint("province", "city", "store_name", name="uq_area_assignment_province_city_store"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    province: Mapped[str] = mapped_column(String(128), index=True)
    city: Mapped[str] = mapped_column(String(128), index=True)
    store_name: Mapped[str] = mapped_column(String(255), default="", server_default=text("''"), index=True)
    region: Mapped[str] = mapped_column(String(128), index=True)
    owner: Mapped[str] = mapped_column(String(128), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    value_type: Mapped[str] = mapped_column(String(32), default="number")
    unit: Mapped[str | None] = mapped_column(String(64))
    aggregation: Mapped[str] = mapped_column(String(64), default="sum", server_default=text("'sum'"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
    description: Mapped[str | None] = mapped_column(Text)


class FieldMapping(Base):
    __tablename__ = "field_mappings"
    __table_args__ = (UniqueConstraint("platform_code", "source_field", name="uq_field_mapping_platform_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_code: Mapped[str] = mapped_column(String(64), index=True)
    source_field: Mapped[str] = mapped_column(String(255))
    metric_code: Mapped[str] = mapped_column(String(128), ForeignKey("metrics.code"))
    data_type: Mapped[str] = mapped_column(String(32), default="number")
    clean_rule: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_code: Mapped[str] = mapped_column(String(64), index=True)
    period_start: Mapped[date] = mapped_column(Date, index=True)
    period_end: Mapped[date] = mapped_column(Date, index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="file", server_default=text("'file'"))
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default=text("'pending'"))
    duplicate_policy: Mapped[str] = mapped_column(String(32), default="skip", server_default=text("'skip'"))
    import_options: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    row_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    warning_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    files: Mapped[list["ImportFile"]] = relationship(back_populates="batch")


class ImportFile(Base):
    __tablename__ = "import_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped[ImportBatch] = relationship(back_populates="files")


class RawImportRow(Base):
    __tablename__ = "raw_import_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    raw_data: Mapped[dict] = mapped_column(JSONB)
    normalized_keys: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    warning: Mapped[str | None] = mapped_column(Text)


class MetricValue(Base):
    __tablename__ = "metric_values"
    __table_args__ = (
        UniqueConstraint(
            "metric_date",
            "platform_code",
            "store_code",
            "metric_code",
            "dimension_hash",
            "version",
            name="uq_metric_value_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    metric_date: Mapped[date] = mapped_column(Date, index=True)
    platform_code: Mapped[str] = mapped_column(String(64), index=True)
    store_code: Mapped[str] = mapped_column(String(128), index=True)
    metric_code: Mapped[str] = mapped_column(String(128), ForeignKey("metrics.code"), index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    dimension_hash: Mapped[str] = mapped_column(String(64), default="default", server_default=text("'default'"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TextMetricValue(Base):
    __tablename__ = "text_metric_values"
    __table_args__ = (
        UniqueConstraint(
            "metric_date",
            "platform_code",
            "store_code",
            "metric_code",
            "dimension_hash",
            "version",
            name="uq_text_metric_value_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    metric_date: Mapped[date] = mapped_column(Date, index=True)
    platform_code: Mapped[str] = mapped_column(String(64), index=True)
    store_code: Mapped[str] = mapped_column(String(128), index=True)
    metric_code: Mapped[str] = mapped_column(String(128), ForeignKey("metrics.code"), index=True)
    value: Mapped[str] = mapped_column(String(255))
    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    dimension_hash: Mapped[str] = mapped_column(String(64), default="default", server_default=text("'default'"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DerivedMetricRule(Base):
    __tablename__ = "derived_metric_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    metric_code: Mapped[str] = mapped_column(String(128), ForeignKey("metrics.code"), unique=True)
    expression: Mapped[str] = mapped_column(Text)
    numerator_metric: Mapped[str | None] = mapped_column(String(128))
    denominator_metric: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))


class ReportPreset(Base):
    __tablename__ = "report_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    config: Mapped[dict] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("TRUE"))
