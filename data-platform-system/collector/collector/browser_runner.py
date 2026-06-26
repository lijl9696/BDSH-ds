from __future__ import annotations

import asyncio
import logging
import re
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
    target_date = _target_date(settings)
    logging.info("%s 目标采集日期 target_date=%s", job.code, target_date)
    downloaded = await _download_expected_file(job, settings, target_date)

    client = ReportWebClient(
        settings.report_web_base_url,
        settings.import_auth_username,
        settings.import_auth_password,
    )
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


async def _download_expected_file(job: CollectorJob, settings: Settings, target_date) -> Path:
    attempts = max(1, job.download_retry_attempts)
    for attempt in range(1, attempts + 1):
        logging.info(
            "%s 开始下载尝试 attempt=%s/%s target_date=%s direct_date=%s",
            job.code,
            attempt,
            attempts,
            target_date,
            True,
        )
        downloaded = await download_job(
            job,
            settings,
            target_date=target_date,
            use_direct_date=True,
        )
        file_date_range = _date_range_from_filename(downloaded.name)
        if not file_date_range:
            logging.info("%s 下载文件未识别到日期，跳过文件名日期校验 file=%s", job.code, downloaded.name)
            return downloaded
        start_date, end_date = file_date_range
        if start_date == target_date and end_date == target_date:
            logging.info("%s 下载文件日期校验通过 file=%s", job.code, downloaded.name)
            return downloaded
        if attempt >= attempts:
            raise CollectorError(
                f"{job.code} 下载文件日期不匹配：期望 {target_date:%Y%m%d}-{target_date:%Y%m%d}，"
                f"实际文件 {downloaded.name}。"
            )
        logging.warning(
            "%s 下载文件日期不匹配，等待后重试 attempt=%s/%s expected=%s actual=%s file=%s",
            job.code,
            attempt,
            attempts,
            f"{target_date:%Y%m%d}-{target_date:%Y%m%d}",
            f"{start_date:%Y%m%d}-{end_date:%Y%m%d}",
            downloaded.name,
        )
        await asyncio.sleep(max(1, job.download_retry_delay_seconds))
    raise CollectorError(f"{job.code} 下载重试结束但没有得到目标日期文件。")


async def download_job(
    job: CollectorJob,
    settings: Settings,
    *,
    target_date=None,
    use_direct_date: bool = False,
) -> Path:
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    state_path = settings.state_dir / job.state_file
    if not state_path.exists() and not settings.browser_user_data_dir and not settings.browser_cdp_url:
        raise CollectorError(f"登录态不存在：{state_path}。请先执行 login 命令保存登录态。")
    if target_date is None:
        target_date = _target_date(settings)
    run_context = _build_run_context(job, target_date)

    async with async_playwright() as playwright:
        context = await _new_browser_context(
            playwright,
            settings,
            accept_downloads=True,
            storage_state=state_path if state_path.exists() else None,
        )
        page = await context.new_page()
        try:
            downloaded = await _download_report(
                page,
                job,
                settings.downloads_dir,
                target_date=target_date,
                use_direct_date=use_direct_date,
                run_context=run_context,
            )
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


def _target_date(settings: Settings):
    return datetime.now(_python_timezone(settings.timezone)).date() - timedelta(days=1)


def _date_range_from_filename(filename: str):
    match = re.search(r"-(\d{8})-(\d{8})(?:-|\.|$)", filename)
    if not match:
        return None
    start = datetime.strptime(match.group(1), "%Y%m%d").date()
    end = datetime.strptime(match.group(2), "%Y%m%d").date()
    return start, end


def _build_run_context(job: CollectorJob, target_date) -> dict[str, object]:
    now = datetime.now()
    default_task_name = f"{job.code}_{target_date:%Y%m%d}_{now:%H%M%S}"
    task_name = _format_template(
        job.task_name_template or default_task_name,
        {"target_date": target_date, "now": now, "job_code": job.code},
    )
    return {
        "job_code": job.code,
        "now": now,
        "target_date": target_date,
        "target_date_yyyymmdd": f"{target_date:%Y%m%d}",
        "task_name": task_name,
    }


def _format_template(template: str | None, context: dict[str, object]) -> str | None:
    if template is None:
        return None
    return template.format(**context)


async def _download_report(
    page,
    job: CollectorJob,
    downloads_dir: Path,
    *,
    target_date=None,
    use_direct_date: bool = False,
    run_context: dict[str, object] | None = None,
) -> Path:
    await _goto_page(page, job.report_page_url)
    await _log_browser_clock(page, job.code, "after_goto")
    for step in job.steps or []:
        await _run_step(
            page,
            step,
            target_date=target_date,
            use_direct_date=use_direct_date,
            run_context=run_context,
        )
    if job.wait_after_trigger_seconds > 0:
        await page.wait_for_timeout(job.wait_after_trigger_seconds * 1000)
    if target_date:
        await _log_date_picker_state(page, job.code, target_date, "before_download")
    if job.download_mode == "task_center":
        return await _download_task_center_file(page, job, downloads_dir, run_context or {})
    if job.download_mode == "download_center":
        if not job.download_center_url:
            raise CollectorError(f"{job.code} 使用 download_center 模式但没有配置 download_center_url。")
        await _goto_page(page, job.download_center_url)

    try:
        async with page.expect_download(timeout=job.download_timeout_seconds * 1000) as download_info:
            await _click_download_button(page, job.download_selector)
        download = await download_info.value
    except PlaywrightTimeoutError as exc:
        raise CollectorError(f"{job.code} 下载超时，请检查下载中心选择器或登录态。") from exc

    suggested = download.suggested_filename or f"{job.code}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    target = downloads_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{suggested}"
    await download.save_as(str(target))
    return target


