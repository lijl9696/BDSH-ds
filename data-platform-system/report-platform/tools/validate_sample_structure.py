from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile


NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


@dataclass
class Profile:
    code: str
    header_scan_rows: int
    date_fields: list[str]
    store_code_fields: list[str]
    store_name_fields: list[str]
    province_fields: list[str]
    city_fields: list[str]
    region_fields: list[str]
    ignored_fields: list[str]
    field_aliases: dict[str, str]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a platform XLSX sample against import profile and mappings.")
    parser.add_argument("--platform", required=True, choices=["meituan", "douyin"])
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--config-dir", type=Path, default=Path(__file__).resolve().parents[1] / "config")
    args = parser.parse_args()

    profile = load_profile(args.config_dir / "platform_profiles.yml", args.platform)
    mappings = load_mappings(args.config_dir / "default_field_mappings.yml", args.platform)
    sheets = read_xlsx(args.file)
    if not sheets:
        print("No sheets found.", file=sys.stderr)
        return 2

    print(f"Platform: {profile.code}")
    print(f"File: {args.file}")
    print(f"Sheets: {', '.join(sheet['name'] for sheet in sheets)}")

    failed = False
    for sheet in sheets:
        header = choose_header(sheet["rows"], profile)
        if not header:
            print(f"\n[{sheet['name']}] no usable header found")
            failed = True
            continue

        canonical = [profile.field_aliases.get(col, col) for col in header["columns"]]
        mapped = [col for col in canonical if col in mappings]
        ignored_dimensions = set(
            profile.date_fields
            + profile.store_code_fields
            + profile.store_name_fields
            + profile.province_fields
            + profile.city_fields
            + profile.region_fields
            + profile.ignored_fields
        )
        unmapped = [col for col in canonical if col and col not in mappings and col not in ignored_dimensions]
        data_rows = count_data_rows(sheet["rows"], header["row_index"])

        print(f"\n[{sheet['name']}]")
        print(f"Header row: {header['row_number']}")
        print(f"Data rows: {data_rows}")
        print(f"Date field: {first_existing(canonical, profile.date_fields) or '-'}")
        print(f"Store code field: {first_existing(canonical, profile.store_code_fields) or '-'}")
        print(f"Store name field: {first_existing(canonical, profile.store_name_fields) or '-'}")
        print(f"Province field: {first_existing(canonical, profile.province_fields) or '-'}")
        print(f"City field: {first_existing(canonical, profile.city_fields) or '-'}")
        print(f"Region field: {first_existing(canonical, profile.region_fields) or '-'}")
        print(f"Mapped metric fields ({len(mapped)}): {', '.join(mapped)}")
        print(f"Unmapped non-key fields ({len(unmapped)}): {', '.join(unmapped[:40])}")
        if len(unmapped) > 40:
            print("...")

        required_missing = [
            label
            for label, fields in [
                ("date", profile.date_fields),
                ("store_code", profile.store_code_fields),
                ("store_name", profile.store_name_fields),
            ]
            if not first_existing(canonical, fields)
        ]
        if required_missing:
            failed = True
            print(f"Missing required keys: {', '.join(required_missing)}")

    return 1 if failed else 0


def read_xlsx(path: Path) -> list[dict]:
    with ZipFile(path) as z:
        shared = []
        names = z.namelist()
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for item in root.findall("a:si", NS):
                shared.append("".join(t.text or "" for t in item.findall(".//a:t", NS)))

        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        sheets = []
        for sheet in workbook.findall(".//a:sheet", NS):
            name = sheet.attrib.get("name", "")
            rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
            target = relmap[rid]
            sheet_path = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
            root = ET.fromstring(z.read(sheet_path))
            rows = []
            for row in root.findall(".//a:sheetData/a:row", NS):
                rows.append(
                    {
                        "row_number": int(row.attrib.get("r", len(rows) + 1)),
                        "values": row_values(row, shared),
                    }
                )
            sheets.append({"name": name, "rows": rows})
        return sheets


def row_values(row: ET.Element, shared: list[str]) -> list[str]:
    values_by_col = {}
    max_col = 0
    for cell in row.findall("a:c", NS):
        ref = cell.attrib.get("r", "")
        col = column_number(ref)
        max_col = max(max_col, col)
        values_by_col[col] = cell_value(cell, shared)
    return [values_by_col.get(index, "") for index in range(1, max_col + 1)]


def cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//a:t", NS)).strip()
    value = cell.find("a:v", NS)
    if value is None:
        return ""
    if cell_type == "s":
        return shared[int(value.text or 0)].strip()
    return (value.text or "").strip()


