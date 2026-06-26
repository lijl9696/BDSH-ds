from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from .browser_runner import download_job, run_job, save_login_state
from .config import CollectorJob, load_jobs, load_settings
from .daily_report import fetch_daily_region_report, render_daily_region_report, send_wecom_image


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="BDSH report collector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="立即执行一个采集任务")
    run_parser.add_argument("job_code")

    download_parser = subparsers.add_parser("download", help="只下载报表文件，不调用导入接口")
    download_parser.add_argument("job_code")

    login_parser = subparsers.add_parser("login", help="人工登录并保存 Playwright storage_state")
    login_parser.add_argument("job_code")
    login_parser.add_argument("--login-url")

    report_parser = subparsers.add_parser("daily-report", help="生成并可选推送大区汇总日报图片")
    report_parser.add_argument("--date", help="日报日期，格式 YYYY-MM-DD；默认昨天")
    report_parser.add_argument("--platform", default="all", help="平台编码，默认 all（美团+抖音）")
    report_parser.add_argument("--scope", default="direct", choices=["direct", "franchise"], help="日报范围：direct 直营，franchise 加盟")
    report_parser.add_argument("--output", help="输出 PNG 路径；默认写入日志目录")
    report_parser.add_argument("--send", action="store_true", help="发送到企业微信机器人")

    args = parser.parse_args()
    settings = load_settings()
    if args.command == "daily-report":
        report_date = _parse_report_date(args.date)
        output = Path(args.output) if args.output else settings.logs_dir / f"daily_region_report_{args.scope}_{args.platform}_{report_date:%Y%m%d}.png"
        report = fetch_daily_region_report(settings, report_date, args.platform, args.scope)
        image_path = render_daily_region_report(report, output, settings.report_font_path, settings.report_logo_path)
        result = {"status": "generated", "path": str(image_path), "rows": len(report.rows)}
        if args.send:
            webhook_url = (
                settings.franchise_wecom_webhook_url
                if args.scope == "franchise"
                else settings.wecom_webhook_url
            )
            webhook_env_name = "WECOM_FRANCHISE_WEBHOOK_URL" if args.scope == "franchise" else "WECOM_WEBHOOK_URL"
            if not webhook_url:
                raise SystemExit(f"缺少 {webhook_env_name}，无法推送企业微信。")
            result["wecom"] = send_wecom_image(webhook_url, image_path)
            result["status"] = "sent"
        print(result)
        return

    jobs = {job.code: job for job in load_jobs(settings.jobs_path)}
    job = jobs.get(args.job_code)
    if not job:
        raise SystemExit(f"找不到任务：{args.job_code}")

    if args.command == "run":
        print(asyncio.run(run_job(job, settings)))
    elif args.command == "download":
        print(asyncio.run(download_job(job, settings)))
    elif args.command == "login":
        print(asyncio.run(save_login_state(job, settings, args.login_url)))


def _parse_report_date(value: str | None):
    if value:
        return datetime.strptime(value, "%Y-%m-%d").date()
    return datetime.now().date() - timedelta(days=1)


if __name__ == "__main__":
    main()