async def _download_task_center_file(
    page,
    job: CollectorJob,
    downloads_dir: Path,
    run_context: dict[str, object],
) -> Path:
    if not job.download_center_url:
        raise CollectorError(f"{job.code} 使用 task_center 模式但没有配置 download_center_url。")
    for field_name in [
        "task_refresh_selector",
        "task_row_selector",
        "task_name_selector",
        "task_status_selector",
        "task_download_selector",
    ]:
        if not getattr(job, field_name):
            raise CollectorError(f"{job.code} 使用 task_center 模式但没有配置 {field_name}。")

    await _goto_page(page, _format_template(job.download_center_url, run_context))
    deadline = monotonic() + job.task_timeout_seconds
    last_status = "未找到任务"
    while monotonic() < deadline:
        row = await _find_task_row(
            page,
            job.task_row_selector or "",
            job.task_name_selector or "",
            str(run_context.get("task_name") or ""),
        )
        if row:
            try:
                last_status = (await row.locator(job.task_status_selector or "").inner_text(timeout=2000)).strip()
            except Exception:
                last_status = "无法读取状态"
            if job.task_failed_text and job.task_failed_text in last_status:
                raise CollectorError(f"{job.code} 下载任务失败：{last_status}")
            if job.task_done_text in last_status:
                async with page.expect_download(timeout=job.download_timeout_seconds * 1000) as download_info:
                    await _click_locator(page, row.locator(job.task_download_selector or "").first)
                download = await download_info.value
                suggested = download.suggested_filename or f"{job.code}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
                target = downloads_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{suggested}"
                await download.save_as(str(target))
                return target

        logging.info(
            "%s 下载任务未完成 task=%s status=%s，等待后刷新。",
            job.code,
            run_context.get("task_name"),
            last_status,
        )
        await page.wait_for_timeout(max(1, job.task_poll_interval_seconds) * 1000)
        refresh = await _find_locator(page, job.task_refresh_selector or "", timeout_ms=5000)
        await _click_locator(page, refresh)
        await page.wait_for_timeout(2000)
    raise CollectorError(f"{job.code} 等待下载任务超时 task={run_context.get('task_name')} last_status={last_status}")


async def _find_task_row(page, row_selector: str, name_selector: str, task_name: str):
    rows = page.locator(row_selector)
    try:
        count = await rows.count()
    except Exception:
        return None
    for index in range(count):
        row = rows.nth(index)
        try:
            name = (await row.locator(name_selector).inner_text(timeout=1000)).strip()
        except Exception:
            continue
        if task_name in name:
            return row
    return None


async def _run_step(
    page,
    step: BrowserStep,
    *,
    target_date=None,
    use_direct_date: bool = False,
    run_context: dict[str, object] | None = None,
) -> None:
    selector = _format_template(step.selector, run_context or {}) if step.selector else None
    value = _format_template(step.value, run_context or {}) if step.value else None
    url = _format_template(step.url, run_context or {}) if step.url else None
    if step.action == "goto":
        if not url:
            raise CollectorError("goto step 缺少 url")
        await _goto_page(page, url)
        return
    if step.action == "click":
        if not selector:
            raise CollectorError("click step 缺少 selector")
        if target_date and "昨天" in selector:
            logging.info("检测到“昨天”快捷按钮 step，跳过快捷按钮，改为直接选择目标日期 target_date=%s selector=%s", target_date, selector)
            await _click_target_date_range(page, target_date)
            return
        locator = await _find_locator(page, selector)
        await _click_locator(page, locator)
        return
    if step.action == "ensure_meituan_all_shops":
        await _ensure_meituan_all_shops(page)
        return
    if step.action == "click_form_control_by_label":
        if not value:
            raise CollectorError("click_form_control_by_label step 缺少 value")
        await _click_form_control_by_label(page, value)
        return
    if step.action == "click_target_date_range":
        if not target_date:
            raise CollectorError("click_target_date_range 缺少 target_date")
        await _click_target_date_range(page, target_date)
        return
    if step.action == "click_all_enabled_checkboxes":
        await _click_all_enabled_checkboxes(page)
        return
    if step.action == "click_all_by_text":
        await _click_all_by_text(page, value or "全选")
        return
    if step.action == "js_click":
        if not selector:
            raise CollectorError("js_click step 缺少 selector")
        locator = await _find_locator(page, selector)
        await _js_click_locator(page, locator)
        return
    if step.action == "fill":
        if not selector:
            raise CollectorError("fill step 缺少 selector")
        locator = await _find_locator(page, selector)
        await locator.fill(value or "")
        return
    if step.action == "fill_douyin_task_name":
        await _fill_douyin_task_name(page, value or "")
        return
    if step.action == "confirm_douyin_task_name":
        await _click_douyin_task_name_confirm(page)
        return
    if step.action == "wait":
        await page.wait_for_timeout((step.seconds or 1) * 1000)
        return
    raise CollectorError(f"不支持的 step action：{step.action}")


async def _goto_page(page, url: str | None) -> None:
    if not url:
        raise CollectorError("页面跳转缺少 url")
    try:
        await page.goto(url, wait_until="commit", timeout=90000)
    except PlaywrightTimeoutError:
        # 有些后台页面长期加载埋点/通知流，commit 等不到时继续让后续 selector 判断页面是否可用。
        logging.warning("页面跳转超时，继续等待目标元素 url=%s", url)


async def _click_locator(page, locator) -> None:
    """点击元素前先清理干扰浮层；失败后再次清理并兜底强制点击。

    这里不要一开始就 force click。正常点击可以让 Playwright 帮我们发现
    “元素被遮挡 / 不可点击 / 未滚动到视口”等问题，只有兜底时才 force。
    """
    await _clear_blocking_overlays(page)
    try:
        await locator.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    try:
        await locator.click(timeout=10000)
    except PlaywrightError:
        await _clear_blocking_overlays(page)
        await page.wait_for_timeout(500)
        await locator.click(force=True, timeout=10000)


