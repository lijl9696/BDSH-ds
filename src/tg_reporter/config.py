from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd


PLATFORMS = {"meituan": "美团", "douyin": "抖音"}
PLATFORM_ALIASES = {
    "meituan": "meituan",
    "美团": "meituan",
    "mt": "meituan",
    "douyin": "douyin",
    "抖音": "douyin",
    "dy": "douyin",
}


def norm_text(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).replace("\u3000", " ").strip()
    return " ".join(text.split())


def norm_platform(value: object) -> str:
    key = norm_text(value).lower()
    return PLATFORM_ALIASES.get(key, PLATFORM_ALIASES.get(norm_text(value), key))


def truthy(value: object) -> bool:
    text = norm_text(value).lower()
    return text in {"1", "true", "yes", "y", "是", "启用", "输出", "汇总", "日期"}


Aggregate = Literal["sum", "mean", "count", "none"]


def parse_aggregate(value: object, fallback: object = "") -> Aggregate:
    text = norm_text(value).lower()
    if not text:
        text = norm_text(fallback).lower()
    if text in {"", "0", "false", "no", "n", "否", "不汇总", "none"}:
        return "none"
    if "不汇总" in text:
        return "none"
    if any(word in text for word in ["平均", "均值", "avg", "average", "mean"]):
        return "mean"
    if any(word in text for word in ["计数", "统计", "count"]):
        return "count"
    if any(word in text for word in ["求和", "合计", "汇总", "sum", "total"]):
        return "sum"
    if text in {"平均", "平均值", "均值", "avg", "average", "mean"}:
        return "mean"
    if text in {"计数", "统计计数", "数量", "count"}:
        return "count"
    if text in {"求和", "合计", "汇总", "sum", "total", "是", "1", "true", "yes", "y"}:
        return "sum"
    return "sum" if truthy(text) else "none"


@dataclass(frozen=True)
class FieldMapping:
    platform: str
    source: str
    standard: str
    dtype: Literal["text", "number", "date"]
    output: bool
    aggregate: Aggregate
    date_field: bool


@dataclass(frozen=True)
class RankingConfig:
    name: str
    scope: str
    dimension: str
    metric: str
    order: Literal["asc", "desc"]
    top_n: int
    size: str
    enabled: bool
    unit: str = ""
    filter_field: str = ""
    filter_value: str = ""


@dataclass(frozen=True)
class BriefingConfig:
    name: str
    scope: str
    size: str
    enabled: bool
    store_field: str = "门店名"
    group_field: str = "大区"
    rating_field: str = "经营评分"
    star_field: str = "美团星级"
    compare_star_field: str = "点评星级"
    level_field: str = "牌级别"
    review_field: str = "新增评价"
    good_review_field: str = "新增好评"
    bad_review_field: str = "新增差评"
    order_field: str = "核销单量"
    top_n: int = 12


@dataclass
class AppConfig:
    field_mappings: list[FieldMapping]
    store_map: pd.DataFrame
    area_map: pd.DataFrame
    rankings: list[RankingConfig]
    briefings: list[BriefingConfig]

    @property
    def summary_fields(self) -> list[str]:
        fields = []
        for item in self.field_mappings:
            if item.aggregate != "none" and item.standard not in fields:
                fields.append(item.standard)
        return fields

    @property
    def aggregate_map(self) -> dict[str, Aggregate]:
        mapping: dict[str, Aggregate] = {}
        priority = {"none": 0, "count": 1, "mean": 2, "sum": 3}
        for item in self.field_mappings:
            current = mapping.get(item.standard, "none")
            if priority[item.aggregate] > priority[current]:
                mapping[item.standard] = item.aggregate
        return mapping

    @property
    def category_count_fields(self) -> list[str]:
        fields = []
        excluded = {"统计日期", "门店名", "门店ID"}
        for item in self.field_mappings:
            if item.aggregate != "count" or item.dtype != "text":
                continue
            if item.standard in excluded or item.standard.endswith("计数"):
                continue
            if item.standard not in fields:
                fields.append(item.standard)
        return fields

    @property
    def output_fields(self) -> list[str]:
        fields = []
        for item in self.field_mappings:
            if item.output and item.standard not in fields:
                fields.append(item.standard)
        return fields

    def mappings_for(self, platform: str) -> list[FieldMapping]:
        platform = norm_platform(platform)
        return [item for item in self.field_mappings if item.platform == platform]

    def date_mapping_for(self, platform: str) -> FieldMapping | None:
        for item in self.mappings_for(platform):
            if item.date_field:
                return item
        return None


