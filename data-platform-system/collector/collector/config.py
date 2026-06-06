from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Settings:
    report_web_base_url: str
    import_auth_username: str
    import_auth_password: str
    jobs_path: Path
    downloads_dir: Path
    state_dir: Path
    logs_dir: Path
    headless: bool
    browser_channel: str | None
    browser_user_data_dir: Path | None
    browser_cdp_url: str | None


@dataclass(frozen=True)
class BrowserStep:
    action: str
    selector: str | None = None
    value: str | None = None
    url: str | None = None
    seconds: int | None = None


@dataclass(frozen=True)
class CollectorJob:
    code: str
    enabled: bool
    platform_code: str
    schedule_cron: str
    state_file: str
    report_page_url: str
    download_selector: str
    download_mode: str = "direct"
    steps: list[BrowserStep] | None = None
    download_center_url: str | None = None
    date_field: str | None = None
    store_code_field: str | None = None
    store_name_field: str | None = None
    duplicate_policy: str = "skip"
    wait_after_trigger_seconds: int = 5
    download_timeout_seconds: int = 600


def load_settings() -> Settings:
    return Settings(
        report_web_base_url=os.getenv("REPORT_WEB_BASE_URL", "http://report-web:8000").rstrip("/"),
        import_auth_username=os.getenv("IMPORT_AUTH_USERNAME", "admin"),
        import_auth_password=os.getenv("IMPORT_AUTH_PASSWORD", ""),
        jobs_path=Path(os.getenv("COLLECTOR_JOBS_PATH", "/app/config/jobs.yml")),
        downloads_dir=Path(os.getenv("COLLECTOR_DOWNLOADS_DIR", "/app/downloads")),
        state_dir=Path(os.getenv("COLLECTOR_STATE_DIR", "/app/state")),
        logs_dir=Path(os.getenv("COLLECTOR_LOGS_DIR", "/app/logs")),
        headless=os.getenv("COLLECTOR_HEADLESS", "true").lower() not in {"0", "false", "no"},
        browser_channel=os.getenv("COLLECTOR_BROWSER_CHANNEL") or None,
        browser_user_data_dir=(
            Path(os.environ["COLLECTOR_BROWSER_USER_DATA_DIR"])
            if os.getenv("COLLECTOR_BROWSER_USER_DATA_DIR")
            else None
        ),
        browser_cdp_url=os.getenv("COLLECTOR_BROWSER_CDP_URL") or None,
    )


def load_jobs(path: Path) -> list[CollectorJob]:
    if not path.exists():
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_jobs: list[dict[str, Any]] = payload.get("jobs", [])
    jobs: list[CollectorJob] = []
    for raw_job in raw_jobs:
        raw_steps = raw_job.pop("steps", None) or []
        steps = [BrowserStep(**step) for step in raw_steps]
        jobs.append(CollectorJob(**raw_job, steps=steps))
    return jobs