async def _js_click_locator(page, locator) -> None:
    await _clear_blocking_overlays(page)
    await locator.evaluate(
        """
        (element) => {
          const target = element.querySelector('span, input, button') || element;
          const options = { bubbles: true, cancelable: true, view: window };
          for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
            target.dispatchEvent(new MouseEvent(type, options));
          }
        }
        """
    )


async def _click_download_button(page, selector: str) -> None:
    """Click the active download button without treating the download modal as a stray overlay."""
    deadline = monotonic() + 30
    while monotonic() < deadline:
        try:
            locator = page.locator(selector)
            visible = await _first_visible_locator(locator, limit=10)
            if visible:
                await visible.click(timeout=5000)
                return
        except Exception:
            pass

        for context in [page, *page.frames]:
            try:
                clicked = await context.evaluate(
                    """
                    () => {
                      const modal = Array.from(document.querySelectorAll('.download-modal, .mtd-modal'))
                        .find((element) => element.innerText.includes('日全数据下载'));
                      const root = modal || document;
                      const button = root.querySelector('button[data-click-bid="b_cbg_o9nfc94x_mc"]')
                        || Array.from(root.querySelectorAll('button'))
                          .find((element) => !String(element.className || '').includes('disabled')
                            && (element.innerText || '').trim() === '下载');
                      if (!button) return false;
                      button.scrollIntoView({ block: 'center', inline: 'center' });
                      const options = { bubbles: true, cancelable: true, view: window };
                      for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                        button.dispatchEvent(new MouseEvent(type, options));
                      }
                      button.click();
                      return true;
                    }
                    """
                )
                if clicked:
                    return
            except Exception:
                continue

        await page.wait_for_timeout(500)

    raise CollectorError(f"找不到下载按钮：{selector}")


async def _fill_douyin_task_name(page, task_name: str) -> None:
    """Fill Douyin's task-name dialog with structure-tolerant DOM probing."""
    if not task_name:
        raise CollectorError("fill_douyin_task_name step 缺少 value")

    script = """
    (taskName) => {
      const visible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && Number(style.opacity || '1') !== 0
          && rect.width > 0
          && rect.height > 0;
      };
      const textOf = (element) => String(element.innerText || element.textContent || '').trim();
      const zIndexOf = (element) => {
        const value = Number.parseInt(window.getComputedStyle(element).zIndex || '0', 10);
        return Number.isFinite(value) ? value : 0;
      };
      const roots = Array.from(document.querySelectorAll([
        '.byted-modal',
        '.byted-content-container',
        '[role="dialog"]',
        '[class*="modal"]',
        '[class*="Modal"]'
      ].join(',')))
        .filter(visible)
        .sort((a, b) => zIndexOf(b) - zIndexOf(a));

      const preferredRoots = roots.filter((root) => {
        const text = textOf(root);
        return text.includes('任务命名') || text.includes('请输入任务名');
      });
      const searchRoots = preferredRoots.length ? preferredRoots : roots.concat([document]);

      for (const root of searchRoots) {
        const inputs = Array.from(root.querySelectorAll('input, textarea'))
          .filter((input) => visible(input) && !input.disabled && input.type !== 'hidden');
        const input = inputs.find((candidate) => {
          const placeholder = String(candidate.getAttribute('placeholder') || '');
          return placeholder.includes('任务名') || placeholder === '';
        }) || inputs[0];
        if (!input) continue;

        input.scrollIntoView({ block: 'center', inline: 'center' });
        input.focus();
        const proto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        if (setter) {
          setter.call(input, taskName);
        } else {
          input.value = taskName;
        }
        for (const type of ['input', 'change']) {
          input.dispatchEvent(new Event(type, { bubbles: true }));
        }
        return true;
      }
      return false;
    }
    """

    deadline = monotonic() + 30
    while monotonic() < deadline:
        await _clear_blocking_overlays(page)
        for context in [page, *page.frames]:
            try:
                if await context.evaluate(script, task_name):
                    await page.wait_for_timeout(500)
                    return
            except Exception:
                continue
        await page.wait_for_timeout(500)

    raise CollectorError("找不到抖音任务命名输入框")


async def _click_douyin_task_name_confirm(page) -> None:
    script = """
    () => {
      const visible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && Number(style.opacity || '1') !== 0
          && rect.width > 0
          && rect.height > 0;
      };
      const textOf = (element) => String(element.innerText || element.textContent || '').trim();
      const zIndexOf = (element) => {
        const value = Number.parseInt(window.getComputedStyle(element).zIndex || '0', 10);
        return Number.isFinite(value) ? value : 0;
      };
      const roots = Array.from(document.querySelectorAll([
        '.byted-modal',
        '.byted-content-container',
        '[role="dialog"]',
        '[class*="modal"]',
        '[class*="Modal"]'
      ].join(',')))
        .filter(visible)
        .sort((a, b) => zIndexOf(b) - zIndexOf(a));
      const preferredRoots = roots.filter((root) => {
        const text = textOf(root);
        return text.includes('任务命名') || text.includes('请输入任务名');
      });
      const searchRoots = preferredRoots.length ? preferredRoots : roots;

      for (const root of searchRoots) {
        const buttons = Array.from(root.querySelectorAll('button, [role="button"], .byted-confirm-ok'))
          .filter(visible);
        const button = buttons.find((candidate) => textOf(candidate) === '确定')
          || buttons.find((candidate) => String(candidate.className || '').includes('byted-confirm-ok'));
        if (!button) continue;
        button.scrollIntoView({ block: 'center', inline: 'center' });
        const options = { bubbles: true, cancelable: true, view: window };
        for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
          button.dispatchEvent(new MouseEvent(type, options));
        }
        button.click();
        return true;
      }
      return false;
    }
    """

    deadline = monotonic() + 30
    while monotonic() < deadline:
        await _clear_blocking_overlays(page)
        for context in [page, *page.frames]:
            try:
                if await context.evaluate(script):
                    await page.wait_for_timeout(500)
                    return
            except Exception:
                continue
        await page.wait_for_timeout(500)

    raise CollectorError("找不到抖音任务命名确定按钮")


