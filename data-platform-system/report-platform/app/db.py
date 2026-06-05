from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    ensure_server_defaults()


def ensure_server_defaults() -> None:
    statements = [
        "ALTER TABLE platforms ALTER COLUMN enabled SET DEFAULT TRUE",
        "ALTER TABLE metrics ALTER COLUMN enabled SET DEFAULT TRUE",
        "ALTER TABLE field_mappings ALTER COLUMN clean_rule SET DEFAULT '{}'::jsonb",
        "ALTER TABLE field_mappings ALTER COLUMN enabled SET DEFAULT TRUE",
        "ALTER TABLE import_batches ALTER COLUMN status SET DEFAULT 'pending'",
        "ALTER TABLE import_batches ALTER COLUMN duplicate_policy SET DEFAULT 'skip'",
        "ALTER TABLE import_batches ALTER COLUMN source_type SET DEFAULT 'file'",
        "ALTER TABLE import_batches ALTER COLUMN import_options SET DEFAULT '{}'::jsonb",
        "ALTER TABLE import_batches ALTER COLUMN row_count SET DEFAULT 0",
        "ALTER TABLE import_batches ALTER COLUMN warning_count SET DEFAULT 0",
        "ALTER TABLE metric_values ALTER COLUMN dimensions SET DEFAULT '{}'::jsonb",
        "ALTER TABLE metric_values ALTER COLUMN dimension_hash SET DEFAULT 'default'",
        "ALTER TABLE metric_values ALTER COLUMN version SET DEFAULT 1",
        "ALTER TABLE metric_values ALTER COLUMN is_active SET DEFAULT TRUE",
        "ALTER TABLE text_metric_values ALTER COLUMN dimensions SET DEFAULT '{}'::jsonb",
        "ALTER TABLE text_metric_values ALTER COLUMN dimension_hash SET DEFAULT 'default'",
        "ALTER TABLE text_metric_values ALTER COLUMN version SET DEFAULT 1",
        "ALTER TABLE text_metric_values ALTER COLUMN is_active SET DEFAULT TRUE",
        "ALTER TABLE derived_metric_rules ALTER COLUMN enabled SET DEFAULT TRUE",
        "ALTER TABLE report_presets ALTER COLUMN enabled SET DEFAULT TRUE",
        "ALTER TABLE stores ALTER COLUMN status SET DEFAULT 'active'",
        "ALTER TABLE stores ALTER COLUMN aliases SET DEFAULT '{}'::jsonb",
        "ALTER TABLE area_assignments ALTER COLUMN store_name SET DEFAULT ''",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)


def seed_defaults() -> None:
    if not settings.seed_path.exists():
        return
    sql = settings.seed_path.read_text(encoding="utf-8")
    statements = [statement.strip() for statement in sql.split(";\n\n") if statement.strip()]
    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement if statement.endswith(";") else f"{statement};")
