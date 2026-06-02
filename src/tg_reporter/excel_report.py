from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as OpenpyxlImage
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .config import PLATFORMS
from .paths import app_root
from .processor import ProcessResult


ROOT = app_root()
DEFAULT_TEMPLATE = ROOT / "配置表" / "输出报表模板.xlsx"
MEITUAN_LOGO = ROOT / "assets" / "excel" / "meituan.png"
MEITUAN_YELLOW = "FFD100"
TRAFFIC_FIELDS = ["曝光人数(人)", "访问人数(人)", "下单人数(人)", "核销单量", "新客核销（人）", "新增评价", "新增好评"]
BUSINESS_LEVELS = ["金牌", "银牌", "铜牌", "无等级"]


def write_excel_report(result: ProcessResult, output_path: str | Path, template_path: str | Path | None = None) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    template = Path(template_path) if template_path else DEFAULT_TEMPLATE if DEFAULT_TEMPLATE.exists() else None
    if template:
        wb = load_workbook(template)
        if not wb.sheetnames:
            wb = Workbook()
    else:
        wb = Workbook()
        wb.active.title = "合并明细"

    sheets: dict[str, pd.DataFrame] = {
        "美团明细": result.details_by_platform.get("meituan", pd.DataFrame()),
        "抖音明细": result.details_by_platform.get("douyin", pd.DataFrame()),
        "合并明细": result.combined_detail,
        "美团流量汇总": build_meituan_traffic_summary(result),
        "美团经营汇总": build_meituan_business_summary(result),
        "抖音汇总": build_douyin_summary(result),
        "合并汇总": result.combined_summary,
    }
    for name, df in sheets.items():
        if name in {"美团流量汇总", "美团经营汇总"}:
            _write_meituan_summary_sheet(wb, name, df, result)
        else:
            _write_sheet(wb, name, df)

    if "处理说明" not in wb.sheetnames:
        ws = wb.create_sheet("处理说明")
    else:
        ws = wb["处理说明"]
        _clear_sheet(ws)
    ws.append(["统计开始日期", result.period_start])
    ws.append(["统计结束日期", result.period_end])
    ws.append(["已导入平台", "、".join(PLATFORMS.get(key, key) for key in result.details_by_platform)])
    ws.append(["汇总指标", "、".join(_metric_label(field, result.aggregate_map.get(field, "sum")) for field in result.summary_fields)])
    for cell in ws[1]:
        cell.font = Font(bold=True)

    wb.save(output_path)
    return output_path


def build_meituan_traffic_summary(result: ProcessResult) -> pd.DataFrame:
    df = result.details_by_platform.get("meituan", pd.DataFrame()).copy()
    columns = ["平台", "大区", "门店负责人", "门店名称", *TRAFFIC_FIELDS]
    if df.empty:
        return pd.DataFrame(columns=columns)
    for col in ["平台", "大区", "门店负责人", "门店名"]:
        if col not in df.columns:
            df[col] = ""
    for field in TRAFFIC_FIELDS:
        df[field] = _number_column(df, field)
    summary = (
        df.groupby(["平台", "大区", "门店负责人", "门店名"], dropna=False)[TRAFFIC_FIELDS]
        .sum()
        .reset_index()
        .rename(columns={"门店名": "门店名称"})
    )
    return _order_existing(summary, columns)