async def _click_form_control_by_label(page, label_text: str) -> None:
    await _clear_blocking_overlays(page)
    script = """
        (labelText) => {
          const visible = (element) => {
            const rect = element.getBoundingClientRect();
            const style = window.getComputedStyle(element);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const labels = Array.from(document.querySelectorAll('label, .byted-form-container-label, .mtd-form-item-label'))
            .filter((element) => (element.innerText || '').trim().includes(labelText));
          const fireClick = (element) => {
            element.scrollIntoView({ block: 'center', inline: 'center' });
            const options = { bubbles: true, cancelable: true, view: window };
            for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
              element.dispatchEvent(new MouseEvent(type, options));
            }
            element.click();
          };
          for (const label of labels) {
            const container = label.closest('.byted-form-container, .mtd-form-item, .form-item') || label.parentElement;
            if (!container) continue;
            const preferredInput = Array.from(container.querySelectorAll('input'))
              .find((input) => visible(input) && String(input.getAttribute('placeholder') || '').includes('时间'));
            if (preferredInput) {
              const wrapper = preferredInput.closest(
                '[class*="date"], [class*="Date"], [class*="picker"], [class*="Picker"], [class*="input"], [class*="Input"]'
              ) || preferredInput;
              fireClick(wrapper);
              fireClick(preferredInput);
              return true;
            }
            const candidates = Array.from(container.querySelectorAll('input, button, [class*="picker"], [class*="input"]'))
              .filter((element) => element !== label && visible(element));
            for (const element of candidates) {
              fireClick(element);
              return true;
            }
          }
          return false;
        }
        """
    for context in [page, *page.frames]:
        try:
            if await context.evaluate(script, label_text):
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue
    raise CollectorError(f"找不到表单项：{label_text}")


async def _ensure_meituan_all_shops(page) -> None:
    """Switch Meituan top-left shop selector to all shops without generic overlay cleanup."""
    await _clear_blocking_overlays(page, aggressive=True)
    shop = page.locator("#shopName").first
    try:
        current = (await shop.inner_text(timeout=5000)).strip()
    except Exception as exc:
        body_has_all_shops = await _page_or_frame_has_text(page, "全部门店")
        if body_has_all_shops:
            logging.info("找不到美团门店选择器 #shopName，但页面文本已显示全部门店，继续执行。")
            return
        raise CollectorError("找不到美团门店选择器 #shopName") from exc
    if "全部门店" in current:
        return

    for attempt in range(3):
        await shop.click(force=True, timeout=5000)
        await page.wait_for_timeout(1000)
        try:
            clicked = await page.evaluate(
                """
                () => {
                  const matches = Array.from(document.querySelectorAll('body *'))
                    .filter((element) => (element.innerText || '').trim() === '全部门店');
                  const option = matches.find((element) =>
                    String(element.className || '').includes('shop-item')
                    || element.closest('.shop-item, .slot-item')
                  );
                  if (!option) return false;
                  const target = option.closest('.shop-item, .slot-item') || option;
                  const events = { bubbles: true, cancelable: true, view: window };
                  for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    target.dispatchEvent(new MouseEvent(type, events));
                  }
                  target.click();
                  return true;
                }
                """
            )
            if clicked:
                await page.wait_for_timeout(1000)
                confirm = page.locator("button:has-text('确定')").first
                if await confirm.count():
                    try:
                        if await confirm.is_visible(timeout=1000):
                            await confirm.click(force=True, timeout=3000)
                            await page.wait_for_timeout(1000)
                    except Exception:
                        pass
                current = (await shop.inner_text(timeout=5000)).strip()
                if "全部门店" in current:
                    return
        except Exception:
            logging.info("切换全部门店重试 attempt=%s", attempt + 1)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)

    raise CollectorError("无法切换到全部门店")


async def _page_or_frame_has_text(page, text: str) -> bool:
    script = "(text) => String(document.body?.innerText || '').includes(text)"
    for context in [page, *page.frames]:
        try:
            if await context.evaluate(script, text):
                return True
        except Exception:
            continue
    return False


async def _click_target_date_range(page, target_date) -> None:
    await _log_date_picker_state(page, "date_range", target_date, "before_clear")
    cleared = await _clear_blocking_overlays(page)
    if cleared:
        logging.info("选择目标日期前清理干扰浮层 count=%s target_date=%s", cleared, target_date)
    await _log_date_picker_state(page, "date_range", target_date, "after_clear")
    if await _confirm_target_date_if_selected(page, target_date):
        logging.info("目标日期已在输入框中，无需重复选择 target_date=%s", target_date)
        return
    if not await _has_visible_date_picker(page):
        await _log_date_picker_state(page, "date_range", target_date, "picker_missing")
        raise CollectorError(f"日期弹窗未显示，无法选择目标日期：{target_date:%Y-%m-%d}")
    for _ in range(2):
        clicked = await _click_target_date_in_contexts(page, target_date)
        if not clicked:
            await _log_date_picker_state(page, "date_range", target_date, "target_not_found")
            raise CollectorError(f"找不到目标日期：{target_date:%Y-%m-%d}")
        await page.wait_for_timeout(500)
        await _log_date_picker_state(page, "date_range", target_date, "after_click")
        if await _confirm_target_date_if_selected(page, target_date):
            logging.info("目标日期已选择完成 target_date=%s", target_date)
            return
    await _click_visible_button_text(page, "确认", required=False)
    await page.wait_for_timeout(500)
    if not await _confirm_target_date_if_selected(page, target_date):
        await _log_date_picker_state(page, "date_range", target_date, "selected_value_mismatch")
        raise CollectorError(f"目标日期选择后输入框未变为预期范围：{target_date:%Y-%m-%d}")


