from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from .processor import ProcessResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    platform TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    exception_count INTEGER NOT NULL,
    UNIQUE(period_start, period_end, platform)
);

CREATE TABLE IF NOT EXISTS normalized_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    platform TEXT NOT NULL,
    store_name TEXT,
    store_id TEXT,
    store_owner TEXT,
    region TEXT,
    region_owner TEXT,
    status TEXT,
    reason TEXT,
    metrics_json TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
"""


def archive_result(result: ProcessResult, db_path: str | Path) -> Path:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    imported_at = datetime.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        for platform_name, df in result.details_by_platform.items():
            platform_label = _platform_label(df, platform_name)
            exception_count = int((df.get("处理状态") == "异常").sum()) if "处理状态" in df.columns else 0
            conn.execute(
                """
                INSERT INTO import_batches(period_start, period_end, platform, imported_at, row_count, exception_count)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(period_start, period_end, platform)
                DO UPDATE SET imported_at=excluded.imported_at, row_count=excluded.row_count, exception_count=excluded.exception_count
                """,
                (result.period_start, result.period_end, platform_label, imported_at, len(df), exception_count),
            )
            conn.execute(
                "DELETE FROM normalized_records WHERE period_start=? AND period_end=? AND platform=?",
                (result.period_start, result.period_end, platform_label),
            )
            _insert_records(conn, df, result, platform_label, imported_at)
        conn.commit()
    finally:
        conn.close()
    return db_path


def _insert_records(conn: sqlite3.Connection, df: pd.DataFrame, result: ProcessResult, platform: str, imported_at: str) -> None:
    fixed = {"平台", "统计日期", "门店名", "门店ID", "门店负责人", "大区", "大区负责人", "处理状态", "异常原因"}
    for _, row in df.iterrows():
        metrics = {col: _json_value(row.get(col)) for col in df.columns if col not in fixed}
        conn.execute(
            """
            INSERT INTO normalized_records(
                period_start, period_end, platform, store_name, store_id, store_owner,
                region, region_owner, status, reason, metrics_json, imported_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.period_start,
                result.period_end,
                platform,
                row.get("门店名", ""),
                row.get("门店ID", ""),
                row.get("门店负责人", ""),
                row.get("大区", ""),
                row.get("大区负责人", ""),
                row.get("处理状态", ""),
                row.get("异常原因", ""),
                json.dumps(metrics, ensure_ascii=False),
                imported_at,
            ),
        )


def _platform_label(df: pd.DataFrame, fallback: str) -> str:
    if "平台" in df.columns and not df.empty:
        return str(df["平台"].iloc[0])
    return fallback


def _json_value(value: object) -> object:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value

