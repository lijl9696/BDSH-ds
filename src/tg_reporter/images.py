from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from .config import BriefingConfig, RankingConfig
from .processor import ProcessResult


SIZE_PRESETS = {
    "竖版": (1080, 1920),
    "手机竖版": (1080, 1920),
    "横版": (1600, 900),
    "汇报横版": (1600, 900),
}


def generate_ranking_images(result: ProcessResult, rankings: list[RankingConfig], output_dir: str | Path, brand: str = "团购运营") -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for ranking in rankings:
        if not ranking.enabled:
            continue
        table = build_ranking(result, ranking)
        sizes = _sizes_for(ranking.size)
        for size_name, size in sizes.items():
            path = output_dir / f"{_safe_name(_ranking_file_stem(ranking))}_{size_name}.png"
            _draw_ranking(path, ranking, table, result, size, brand)
            paths.append(path)
    return paths


def generate_briefing_images(result: ProcessResult, briefings: list[BriefingConfig], output_dir: str | Path, brand: str = "团购运营") -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for briefing in briefings:
        if not briefing.enabled:
            continue
        for size_name, size in _sizes_for(briefing.size).items():
            path = output_dir / f"{_safe_name(briefing.name)}_{size_name}.png"
            _draw_briefing(path, briefing, result, size, brand)
            paths.append(path)
    return paths


def generate_report_images(
    result: ProcessResult,
    rankings: list[RankingConfig],
    briefings: list[BriefingConfig],
    output_dir: str | Path,
    brand: str = "团购运营",
) -> list[Path]:
    output_dir = Path(output_dir)
    paths = generate_ranking_images(result, rankings, output_dir, brand)
    paths.extend(generate_briefing_images(result, briefings, output_dir, brand))
    return paths


def build_ranking(result: ProcessResult, ranking: RankingConfig) -> pd.DataFrame:
    df = _scope_df(result, ranking.scope)
    if df.empty or ranking.dimension not in df.columns or ranking.metric not in df.columns:
        return pd.DataFrame(columns=[ranking.dimension, ranking.metric])
    data = df.copy()
    if ranking.filter_field and ranking.filter_value:
        if ranking.filter_field not in data.columns:
            return pd.DataFrame(columns=[ranking.dimension, ranking.metric])
        data = data[data[ranking.filter_field].map(_norm_category) == _norm_category(ranking.filter_value)]
    method = result.aggregate_map.get(ranking.metric, "sum")
    if method in {"sum", "mean"}:
        data[ranking.metric] = pd.to_numeric(data[ranking.metric], errors="coerce")
    grouped = data.groupby(ranking.dimension, dropna=False)[ranking.metric]
    if method == "mean":
        table = grouped.mean().reset_index()
    elif method == "count":
        table = grouped.count().reset_index()
    else:
        table = grouped.sum().reset_index()
    table = table.sort_values(ranking.metric, ascending=ranking.order == "asc").head(ranking.top_n)
    table.insert(0, "排名", range(1, len(table) + 1))
    return table


