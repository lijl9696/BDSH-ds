from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .browser_runner import run_job
from .config import CollectorJob, load_jobs, load_settings


def main() -> None:
    settings = load_settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(settings.logs_dir)

    jobs = [job for job in load_jobs(settings.jobs_path) if job.enabled]
    if not jobs:
        logging.warning("没有启用的采集任务，检查 %s", settings.jobs_path)

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    for job in jobs:
        scheduler.add_job(
            _run_job_sync,
            CronTrigger.from_crontab(job.schedule_cron, timezone="Asia/Shanghai"),
            args=[job, settings],
            id=job.code,
            replace_existing=True,
            max_instances=1,
        )
        logging.info("已注册采集任务 %s cron=%s", job.code, job.schedule_cron)

    scheduler.start()


def _run_job_sync(job: CollectorJob, settings) -> None:
    logging.info("开始采集任务 %s", job.code)
    try:
        result = asyncio.run(run_job(job, settings))
    except Exception:
        logging.exception("采集任务失败 %s", job.code)
        raise
    logging.info("采集任务完成 %s result=%s", job.code, result)


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
