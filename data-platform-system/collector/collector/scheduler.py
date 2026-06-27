from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .browser_runner import run_job
from .config import CollectorJob, load_jobs, load_settings
from .daily_report import fetch_daily_region_report, render_daily_region_report, report_has_data, send_wecom_image


def main() -> None:
    settings = load_settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(settings.logs_dir)

    jobs = [job for job in load_jobs(settings.jobs_path) if job.enabled]
    if not jobs:
        logging.warning("没有启用的采集任务，检查 %s", settings.jobs_path)

    scheduler = BlockingScheduler(
        timezone=settings.timezone,
        job_defaults={
            "coalesce": True,
            "misfire_grace_time": 600,
            "max_instances": 1,
        },
    )
    for job in jobs:
        scheduler.add_job(
            _run_job_sync,
            CronTrigger.from_crontab(job.schedule_cron, timezone=settings.timezone),
            args=[job, settings],
            id=job.code,
            replace_existing=True,
        )
        logging.info("已注册采集任务 %s cron=%s", job.code, job.schedule_cron)

    _add_daily_report_job(
        scheduler,
        settings,
        scope="direct",
        webhook_url=settings.wecom_webhook_url,
        cron=settings.daily_report_cron,
        job_id="wecom_daily_report_direct",
        label="直营",
    )
    _add_daily_report_job(
        scheduler,
        settings,
        scope="franchise",
        webhook_url=settings.franchise_wecom_webhook_url,
        cron=settings.franchise_daily_report_cron,
        job_id="wecom_daily_report_franchise",
        label="加盟",
    )

    scheduler.start()


def _run_job_sync(job: CollectorJob, settings) -> None:
    logging.info("开始采集任务 %s", job.code)
    try:
        result = asyncio.run(run_job(job, settings))
    except Exception:
        logging.exception("采集任务失败 %s", job.code)
        raise
    logging.info("采集任务完成 %s result=%s", job.code, result)


def _add_daily_report_job(
    scheduler: BlockingScheduler,
    settings,
    *,
    scope: str,
    webhook_url: str | None,
    cron: str | None,
    job_id: str,
    label: str,
) -> None:
    if webhook_url and cron:
        scheduler.add_job(
            _send_daily_report_sync,
            CronTrigger.from_crontab(cron, timezone=settings.timezone),
            args=[settings, scope, webhook_url, label],
            id=job_id,
            replace_existing=True,
        )
        logging.info("已注册%s企业微信日报推送任务 cron=%s", label, cron)
    else:
        logging.info("未启用%s企业微信日报推送任务，检查对应 WECOM webhook 和 cron", label)


def _send_daily_report_sync(settings, scope: str, webhook_url: str, label: str) -> None:
    report_date = datetime.now(ZoneInfo(settings.timezone)).date() - timedelta(days=1)
    output_path = settings.logs_dir / f"daily_region_report_{scope}_all_{report_date:%Y%m%d}.png"
    logging.info("开始%s企业微信日报推送 date=%s", label, report_date)
    try:
        report = fetch_daily_region_report(settings, report_date, "all", scope)
        if not report_has_data(report):
            logging.warning("%s企业微信日报跳过推送：目标日期没有可用数据 date=%s", label, report_date)
            return
        image_path = render_daily_region_report(report, output_path, settings.report_font_path, settings.report_logo_path)
        result = send_wecom_image(webhook_url, image_path)
    except Exception:
        logging.exception("%s企业微信日报推送失败 date=%s", label, report_date)
        raise
    logging.info("%s企业微信日报推送完成 date=%s rows=%s result=%s", label, report_date, len(report.rows), result)


def _setup_logging(logs_dir: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(logs_dir / "collector.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


if __name__ == "__main__":
    main()
