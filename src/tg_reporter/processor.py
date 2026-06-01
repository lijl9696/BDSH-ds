from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pandas as pd

from .config import Aggregate, AppConfig, PLATFORMS, FieldMapping, norm_platform, norm_text
from .io import read_table


BASE_COLUMNS = [
    "平台",
    "统计日期",
    "省份",
    "城市",
    "门店名",
    "门店ID",
    "城市负责人",
    "门店负责人",
    "大区",
    "大区负责人",
    "处理状态",
    "异常原因",
]


@dataclass
class ProcessResult:
    details_by_platform: dict[str, pd.DataFrame]
    combined_detail: pd.DataFrame
    platform_summary: pd.DataFrame
    combined_summary: pd.DataFrame
    category_summary: pd.DataFrame
    period_start: str
    period_end: str
    summary_fields: list[str]
    aggregate_map: dict[str, Aggregate]


def process_reports(
    config: AppConfig,
    report_paths: Mapping[str, str | Path | None],
    manual_dates: Mapping[str, str] | None = None,
) -> ProcessResult:
    manual_dates = manual_dates or {}
    details: dict[str, pd.DataFrame] = {}
    for platform, path in report_paths.items():
        if not path:
            continue
        platform_key = norm_platform(platform)
        expected_fields = [item.source for item in config.mappings_for(platform_key)]
        raw = read_table(path, expected_fields)
        details[platform_key] = normalize_platform(raw, platform_key, config, manual_dates.get(platform_key, ""))

    if not details:
        raise ValueError("请至少导入一个平台报表。")

    combined = pd.concat(details.values(), ignore_index=True, sort=False)
    combined = _order_columns(combined, config)
    period_start, period_end = _period_range(combined)
    summary_fields = [field for field in config.summary_fields if field in combined.columns]
    aggregate_map = {field: config.aggregate_map.get(field, "sum") for field in summary_fields}
    platform_summary = build_summary(combined, ["平台", "大区", "门店负责人"], aggregate_map)
    combined_summary = build_summary(combined, ["大区", "大区负责人"], aggregate_map)
    category_summary = build_category_summary(combined, config.category_count_fields)
    return ProcessResult(
        details,
        combined,
        platform_summary,
        combined_summary,
        category_summary,
        period_start,
        period_end,
        summary_fields,
        aggregate_map,
    )


def normalize_platform(
    raw: pd.DataFrame,
    platform: str,
    config: AppConfig,
    manual_date: str = "",
) -> pd.DataFrame:
    platform = norm_platform(platform)
    mappings = config.mappings_for(platform)
    if not mappings:
        raise ValueError(f"配置表没有平台字段映射：{PLATFORMS.get(platform, platform)}")

    raw = raw.copy()
    raw.columns = [norm_text(col) for col in raw.columns]
    data = pd.DataFrame(index=raw.index)
    warnings: list[str] = []
    for item in mappings:
        if item.source in raw.columns:
            data[item.standard] = _coerce_series(raw[item.source], item)
        else:
            data[item.standard] = pd.NA
            warnings.append(f"缺少字段:{item.source}")

    if "门店名" not in data.columns:
        raise ValueError(f"{PLATFORMS.get(platform, platform)} 缺少标准字段映射：门店名")
    if "门店ID" not in data.columns:
        data["门店ID"] = ""

    date_mapping = config.date_mapping_for(platform)
    if "统计日期" not in data.columns:
        data["统计日期"] = pd.NA
    if date_mapping and date_mapping.standard in data.columns:
        data["统计日期"] = pd.to_datetime(data[date_mapping.standard], errors="coerce").dt.date.astype("string")
    if data["统计日期"].isna().all() and manual_date:
        data["统计日期"] = manual_date

    data.insert(0, "平台", PLATFORMS.get(platform, platform))
    data["门店名"] = data["门店名"].map(norm_text)
    data["门店ID"] = data["门店ID"].map(norm_text)
    data = enrich_store_info(data, platform, config)
    data = mark_status(data, warnings)
    return data


def enrich_store_info(df: pd.DataFrame, platform: str, config: AppConfig) -> pd.DataFrame:
    store_map = config.store_map.copy()
    platform_key = norm_platform(platform)
    exact = store_map[(store_map["平台"] == platform_key) | (store_map["平台"] == "")]
    exact = exact.drop_duplicates(["门店名匹配键"], keep="first")
    store_cols = ["门店名匹配键"]
    # Main attribution chain: report store name -> store owner -> owner's province/city/region.
    # Platform province/city fields are preserved as source fields but do not drive attribution.
    supplemental_cols = ["门店负责人"]
    for col in supplemental_cols:
        if col in exact.columns:
            store_cols.append(col)
    merged = df.merge(
        exact[store_cols],
        left_on=df["门店名"].map(norm_text),
        right_on="门店名匹配键",
        suffixes=("", "_门店对照"),
        how="left",
    ).drop(columns=["key_0", "门店名匹配键"], errors="ignore")

    for col in supplemental_cols:
        backup = f"{col}_门店对照"
        if backup in merged.columns:
            if col in merged.columns:
                merged[col] = merged[col].where(merged[col].map(norm_text) != "", merged[backup])
                merged = merged.drop(columns=[backup])
            else:
                merged = merged.rename(columns={backup: col})

    merged = enrich_area_info(merged, config)
    return merged