def build_briefing(result: ProcessResult, briefing: BriefingConfig) -> dict[str, object]:
    df = _scope_df(result, briefing.scope).copy()
    if df.empty:
        return {
            "store_table": pd.DataFrame(),
            "groups": pd.DataFrame(),
            "levels": pd.DataFrame(),
            "kpis": {},
        }
    store_field = briefing.store_field if briefing.store_field in df.columns else "门店名"
    group_field = briefing.group_field if briefing.group_field in df.columns else "大区"
    if store_field not in df.columns:
        df[store_field] = "未知门店"
    if group_field not in df.columns:
        df[group_field] = "未分组"

    rating = _number_series(df, briefing.rating_field)
    star = _number_series(df, briefing.star_field)
    compare_star = _number_series(df, briefing.compare_star_field)
    reviews = _number_series(df, briefing.review_field)
    good_reviews = _number_series(df, briefing.good_review_field)
    orders = _number_series(df, briefing.order_field)
    bad_reviews = _number_series(df, briefing.bad_review_field)
    if briefing.bad_review_field not in df.columns:
        bad_reviews = (reviews - good_reviews).clip(lower=0)
    levels = df[briefing.level_field].map(_norm_category) if briefing.level_field in df.columns else pd.Series("未配置", index=df.index)

    work = pd.DataFrame(
        {
            "门店": df[store_field].map(_norm_category),
            "分组": df[group_field].map(_norm_category),
            "评分": rating,
            "主星级": star,
            "对比星级": compare_star,
            "等级": levels,
            "评价数": reviews,
            "好评数": good_reviews,
            "差评数": bad_reviews,
            "订单数": orders,
        }
    )
    store_table = (
        work.groupby(["门店", "分组"], dropna=False)
        .agg(
            评分=("评分", _mean_nonzero),
            主星级=("主星级", _mean_nonzero),
            对比星级=("对比星级", _mean_nonzero),
            等级=("等级", _mode_text),
            评价数=("评价数", "sum"),
            好评数=("好评数", "sum"),
            差评数=("差评数", "sum"),
            订单数=("订单数", "sum"),
        )
        .reset_index()
    )
    store_table["评价率"] = _ratio(store_table["评价数"], store_table["订单数"])
    store_table["好评率"] = _ratio(store_table["好评数"], store_table["评价数"])

    group_table = (
        store_table.groupby("分组", dropna=False)
        .agg(
            门店数=("门店", "nunique"),
            平均评分=("评分", _mean_nonzero),
            评价数=("评价数", "sum"),
            好评数=("好评数", "sum"),
            订单数=("订单数", "sum"),
        )
        .reset_index()
    )
    group_table["评价率"] = _ratio(group_table["评价数"], group_table["订单数"])
    group_table["好评率"] = _ratio(group_table["好评数"], group_table["评价数"])
    group_table = group_table.sort_values(["好评率", "评价数"], ascending=[False, False])

    level_table = store_table["等级"].map(_norm_category).value_counts().reset_index()
    level_table.columns = ["等级", "门店数"]

    total_reviews = float(store_table["评价数"].sum())
    total_good = float(store_table["好评数"].sum())
    total_orders = float(store_table["订单数"].sum())
    kpis = {
        "门店数": int(store_table["门店"].nunique()),
        "平均评分": _mean_nonzero(store_table["评分"]),
        "美团星级": _mean_nonzero(store_table["主星级"]),
        "点评星级": _mean_nonzero(store_table["对比星级"]),
        "评价数": int(total_reviews),
        "好评率": total_good / total_reviews if total_reviews else 0,
        "评价率": total_reviews / total_orders if total_orders else 0,
    }
    return {
        "store_table": store_table,
        "groups": group_table,
        "levels": level_table,
        "kpis": kpis,
    }


def _scope_df(result: ProcessResult, scope: str) -> pd.DataFrame:
    if scope in {"美团", "meituan"}:
        return result.details_by_platform.get("meituan", pd.DataFrame())
    if scope in {"抖音", "douyin"}:
        return result.details_by_platform.get("douyin", pd.DataFrame())
    return result.combined_detail


def _sizes_for(value: str) -> dict[str, tuple[int, int]]:
    if value in {"全部", "all", ""}:
        return {"竖版": SIZE_PRESETS["竖版"], "横版": SIZE_PRESETS["横版"]}
    return {value: SIZE_PRESETS.get(value, SIZE_PRESETS["竖版"])}