def build_meituan_business_summary(result: ProcessResult) -> pd.DataFrame:
    df = result.details_by_platform.get("meituan", pd.DataFrame()).copy()
    columns = ["平台", "大区", "大区负责人", "分类字段", "分类值", "门店数", "新增评价", "新增好评", "经营评分平均值", "经营评分最高值"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    for col in ["平台", "大区", "大区负责人", "城市负责人", "门店负责人", "门店名", "牌级别"]:
        if col not in df.columns:
            df[col] = ""
    df["大区负责人"] = _first_non_empty(df, ["大区负责人", "城市负责人", "门店负责人"])
    df["分类字段"] = "牌级别"
    df["分类值"] = df["牌级别"].map(_business_level)
    df["新增评价"] = _number_column(df, "新增评价")
    df["新增好评"] = _number_column(df, "新增好评")
    df["经营评分"] = _number_column(df, "经营评分")
    rows: list[dict[str, object]] = []
    grouped = df.groupby(["平台", "大区", "大区负责人", "分类字段", "分类值"], dropna=False)
    for keys, group in grouped:
        platform, area, owner, category_field, category_value = keys
        scores = group["经营评分"][group["经营评分"] > 0]
        rows.append(
            {
                "平台": platform,
                "大区": area,
                "大区负责人": owner,
                "分类字段": category_field,
                "分类值": category_value,
                "门店数": group["门店名"].map(_text_value).replace("", pd.NA).nunique(),
                "新增评价": group["新增评价"].sum(),
                "新增好评": group["新增好评"].sum(),
                "经营评分平均值": scores.mean() if not scores.empty else 0,
                "经营评分最高值": scores.max() if not scores.empty else 0,
            }
        )
    summary = pd.DataFrame(rows, columns=columns)
    if summary.empty:
        return summary
    summary["_level_order"] = summary["分类值"].map({name: idx for idx, name in enumerate(BUSINESS_LEVELS)})
    summary = summary.sort_values(["大区", "大区负责人", "_level_order"]).drop(columns=["_level_order"])
    return summary


def build_douyin_summary(result: ProcessResult) -> pd.DataFrame:
    df = result.details_by_platform.get("douyin", pd.DataFrame()).copy()
    if df.empty:
        return pd.DataFrame(columns=["平台", "大区", "门店负责人", "记录数"])
    metrics = [field for field, method in result.aggregate_map.items() if method != "none" and field in df.columns]
    for metric in metrics:
        df[metric] = _number_column(df, metric)
    for col in ["平台", "大区", "门店负责人"]:
        if col not in df.columns:
            df[col] = ""
    if metrics:
        summary = df.groupby(["平台", "大区", "门店负责人"], dropna=False)[metrics].sum().reset_index()
    else:
        summary = df.groupby(["平台", "大区", "门店负责人"], dropna=False).size().reset_index(name="记录数")
    counts = df.groupby(["平台", "大区", "门店负责人"], dropna=False).size().reset_index(name="记录数")
    if "记录数" in summary.columns:
        summary = summary.drop(columns=["记录数"])
    return summary.merge(counts, on=["平台", "大区", "门店负责人"], how="left")


def _write_meituan_summary_sheet(wb, name: str, df: pd.DataFrame, result: ProcessResult) -> None:
    ws = wb[name] if name in wb.sheetnames else wb.create_sheet(name)
    _clear_sheet(ws)
    end_col = max(len(df.columns), 1)

    for col in range(1, end_col + 1):
        ws.cell(1, col).fill = PatternFill("solid", fgColor="FFFFFF")
        ws.cell(2, col).fill = PatternFill("solid", fgColor=MEITUAN_YELLOW)
        ws.cell(3, col).fill = PatternFill("solid", fgColor="FFFFFF")

    ws.row_dimensions[1].height = 43
    ws.row_dimensions[2].height = 28
    ws.row_dimensions[3].height = 24
    _add_meituan_logo(ws)

    title = f"{name}数据"
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=end_col)
    ws["A2"] = title
    ws["A2"].font = Font(bold=True, color="111827", size=14)
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center")

    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=end_col)
    ws["A3"] = _period_label(result)
    ws["A3"].font = Font(color="374151", size=11)
    ws["A3"].alignment = Alignment(horizontal="center", vertical="center")

    _write_sheet(wb, name, df, start_row=4, clear=False)


def _write_sheet(wb, name: str, df: pd.DataFrame, start_row: int = 1, clear: bool = True) -> None:
    ws = wb[name] if name in wb.sheetnames else wb.create_sheet(name)
    if clear:
        _clear_sheet(ws)
    if df.empty:
        ws.cell(start_row, 1, "暂无数据")
        ws.cell(start_row, 1).font = Font(bold=True, color="666666")
        return

    for col_idx, column in enumerate(df.columns, 1):
        ws.cell(start_row, col_idx, column)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[start_row]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_idx, row in enumerate(df.itertuples(index=False), start_row + 1):
        for col_idx, value in enumerate(row, 1):
            ws.cell(row_idx, col_idx, _excel_value(value))

    ws.freeze_panes = f"A{start_row + 1}"
    last_col = get_column_letter(len(df.columns))
    last_row = start_row + len(df)
    ws.auto_filter.ref = f"A{start_row}:{last_col}{last_row}"
    for idx, column in enumerate(df.columns, 1):
        max_len = max([len(str(column))] + [len(str(value)) for value in df[column].head(200).fillna("")])
        ws.column_dimensions[get_column_letter(idx)].width = min(max(max_len + 2, 10), 36)


def _add_meituan_logo(ws) -> None:
    if not MEITUAN_LOGO.exists():
        return
    try:
        logo = OpenpyxlImage(str(MEITUAN_LOGO))
    except Exception:
        return
    target_height_px = 57  # 1.5 cm at roughly 96 dpi.
    if logo.height:
        ratio = target_height_px / logo.height
        logo.height = target_height_px
        logo.width = int(logo.width * ratio)
    logo.anchor = "A1"
    ws.add_image(logo)


def _period_label(result: ProcessResult) -> str:
    start = _date_mmdd(result.period_start)
    end = _date_mmdd(result.period_end)
    if start and end:
        return f"统计周期：{start}-{end}"
    return "统计周期：未识别"


def _date_mmdd(value: str) -> str:
    if not value:
        return ""
    try:
        date = pd.to_datetime(value, errors="coerce")
        if pd.isna(date):
            return ""
        return date.strftime("%m.%d")
    except Exception:
        return ""


def _clear_sheet(ws) -> None:
    for merged_range in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(merged_range))
    if ws.max_row:
        ws.delete_rows(1, ws.max_row)


def _metric_label(field: str, method: str) -> str:
    labels = {"sum": "求和", "mean": "平均", "count": "计数", "none": "不汇总"}
    return f"{field}({labels.get(method, method)})"


def _excel_value(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _number_column(df: pd.DataFrame, field: str) -> pd.Series:
    if field not in df.columns:
        return pd.Series(0, index=df.index, dtype="float64")
    return pd.to_numeric(df[field], errors="coerce").fillna(0)


def _text_value(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).replace("\u3000", " ").strip()


def _business_level(value: object) -> str:
    text = _text_value(value)
    return text if text in BUSINESS_LEVELS[:3] else "无等级"


def _first_non_empty(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series("", index=df.index, dtype="object")
    for column in columns:
        if column not in df.columns:
            continue
        values = df[column].map(_text_value)
        result = result.where(result.map(_text_value) != "", values)
    return result


def _order_existing(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column not in df.columns:
            df[column] = ""
    return df[columns]
