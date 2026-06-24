from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
from PIL import Image, ImageDraw, ImageFont

from .config import Settings


METRIC_CODES = (
    "paid_amount",
    "verified_amount",
    "verified_count",
    "verified_new_customer_count",
    "new_positive_review_count",
)

REPORT_REGIONS = ("郑北", "郑中", "郑东")


@dataclass(frozen=True)
class RegionSummary:
    region: str
    owner: str
    paid_amount: Decimal
    verified_amount: Decimal
    verified_count: Decimal
    verified_new_customer_count: Decimal
    positive_review_count: Decimal


@dataclass(frozen=True)
class DailyReportData:
    report_date: date
    platform_code: str
    yesterday_rows: list[RegionSummary]
    month_rows: list[RegionSummary]

    @property
    def month_start(self) -> date:
        return self.report_date.replace(day=1)

    @property
    def rows(self) -> list[RegionSummary]:
        return self.yesterday_rows

    @property
    def total_paid_amount(self) -> Decimal:
        return _total_paid_amount(self.yesterday_rows)

    @property
    def total_verified_amount(self) -> Decimal:
        return _total_verified_amount(self.yesterday_rows)

    @property
    def total_verified_count(self) -> Decimal:
        return _total_verified_count(self.yesterday_rows)

    @property
    def total_verified_new_customer_count(self) -> Decimal:
        return _total_verified_new_customer_count(self.yesterday_rows)

    @property
    def total_positive_review_count(self) -> Decimal:
        return _total_positive_review_count(self.yesterday_rows)


def fetch_daily_region_report(settings: Settings, report_date: date, platform_code: str = "meituan") -> DailyReportData:
    month_start = report_date.replace(day=1)
    with psycopg.connect(settings.database_url) as connection:
        yesterday_rows = _fetch_region_rows(connection, report_date, report_date, platform_code)
        month_rows = _fetch_region_rows(connection, month_start, report_date, platform_code)
    return DailyReportData(
        report_date=report_date,
        platform_code=platform_code,
        yesterday_rows=yesterday_rows,
        month_rows=month_rows,
    )