def enrich_area_info(df: pd.DataFrame, config: AppConfig) -> pd.DataFrame:
    area = config.area_map.copy()
    if area.empty:
        for col in ["省份", "城市", "城市负责人", "大区负责人"]:
            if col not in df.columns:
                df[col] = ""
        return df

    owner_area = area[(area["负责人匹配键"] != "")].drop_duplicates(["负责人匹配键"], keep="first")
    merged = df.merge(
        owner_area[["负责人匹配键", "省份", "城市", "城市负责人", "大区", "大区负责人"]],
        left_on=df.get("门店负责人", pd.Series("", index=df.index)).map(norm_text),
        right_on="负责人匹配键",
        suffixes=("", "_区域"),
        how="left",
    ).drop(columns=["key_0", "负责人匹配键"], errors="ignore")

    if "门店负责人" not in merged.columns:
        merged["门店负责人"] = ""
    for col in ["省份", "城市", "城市负责人", "大区", "大区负责人"]:
        area_col = f"{col}_区域"
        if area_col in merged.columns:
            merged[col] = merged[area_col]
            merged = merged.drop(columns=[area_col])
        elif col not in merged.columns:
            merged[col] = ""
    return merged


def mark_status(df: pd.DataFrame, warnings: list[str]) -> pd.DataFrame:
    reasons: list[str] = []
    for _, row in df.iterrows():
        row_reasons = list(warnings)
        if not norm_text(row.get("门店名")):
            row_reasons.append("缺少门店名")
        if not norm_text(row.get("统计日期")) or norm_text(row.get("统计日期")) == "<NA>":
            row_reasons.append("缺少统计日期")
        if not norm_text(row.get("门店负责人")):
            row_reasons.append("未匹配门店负责人")
        if norm_text(row.get("门店负责人")) and not norm_text(row.get("城市负责人")):
            row_reasons.append("负责人未匹配区域负责人")
        if norm_text(row.get("门店负责人")) and not norm_text(row.get("城市")):
            row_reasons.append("负责人未匹配城市")
        if not norm_text(row.get("大区")):
            row_reasons.append("未匹配大区")
        reasons.append("；".join(dict.fromkeys(row_reasons)))
    df["异常原因"] = reasons
    df["处理状态"] = df["异常原因"].map(lambda value: "正常" if not value else "异常")
    return df


def build_summary(df: pd.DataFrame, dimensions: list[str], aggregate_map: Mapping[str, Aggregate]) -> pd.DataFrame:
    existing_dims = [col for col in dimensions if col in df.columns]
    if not existing_dims:
        existing_dims = ["平台"] if "平台" in df.columns else []
    data = df.copy()
    metrics = [metric for metric in aggregate_map if metric in data.columns and aggregate_map[metric] != "none"]
    for metric in metrics:
        if aggregate_map[metric] in {"sum", "mean"}:
            data[metric] = pd.to_numeric(data[metric], errors="coerce")
    if metrics:
        grouped = data.groupby(existing_dims, dropna=False)
        parts: list[pd.DataFrame] = []
        for metric in metrics:
            method = aggregate_map[metric]
            if method == "mean":
                series = grouped[metric].mean()
            elif method == "count":
                series = grouped[metric].count()
            else:
                series = grouped[metric].sum()
            parts.append(series.rename(metric).reset_index())
        summary = parts[0]
        for part in parts[1:]:
            summary = summary.merge(part, on=existing_dims, how="outer")
    else:
        summary = data.groupby(existing_dims, dropna=False).size().reset_index(name="记录数")
    counts = data.groupby(existing_dims, dropna=False).size().reset_index(name="记录数")
    if "记录数" in summary.columns:
        summary = summary.drop(columns=["记录数"])
    summary = summary.merge(counts, on=existing_dims, how="left")
    return summary


def build_category_summary(df: pd.DataFrame, category_fields: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    group_cols = [col for col in ["平台", "大区", "大区负责人"] if col in df.columns]
    if not group_cols:
        group_cols = ["平台"] if "平台" in df.columns else []
    for field in category_fields:
        if field not in df.columns:
            continue
        data = df.copy()
        data["分类字段"] = field
        data["分类值"] = data[field].map(lambda value: norm_text(value) or "空白")
        grouped = data.groupby(group_cols + ["分类字段", "分类值"], dropna=False)
        for key, group in grouped:
            key_values = key if isinstance(key, tuple) else (key,)
            row = dict(zip(group_cols + ["分类字段", "分类值"], key_values))
            if "门店名" in group.columns:
                row["门店数"] = group["门店名"].map(norm_text).replace("", pd.NA).dropna().nunique()
            else:
                row["门店数"] = len(group)
            row["记录数"] = len(group)
            rows.append(row)
    columns = group_cols + ["分类字段", "分类值", "门店数", "记录数"]
    return pd.DataFrame(rows, columns=columns)


def _coerce_series(series: pd.Series, item: FieldMapping) -> pd.Series:
    if item.dtype == "number":
        cleaned = (
            series.astype(str)
            .str.replace("\u3000", "", regex=False)
            .str.replace(" ", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.replace("，", "", regex=False)
            .str.replace("￥", "", regex=False)
            .str.replace("¥", "", regex=False)
            .str.replace("元", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace("—", "", regex=False)
        )
        cleaned = cleaned.mask(cleaned.str.lower().isin({"nan", "none", "-", "--", ""}), "")
        return pd.to_numeric(cleaned, errors="coerce").fillna(0)
    if item.dtype == "date":
        return pd.to_datetime(series, errors="coerce").dt.date.astype("string")
    return series.map(norm_text)


def _period_range(df: pd.DataFrame) -> tuple[str, str]:
    dates = pd.to_datetime(df.get("统计日期"), errors="coerce").dropna()
    if dates.empty:
        return "", ""
    return dates.min().date().isoformat(), dates.max().date().isoformat()


def _order_columns(df: pd.DataFrame, config: AppConfig) -> pd.DataFrame:
    preferred = BASE_COLUMNS + [col for col in config.output_fields if col not in BASE_COLUMNS]
    rest = [col for col in df.columns if col not in preferred]
    return df[[col for col in preferred if col in df.columns] + rest]