async def _click_target_date_in_contexts(page, target_date) -> bool:
    script = """
    ({ year, month, day }) => {
      const isVisible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      };
      const isDisabled = (element) => {
        for (let node = element; node && node !== document.body; node = node.parentElement) {
          const className = String(node.className || '');
          if (className.includes('disabled') || className.includes('forbidden')) {
            return true;
          }
        }
        return false;
      };
      const fireClick = (element) => {
        element.scrollIntoView({ block: 'center', inline: 'center' });
        const options = { bubbles: true, cancelable: true, view: window };
        for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
          element.dispatchEvent(new MouseEvent(type, options));
        }
        element.click();
      };
      const compactTarget = `${year}${String(month).padStart(2, '0')}${String(day).padStart(2, '0')}`;
      const looseDayPattern = new RegExp(`(^|\\\\D)${day}(\\\\D|$)`);
      const matchesTarget = (element) => {
        const pieces = [
          element.innerText,
          element.textContent,
          element.getAttribute('title'),
          element.getAttribute('aria-label'),
          element.getAttribute('data-date'),
          element.getAttribute('data-value')
        ].map((value) => String(value || '').trim()).filter(Boolean);
        for (const value of pieces) {
          const compact = value.replace(/[^0-9]/g, '');
          if (compact === String(day) || compact === compactTarget) return true;
          if (value.length <= 12 && looseDayPattern.test(value)) return true;
        }
        return false;
      };
      const headerText = `${year}年 ${month}月`;
      const mtdRoots = Array.from(document.querySelectorAll('.mtd-picker-panel-content, .mtd-picker-panel-body-date, .mtd-date-picker-panel'))
        .filter((root) => root.innerText.includes(headerText) || root.innerText.includes(String(day)));
      const bytedRoots = Array.from(document.querySelectorAll('.byted-date-view, .byted-date-container, .byted-popover-wrapper'))
        .filter((root) => root.innerText.includes(`${year}年`) || root.innerText.includes(`${month}月`) || root.innerText.includes(String(day)));
      const roots = mtdRoots.length
        ? mtdRoots
        : (bytedRoots.length ? bytedRoots : Array.from(document.querySelectorAll('.mtd-picker-panel-body-date, .mtd-date-picker-panel, .byted-date-container, body')));
      for (const root of roots) {
        const candidates = Array.from(root.querySelectorAll('td, div, span, button'))
          .filter((element) => matchesTarget(element))
          .filter((element) => {
            const clickable = element.closest('td, [class*="date-picker-cell"], [class*="date-col"], [class*="date-item"]') || element;
            const monthPanel = clickable.closest('.mtd-picker-panel-content, .byted-date-view, .byted-date-container');
            return (!monthPanel || monthPanel.innerText.includes(headerText))
              && isVisible(element)
              && !isDisabled(element);
          });
        for (const element of candidates) {
          const clickable = element.closest('td, [class*="date-picker-cell"], [class*="date-col"], [class*="date-item"]') || element;
          fireClick(clickable);
          return true;
        }
      }
      return false;
    }
    """
    payload = {"year": target_date.year, "month": target_date.month, "day": target_date.day}
    for context in [page, *page.frames]:
        try:
            if await context.evaluate(script, payload):
                return True
        except Exception:
            continue
    return False


async def _has_visible_date_picker(page) -> bool:
    script = """
    () => {
      const visible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      };
      return Array.from(document.querySelectorAll([
        '.mtd-picker-panel-body-date',
        '.mtd-date-picker-panel',
        '.mtd-picker-panel-content',
        '.byted-date-view',
        '.byted-date-container',
        '.byted-popover-wrapper'
      ].join(','))).some(visible);
    }
    """
    for context in [page, *page.frames]:
        try:
            if await context.evaluate(script):
                return True
        except Exception:
            continue
    return False


async def _confirm_target_date_if_selected(page, target_date) -> bool:
    expected = f"{target_date:%Y%m%d}"
    script = """
    (expected) => Array.from(document.querySelectorAll('input')).some((input) => {
      const value = String(input.value || '').trim();
      const normalized = value.replaceAll('/', '-').replace(/[^0-9]/g, '');
      const count = normalized.split(expected).length - 1;
      return count >= 2 || (count >= 1 && !value.includes('～') && !value.includes('~') && !value.includes('至'));
    })
    """
    for context in [page, *page.frames]:
        try:
            if await context.evaluate(script, expected):
                await _click_visible_button_text(page, "确认", required=False)
                return True
        except Exception:
            continue
    return False


async def _log_browser_clock(page, job_code: str, stage: str) -> None:
    script = """
    () => ({
      href: window.location.href,
      dateString: new Date().toString(),
      isoString: new Date().toISOString(),
      timezoneOffsetMinutes: new Date().getTimezoneOffset(),
      language: navigator.language,
      userAgent: navigator.userAgent.slice(0, 160)
    })
    """
    try:
        payload = await page.evaluate(script)
        logging.info("%s 浏览器时间诊断 stage=%s payload=%s", job_code, stage, payload)
    except Exception as exc:
        logging.info("%s 浏览器时间诊断失败 stage=%s error=%s", job_code, stage, exc)


