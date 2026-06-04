from __future__ import annotations

from decimal import Decimal


SUPPORTED_AGGREGATIONS = {
    "sum",
    "avg",
    "weighted_avg",
    "max",
    "min",
    "count",
    "count_distinct",
    "latest",
    "first",
    "ratio",
    "formula",
}


def validate_aggregation(name: str) -> str:
    if name not in SUPPORTED_AGGREGATIONS:
        supported = ", ".join(sorted(SUPPORTED_AGGREGATIONS))
        raise ValueError(f"Unsupported aggregation '{name}'. Supported: {supported}")
    return name


def safe_ratio(numerator: Decimal | int | float | None, denominator: Decimal | int | float | None) -> Decimal | None:
    if numerator is None or denominator in (None, 0):
        return None
    numerator_decimal = Decimal(str(numerator))
    denominator_decimal = Decimal(str(denominator))
    if denominator_decimal == 0:
        return None
    return numerator_decimal / denominator_decimal