def _read_sheet(path: Path, sheet: str) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name=sheet, dtype=str)
    except ValueError as exc:
        raise ValueError(f"配置表缺少工作表：{sheet}") from exc
    df.columns = [norm_text(col) for col in df.columns]
    return df.dropna(how="all")


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"配置表不存在：{path}")

    mapping_df = _read_sheet(path, "字段映射")
    required = {"平台", "原始字段", "标准字段"}
    missing = required - set(mapping_df.columns)
    if missing:
        raise ValueError(f"字段映射缺少列：{', '.join(sorted(missing))}")

    mappings: list[FieldMapping] = []
    for _, row in mapping_df.iterrows():
        platform = norm_platform(row.get("平台"))
        source = norm_text(row.get("原始字段"))
        standard = norm_text(row.get("标准字段"))
        if not platform or not source or not standard:
            continue
        dtype_raw = norm_text(row.get("字段类型")).lower()
        if dtype_raw in {"数值", "number", "numeric", "金额", "整数"}:
            dtype = "number"
        elif dtype_raw in {"日期", "date", "datetime"}:
            dtype = "date"
        else:
            dtype = "text"
        aggregate_col = _first_existing(row, ["汇总方式", "统计方式", "聚合方式", "计算方式"])
        mappings.append(
            FieldMapping(
                platform=platform,
                source=source,
                standard=standard,
                dtype=dtype,  # type: ignore[arg-type]
                output=truthy(row.get("是否输出", "是")),
                aggregate=parse_aggregate(aggregate_col, row.get("是否汇总", "")),
                date_field=truthy(row.get("是否日期字段", "")),
            )
        )

    store_map = _read_sheet(path, "门店对照")
    for col in ["门店名"]:
        if col not in store_map.columns:
            raise ValueError(f"门店对照缺少列：{col}")
    if "门店ID" not in store_map.columns:
        store_map["门店ID"] = ""
    if "平台" not in store_map.columns:
        store_map["平台"] = ""
    for col in ["省份", "城市", "门店负责人", "大区"]:
        if col not in store_map.columns:
            store_map[col] = ""
    store_map["平台"] = store_map["平台"].map(norm_platform)
    store_map["门店名匹配键"] = store_map["门店名"].map(norm_text)
    store_map["负责人匹配键"] = store_map["门店负责人"].map(norm_text)

    area_map = _load_area_map(path)

    ranking_df = _read_sheet(path, "排行榜配置")
    rankings: list[RankingConfig] = []
    if not ranking_df.empty:
        for _, row in ranking_df.iterrows():
            name = norm_text(row.get("榜单名称"))
            dimension = norm_text(row.get("统计维度"))
            metric = norm_text(row.get("指标字段"))
            if not name or not dimension or not metric:
                continue
            order = norm_text(row.get("排序", "desc")).lower()
            order_value = "asc" if order in {"asc", "升序", "从小到大"} else "desc"
            try:
                top_n = int(float(norm_text(row.get("TopN", 10)) or 10))
            except ValueError:
                top_n = 10
            rankings.append(
                RankingConfig(
                    name=name,
                    scope=norm_text(row.get("数据范围", "合并")) or "合并",
                    dimension=dimension,
                    metric=metric,
                    order=order_value,  # type: ignore[arg-type]
                    top_n=max(top_n, 1),
                    size=norm_text(row.get("输出尺寸", "全部")) or "全部",
                    enabled=truthy(row.get("是否启用", "是")),
                    unit=norm_text(row.get("单位", "")),
                    filter_field=norm_text(row.get("筛选字段", "")),
                    filter_value=norm_text(row.get("筛选值", "")),
                )
            )

    briefings = _load_briefings(path)

    return AppConfig(mappings, store_map, area_map, rankings, briefings)


