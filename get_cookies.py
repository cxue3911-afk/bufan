#!/usr/bin/env python3
"""自动登录不凡商城后台，获取 Cookie 并更新 config.py。"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright
from browser_runtime import browser_launch_options


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGIN_URL = "https://admin.bufanshop.kcshop.vip/zonWwkqVHm.php/index/login"
TARGET_COOKIES = {"keeplogin", "PHPSESSID"}
CONFIG_PATH = os.path.join(BASE_DIR, "config.py")
LOGIN_CONFIG_PATH = os.path.join(BASE_DIR, "login_config.json")
DEFAULT_CAPTCHA_DIR = os.path.join(BASE_DIR, "data", "captcha")
ocr = None


def load_login_config() -> dict:
    if not os.path.exists(LOGIN_CONFIG_PATH):
        return {}

    try:
        with open(LOGIN_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as exc:
        print(f"⚠️ 登录配置读取失败: {LOGIN_CONFIG_PATH} - {exc}")
        return {}

    if not isinstance(config, dict):
        print(f"⚠️ 登录配置格式不正确: {LOGIN_CONFIG_PATH}")
        return {}

    return config


def get_config_value(config: dict, key: str) -> str:
    bufan_config = config.get("bufan", {})
    if isinstance(bufan_config, dict):
        value = bufan_config.get(key, "")
        if value:
            return str(value).strip()

    value = config.get(key, "")
    return str(value).strip() if value else ""


def get_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value or os.name != "nt":
        return value

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            registry_value, _ = winreg.QueryValueEx(key, name)
        return str(registry_value).strip()
    except Exception:
        return ""


def get_login_credentials() -> tuple[str, str, str]:
    # 批量轮换账号时，优先使用本次进程注入的凭证；
    # 未提供时再回退到本地 login_config.json，兼容原有定时任务。
    env_username = get_env("BUFAN_USERNAME")
    env_password = get_env("BUFAN_PASSWORD")
    if env_username and env_password:
        return env_username, env_password, "environment variables"

    config = load_login_config()
    username = get_config_value(config, "username")
    password = get_config_value(config, "password")
    if username and password:
        return username, password, LOGIN_CONFIG_PATH

    return "", "", ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="登录后台并刷新 Cookie")
    parser.add_argument("--headless", action="store_true", help="保留兼容参数，默认即为无头模式")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口")
    parser.add_argument(
        "--captcha-dir",
        default=DEFAULT_CAPTCHA_DIR,
        help="验证码截图保存目录，默认 data/captcha",
    )
    parser.add_argument(
        "--manual-captcha",
        action="store_true",
        help="OCR 失败时强制在命令行等待人工输入验证码",
    )
    parser.add_argument(
        "--no-manual-captcha",
        action="store_true",
        help="OCR 失败时不等待人工输入，只保存截图并失败退出/重试",
    )
    return parser


def create_ocr():
    try:
        import ddddocr
    except Exception as exc:
        print(f"⚠️ OCR 库不可用，将使用人工兜底: {exc}")
        return None

    try:
        return ddddocr.DdddOcr()
    except Exception as exc:
        print(f"⚠️ OCR 初始化失败，将使用人工兜底: {exc}")
        return None


def is_interactive_terminal() -> bool:
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())


def manual_prompt_enabled(args: argparse.Namespace) -> bool:
    if args.no_manual_captcha:
        return False
    return args.manual_captcha or is_interactive_terminal()


def should_run_headless(args: argparse.Namespace) -> bool:
    return not args.no_headless


async def save_captcha_snapshot(captcha_img, captcha_dir: str) -> str:
    os.makedirs(captcha_dir, exist_ok=True)
    filename = f"captcha_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
    snapshot_path = os.path.abspath(os.path.join(captcha_dir, filename))
    latest_path = os.path.abspath(os.path.join(captcha_dir, "latest.png"))

    await captcha_img.screenshot(path=snapshot_path)
    print(f"验证码截图已保存: {snapshot_path}")

    try:
        await captcha_img.screenshot(path=latest_path)
        print(f"最新验证码快捷路径: {latest_path}")
    except Exception as exc:
        print(f"⚠️ latest.png 更新失败，不影响当前截图: {exc}")

    return snapshot_path


async def fetch_captcha_bytes(page, captcha_img) -> bytes:
    src = await captcha_img.get_attribute("src")
    if not src:
        return b""

    if src.startswith("/"):
        parsed = urlparse(LOGIN_URL)
        src = f"{parsed.scheme}://{parsed.netloc}{src}"

    resp = await page.context.request.get(src)
    return await resp.body()


async def prompt_manual_captcha(snapshot_path: str) -> str:
    if not snapshot_path:
        prompt = "OCR 未能识别验证码，请输入验证码后回车: "
    else:
        prompt = f"OCR 未能识别验证码，请查看图片后输入验证码\n{snapshot_path}\n验证码: "

    try:
        value = await asyncio.to_thread(input, prompt)
    except EOFError:
        print("⚠️ 当前没有可交互命令行，无法人工输入验证码。")
        return ""
    except KeyboardInterrupt:
        print("\n已取消人工验证码输入。")
        return ""

    return value.strip()


async def handle_login_dialog(dialog_messages: list[str], dialog) -> None:
    message = (dialog.message or "").strip()
    if message:
        dialog_messages.append(message)
    try:
        await dialog.accept()
    except Exception:
        pass


async def collect_login_error_messages(page) -> list[str]:
    """Collect common login error text from visible page elements."""
    try:
        messages = await page.evaluate(
            """
            () => {
                const selectors = [
                    '.layui-layer-content',
                    '.layui-layer-dialog',
                    '.layui-layer-msg',
                    '.alert',
                    '.alert-danger',
                    '.error',
                    '.errors',
                    '.help-block',
                    '.invalid-feedback',
                    '.toast',
                    '.toast-message',
                    '.message',
                    '.msg',
                    '.tips',
                    '[class*="error"]',
                    '[class*="danger"]',
                    '[class*="warning"]',
                    '[class*="toast"]',
                    '[class*="message"]',
                    '[class*="msg"]',
                    '[class*="tips"]'
                ];

                const seen = new Set();
                const result = [];

                function isVisible(el) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0'
                        && rect.width > 0
                        && rect.height > 0;
                }

                for (const selector of selectors) {
                    for (const el of document.querySelectorAll(selector)) {
                        if (!isVisible(el)) continue;
                        const text = (el.innerText || el.textContent || '').trim();
                        if (text && !seen.has(text)) {
                            seen.add(text);
                            result.push(text);
                        }
                    }
                }

                const bodyText = (document.body?.innerText || '').trim();
                if (bodyText) {
                    for (const line of bodyText.split('\\n')) {
                        const text = line.trim();
                        if (!text || text.length > 120 || seen.has(text)) continue;
                        if (/错|误|失败|不能为空|不存在|密码|账号|验证码|captcha|error|fail/i.test(text)) {
                            seen.add(text);
                            result.push(text);
                        }
                    }
                }

                return result.slice(0, 8);
            }
            """
        )
    except Exception as exc:
        return [f"读取登录错误提示失败: {exc}"]

    return [str(message).strip() for message in messages if str(message).strip()]


def print_login_failure_details(dialog_messages: list[str], page_messages: list[str]) -> None:
    messages = []
    seen = set()
    for message in [*dialog_messages, *page_messages]:
        if message and message not in seen:
            seen.add(message)
            messages.append(message)

    if messages:
        print("登录失败原因提示:")
        for message in messages:
            print(f"  - {message}")
    else:
        print("登录页没有返回明确错误提示，请重点核对账号、密码、验证码。")


async def get_captcha_text(
    page,
    captcha_dir: str,
    allow_manual_prompt: bool,
    force_manual_prompt: bool = False,
) -> str:
    captcha_img = await page.query_selector('img[src*="captcha"]')
    if not captcha_img:
        print("❌ 未找到验证码图片")
        return ""

    snapshot_path = ""
    try:
        snapshot_path = await save_captcha_snapshot(captcha_img, captcha_dir)
    except Exception as exc:
        print(f"⚠️ 验证码截图保存失败: {exc}")

    manual_text = os.getenv("BUFAN_CAPTCHA_TEXT", "").strip()
    if manual_text:
        print("使用环境变量 BUFAN_CAPTCHA_TEXT 提供的验证码")
        return manual_text

    if force_manual_prompt and allow_manual_prompt:
        print("上一次验证码可能识别错误，本轮改用人工输入。")
        return await prompt_manual_captcha(snapshot_path)

    if not ocr:
        print("⚠️ ddddocr/onnxruntime 当前不可用")
        return await prompt_manual_captcha(snapshot_path) if allow_manual_prompt else ""

    try:
        img_bytes = await fetch_captcha_bytes(page, captcha_img)
        result = (ocr.classification(img_bytes) or "").strip()
    except Exception as exc:
        print(f"⚠️ OCR 识别异常: {exc}")
        result = ""

    print(f"OCR 识别验证码: {result or '(空)'}")
    if result:
        return result

    if allow_manual_prompt:
        return await prompt_manual_captcha(snapshot_path)

    print("⚠️ OCR 识别为空，且当前未启用人工输入。")
    return ""


async def update_config_cookies(keeplogin: str, phpsessid: str) -> None:
    if not os.path.exists(CONFIG_PATH):
        print(f"⚠️ 未找到 {CONFIG_PATH}，请手动更新 Cookie")
        return

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        if line.strip().startswith("'keeplogin'"):
            line = f"    'keeplogin': '{keeplogin}',\n"
        elif line.strip().startswith("'PHPSESSID'"):
            line = f"    'PHPSESSID': '{phpsessid}',\n"
        new_lines.append(line)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"\n✅ 已自动更新到 config.py ({CONFIG_PATH})")


async def run_login(args: argparse.Namespace) -> int:
    global ocr

    username, password, credential_source = get_login_credentials()

    if not username or not password:
        print("Missing BUFAN_USERNAME or BUFAN_PASSWORD environment variable")
        print(f"也可以在配置文件里填写账号密码: {LOGIN_CONFIG_PATH}")
        return 1

    print(f"登录账号配置来源: {credential_source}")
    ocr = create_ocr()
    allow_manual_prompt = manual_prompt_enabled(args)
    captcha_dir = os.path.abspath(args.captcha_dir)

    if not ocr:
        print(f"⚠️ OCR 不可用，验证码会保存到 {captcha_dir}")
    if allow_manual_prompt:
        print("人工验证码兜底已启用：OCR 失败时会在命令行等待输入。")
    else:
        print("人工验证码兜底未启用：后台/定时任务会只保存截图，不阻塞等待。")

    print("=" * 50)
    print("[AI] 自动登录 - 获取 Cookie")
    print("=" * 50)

    async with async_playwright() as p:
        browser = await p.chromium.launch(**browser_launch_options(
            headless=should_run_headless(args),
            args=["--start-maximized"],
        ))
        ctx = await browser.new_context(no_viewport=True)
        page = await ctx.new_page()
        dialog_messages = []
        page.on("dialog", lambda dialog: asyncio.create_task(handle_login_dialog(dialog_messages, dialog)))

        try:
            print("打开登录页...")
            await page.goto(LOGIN_URL, wait_until="networkidle")
            await page.wait_for_timeout(1500)

            await page.fill('input[name="username"]', username)
            print(f"已填写账号: {username}")

            await page.fill('input[name="password"]', password)
            print("已填写密码")

            logged_in = False
            force_manual_next = False
            for attempt in range(1, 4):
                print(f"\n第 {attempt} 次尝试验证码...")

                if attempt > 1:
                    captcha_img = await page.query_selector('img[src*="captcha"]')
                    if captcha_img:
                        await captcha_img.click()
                        await page.wait_for_timeout(800)

                captcha_text = await get_captcha_text(
                    page,
                    captcha_dir,
                    allow_manual_prompt,
                    force_manual_prompt=force_manual_next,
                )
                if not captcha_text:
                    print("⚠️ 验证码为空，准备重试...")
                    force_manual_next = allow_manual_prompt
                    continue

                captcha_input = page.locator('input[name="captcha"]')
                await captcha_input.fill("")
                await captcha_input.fill(captcha_text)

                keep_check = page.locator('input[name="keeplogin"]')
                if await keep_check.is_visible() and not await keep_check.is_checked():
                    await keep_check.check()
                    print("已勾选记住登录")

                print("提交登录...")
                submit_btn = await page.query_selector('button[type="submit"], input[type="submit"]')
                if submit_btn:
                    await submit_btn.click()
                else:
                    await page.keyboard.press("Enter")

                await page.wait_for_timeout(800)
                page_messages = await collect_login_error_messages(page)
                await page.wait_for_timeout(1200)
                current_url = page.url
                print(f"当前 URL: {current_url}")

                if "login" not in current_url.lower():
                    print("✅ 登录成功")
                    logged_in = True
                    break

                print("⚠️ 登录失败，可能是验证码错误或页面未跳转")
                print_login_failure_details(dialog_messages, page_messages)
                dialog_messages.clear()
                force_manual_next = allow_manual_prompt
                if attempt == 3:
                    print("❌ 已重试 3 次，放弃")

            if not logged_in:
                return 1

            await page.wait_for_timeout(1000)
            cookies = await ctx.cookies()
        finally:
            await browser.close()

    found = {c["name"]: c["value"] for c in cookies if c["name"] in TARGET_COOKIES}
    if len(found) != 2:
        missing = TARGET_COOKIES - set(found.keys())
        print(f"\n⚠️ 缺少 Cookie: {missing}")
        return 1

    keeplogin = found["keeplogin"]
    phpsessid = found["PHPSESSID"]

    print("\n✅ Cookie 获取成功!")
    print(f"   keeplogin = {keeplogin}")
    print(f"   PHPSESSID = {phpsessid}")

    await update_config_cookies(keeplogin, phpsessid)
    print("\n✅ 完成")
    return 0


async def main() -> int:
    args = build_parser().parse_args()
    return await run_login(args)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
