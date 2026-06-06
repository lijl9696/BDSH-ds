from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from .config import CollectorJob, Settings
from .import_client import ReportWebClient


class CollectorError(RuntimeError):
    pass


async def run_job(job: CollectorJob, settings: Settings) -> dict:
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    state_path = settings.state_dir / job.state_file
    if not state_path.exists():
        raise CollectorError(f"登录态不存在：{state_path}。请先执行 login 命令保存登录态。")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        context = await browser.new_context(accept_downloads=True, storage_state=str(state_path))
        page = await context.new_page()
        try:
            downloaded = await _download_report(page, job, settings.downloads_dir)
        finally:
            await context.close()
            await browser.close()

    client = ReportWebClient(
        settings.report_web_base_url,
        settings.import_auth_username,
        settings.import_auth_password,
    )
    today = date.today()
    return client.import_file(
        platform_code=job.platform_code,
        file_path=downloaded,
        period_start=today,
        period_end=today,
        duplicate_policy=job.duplicate_policy,
        date_field=job.date_field,
        store_code_field=job.store_code_field,
        store_name_field=job.store_name_field,
    )


async def save_login_state(job: CollectorJob, settings: Settings, login_url: str | None = None) -> Path:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    state_path = settings.state_dir / job.state_file
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(login_url or job.report_page_url)
        print("请在打开的浏览器里完成登录。登录完成并能访问报表页面后，回到终端按回车保存登录态。")
        await asyncio.to_thread(input)
        await context.storage_state(path=str(state_path))
        await context.close()
        await browser.close()
    return state_path


async def _download_report(page, job: CollectorJob, downloads_dir: Path) -> Path:
    await page.goto(job.report_page_url, wait_until="domcontentloaded")
    await page.locator(job.trigger_selector).click()
    await page.wait_for_timeout(job.wait_after_trigger_seconds * 1000)
    await page.goto(job.download_center_url, wait_until="domcontentloaded")

    try:
        async with page.expect_download(timeout=job.download_timeout_seconds * 1000) as download_info:
            await page.locator(job.download_selector).click()
        download = await download_info.value
    except PlaywrightTimeoutError as exc:
        raise CollectorError(f"{job.code} 下载超时，请检查下载中心选择器或登录态。") from exc

    suggested = download.suggested_filename or f"{job.code}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    target = downloads_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{suggested}"
    await download.save_as(str(target))
    return target