async def _log_date_picker_state(page, job_code: str, target_date, stage: str) -> None:
    states = []
    for index, context in enumerate([page, *page.frames]):
        try:
            state = await context.evaluate(
                """
                ({ year, month, day }) => {
                  const visible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                      && style.display !== 'none'
                      && rect.width > 0
                      && rect.height > 0;
                  };
                  const disabled = (element) => {
                    for (let node = element; node && node !== document.body; node = node.parentElement) {
                      const className = String(node.className || '');
                      if (
                        node.disabled
                        || node.getAttribute('aria-disabled') === 'true'
                        || className.includes('disabled')
                        || className.includes('forbidden')
                      ) return true;
                    }
                    return false;
                  };
                  const textOf = (element) => String(element.innerText || element.textContent || '').trim();
                  const dateInputs = Array.from(document.querySelectorAll('input'))
                    .filter((input) => visible(input))
                    .map((input) => ({
                      placeholder: input.getAttribute('placeholder') || '',
                      value: input.value || '',
                      disabled: Boolean(input.disabled),
                    }))
                    .filter((input) =>
                      input.placeholder.includes('时间')
                      || input.placeholder.includes('日期')
                      || input.value.includes(String(year))
                      || input.value.includes('～')
                    )
                    .slice(0, 8);
                  const datePanels = Array.from(document.querySelectorAll([
                    '.mtd-picker-panel-body-date',
                    '.mtd-date-picker-panel',
                    '.mtd-picker-panel-content',
                    '.byted-date-view',
                    '.byted-date-container',
                    '.byted-popover-wrapper'
                  ].join(','))).filter(visible);
                  const headerText = `${year}年 ${month}月`;
                  const targetCandidates = [];
                  for (const root of datePanels.length ? datePanels : [document]) {
                    for (const element of Array.from(root.querySelectorAll('td, div, span, button'))) {
                      if (textOf(element) !== String(day)) continue;
                      const clickable = element.closest('td, [class*="date-picker-cell"], [class*="date-col"], [class*="date-item"]') || element;
                      const monthPanel = clickable.closest('.mtd-picker-panel-content, .byted-date-view, .byted-date-container');
                      const rect = clickable.getBoundingClientRect();
                      targetCandidates.push({
                        text: textOf(element),
                        className: String(clickable.className || '').slice(0, 140),
                        visible: visible(clickable),
                        disabled: disabled(clickable),
                        rect: {
                          x: Math.round(rect.x),
                          y: Math.round(rect.y),
                          width: Math.round(rect.width),
                          height: Math.round(rect.height),
                        },
                        panelHasTargetMonth: textOf(monthPanel || root).includes(headerText),
                      });
                    }
                  }
                  const blockingLayers = Array.from(document.querySelectorAll('body *'))
                    .filter(visible)
                    .map((element) => {
                      const style = window.getComputedStyle(element);
                      const rect = element.getBoundingClientRect();
                      const zIndex = Number.parseInt(style.zIndex || '0', 10);
                      return {
                        tag: element.tagName,
                        className: String(element.className || '').slice(0, 120),
                        text: textOf(element).slice(0, 80),
                        zIndex: Number.isFinite(zIndex) ? zIndex : 0,
                        position: style.position,
                        area: Math.round(rect.width * rect.height),
                      };
                    })
                    .filter((item) =>
                      (item.position === 'fixed' || item.position === 'absolute')
                      && item.zIndex >= 1000
                      && item.area >= window.innerWidth * window.innerHeight * 0.04
                    )
                    .sort((a, b) => b.zIndex - a.zIndex || b.area - a.area)
                    .slice(0, 5);
                  const shortcuts = Array.from(document.querySelectorAll('.mtd-picker-panel-shortcut, [class*="shortcut"], [class*="Shortcut"]'))
                    .filter(visible)
                    .map((element) => textOf(element).slice(0, 40))
                    .filter(Boolean)
                    .slice(0, 8);
                  return {
                    href: window.location.href,
                    browserDate: new Date().toString(),
                    target: `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`,
                    dateInputs,
                    datePanelCount: datePanels.length,
                    datePanelTexts: datePanels.slice(0, 3).map((panel) => textOf(panel).slice(0, 160)),
                    shortcuts,
                    targetCandidates: targetCandidates.slice(0, 12),
                    blockingLayers,
                  };
                }
                """,
                {"year": target_date.year, "month": target_date.month, "day": target_date.day},
            )
            if state.get("dateInputs") or state.get("datePanelCount") or state.get("targetCandidates") or state.get("blockingLayers"):
                states.append({"frame": index, "url": context.url, **state})
        except Exception as exc:
            states.append({"frame": index, "error": f"{type(exc).__name__}: {exc}"})
    logging.info("%s 日期选择诊断 stage=%s target_date=%s states=%s", job_code, stage, target_date, states[:4])


async def _click_visible_button_text(page, text: str, *, required: bool = True) -> bool:
    locator = page.locator(f"button:has-text('{text}')")
    try:
        count = await locator.count()
    except Exception:
        count = 0
    if count == 0:
        if required:
            raise CollectorError(f"找不到按钮：{text}")
        return False
    for index in range(count - 1, -1, -1):
        button = locator.nth(index)
        try:
            if await button.is_visible() and await button.is_enabled():
                await _click_locator(page, button)
                await page.wait_for_timeout(500)
                return True
        except Exception:
            continue
    if required:
        raise CollectorError(f"找不到按钮：{text}")
    return False


async def _click_all_enabled_checkboxes(page) -> None:
    await _clear_blocking_overlays(page)
    clicked = await page.evaluate(
        """
        () => {
          let clickedCount = 0;
          const checkboxes = Array.from(document.querySelectorAll('input[type="checkbox"]'))
            .filter((input) => !input.disabled && !input.checked);
          for (const input of checkboxes) {
            const clickable = input.closest('label, [class*="checkbox"], [class*="check"]') || input.parentElement || input;
            clickable.click();
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            clickedCount += 1;
          }
          return clickedCount;
        }
        """
    )
    logging.info("点击未选中的可用复选框 count=%s", clicked)
    await page.wait_for_timeout(1000)