def _draw_ranking(
    path: Path,
    ranking: RankingConfig,
    table: pd.DataFrame,
    result: ProcessResult,
    size: tuple[int, int],
    brand: str,
) -> None:
    w, h = size
    img = Image.new("RGB", size, "#F7F8FA")
    draw = ImageDraw.Draw(img)
    title_font = _font(62 if h > w else 52, bold=True)
    sub_font = _font(30 if h > w else 24)
    head_font = _font(34 if h > w else 28, bold=True)
    row_font = _font(32 if h > w else 26)
    small_font = _font(24 if h > w else 20)

    margin = 76 if h > w else 88
    draw.rounded_rectangle((margin, margin, w - margin, h - margin), radius=28, fill="#FFFFFF")
    accent = "#0F766E"
    draw.rectangle((margin, margin, margin + 18, h - margin), fill=accent)
    draw.text((margin + 48, margin + 54), ranking.name, fill="#111827", font=title_font)
    period = f"{result.period_start} 至 {result.period_end}" if result.period_start else "未识别日期"
    draw.text((margin + 52, margin + 134), f"{brand} | {period}", fill="#4B5563", font=sub_font)
    if ranking.filter_field and ranking.filter_value:
        draw.text((margin + 52, margin + 174), f"{ranking.filter_field}：{ranking.filter_value}", fill="#4B5563", font=sub_font)

    top = margin + (270 if h > w and ranking.filter_field else 230 if h > w else 220 if ranking.filter_field else 190)
    left = margin + 52
    right = w - margin - 52
    col_rank = left
    col_name = left + int((right - left) * 0.18)
    col_value = right - int((right - left) * 0.28)

    draw.rounded_rectangle((left, top, right, top + 64), radius=14, fill="#E6F4F1")
    draw.text((col_rank, top + 16), "排名", fill="#0F766E", font=head_font)
    draw.text((col_name, top + 16), ranking.dimension, fill="#0F766E", font=head_font)
    draw.text((col_value, top + 16), ranking.metric, fill="#0F766E", font=head_font)

    row_h = 82 if h > w else 58
    y = top + 82
    if table.empty:
        draw.text((left, y + 40), "暂无可生成排行榜的数据，请检查排行榜配置。", fill="#6B7280", font=row_font)
    else:
        max_rows = min(len(table), max(3, int((h - y - margin - 100) / row_h)))
        max_value = float(pd.to_numeric(table[ranking.metric], errors="coerce").max() or 0)
        for _, row in table.head(max_rows).iterrows():
            rank = int(row["排名"])
            name = str(row[ranking.dimension])
            value = float(row[ranking.metric])
            bg = "#F9FAFB" if rank % 2 else "#FFFFFF"
            draw.rounded_rectangle((left, y, right, y + row_h - 12), radius=12, fill=bg)
            draw.text((col_rank, y + 16), str(rank), fill="#111827", font=row_font)
            draw.text((col_name, y + 16), _truncate(name, 16 if h > w else 24), fill="#111827", font=row_font)
            value_text = _format_number(value, ranking.unit)
            draw.text((col_value, y + 16), value_text, fill="#111827", font=row_font)
            if max_value > 0:
                bar_w = int((right - col_value - 30) * (value / max_value))
                draw.rounded_rectangle((col_value, y + row_h - 20, col_value + bar_w, y + row_h - 12), radius=4, fill=accent)
            y += row_h

    footer = f"数据来源：美团/抖音后台导出报表 | 生成时间由本地程序记录"
    draw.text((margin + 52, h - margin - 58), footer, fill="#6B7280", font=small_font)
    img.save(path)


