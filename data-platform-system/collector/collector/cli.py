from __future__ import annotations

import argparse
import asyncio

from .browser_runner import download_job, run_job, save_login_state
from .config import CollectorJob, load_jobs, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="BDSH report collector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="立即执行一个采集任务")
    run_parser.add_argument("job_code")

    download_parser = subparsers.add_parser("download", help="只下载报表文件，不调用导入接口")
    download_parser.add_argument("job_code")

    login_parser = subparsers.add_parser("login", help="人工登录并保存 Playwright storage_state")
    login_parser.add_argument("job_code")
    login_parser.add_argument("--login-url")

    args = parser.parse_args()
    settings = load_settings()
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


if __name__ == "__main__":
    main()