def _fetch_region_rows(connection, start_date: date, end_date: date, platform_code: str) -> list[RegionSummary]:
    query = """
    SELECT
      COALESCE(stores.region, '未配置') AS region,
      COALESCE(NULLIF(string_agg(DISTINCT NULLIF(stores.owner, ''), '、'), ''), '未配置') AS owner,
      SUM(CASE WHEN metric_values.metric_code = 'paid_amount' THEN metric_values.value ELSE 0 END) AS paid_amount,
      SUM(CASE WHEN metric_values.metric_code = 'verified_amount' THEN metric_values.value ELSE 0 END) AS verified_amount,
      SUM(CASE WHEN metric_values.metric_code = 'verified_count' THEN metric_values.value ELSE 0 END) AS verified_count,
      SUM(CASE WHEN metric_values.metric_code = 'verified_new_customer_count' THEN metric_values.value ELSE 0 END) AS verified_new_customer_count,
      SUM(CASE WHEN metric_values.metric_code = 'new_positive_review_count' THEN metric_values.value ELSE 0 END) AS positive_review_count
    FROM metric_values
    LEFT JOIN stores ON stores.store_code = metric_values.store_code
    WHERE metric_values.is_active = TRUE
      AND metric_values.platform_code = %s
      AND metric_values.metric_date BETWEEN %s AND %s
      AND metric_values.metric_code = ANY(%s)
      AND COALESCE(stores.region, '未配置') = ANY(%s)
    GROUP BY COALESCE(stores.region, '未配置')
    ORDER BY verified_amount DESC, paid_amount DESC, region ASC;
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (platform_code, start_date, end_date, list(METRIC_CODES), list(REPORT_REGIONS)))
        return [
            RegionSummary(
                region=str(row[0]),
                owner=str(row[1]),
                paid_amount=_decimal(row[2]),
                verified_amount=_decimal(row[3]),
                verified_count=_decimal(row[4]),
                verified_new_customer_count=_decimal(row[5]),
                positive_review_count=_decimal(row[6]),
            )
            for row in cursor.fetchall()
        ]


def render_daily_region_report(
    report: DailyReportData,
    output_path: Path,
    font_path: str | None = None,
    logo_path: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    width = 1200
    scale = 3
    margin = 44
    card_gap = 18
    header_h = 188
    summary_h = 132
    section_title_h = 34
    table_header_h = 58
    row_h = 48
    yesterday_table_h = section_title_h + table_header_h + max(1, len(report.yesterday_rows)) * row_h
    month_table_h = section_title_h + table_header_h + max(1, len(report.month_rows)) * row_h
    height = (
        margin * 2
        + header_h
        + card_gap
        + summary_h
        + card_gap
        + summary_h
        + card_gap
        + yesterday_table_h
        + card_gap
        + month_table_h
        + 26
    )

    fonts = _load_fonts(font_path, scale=scale)
    s = lambda value: int(round(value * scale))
    box = lambda values: tuple(s(value) for value in values)

    brand_blue = "#1437f5"
    deep_blue = "#0826b8"
    brand_yellow = "#ffc400"
    image = Image.new("RGB", (s(width), s(height)), brand_blue)
    draw = ImageDraw.Draw(image)

    logo = _prepare_logo(logo_path, max_width=s(210), max_height=s(150), brand_blue=brand_blue)
    if logo:
        image.paste(logo, (s(width - margin - 230), s(margin + 12)), logo if logo.mode == "RGBA" else None)

    title_x = margin + 16
    title = "彭世修脚团购日报"
    subtitle = (
        f"{_platform_name(report.platform_code)} | {report.report_date:%Y-%m-%d} | "
        f"本月累计 {report.month_start:%m.%d}-{report.report_date:%m.%d}"
    )
    draw.text((s(title_x), s(margin + 32)), title, font=fonts["title"], fill="#ffffff")
    draw.rounded_rectangle(box((title_x, margin + 96, title_x + 86, margin + 105)), radius=s(5), fill=brand_yellow)
    draw.text((s(title_x), s(margin + 122)), subtitle, font=fonts["body"], fill="#f7fbff")

    y = margin + header_h + card_gap
    _draw_summary_card(
        image,
        draw,
        box,
        s,
        fonts,
        (margin, y, width - margin, y + summary_h),
        "昨日汇总数据",
        _summary_items(report.yesterday_rows),
        brand_yellow,
    )
    y += summary_h + card_gap
    _draw_summary_card(
        image,
        draw,
        box,
        s,
        fonts,
        (margin, y, width - margin, y + summary_h),
        "本月累计汇总数据",
        _summary_items(report.month_rows),
        brand_yellow,
    )
    y += summary_h + card_gap
    _draw_region_table(
        draw,
        box,
        s,
        fonts,
        (margin, y, width - margin, y + yesterday_table_h),
        "昨日大区数据",
        report.yesterday_rows,
        deep_blue,
        section_title_h,
        table_header_h,
        row_h,
    )
    y += yesterday_table_h + card_gap
    _draw_region_table(
        draw,
        box,
        s,
        fonts,
        (margin, y, width - margin, y + month_table_h),
        "本月累计大区数据",
        report.month_rows,
        deep_blue,
        section_title_h,
        table_header_h,
        row_h,
    )

    image = image.resize((width, height), Image.Resampling.LANCZOS)
    image.save(output_path, "PNG", optimize=True)
    return output_path


def send_wecom_image(webhook_url: str, image_path: Path) -> dict:
    content = image_path.read_bytes()
    payload = {
        "msgtype": "image",
        "image": {
            "base64": base64.b64encode(content).decode("ascii"),
            "md5": hashlib.md5(content).hexdigest(),
        },
    }
    response = httpx.post(webhook_url, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def _decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value: Decimal) -> str:
    return f"{float(value):,.2f}"


def _number(value: Decimal) -> str:
    return f"{int(value):,}"


def _rate(numerator: Decimal, denominator: Decimal) -> str:
    if not denominator:
        return "0.00%"
    return f"{float(numerator / denominator * 100):.2f}%"


def _total_paid_amount(rows: list[RegionSummary]) -> Decimal:
    return sum((row.paid_amount for row in rows), Decimal("0"))


def _total_verified_amount(rows: list[RegionSummary]) -> Decimal:
    return sum((row.verified_amount for row in rows), Decimal("0"))


def _total_verified_count(rows: list[RegionSummary]) -> Decimal:
    return sum((row.verified_count for row in rows), Decimal("0"))


def _total_verified_new_customer_count(rows: list[RegionSummary]) -> Decimal:
    return sum((row.verified_new_customer_count for row in rows), Decimal("0"))


def _total_positive_review_count(rows: list[RegionSummary]) -> Decimal:
    return sum((row.positive_review_count for row in rows), Decimal("0"))


def _summary_items(rows: list[RegionSummary]) -> list[tuple[str, str]]:
    return [
        ("下单金额", _money(_total_paid_amount(rows))),
        ("核销金额", _money(_total_verified_amount(rows))),
        ("核销订单数", _number(_total_verified_count(rows))),
        ("好评数", _number(_total_positive_review_count(rows))),
    ]


def _platform_name(platform_code: str) -> str:
    return {"meituan": "美团", "douyin": "抖音"}.get(platform_code, platform_code)


def _prepare_logo(logo_path: str | None, *, max_width: int, max_height: int, brand_blue: str):
    if not logo_path or not Path(logo_path).exists():
        return None
    image = Image.open(logo_path).convert("RGBA")
    alpha_box = image.getbbox()
    if alpha_box:
        image = image.crop(alpha_box)

    bg = _hex_to_rgb(brand_blue)
    pixels = image.load()
    min_x, min_y = image.width, image.height
    max_x, max_y = 0, 0
    for y in range(0, image.height, 4):
        for x in range(0, image.width, 4):
            r, g, b, a = pixels[x, y]
            if a > 0 and abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > 60:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x <= min_x or max_y <= min_y:
        cropped = image
    else:
        pad = 24
        cropped = image.crop(
            (
                max(0, min_x - pad),
                max(0, min_y - pad),
                min(image.width, max_x + pad),
                min(image.height, max_y + pad),
            )
        )

    scale = min(max_width / cropped.width, max_height / cropped.height)
    size = (max(1, int(cropped.width * scale)), max(1, int(cropped.height * scale)))
    return cropped.resize(size, Image.Resampling.LANCZOS)


def _draw_top_accent_card(
    image: Image.Image,
    rect: tuple[int, int, int, int],
    *,
    radius: int,
    accent_h: int,
    accent_fill: str,
    body_fill: str,
) -> None:
    x1, y1, x2, y2 = rect
    width = x2 - x1
    height = y2 - y1
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    layer_draw = ImageDraw.Draw(layer)
    layer_draw.rectangle((0, 0, width, height), fill=accent_fill)
    layer_draw.rectangle((0, accent_h, width, height), fill=body_fill)

    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
    layer.putalpha(mask)
    image.paste(layer, (x1, y1), layer)


def _draw_summary_card(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    box,
    s,
    fonts,
    rect: tuple[int, int, int, int],
    title: str,
    items: list[tuple[str, str]],
    brand_yellow: str,
) -> None:
    x1, y1, x2, y2 = rect
    _draw_top_accent_card(
        image,
        box(rect),
        radius=s(22),
        accent_h=s(14),
        accent_fill=brand_yellow,
        body_fill="#ffffff",
    )
    draw.text((s(x1 + 24), s(y1 + 28)), title, font=fonts["small"], fill="#0826b8")
    item_w = (x2 - x1 - 48) / len(items)
    for index, (label, value) in enumerate(items):
        x = x1 + 24 + index * item_w
        if index:
            draw.line(box((x - 16, y1 + 48, x - 16, y2 - 28)), fill="#e6ecfa", width=s(2))
        draw.text((s(x), s(y1 + 58)), label, font=fonts["small"], fill="#52617d")
        draw.text((s(x), s(y1 + 91)), value, font=fonts["metric"], fill="#07142f")


def _draw_region_table(
    draw: ImageDraw.ImageDraw,
    box,
    s,
    fonts,
    rect: tuple[int, int, int, int],
    title: str,
    rows: list[RegionSummary],
    deep_blue: str,
    section_title_h: int,
    table_header_h: int,
    row_h: int,
) -> None:
    x1, y1, x2, y2 = rect
    draw.rounded_rectangle(box(rect), radius=s(22), fill="#ffffff")
    draw.text((s(x1 + 24), s(y1 + 12)), title, font=fonts["section"], fill="#07142f")

    columns = [
        ("大区", 120),
        ("负责人", 180),
        ("下单金额", 145),
        ("核销金额", 145),
        ("核销订单数", 135),
        ("核销新客数", 135),
        ("好评数", 115),
        ("好评率", 115),
    ]
    header_y = y1 + section_title_h
    draw.rounded_rectangle(box((x1 + 14, header_y + 8, x2 - 14, header_y + table_header_h - 8)), radius=s(14), fill="#eef4ff")
    x = x1 + 24
    for label, col_w in columns:
        draw.text((s(x), s(header_y + 18)), label, font=fonts["table_header"], fill=deep_blue)
        x += col_w

    if not rows:
        draw.text((s(x1 + 24), s(header_y + table_header_h + 24)), "暂无数据", font=fonts["body"], fill="#53607a")
        return

    for row_index, row in enumerate(rows):
        row_y = header_y + table_header_h + row_index * row_h
        if row_index % 2 == 1:
            draw.rounded_rectangle(box((x1 + 14, row_y + 4, x2 - 14, row_y + row_h - 4)), radius=s(10), fill="#f4f7ff")
        values = [
            row.region,
            row.owner,
            _money(row.paid_amount),
            _money(row.verified_amount),
            _number(row.verified_count),
            _number(row.verified_new_customer_count),
            _number(row.positive_review_count),
            _rate(row.positive_review_count, row.verified_count),
        ]
        x = x1 + 24
        for (label, col_w), value in zip(columns, values):
            draw.text(
                (s(x), s(row_y + 11)),
                _fit_text(draw, value, fonts["table"], s(col_w - 12)),
                font=fonts["table"],
                fill="#101828",
            )
            x += col_w


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _load_fonts(font_path: str | None, *, scale: int = 1) -> dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    regular_candidates = [
        font_path,
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    bold_candidates = [
        font_path,
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    regular = next((path for path in regular_candidates if path and Path(path).exists()), None)
    bold = next((path for path in bold_candidates if path and Path(path).exists()), regular)

    def font(size: int, *, use_bold: bool = False):
        target = bold if use_bold else regular
        if target:
            return ImageFont.truetype(target, size=size * scale)
        return ImageFont.load_default()

    return {
        "title": font(42, use_bold=True),
        "metric": font(31, use_bold=True),
        "body": font(22, use_bold=True),
        "small": font(18, use_bold=True),
        "section": font(21, use_bold=True),
        "table_header": font(20, use_bold=True),
        "table": font(19, use_bold=True),
    }


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    result = text
    while result and draw.textlength(result + "...", font=font) > max_width:
        result = result[:-1]
    return result + "..." if result else "..."