def _draw_briefing(
    path: Path,
    briefing: BriefingConfig,
    result: ProcessResult,
    size: tuple[int, int],
    brand: str,
) -> None:
    data = build_briefing(result, briefing)
    store_table = data["store_table"]
    group_table = data["groups"]
    level_table = data["levels"]
    kpis = data["kpis"]
    if not isinstance(store_table, pd.DataFrame):
        store_table = pd.DataFrame()
    if not isinstance(group_table, pd.DataFrame):
        group_table = pd.DataFrame()
    if not isinstance(level_table, pd.DataFrame):
        level_table = pd.DataFrame()
    if not isinstance(kpis, dict):
        kpis = {}

    w, h = size
    landscape = w >= h
    img = Image.new("RGB", size, "#EEF2F6")
    draw = ImageDraw.Draw(img)
    title_font = _font(48 if landscape else 56, bold=True)
    sub_font = _font(22 if landscape else 28)
    card_title_font = _font(22 if landscape else 26, bold=True)
    metric_font = _font(34 if landscape else 40, bold=True)
    body_font = _font(21 if landscape else 25)
    small_font = _font(18 if landscape else 22)

    margin = 54 if landscape else 58
    accent = "#0E7490"
    navy = "#0F172A"
    muted = "#64748B"
    good = "#10B981"
    warn = "#F59E0B"
    danger = "#EF4444"

    draw.rounded_rectangle((margin, margin, w - margin, h - margin), radius=30, fill="#FFFFFF")
    draw.rounded_rectangle((margin, margin, w - margin, margin + 132), radius=30, fill=navy)
    draw.rectangle((margin, margin + 78, w - margin, margin + 132), fill=navy)
    period = f"{result.period_start} 至 {result.period_end}" if result.period_start else "未识别日期"
    draw.text((margin + 42, margin + 30), briefing.name, fill="#FFFFFF", font=title_font)
    draw.text((margin + 44, margin + 94), f"{brand} | {briefing.scope} | {period}", fill="#CBD5E1", font=sub_font)
    _pill(draw, (w - margin - 240, margin + 42, w - margin - 42, margin + 88), "综合简报", "#E0F2FE", accent, small_font)

    content_top = margin + 162
    content_bottom = h - margin - 48
    gap = 22
    if landscape:
        left = margin + 34
        right = w - margin - 34
        kpi_h = 130
        _draw_kpi_cards(draw, (left, content_top, right, content_top + kpi_h), kpis, [accent, good, warn, "#6366F1"], card_title_font, metric_font, small_font)
        top2 = content_top + kpi_h + gap
        left_w = int((right - left - gap) * 0.58)
        _draw_level_card(draw, (left, top2, left + left_w, top2 + 220), level_table, "牌级分布", accent, card_title_font, body_font, small_font)
        _draw_group_card(draw, (left + left_w + gap, top2, right, top2 + 220), group_table, "大区表现", good, card_title_font, body_font, small_font)
        table_top = top2 + 220 + gap
        _draw_store_table(
            draw,
            (left, table_top, right, content_bottom),
            store_table,
            briefing.top_n,
            "重点门店表现",
            card_title_font,
            body_font,
            small_font,
            muted,
        )
    else:
        left = margin + 34
        right = w - margin - 34
        _draw_kpi_cards(draw, (left, content_top, right, content_top + 320), kpis, [accent, good, warn, "#6366F1"], card_title_font, metric_font, small_font)
        top2 = content_top + 342
        _draw_level_card(draw, (left, top2, right, top2 + 260), level_table, "牌级分布", accent, card_title_font, body_font, small_font)
        top3 = top2 + 282
        _draw_group_card(draw, (left, top3, right, top3 + 260), group_table, "大区表现", good, card_title_font, body_font, small_font)
        top4 = top3 + 282
        _draw_store_table(
            draw,
            (left, top4, right, content_bottom),
            store_table,
            briefing.top_n,
            "重点门店表现",
            card_title_font,
            body_font,
            small_font,
            muted,
        )

    footer = "数据来源：后台导出报表与本地配置表 | 本图由综合简报配置生成"
    draw.text((margin + 42, h - margin - 34), footer, fill=muted, font=small_font)
    img.save(path)


def _draw_kpi_cards(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], kpis: dict[str, object], colors: list[str], title_font, metric_font, small_font) -> None:
    x1, y1, x2, y2 = box
    gap = 16
    cards = [
        ("覆盖门店", f"{int(kpis.get('门店数', 0))}", "家门店"),
        ("平均经营评分", _format_score(kpis.get("平均评分", 0)), "经营评分得分"),
        ("评价率", _format_percent(kpis.get("评价率", 0)), "新增评价 / 核销单量"),
        ("好评率", _format_percent(kpis.get("好评率", 0)), "新增好评 / 新增评价"),
    ]
    cols = 4 if x2 - x1 > 900 else 2
    rows = 1 if cols == 4 else 2
    card_w = int((x2 - x1 - gap * (cols - 1)) / cols)
    card_h = int((y2 - y1 - gap * (rows - 1)) / rows)
    for idx, (title, value, note) in enumerate(cards):
        col = idx % cols
        row = idx // cols
        cx1 = x1 + col * (card_w + gap)
        cy1 = y1 + row * (card_h + gap)
        cx2 = cx1 + card_w
        cy2 = cy1 + card_h
        color = colors[idx % len(colors)]
        draw.rounded_rectangle((cx1, cy1, cx2, cy2), radius=18, fill="#F8FAFC")
        draw.rectangle((cx1, cy1 + 16, cx1 + 8, cy2 - 16), fill=color)
        draw.text((cx1 + 28, cy1 + 22), title, fill="#334155", font=title_font)
        draw.text((cx1 + 28, cy1 + 56), value, fill="#0F172A", font=metric_font)
        draw.text((cx1 + 28, cy2 - 32), note, fill="#64748B", font=small_font)


