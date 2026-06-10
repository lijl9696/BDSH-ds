from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import Error as PlaywrightError
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
    target_date = datetime.now(_python_timezone(settings.timezone)).date() - timedelta(days=1)
    return client.import_file(
        platform_code=job.platform_code,
        file_path=downloaded,
        period_start=target_date,
        period_end=target_date,
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
        except Exception:
            await _save_debug_artifacts(page, job, settings.logs_dir)
            raise
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
        return await browser.new_context(
            accept_downloads=accept_downloads,
            timezone_id=settings.timezone,
        )

    if settings.browser_user_data_dir:
        settings.browser_user_data_dir.mkdir(parents=True, exist_ok=True)
        return await playwright.chromium.launch_persistent_context(
            str(settings.browser_user_data_dir),
            headless=headless,
            channel=settings.browser_channel,
            accept_downloads=accept_downloads,
            timezone_id=settings.timezone,
        )

    browser = await playwright.chromium.launch(
        headless=headless,
        channel=settings.browser_channel,
    )
    return await browser.new_context(
        accept_downloads=accept_downloads,
        storage_state=str(storage_state) if storage_state else None,
        timezone_id=settings.timezone,
    )


def _python_timezone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == "Asia/Shanghai":
            return timezone(timedelta(hours=8))
        raise


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
            await _click_locator(page, locator)
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
        await _click_locator(page, locator)
        return
    if step.action == "js_click":
        if not step.selector:
            raise CollectorError("js_click step 缺少 selector")
        locator = await _find_locator(page, step.selector)
        await _js_click_locator(page, locator)
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


async def _click_locator(page, locator) -> None:
    await _clear_blocking_overlays(page)
    try:
        await locator.click()
    except PlaywrightError:
        await _clear_blocking_overlays(page)
        await locator.click(force=True)


async def _js_click_locator(page, locator) -> None:
    await _clear_blocking_overlays(page)
    await locator.evaluate("(element) => element.click()")


async def _clear_blocking_overlays(page) -> None:
    script = """
    () => {
      const selectors = [
        '.driver-overlay',
        '.driver-overlay-animated',
        '.driver-popover',
        '.driver-popover-wrapper',
        '.driver-stage',
        '.driver-highlighted-element',
        '#guide-notification-modal',
        '.mtd-notification[aria-controls="driver-popover-content"]'
      ];
      for (const selector of selectors) {
        for (const element of document.querySelectorAll(selector)) {
          element.remove();
        }
      }
      document.body?.classList.remove('driver-active', 'driver-fade');
      for (const element of document.querySelectorAll('[class*="driver"]')) {
        if (element.tagName === 'SVG' || element.getAttribute('aria-controls') === 'driver-popover-content') {
          element.remove();
          continue;
        }
        element.classList.remove('driver-active-element', 'driver-no-interaction', 'driver-highlighted-element');
      }
    }
    """
    try:
        await page.evaluate(script)
    except Exception:
        pass
    for frame in page.frames:
        try:
            await frame.evaluate(script)
        except Exception:
            continue


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


async def _save_debug_artifacts(page, job: CollectorJob, logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = logs_dir / f"{stamp}_{job.code}_debug"
    try:
        await page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
    except Exception:
        pass
    lines: list[str] = []
    try:
        lines.append(f"page_title={await page.title()}")
        lines.append(f"page_url={page.url}")
    except Exception:
        pass
    for index, frame in enumerate(page.frames):
        lines.append(f"\n--- frame {index} {frame.url} ---")
        try:
            body_text = await frame.locator("body").inner_text(timeout=2000)
            lines.append(body_text[:5000])
        except Exception as exc:
            lines.append(f"<body failed: {type(exc).__name__}: {exc}>")
        for selector in [
            "button",
            "input",
            ".download-modal",
            ".mtd-picker-panel-shortcut",
            "[class*='driver']",
        ]:
            try:
                payload = await frame.locator(selector).evaluate_all(
                    """elements => elements.slice(0, 30).map((element, index) => ({
                      index,
                      tag: element.tagName,
                      className: element.className,
                      text: (element.innerText || '').slice(0, 300),
                      placeholder: element.getAttribute('placeholder'),
                      dataClickBid: element.getAttribute('data-click-bid'),
                      outerHTML: element.outerHTML.slice(0, 500)
                    }))"""
                )
            except Exception as exc:
                payload = f"<selector failed: {type(exc).__name__}: {exc}>"
            lines.append(f"\nselector={selector}\n{payload}")
    try:
        base.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass
