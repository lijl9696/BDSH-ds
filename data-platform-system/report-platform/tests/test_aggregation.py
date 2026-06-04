from decimal import Decimal

import pytest

from app.aggregation import safe_ratio, validate_aggregation


def test_validate_aggregation_accepts_supported_names():
    assert validate_aggregation("sum") == "sum"
    assert validate_aggregation("formula") == "formula"


def test_validate_aggregation_rejects_unknown_name():
    with pytest.raises(ValueError):
        validate_aggregation("median-ish")


def test_safe_ratio_handles_zero_and_none():
    assert safe_ratio(10, 2) == Decimal("5")
    assert safe_ratio(10, 0) is None
    assert safe_ratio(None, 10) is None