async def _click_all_by_text(page, text: str) -> None:
    await _clear_blocking_overlays(page)
    locator = page.get_by_text(text, exact=True)
    count = await locator.count()
    clicked = 0
    for index in range(count):
        item = locator.nth(index)
        try:
            if await item.is_visible():
                await item.click()
                clicked += 1
                await page.wait_for_timeout(300)
        except Exception:
            continue
    logging.info("点击文本 count=%s text=%s", clicked, text)
    await page.wait_for_timeout(1000)


async def _clear_blocking_overlays(page, *, aggressive: bool = False) -> int:
    """清理可能遮挡点击的广告/引导浮层。

    设计原则：
    1. 优先点击关闭按钮，而不是直接删除弹窗 DOM。
    2. 只删除明确的新手引导/广告/权益类浮层。
    3. 不再无差别删除 .mtd-modal、.ant-modal、[class*="modal"]，避免误伤日期选择、下载确认等业务弹窗。
    """
    script = """
    (aggressive) => {
      let handled = 0;

      const visible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== 'hidden'
          && style.display !== 'none'
          && rect.width > 0
          && rect.height > 0;
      };

      const safeText = (element) => String(element.innerText || element.textContent || '').trim();

      const clickElement = (element) => {
        if (!element || !visible(element)) return false;
        try {
          element.click();
          handled += 1;
          return true;
        } catch (_) {
          return false;
        }
      };

      // 0. driver.js / 新手引导会跨 iframe 遮挡真实业务按钮，先强制移除。
      // 这类浮层不是业务弹窗，保留反而会导致 iframe 内按钮“看得见但点不到”。
      const forceRemoveGuideSelectors = [
        '.driver-overlay',
        '.driver-overlay-content',
        '.driver-overlay-animated',
        '.driver-popover',
        '.driver-popover-wrapper',
        '.driver-stage',
        '#driver-popover-content',
        '#guide-notification-modal',
        '.guide-custom-popover',
        '.mtd-notification.custom-notification',
        '.custom-notification',
        '.mtd-notification[aria-controls="driver-popover-content"]',
        '[aria-controls="driver-popover-content"]'
      ];

      for (const selector of forceRemoveGuideSelectors) {
        for (const element of Array.from(document.querySelectorAll(selector))) {
          try {
            element.remove();
            handled += 1;
          } catch (_) {}
        }
      }
      document.body?.classList.remove('driver-active', 'driver-fade', 'mtd-lock-scroll');
      for (const element of Array.from(document.querySelectorAll('[class*="driver"], [class*="guide-"]'))) {
        try {
          if (element === document.body || element === document.documentElement) continue;
          const className = String(element.className || '');
          if (
            className.includes('driver-fix-stacking')
            || className.includes('driver-highlighted-element')
            || className.includes('driver-no-interaction')
            || className.includes('driver-active-element')
          ) {
            element.classList.remove(
              'driver-fix-stacking',
              'driver-highlighted-element',
              'driver-no-interaction',
              'driver-active-element'
            );
            handled += 1;
            continue;
          }
          const text = safeText(element).slice(0, 1000);
          const businessKeywords = [
            '时间范围', '报表预览', '下载', '复制报表', '任务命名',
            '统计周期', '汇总方式', '创建下载任务', '请输入任务名',
            '昨天', '近7天', '近30天', '全部门店', '报表中心', '使用模板'
          ];
          if (businessKeywords.some((keyword) => text.includes(keyword))) continue;
          element.remove();
          handled += 1;
        } catch (_) {}
      }

      // 1. 先处理常见关闭按钮。这里尽量“点击关闭”，不直接 remove 业务弹窗。
      const closeSelectors = [
        '[aria-label="close"]',
        '[aria-label="Close"]',
        '[title="关闭"]',
        '[title="Close"]',
        '.close',
        '.close-btn',
        '.modal-close',
        '.mtd-modal-close',
        '.ant-modal-close',
        '[class*="close"]',
        '[class*="Close"]'
      ];

      for (const selector of closeSelectors) {
        const candidates = Array.from(document.querySelectorAll(selector)).filter(visible);
        // 从后往前点，通常后出现的弹窗在更上层。
        for (let i = candidates.length - 1; i >= 0; i--) {
          const container = candidates[i].closest('.mtd-modal, .ant-modal, .byted-modal, [role="dialog"], [class*="popover"], [class*="Popover"]');
          const text = safeText(container || candidates[i]).slice(0, 1000);
          const businessKeywords = [
            '时间范围', '报表预览', '下载', '复制报表', '任务命名',
            '统计周期', '汇总方式', '创建下载任务', '请输入任务名',
            '昨天', '近7天', '近30天'
          ];
          if (businessKeywords.some((keyword) => text.includes(keyword))) continue;
          if (clickElement(candidates[i])) return handled;
        }
      }

      // 2. 按文字找“关闭/跳过/稍后”类按钮。不要点“确定/取消”，避免误伤业务弹窗。
      const closeTexts = [
        '关闭', '知道了', '我知道了', '好的',
        '暂不', '暂不开启', '跳过',
        '稍后再说', '以后再说', '不再提醒', '我再想想'
      ];

      const textCandidates = Array.from(document.querySelectorAll('button, a, span, div'))
        .filter(visible)
        .filter((element) => {
          const text = safeText(element);
          // 太长的容器一般不是按钮，避免点到业务区域。
          return text && text.length <= 20;
        });

      for (let i = textCandidates.length - 1; i >= 0; i--) {
        const element = textCandidates[i];
        const text = safeText(element);
        if (closeTexts.some((closeText) => text === closeText || text.includes(closeText))) {
          if (clickElement(element)) return handled;
        }
      }

      // 3. 删除明确的新手引导/driver.js 类浮层。
      const safeRemoveSelectors = [
        '.driver-overlay',
        '.driver-overlay-content',
        '.driver-overlay-animated',
        '.driver-popover',
        '.driver-popover-wrapper',
        '.driver-stage',
        '.driver-highlighted-element',
        '#guide-notification-modal',
        '.mtd-notification[aria-controls="driver-popover-content"]',
        '[aria-controls="driver-popover-content"]'
      ];

      for (const selector of safeRemoveSelectors) {
        for (const element of Array.from(document.querySelectorAll(selector))) {
          try {
            element.remove();
            handled += 1;
          } catch (_) {}
        }
      }

      document.body?.classList.remove('driver-active', 'driver-fade');
      for (const element of Array.from(document.querySelectorAll('[class*="driver"]'))) {
        try {
          const className = String(element.className || '');
          if (className.includes('driver-fix-stacking')) {
            element.classList.remove('driver-fix-stacking');
          } else if (element.tagName === 'SVG' || element.getAttribute('aria-controls') === 'driver-popover-content') {
            element.remove();
            handled += 1;
          } else {
            element.classList.remove('driver-active-element', 'driver-no-interaction', 'driver-highlighted-element');
          }
        } catch (_) {}
      }

      // 4. 谨慎删除明显广告/权益/新功能类弹窗。
      // 注意：这里不删除所有 modal，只删除文本命中广告/引导关键词的弹窗容器。
      const adKeywords = [
        '广告', '活动', '权益', '升级', '新功能', '新手引导', '引导',
        '立即体验', '立即开通', '去开通', '限时', '推荐使用',
        '免费试用', '福利', '大促', '营销', '订购', '续费',
        '重点消息', '消息待查看', '行业资讯', '产品动态', '立即参与'
      ];

      const modalCandidates = Array.from(document.querySelectorAll([
        '.mtd-modal',
        '.mtd-modal-wrap',
        '.ant-modal',
        '.ant-modal-wrap',
        '.mtd-notification',
        '.ant-notification',
        '.tippy-box',
        '.popper-wrapper',
        '[class*="popover"]',
        '[class*="Popover"]',
        '[class*="notification"]',
        '[class*="Notification"]'
      ].join(','))).filter(visible);

      for (let i = modalCandidates.length - 1; i >= 0; i--) {
        const element = modalCandidates[i];
        const text = safeText(element).slice(0, 1000);
        if (adKeywords.some((keyword) => text.includes(keyword))) {
          try {
            element.remove();
            handled += 1;
          } catch (_) {}
        }
      }

      // 5. 目标元素一直找不到时，清理最上层的非业务浮层。
      if (aggressive) {
        const businessKeywords = [
          '时间范围', '报表预览', '下载', '复制报表', '任务命名',
          '统计周期', '汇总方式', '创建下载任务', '请输入任务名',
          '昨天', '近7天', '近30天'
        ];
        const viewportArea = window.innerWidth * window.innerHeight;
        const layerCandidates = Array.from(document.querySelectorAll('body *'))
          .filter(visible)
          .map((element) => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            const zIndex = Number.parseInt(style.zIndex || '0', 10);
            return { element, style, rect, zIndex: Number.isFinite(zIndex) ? zIndex : 0 };
          })
          .filter((item) => {
            const position = item.style.position;
            const area = item.rect.width * item.rect.height;
            return (position === 'fixed' || position === 'absolute')
              && item.zIndex >= 1000
              && area >= viewportArea * 0.05;
          })
          .sort((a, b) => b.zIndex - a.zIndex || (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));
        for (const item of layerCandidates.slice(0, 3)) {
          const text = safeText(item.element).slice(0, 1000);
          if (businessKeywords.some((keyword) => text.includes(keyword))) continue;
          const close = item.element.querySelector('[class*="close"], [class*="Close"], [aria-label="Close"], [aria-label="关闭"]');
          if (close && visible(close) && clickElement(close)) return handled;
          try {
            item.element.remove();
            handled += 1;
            return handled;
          } catch (_) {}
        }
      }

      return handled;
    }
    """
    total_handled = 0
    try:
        for _ in range(3):
            count = await page.evaluate(script, aggressive)
            total_handled += count
            if count == 0:
                break
            await page.wait_for_timeout(300)

        for frame in page.frames:
            try:
                count = await frame.evaluate(script, aggressive)
                total_handled += count
            except Exception:
                continue

        if total_handled:
            logging.info("清理/关闭干扰弹窗 count=%s", total_handled)

        return total_handled
    except Exception:
        return total_handled


