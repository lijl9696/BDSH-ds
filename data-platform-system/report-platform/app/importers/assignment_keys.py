from __future__ import annotations

import re
from typing import Any

import pandas as pd


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def normalize_location(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    text = re.sub(r"\s+", "", text)
    for suffix in ("维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "特别行政区", "省", "市"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def normalize_store_name(value: Any) -> str:
    text = clean_text(value)
    if not text:
        return ""
    text = text.replace("(", "（").replace(")", "）")
    return re.sub(r"\s+", "", text)


def assignment_key(province: Any, city: Any, store_name: Any = "") -> tuple[str, str, str]:
    return (normalize_location(province), normalize_location(city), normalize_store_name(store_name))
