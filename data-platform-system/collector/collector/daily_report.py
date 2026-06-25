from __future__ import annotations

import base64
import hashlib
import html
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import psycopg
from PIL import Image
from playwright.sync_api import sync_playwright

from .config import Settings


METRIC_CODES = (
    "paid_amount",
    "verified_amount",
    "verified_count",
    "verified_coupon_count",
    "verified_new_customer_count",
    "new_positive_review_count",
)

REPORT_REGIONS = ("郑北", "郑中", "郑东")
REPORT_PLATFORMS = ("meituan", "douyin")


@dataclass(frozen=True)
class RegionSummary:
    region: str
    owner: str
    platform_code: str
    platform_name: str
    row_type: str
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


def fetch_daily_region_report(settings: Settings, report_date: date, platform_code: str = "all") -> DailyReportData:
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
    platform_codes = list(REPORT_PLATFORMS) if platform_code in {"all", "combined", "multi"} else [platform_code]
    query = """
    SELECT
      COALESCE(stores.region, '未配置') AS region,
      metric_values.platform_code AS platform_code,
      COALESCE(NULLIF(string_agg(DISTINCT NULLIF(stores.owner, ''), '、'), ''), '未配置') AS owner,
      SUM(CASE WHEN metric_values.metric_code = 'paid_amount' THEN metric_values.value ELSE 0 END) AS paid_amount,
      SUM(CASE WHEN metric_values.metric_code = 'verified_amount' THEN metric_values.value ELSE 0 END) AS verified_amount,
      -- Douyin exports coupon verification count but no order-count field.
      SUM(
        CASE
          WHEN metric_values.platform_code = 'douyin' AND metric_values.metric_code = 'verified_coupon_count'
            THEN metric_values.value
          WHEN metric_values.platform_code <> 'douyin' AND metric_values.metric_code = 'verified_count'
            THEN metric_values.value
          ELSE 0
        END
      ) AS verified_count,
      SUM(CASE WHEN metric_values.metric_code = 'verified_new_customer_count' THEN metric_values.value ELSE 0 END) AS verified_new_customer_count,
      SUM(CASE WHEN metric_values.metric_code = 'new_positive_review_count' THEN metric_values.value ELSE 0 END) AS positive_review_count
    FROM metric_values
    LEFT JOIN stores ON stores.store_code = metric_values.store_code
    WHERE metric_values.is_active = TRUE
      AND metric_values.platform_code = ANY(%s)
      AND metric_values.metric_date BETWEEN %s AND %s
      AND metric_values.metric_code = ANY(%s)
      AND COALESCE(stores.region, '未配置') = ANY(%s)
    GROUP BY COALESCE(stores.region, '未配置'), metric_values.platform_code
    ORDER BY region ASC, platform_code ASC;
    """
    with connection.cursor() as cursor:
        cursor.execute(query, (platform_codes, start_date, end_date, list(METRIC_CODES), list(REPORT_REGIONS)))
        detail_rows = [
            RegionSummary(
                region=str(row[0]),
                platform_code=str(row[1]),
                platform_name=_platform_name(str(row[1])),
                owner=str(row[2]),
                row_type="detail",
                paid_amount=_decimal(row[3]),
                verified_amount=_decimal(row[4]),
                verified_count=_decimal(row[5]),
                verified_new_customer_count=_decimal(row[6]),
                positive_review_count=_decimal(row[7]),
            )
            for row in cursor.fetchall()
        ]
    return _compose_report_rows(detail_rows, platform_codes)