async def _find_locator(page, selector: str, timeout_ms: int = 60000):
    deadline = monotonic() + timeout_ms / 1000
    misses = 0
    while monotonic() < deadline:
        await _clear_blocking_overlays(page, aggressive=misses >= 4)
        page_locator = page.locator(selector)
        try:
            visible = await _first_visible_locator(page_locator)
            if visible:
                return visible
        except Exception:
            await page.wait_for_timeout(500)
            continue

        for frame in page.frames:
            frame_locator = frame.locator(selector)
            try:
                visible = await _first_visible_locator(frame_locator)
                if visible:
                    return visible
            except Exception:
                continue

        misses += 1
        await page.wait_for_timeout(500)
    raise CollectorError(f"找不到页面元素：{selector}")


async def _first_visible_locator(locator, *, limit: int = 50):
    count = await locator.count()
    for index in range(min(count, limit)):
        item = locator.nth(index)
        try:
            if await _locator_is_really_visible(item):
                return item
        except Exception:
            continue
    return None


async def _locator_is_really_visible(locator) -> bool:
    return bool(
        await locator.evaluate(
            """
            (element) => {
              for (let node = element; node && node.nodeType === Node.ELEMENT_NODE; node = node.parentElement) {
                const style = window.getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                if (
                  style.display === 'none'
                  || style.visibility === 'hidden'
                  || Number(style.opacity || '1') === 0
                  || rect.width <= 0
                  || rect.height <= 0
                ) {
                  return false;
                }
              }
              return true;
            }
            """
        )
    )


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
