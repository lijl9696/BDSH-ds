from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .config import load_config
from .excel_report import write_platform_excel_reports
from .history import archive_result
from .images import generate_report_images
from .processor import process_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="团购运营报表处理")
    parser.add_argument("--config", required=True, help="配置表路径")
    parser.add_argument("--meituan", help="美团报表路径")
    parser.add_argument("--douyin", help="抖音报表路径")
    parser.add_argument("--template", help="输出 Excel 模板路径")
    parser.add_argument("--output-dir", default="outputs", help="输出目录")
    parser.add_argument("--brand", default="团购运营", help="报告图品牌名")
    parser.add_argument("--images", action="store_true", help="同时生成报告图")
    args = parser.parse_args()

    config = load_config(args.config)
    result = process_reports(config, {"meituan": args.meituan, "douyin": args.douyin})
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_paths = write_platform_excel_reports(result, output_dir, timestamp, args.template)
    db_path = archive_result(result, Path("data") / "history.sqlite")
    for excel_path in excel_paths:
        print(f"Excel: {excel_path}")
    print(f"History: {db_path}")
    if args.images:
        image_paths = generate_report_images(result, config.rankings, config.briefings, output_dir / "images" / timestamp, args.brand)
        for path in image_paths:
            print(f"Image: {path}")


if __name__ == "__main__":
    main()