def _draw_level_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], table: pd.DataFrame, title: str, accent: str, title_font, body_font, small_font) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=22, fill="#F8FAFC")
    draw.text((x1 + 26, y1 + 22), title, fill="#0F172A", font=title_font)
    if table.empty:
        draw.text((x1 + 26, y1 + 74), "暂无等级数据", fill="#64748B", font=body_font)
        return
    total = max(float(table["门店数"].sum()), 1)
    palette = ["#F59E0B", "#94A3B8", "#B45309", "#38BDF8", "#A78BFA", "#CBD5E1"]
    bar_x = x1 + 116
    bar_right = x2 - 32
    y = y1 + 72
    for idx, row in table.head(5).iterrows():
        level = str(row["等级"])
        count = float(row["门店数"])
        pct = count / total
        color = palette[int(idx) % len(palette)] if isinstance(idx, int) else accent
        draw.text((x1 + 26, y), _truncate(level, 6), fill="#334155", font=body_font)
        draw.rounded_rectangle((bar_x, y + 8, bar_right, y + 28), radius=8, fill="#E2E8F0")
        draw.rounded_rectangle((bar_x, y + 8, bar_x + int((bar_right - bar_x) * pct), y + 28), radius=8, fill=color)
        draw.text((bar_right - 92, y + 32), f"{int(count)}家  {_format_percent(pct)}", fill="#64748B", font=small_font)
        y += 52


def _draw_group_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], table: pd.DataFrame, title: str, accent: str, title_font, body_font, small_font) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=22, fill="#F8FAFC")
    draw.text((x1 + 26, y1 + 22), title, fill="#0F172A", font=title_font)
    if table.empty:
        draw.text((x1 + 26, y1 + 74), "暂无分组数据", fill="#64748B", font=body_font)
        return
    y = y1 + 72
    max_reviews = max(float(table["评价数"].max() or 0), 1)
    for _, row in table.head(4).iterrows():
        name = _truncate(str(row["分组"]), 8)
        rate = float(row.get("好评率", 0) or 0)
        reviews = float(row.get("评价数", 0) or 0)
        draw.text((x1 + 26, y), name, fill="#334155", font=body_font)
        draw.text((x2 - 138, y), _format_percent(rate), fill="#0F172A", font=body_font)
        bar_x = x1 + 136
        bar_right = x2 - 154
        draw.rounded_rectangle((bar_x, y + 8, bar_right, y + 28), radius=8, fill="#E2E8F0")
        draw.rounded_rectangle((bar_x, y + 8, bar_x + int((bar_right - bar_x) * reviews / max_reviews), y + 28), radius=8, fill=accent)
        draw.text((bar_x, y + 32), f"{int(reviews):,}条评价", fill="#64748B", font=small_font)
        y += 52


