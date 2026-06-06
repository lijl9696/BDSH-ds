from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from time import monotonic

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from .config import BrowserStep, CollectorJob, Settings
from .import_client import ReportWebClient


class CollectorError(RuntimeError):
    pass


async def run_job(job: CollectorJob, settings: Settings) -> dict:
    downloaded = await download_job(job, settings)

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


async def download_job(job: CollectorJob, settings: Settings) -> Path:
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    state_path = settings.state_dir / job.state_file
    if not state_path.exists() and not settings.browser_user_data_dir and not settings.browser_cdp_url:
        raise CollectorError(f"登录态不存在：{state_path}。请先执行 login 命令保存登录态。")

    async with async_playwright() as playwright:
        context = await _new_browser_context(
            playwright,
            settings,
            accept_downloads=True,
            storage_state=state_path if state_path.exists() else None,
        )
        page = await context.new_page()
        try:
            downloaded = await _download_report(page, job, settings.downloads_dir)
        finally:
            if settings.browser_cdp_url:
                await page.close()
            else:
                browser = context.browser
                await context.close()
                if browser:
                    await browser.close()
    return downloaded


async def save_login_state(job: CollectorJob, settings: Settings, login_url: str | None = None) -> Path:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    state_path = settings.state_dir / job.state_file
    async with async_playwright() as playwright:
        context = await _new_browser_context(
            playwright,
            settings,
            accept_downloads=True,
            force_headless=False,
        )
        page = await context.new_page()
        await page.goto(login_url or job.report_page_url)
        print("请在打开的浏览器里完成登录。登录完成并能访问报表页面后，回到终端按回车保存登录态。")
        await asyncio.to_thread(input)
        await context.storage_state(path=str(state_path))
        browser = context.browser
        await context.close()
        if browser:
            await browser.close()
    return state_path


async def _new_browser_context(
    playwright,
    settings: Settings,
    *,
    accept_downloads: bool,
    storage_state: Path | None = None,
    force_headless: bool | None = None,
):
    headless = settings.headless if force_headless is None else force_headless
    if settings.browser_cdp_url:
        browser = await playwright.chromium.connect_over_cdp(settings.browser_cdp_url)
        if browser.contexts:
            return browser.contexts[0]
        return await browser.new_context(accept_downloads=accept_downloads)

    if settings.browser_user_data_dir:
        settings.browser_user_data_dir.mkdir(parents=True, exist_ok=True)
        return await playwright.chromium.launch_persistent_context(
            str(settings.browser_user_data_dir),
            headless=headless,
            channel=settings.browser_channel,
            accept_downloads=accept_downloads,
        )

    browser = await playwright.chromium.launch(
        headless=headless,
        channel=settings.browser_channel,
    )
    return await browser.new_context(
        accept_downloads=accept_downloads,
        storage_state=str(storage_state) if storage_state else None,
    )


async def _download_report(page, job: CollectorJob, downloads_dir: Path) -> Path:
    await page.goto(job.report_page_url, wait_until="domcontentloaded")
    for step in job.steps or []:
        await _run_step(page, step)
    if job.wait_after_trigger_seconds > 0:
        await page.wait_for_timeout(job.wait_after_trigger_seconds * 1000)
    if job.download_mode == "download_center":
        if not job.download_center_url:
            raise CollectorError(f"{job.code} 使用 download_center 模式但没有配置 download_center_url。")
        await page.goto(job.download_center_url, wait_until="domcontentloaded")

    try:
        async with page.expect_download(timeout=job.download_timeout_seconds * 1000) as download_info:
            locator = await _find_locator(page, job.download_selector)
            await locator.click()
        download = await download_info.value
    except PlaywrightTimeoutError as exc:
        raise CollectorError(f"{job.code} 下载超时，请检查下载中心选择器或登录态。") from exc

    suggested = download.suggested_filename or f"{job.code}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    target = downloads_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{suggested}"
    await download.save_as(str(target))
    return target


async def _run_step(page, step: BrowserStep) -> None:
    if step.action == "goto":
        if not step.url:
            raise CollectorError("goto step 缺少 url")
        await page.goto(step.url, wait_until="domcontentloaded")
        return
    if step.action == "click":
        if not step.selector:
            raise CollectorError("click step 缺少 selector")
        locator = await _find_locator(page, step.selector)
        await locator.click()
        return
    if step.action == "fill":
        if not step.selector:
            raise CollectorError("fill step 缺少 selector")
        locator = await _find_locator(page, step.selector)
        await locator.fill(step.value or "")
        return
    if step.action == "wait":
        await page.wait_for_timeout((step.seconds or 1) * 1000)
        return
    raise CollectorError(f"不支持的 step action：{step.action}")


async def _find_locator(page, selector: str, timeout_ms: int = 30000):
    deadline = monotonic() + timeout_ms / 1000
    while monotonic() < deadline:
        page_locator = page.locator(selector)
        try:
            if await page_locator.count() > 0:
                return page_locator.first
        except Exception:
            await page.wait_for_timeout(500)
            continue

        for frame in page.frames:
            frame_locator = frame.locator(selector)
            try:
                if await frame_locator.count() > 0:
                    return frame_locator.first
            except Exception:
                continue

        await page.wait_for_timeout(500)
    raise CollectorError(f"找不到页面元素：{selector}")