def render_daily_region_report(
    report: DailyReportData,
    output_path: Path,
    font_path: str | None = None,
    logo_path: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_path = output_path.with_suffix(".html")
    html_path.write_text(_render_daily_region_report_html(report, font_path, logo_path), encoding="utf-8")
    _screenshot_html(html_path, output_path)
    return output_path


def _render_daily_region_report_html(
    report: DailyReportData,
    font_path: str | None = None,
    logo_path: str | None = None,
) -> str:
    yesterday_by_key = _rows_by_key(report.yesterday_rows)
    month_by_key = _rows_by_key(report.month_rows)
    ordered_keys = _ordered_row_keys(report.yesterday_rows, report.month_rows)
    overview = _grand_row(report.yesterday_rows)
    month_overview = _grand_row(report.month_rows)
    logo_html = _logo_html(logo_path)
    font_face = _font_face_css(font_path)
    platform_label = "美团 / 抖音" if report.platform_code in {"all", "combined", "multi"} else _platform_name(report.platform_code)
    subtitle = (
        f"{platform_label} | {report.report_date:%Y-%m-%d} | "
        f"本月累计 {report.month_start:%m.%d}-{report.report_date:%m.%d}"
    )

    overview_cards = [
        ("昨日", "核销金额", _money(overview.verified_amount)),
        ("昨日", "核销订单", _number(overview.verified_count)),
        ("昨日", "核销新客", _number(overview.verified_new_customer_count)),
        ("昨日", "好评率", _rate(overview.positive_review_count, overview.verified_count)),
        ("本月", "核销金额", _money(month_overview.verified_amount)),
        ("本月", "核销订单", _number(month_overview.verified_count)),
        ("本月", "核销新客", _number(month_overview.verified_new_customer_count)),
        ("本月", "好评率", _rate(month_overview.positive_review_count, month_overview.verified_count)),
    ]

    cards_html = "\n".join(
        f"""
          <div class="kpi-card{' month' if period == '本月' else ''}">
            <div class="kpi-period">{period}</div>
            <div class="kpi-label">{html.escape(label)}</div>
            <div class="kpi-value">{html.escape(value)}</div>
          </div>
        """
        for period, label, value in overview_cards
    )

    body_html = "\n".join(
        _render_report_row(
            yesterday_by_key.get(key) or _empty_row(*key),
            month_by_key.get(key) or _empty_row(*key),
        )
        for key in ordered_keys
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>彭世修脚团购日报</title>
  <style>
    {font_face}
    :root {{
      --brand-blue: #1739f2;
      --brand-blue-dark: #0b1f9a;
      --brand-yellow: #ffd200;
      --ink: #10182f;
      --muted: #64708a;
      --line: #e8edf7;
      --soft-blue: #eef3ff;
      --soft-yellow: #fff7d9;
      --white: #ffffff;
      --shadow: 0 16px 34px rgba(4, 18, 122, 0.18);
      --radius-xl: 24px;
      --radius-lg: 18px;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      width: 1680px;
      font-family: ReportFont, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      color: var(--ink);
      background: var(--brand-blue);
      line-height: 1.45;
    }}
    .page {{
      width: 1680px;
      padding: 50px 58px 58px;
      background:
        radial-gradient(circle at 85% 8%, rgba(255,255,255,.16), transparent 22%),
        linear-gradient(180deg, #183cf5 0%, #1739f2 46%, #112bd5 100%);
    }}
    .hero {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 32px;
      color: #fff;
      margin-bottom: 24px;
    }}
    .title-wrap h1 {{
      font-size: 54px;
      font-weight: 650;
      letter-spacing: .04em;
      margin: 0;
    }}
    .title-line {{
      width: 104px;
      height: 10px;
      background: var(--brand-yellow);
      border-radius: 999px;
      margin: 22px 0 16px;
    }}
    .subtitle {{
      font-size: 22px;
      opacity: .94;
      letter-spacing: .02em;
      font-weight: 650;
    }}
    .brand-logo {{
      width: 220px;
      min-width: 220px;
      display: flex;
      justify-content: flex-end;
      align-items: center;
    }}
    .brand-logo img {{
      width: 190px;
      height: auto;
      display: block;
      filter: drop-shadow(0 10px 18px rgba(0,0,0,.12));
    }}
    .brand-text {{
      text-align: right;
      min-width: 210px;
      font-weight: 800;
      letter-spacing: .05em;
    }}
    .brand-text-main {{ font-size: 30px; }}
    .brand-text-sub {{ font-size: 13px; opacity: .9; margin-top: 4px; }}
    .section {{
      background: var(--white);
      border-radius: var(--radius-xl);
      margin-bottom: 20px;
      box-shadow: var(--shadow);
      overflow: hidden;
      position: relative;
    }}
    .section::before {{
      content: "";
      position: absolute;
      left: 0;
      top: 0;
      width: 100%;
      height: 14px;
      background: var(--brand-yellow);
    }}
    .section-inner {{ padding: 30px 28px 26px; }}
    .section-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 16px;
    }}
    .section-title {{
      font-size: 24px;
      font-weight: 800;
      color: var(--ink);
      margin: 0;
    }}
    .overview-grid {{
      display: grid;
      grid-template-columns: repeat(8, 1fr);
      border: 1px solid var(--line);
      border-radius: var(--radius-lg);
      overflow: hidden;
    }}
    .kpi-card {{
      min-height: 104px;
      padding: 16px 15px;
      border-right: 1px solid var(--line);
      background: #fff;
    }}
    .kpi-card:nth-child(8) {{ border-right: none; }}
    .kpi-card.month {{ background: #fbfcff; }}
    .kpi-period {{
      display: inline-flex;
      align-items: center;
      height: 24px;
      padding: 0 9px;
      border-radius: 999px;
      font-size: 12px;
      color: var(--brand-blue);
      background: var(--soft-blue);
      margin-bottom: 8px;
      font-weight: 800;
    }}
    .kpi-label {{
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 5px;
      font-weight: 650;
    }}
    .kpi-value {{
      font-size: 30px;
      font-weight: 800;
      letter-spacing: -.02em;
      white-space: nowrap;
    }}
    .table-toolbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 14px;
    }}
    .legend {{
      display: flex;
      gap: 10px;
      align-items: center;
      color: var(--muted);
      font-size: 14px;
      font-weight: 650;
    }}
    .legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      display: inline-block;
    }}
    .dot.total {{ background: var(--brand-blue); }}
    .dot.meituan {{ background: #00b978; }}
    .dot.douyin {{ background: #111827; }}
    .table-wrap {{
      width: 100%;
      overflow: visible;
      border: 1px solid var(--line);
      border-radius: var(--radius-lg);
      background: #fff;
    }}
    table.report-table {{
      width: 100%;
      min-width: 0;
      table-layout: fixed;
      border-collapse: separate;
      border-spacing: 0;
      font-size: 14px;
    }}
    .report-table th,
    .report-table td {{
      padding: 11px 8px;
      border-bottom: 1px solid var(--line);
      text-align: right;
      white-space: nowrap;
      font-weight: 650;
    }}
    .report-table thead th {{
      color: var(--brand-blue-dark);
      font-weight: 850;
      background: var(--soft-blue);
      text-align: center;
    }}
    .report-table thead tr:first-child th {{
      font-size: 16px;
      border-bottom: 2px solid #dce6ff;
      text-align: center;
    }}
    .report-table thead th:first-child,
    .report-table thead th:nth-child(2),
    .report-table tbody td:first-child,
    .report-table tbody td:nth-child(2) {{
      text-align: left;
    }}
    .report-table tbody tr:last-child td {{ border-bottom: none; }}
    .report-table tbody tr:nth-child(even):not(.group-row):not(.grand-row) td {{ background: #fbfcff; }}
    .grand-row td {{
      background: #e9f0ff;
      font-weight: 900;
      color: var(--brand-blue-dark);
    }}
    .group-row td {{
      background: var(--soft-yellow);
      font-weight: 900;
      color: #473b00;
    }}
    .platform-badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 48px;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 850;
    }}
    .badge-total {{ color: #fff; background: var(--brand-blue); }}
    .badge-meituan {{ color: #037750; background: #dff8ee; }}
    .badge-douyin {{ color: #111827; background: #edf0f6; }}
    .split-left {{ border-left: 2px solid #d7e1fb; }}
  </style>
</head>
<body>
  <main class="page">
    <header class="hero">
      <div class="title-wrap">
        <h1>彭世修脚团购日报</h1>
        <div class="title-line"></div>
        <div class="subtitle">{html.escape(subtitle)}</div>
      </div>
      {logo_html}
    </header>

    <section class="section">
      <div class="section-inner">
        <div class="section-header">
          <h2 class="section-title">全平台总览</h2>
        </div>
        <div class="overview-grid">
          {cards_html}
        </div>
      </div>
    </section>

    <section class="section">
      <div class="section-inner">
        <div class="table-toolbar">
          <h2 class="section-title">大区 × 平台经营矩阵</h2>
          <div class="legend" aria-label="图例">
            <span class="legend-item"><span class="dot total"></span>合计</span>
            <span class="legend-item"><span class="dot meituan"></span>美团</span>
            <span class="legend-item"><span class="dot douyin"></span>抖音</span>
          </div>
        </div>

        <div class="table-wrap">
          <table class="report-table">
            <thead>
              <tr>
                <th rowspan="2">大区 / 负责人</th>
                <th rowspan="2">平台</th>
                <th colspan="6">昨日数据</th>
                <th colspan="6" class="split-left">本月累计数据</th>
              </tr>
              <tr>
                <th>下单金额</th>
                <th>核销金额</th>
                <th>核销订单数</th>
                <th>核销新客数</th>
                <th>好评数</th>
                <th>好评率</th>
                <th class="split-left">下单金额</th>
                <th>核销金额</th>
                <th>核销订单数</th>
                <th>核销新客数</th>
                <th>好评数</th>
                <th>好评率</th>
              </tr>
            </thead>
            <tbody>
              {body_html}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </main>
</body>
</html>
"""


def _screenshot_html(html_path: Path, output_path: Path) -> None:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1680, "height": 900}, device_scale_factor=1)
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.locator(".page").screenshot(path=str(output_path))
        browser.close()


def _compose_report_rows(detail_rows: list[RegionSummary], platform_codes: list[str]) -> list[RegionSummary]:
    rows: list[RegionSummary] = []
    rows.append(_sum_rows("全区汇总", "", "total", "合计", "grand", detail_rows))
    for platform_code in platform_codes:
        rows.append(
            _sum_rows(
                "全区汇总",
                "",
                platform_code,
                _platform_name(platform_code),
                "detail",
                [row for row in detail_rows if row.platform_code == platform_code],
            )
        )

    for region in REPORT_REGIONS:
        region_rows = [row for row in detail_rows if row.region == region]
        owner = _join_owners(region_rows)
        rows.append(_sum_rows(region, owner, "total", "合计", "group", region_rows))
        for platform_code in platform_codes:
            platform_rows = [row for row in region_rows if row.platform_code == platform_code]
            if platform_rows:
                rows.append(platform_rows[0])
            else:
                rows.append(_empty_row(region, platform_code, owner=owner))
    return rows


def _sum_rows(
    region: str,
    owner: str,
    platform_code: str,
    platform_name: str,
    row_type: str,
    rows: list[RegionSummary],
) -> RegionSummary:
    return RegionSummary(
        region=region,
        owner=owner,
        platform_code=platform_code,
        platform_name=platform_name,
        row_type=row_type,
        paid_amount=_total_paid_amount(rows),
        verified_amount=_total_verified_amount(rows),
        verified_count=_total_verified_count(rows),
        verified_new_customer_count=_total_verified_new_customer_count(rows),
        positive_review_count=_total_positive_review_count(rows),
    )


def _join_owners(rows: list[RegionSummary]) -> str:
    owners = sorted({row.owner for row in rows if row.owner and row.owner != "未配置"})
    return "、".join(owners) if owners else "未配置"


def _rows_by_key(rows: list[RegionSummary]) -> dict[tuple[str, str], RegionSummary]:
    return {(row.region, row.platform_code): row for row in rows}


def _ordered_row_keys(*row_groups: list[RegionSummary]) -> list[tuple[str, str]]:
    keys = [("全区汇总", "total")]
    for platform_code in REPORT_PLATFORMS:
        keys.append(("全区汇总", platform_code))
    for region in REPORT_REGIONS:
        keys.append((region, "total"))
        for platform_code in REPORT_PLATFORMS:
            keys.append((region, platform_code))

    available = {key for rows in row_groups for key in _rows_by_key(rows)}
    extras = sorted(available - set(keys))
    return keys + extras


def _grand_row(rows: list[RegionSummary]) -> RegionSummary:
    for row in rows:
        if row.region == "全区汇总" and row.platform_code == "total":
            return row
    return _sum_rows("全区汇总", "", "total", "合计", "grand", rows)


def _empty_row(region: str, platform_code: str, owner: str = "未配置") -> RegionSummary:
    return RegionSummary(
        region=region,
        owner="" if region == "全区汇总" else owner,
        platform_code=platform_code,
        platform_name="合计" if platform_code == "total" else _platform_name(platform_code),
        row_type="grand" if region == "全区汇总" and platform_code == "total" else "detail",
        paid_amount=Decimal("0"),
        verified_amount=Decimal("0"),
        verified_count=Decimal("0"),
        verified_new_customer_count=Decimal("0"),
        positive_review_count=Decimal("0"),
    )


def _render_report_row(yesterday: RegionSummary, month: RegionSummary) -> str:
    row_type = yesterday.row_type if yesterday.row_type != "detail" else month.row_type
    row_class = {"grand": "grand-row", "group": "group-row"}.get(row_type, "")
    area = yesterday.region
    owner = yesterday.owner or month.owner
    area_text = area if not owner or area == "全区汇总" else f"{area} / {owner}"
    platform = yesterday.platform_name
    badge_class = _platform_badge_class(yesterday.platform_code)
    return f"""
      <tr class="{row_class}">
        <td>{html.escape(area_text)}</td>
        <td><span class="platform-badge {badge_class}">{html.escape(platform)}</span></td>
        <td>{html.escape(_money(yesterday.paid_amount))}</td>
        <td>{html.escape(_money(yesterday.verified_amount))}</td>
        <td>{html.escape(_number(yesterday.verified_count))}</td>
        <td>{html.escape(_number(yesterday.verified_new_customer_count))}</td>
        <td>{html.escape(_number(yesterday.positive_review_count))}</td>
        <td>{html.escape(_rate(yesterday.positive_review_count, yesterday.verified_count))}</td>
        <td class="split-left">{html.escape(_money(month.paid_amount))}</td>
        <td>{html.escape(_money(month.verified_amount))}</td>
        <td>{html.escape(_number(month.verified_count))}</td>
        <td>{html.escape(_number(month.verified_new_customer_count))}</td>
        <td>{html.escape(_number(month.positive_review_count))}</td>
        <td>{html.escape(_rate(month.positive_review_count, month.verified_count))}</td>
      </tr>
    """


def _platform_badge_class(platform_code: str) -> str:
    if platform_code == "total":
        return "badge-total"
    if platform_code == "meituan":
        return "badge-meituan"
    if platform_code == "douyin":
        return "badge-douyin"
    return "badge-total"


def _logo_html(logo_path: str | None) -> str:
    logo_data = _prepare_logo_data_uri(logo_path)
    if logo_data:
        return f'<div class="brand-logo"><img src="{logo_data}" alt="彭世修脚" /></div>'
    return """
      <div class="brand-text">
        <div class="brand-text-main">彭世修脚</div>
        <div class="brand-text-sub">全国连锁 · 非物质文化遗产</div>
      </div>
    """


def _prepare_logo_data_uri(logo_path: str | None) -> str | None:
    if not logo_path or not Path(logo_path).exists():
        return None
    with Image.open(logo_path).convert("RGBA") as image:
        bbox = image.getchannel("A").getbbox()
        if bbox:
            image = image.crop(bbox)
        with TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "logo.png"
            image.save(target)
            encoded = base64.b64encode(target.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _font_face_css(font_path: str | None) -> str:
    if not font_path or not Path(font_path).exists():
        return ""
    encoded = base64.b64encode(Path(font_path).read_bytes()).decode("ascii")
    suffix = Path(font_path).suffix.lower().lstrip(".") or "ttf"
    return f"""
      @font-face {{
        font-family: ReportFont;
        src: url(data:font/{suffix};base64,{encoded});
        font-weight: 400 900;
      }}
    """


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


def _platform_name(platform_code: str) -> str:
    return {"all": "美团 / 抖音", "combined": "美团 / 抖音", "multi": "美团 / 抖音", "meituan": "美团", "douyin": "抖音"}.get(platform_code, platform_code)
