from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..aggregation import safe_ratio
from ..models import DerivedMetricRule, Metric, MetricValue, Store
from ..schemas import SummaryRequest, SummaryResponse, SummaryRow


router = APIRouter()


TIME_GRAINS = {
    "day": "day",
    "week": "week",
    "month": "month",
    "quarter": "quarter",
    "year": "year",
}


GROUP_COLUMNS = {
    "platform": MetricValue.platform_code,
    "store": MetricValue.store_code,
    "province": Store.province,
    "region": Store.region,
    "city": Store.city,
    "owner": Store.owner,
}


@router.post("", response_model=SummaryResponse)
def summarize(payload: SummaryRequest, db: Session = Depends(get_db)) -> SummaryResponse:
    metric_rows = db.query(Metric).filter(Metric.code.in_(payload.metrics), Metric.enabled.is_(True)).all()
    metric_by_code = {metric.code: metric for metric in metric_rows}
    formula_codes = [metric.code for metric in metric_rows if metric.aggregation == "formula"]
    rules = {}
    if formula_codes:
        rule_rows = db.query(DerivedMetricRule).filter(DerivedMetricRule.metric_code.in_(formula_codes), DerivedMetricRule.enabled.is_(True)).all()
        rules = {rule.metric_code: rule for rule in rule_rows}

    warnings: list[str] = []
    missing = sorted(set(payload.metrics) - set(metric_by_code))
    if missing:
        warnings.append(f"以下指标不存在或已停用：{', '.join(missing)}")

    dimensions = []
    labels = []
    if "time" in payload.group_by:
        time_col = func.date_trunc(TIME_GRAINS[payload.time_grain], MetricValue.metric_date).label("time")
        dimensions.append(time_col)
        labels.append("time")
    for group in payload.group_by:
        if group == "time":
            continue
        dimensions.append(GROUP_COLUMNS[group].label(group))
        labels.append(group)

    value_columns = []
    formula_dependency_labels: dict[str, tuple[str, str]] = {}
    for code, metric in metric_by_code.items():
        if metric.aggregation == "formula":
            rule = rules.get(code)
            if not rule or not rule.numerator_metric or not rule.denominator_metric:
                warnings.append(f"{metric.name} 缺少可执行公式规则，已跳过计算。")
                continue
            numerator_label = f"__{code}_num"
            denominator_label = f"__{code}_den"
            numerator_value = case((MetricValue.metric_code == rule.numerator_metric, MetricValue.value), else_=None)
            denominator_value = case((MetricValue.metric_code == rule.denominator_metric, MetricValue.value), else_=None)
            value_columns.append(func.sum(numerator_value).label(numerator_label))
            value_columns.append(func.sum(denominator_value).label(denominator_label))
            formula_dependency_labels[code] = (numerator_label, denominator_label)
            continue
        value = case((MetricValue.metric_code == code, MetricValue.value), else_=None)
        if metric.aggregation == "sum":
            expr = func.sum(value)
        elif metric.aggregation == "avg":
            expr = func.avg(value)
        elif metric.aggregation == "max":
            expr = func.max(value)
        elif metric.aggregation == "min":
            expr = func.min(value)
        elif metric.aggregation == "count":
            expr = func.count(value)
        elif metric.aggregation in {"latest", "first", "weighted_avg", "count_distinct", "ratio"}:
            warnings.append(f"{metric.name} 使用 {metric.aggregation} 汇总，第一版接口暂按求和返回，后续接入公式/加权逻辑。")
            expr = func.sum(value)
        else:
            warnings.append(f"{metric.name} 的汇总方式未知，第一版接口暂按求和返回。")
            expr = func.sum(value)
        value_columns.append(expr.label(code))

    stmt: Select = select(*dimensions, *value_columns).select_from(MetricValue).join(Store, Store.store_code == MetricValue.store_code, isouter=True)
    stmt = stmt.where(MetricValue.metric_date >= payload.start_date, MetricValue.metric_date <= payload.end_date)
    if not payload.include_inactive_versions:
        stmt = stmt.where(MetricValue.is_active.is_(True))
    if payload.platforms:
        stmt = stmt.where(MetricValue.platform_code.in_(payload.platforms))
    if payload.stores:
        stmt = stmt.where(MetricValue.store_code.in_(payload.stores))
    if payload.provinces:
        stmt = stmt.where(Store.province.in_(payload.provinces))
    if payload.regions:
        stmt = stmt.where(Store.region.in_(payload.regions))
    if payload.owners:
        stmt = stmt.where(Store.owner.in_(payload.owners))
    query_metric_codes = set(metric_by_code)
    for rule in rules.values():
        if rule.numerator_metric:
            query_metric_codes.add(rule.numerator_metric)
        if rule.denominator_metric:
            query_metric_codes.add(rule.denominator_metric)
    if query_metric_codes:
        stmt = stmt.where(MetricValue.metric_code.in_(query_metric_codes))
    if dimensions:
        stmt = stmt.group_by(*dimensions).order_by(*dimensions)

    rows = []
    for db_row in db.execute(stmt).mappings():
        row_dimensions = {label: db_row[label] for label in labels}
        values = {}
        for code, metric in metric_by_code.items():
            if metric.aggregation == "formula":
                labels_for_formula = formula_dependency_labels.get(code)
                if not labels_for_formula:
                    values[code] = None
                    continue
                numerator_label, denominator_label = labels_for_formula
                values[code] = safe_ratio(db_row[numerator_label], db_row[denominator_label])
            else:
                values[code] = db_row[code]
        rows.append(SummaryRow(dimensions=row_dimensions, values=values))
    return SummaryResponse(rows=rows, warnings=warnings)