def _draw_store_table(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    table: pd.DataFrame,
    top_n: int,
    title: str,
    title_font,
    body_font,
    small_font,
    muted: str,
) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=22, fill="#F8FAFC")
    draw.text((x1 + 26, y1 + 22), title, fill="#0F172A", font=title_font)
    if table.empty:
        draw.text((x1 + 26, y1 + 78), "暂无门店数据，请检查综合简报配置。", fill=muted, font=body_font)
        return
    display = table.copy()
    display = display.sort_values(["好评率", "评价数", "评分"], ascending=[False, False, False]).head(top_n)
    headers = ["门店", "大区", "评分", "牌级", "评价", "好评率"]
    widths = [0.34, 0.16, 0.11, 0.12, 0.13, 0.14]
    total_w = x2 - x1 - 52
    xs = [x1 + 26]
    for width in widths[:-1]:
        xs.append(xs[-1] + int(total_w * width))
    header_y = y1 + 76
    draw.rounded_rectangle((x1 + 22, header_y - 10, x2 - 22, header_y + 34), radius=12, fill="#E2E8F0")
    for idx, header in enumerate(headers):
        draw.text((xs[idx], header_y), header, fill="#334155", font=small_font)
    row_h = max(38, int((y2 - header_y - 48) / max(len(display), 1)))
    y = header_y + 48
    for _, row in display.iterrows():
        if y + row_h > y2 - 12:
            break
        draw.line((x1 + 26, y - 10, x2 - 26, y - 10), fill="#E2E8F0", width=1)
        values = [
            _truncate(str(row.get("门店", "")), 16),
            _truncate(str(row.get("分组", "")), 8),
            _format_score(row.get("评分", 0)),
            _truncate(str(row.get("等级", "")), 6),
            f"{int(float(row.get('评价数', 0) or 0)):,}",
            _format_percent(row.get("好评率", 0)),
        ]
        for idx, value in enumerate(values):
            draw.text((xs[idx], y), value, fill="#0F172A", font=body_font if idx == 0 else small_font)
        y += row_h


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    for item in candidates:
        if item and Path(item).exists():
            return ImageFont.truetype(item, size=size)
    return ImageFont.load_default()


def _safe_name(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "_", value).strip() or "ranking"


def _ranking_file_stem(ranking: RankingConfig) -> str:
    if ranking.filter_field and ranking.filter_value:
        return f"{ranking.name}_{ranking.filter_field}_{ranking.filter_value}"
    return ranking.name


def _truncate(value: str, length: int) -> str:
    return value if len(value) <= length else value[: length - 1] + "…"


def _format_number(value: float, unit: str) -> str:
    if abs(value - round(value)) < 0.000001:
        text = f"{int(round(value)):,}"
    else:
        text = f"{value:,.2f}"
    return f"{text}{unit}" if unit else text


def _number_series(df: pd.DataFrame, field: str) -> pd.Series:
    if field not in df.columns:
        return pd.Series(0, index=df.index, dtype="float64")
    return pd.to_numeric(df[field], errors="coerce").fillna(0)


def _mean_nonzero(values: pd.Series) -> float:
    series = pd.to_numeric(values, errors="coerce")
    series = series[series > 0]
    if series.empty:
        return 0.0
    return float(series.mean())


def _mode_text(values: pd.Series) -> str:
    series = values.map(_norm_category)
    series = series[series != "空白"]
    if series.empty:
        return "空白"
    return str(series.mode().iloc[0])


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    top = pd.to_numeric(numerator, errors="coerce").fillna(0)
    bottom = pd.to_numeric(denominator, errors="coerce").fillna(0)
    return top.div(bottom.where(bottom != 0)).fillna(0).clip(lower=0)


def _format_percent(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number * 100:.0f}%"


def _format_score(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number == 0:
        return "-"
    if abs(number) >= 10:
        return f"{number:.1f}"
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _pill(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fill: str, color: str, font) -> None:
    draw.rounded_rectangle(box, radius=18, fill=fill)
    text_box = draw.textbbox((0, 0), text, font=font)
    text_w = text_box[2] - text_box[0]
    text_h = text_box[3] - text_box[1]
    x1, y1, x2, y2 = box
    draw.text((x1 + (x2 - x1 - text_w) / 2, y1 + (y2 - y1 - text_h) / 2 - 1), text, fill=color, font=font)


def _norm_category(value: object) -> str:
    text = "" if pd.isna(value) else str(value).replace("\u3000", " ").strip()
    return " ".join(text.split()) or "空白"