def column_number(ref: str) -> int:
    letters = "".join(re.findall("[A-Z]+", ref))
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - 64
    return number


def choose_header(rows: list[dict], profile: Profile) -> dict | None:
    best = None
    best_score = -1
    for index, row in enumerate(rows[: max(profile.header_scan_rows, 1)]):
        columns = normalize_columns(row["values"], profile)
        score = header_score(columns, profile)
        if score > best_score:
            best = {"row_index": index, "row_number": row["row_number"], "columns": columns}
            best_score = score
    return best


def normalize_columns(columns: list[str], profile: Profile) -> list[str]:
    output = []
    seen = {}
    for column in columns:
        clean = str(column).strip()
        canonical = profile.field_aliases.get(clean, clean)
        if not canonical:
            output.append("")
            continue
        seen[canonical] = seen.get(canonical, 0) + 1
        output.append(canonical if seen[canonical] == 1 else f"{canonical}_{seen[canonical]}")
    return output


def header_score(columns: list[str], profile: Profile) -> int:
    values = set(columns)
    key_fields = profile.date_fields + profile.store_code_fields + profile.store_name_fields
    key_hits = sum(1 for field in key_fields if field in values)
    mapping_hits = sum(1 for field in values if field in profile.field_aliases.values())
    non_empty = sum(1 for field in values if field)
    return key_hits * 10 + mapping_hits + non_empty


def count_data_rows(rows: list[dict], header_index: int) -> int:
    return sum(1 for row in rows[header_index + 1 :] if any(value for value in row["values"]))


def first_existing(columns: list[str], candidates: list[str]) -> str | None:
    values = set(columns)
    for candidate in candidates:
        if candidate in values:
            return candidate
    return None


def load_profile(path: Path, platform: str) -> Profile:
    lines = path.read_text(encoding="utf-8").splitlines()
    block = extract_platform_block(lines, platform)
    return Profile(
        code=platform,
        header_scan_rows=int(read_scalar(block, "header_scan_rows", "1")),
        date_fields=read_list(block, "date_fields"),
        store_code_fields=read_list(block, "store_code_fields"),
        store_name_fields=read_list(block, "store_name_fields"),
        province_fields=read_list(block, "province_fields"),
        city_fields=read_list(block, "city_fields"),
        region_fields=read_list(block, "region_fields"),
        ignored_fields=read_list(block, "ignored_fields"),
        field_aliases=read_mapping(block, "field_aliases"),
    )


def load_mappings(path: Path, platform: str) -> dict[str, str]:
    mappings = {}
    current = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("- platform_code:"):
            if current.get("platform_code") == platform and "source_field" in current and "metric_code" in current:
                mappings[current["source_field"]] = current["metric_code"]
            current = {"platform_code": line.split(":", 1)[1].strip()}
        elif line.startswith("source_field:"):
            current["source_field"] = line.split(":", 1)[1].strip()
        elif line.startswith("metric_code:"):
            current["metric_code"] = line.split(":", 1)[1].strip()
    if current.get("platform_code") == platform and "source_field" in current and "metric_code" in current:
        mappings[current["source_field"]] = current["metric_code"]
    return mappings


def extract_platform_block(lines: list[str], platform: str) -> list[str]:
    start_marker = f"  {platform}:"
    start = None
    for index, line in enumerate(lines):
        if line == start_marker:
            start = index + 1
            break
    if start is None:
        raise ValueError(f"platform not found: {platform}")
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("  ") and not lines[index].startswith("    ") and lines[index].strip().endswith(":"):
            end = index
            break
    return lines[start:end]


def read_scalar(block: list[str], key: str, default: str) -> str:
    prefix = f"    {key}:"
    for line in block:
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return default


def read_list(block: list[str], key: str) -> list[str]:
    values = []
    start = find_section(block, key)
    if start is None:
        return values
    for line in block[start + 1 :]:
        if line.startswith("    ") and not line.startswith("      "):
            break
        stripped = line.strip()
        if stripped.startswith("- "):
            values.append(stripped[2:].strip())
    return values


def read_mapping(block: list[str], key: str) -> dict[str, str]:
    values = {}
    start = find_section(block, key)
    if start is None:
        return values
    for line in block[start + 1 :]:
        if line.startswith("    ") and not line.startswith("      "):
            break
        stripped = line.strip()
        if ":" in stripped:
            left, right = stripped.split(":", 1)
            values[left.strip()] = right.strip()
    return values


def find_section(block: list[str], key: str) -> int | None:
    prefix = f"    {key}:"
    for index, line in enumerate(block):
        if line.startswith(prefix):
            return index
    return None


if __name__ == "__main__":
    raise SystemExit(main())