def _first_existing(row: pd.Series, columns: list[str]) -> object:
    for column in columns:
        if column in row.index:
            value = row.get(column)
            if norm_text(value):
                return value
    return ""


def _load_area_map(path: Path) -> pd.DataFrame:
    try:
        area_map = _read_sheet(path, "区域负责人")
        rename = {
            "所在省份": "省份",
            "所在城市": "城市",
            "运营经理": "城市负责人",
            "负责人": "城市负责人",
        }
        area_map = area_map.rename(columns={key: value for key, value in rename.items() if key in area_map.columns})
        for col in ["省份", "城市", "城市负责人", "大区"]:
            if col not in area_map.columns:
                area_map[col] = ""
        if "大区负责人" not in area_map.columns:
            area_map["大区负责人"] = ""
        area_map["负责人匹配键"] = area_map["城市负责人"].map(norm_text)
        area_map["省份匹配键"] = area_map["省份"].map(norm_text)
        area_map["城市匹配键"] = area_map["城市"].map(norm_text)
        area_map["大区匹配键"] = area_map["大区"].map(norm_text)
        return area_map.dropna(how="all")
    except ValueError:
        pass

    try:
        region_map = _read_sheet(path, "大区对照")
    except ValueError:
        return pd.DataFrame(
            columns=["省份", "城市", "城市负责人", "大区", "大区负责人", "负责人匹配键", "省份匹配键", "城市匹配键", "大区匹配键"]
        )
    for col in ["大区", "大区负责人"]:
        if col not in region_map.columns:
            region_map[col] = ""
    region_map["省份"] = ""
    region_map["城市"] = ""
    region_map["城市负责人"] = ""
    region_map["负责人匹配键"] = ""
    region_map["省份匹配键"] = ""
    region_map["城市匹配键"] = ""
    region_map["大区匹配键"] = region_map["大区"].map(norm_text)
    return region_map


def _load_briefings(path: Path) -> list[BriefingConfig]:
    try:
        briefing_df = _read_sheet(path, "综合简报")
    except ValueError:
        return []
    briefings: list[BriefingConfig] = []
    if briefing_df.empty:
        return briefings
    for _, row in briefing_df.iterrows():
        name = norm_text(row.get("简报名称")) or norm_text(row.get("名称"))
        if not name:
            continue
        field_columns = ["门店字段", "分组字段", "评分字段", "星级字段", "等级字段", "评价数字段", "订单字段"]
        if not any(norm_text(row.get(column)) for column in field_columns):
            continue
        try:
            top_n = int(float(norm_text(row.get("TopN", 12)) or 12))
        except ValueError:
            top_n = 12
        briefings.append(
            BriefingConfig(
                name=name,
                scope=norm_text(row.get("数据范围", "美团")) or "美团",
                size=norm_text(row.get("输出尺寸", "汇报横版")) or "汇报横版",
                enabled=truthy(row.get("是否启用", "是")),
                store_field=norm_text(row.get("门店字段", "门店名")) or "门店名",
                group_field=norm_text(row.get("分组字段", "大区")) or "大区",
                rating_field=norm_text(row.get("评分字段", "经营评分")) or "经营评分",
                star_field=norm_text(row.get("星级字段", "美团星级")) or "美团星级",
                compare_star_field=norm_text(row.get("对比星级字段", "点评星级")) or "点评星级",
                level_field=norm_text(row.get("等级字段", "牌级别")) or "牌级别",
                review_field=norm_text(row.get("评价数字段", "新增评价")) or "新增评价",
                good_review_field=norm_text(row.get("好评数字段", "新增好评")) or "新增好评",
                bad_review_field=norm_text(row.get("差评数字段", "新增差评")) or "新增差评",
                order_field=norm_text(row.get("订单字段", "核销单量")) or "核销单量",
                top_n=max(top_n, 1),
            )
        )
    return briefings
