#!/usr/bin/env python3
"""每日发布任务：刷新 Cookie、批量发布、推送通知、更新记录。"""

import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON_ALT = os.path.join(BASE_DIR, "ai", ".venv", "Scripts", "python.exe")
    VENV_PYTHON = VENV_PYTHON_ALT if os.path.exists(VENV_PYTHON_ALT) else sys.executable

COOKIE_SCRIPT = os.path.join(BASE_DIR, "get_cookies.py")
PUBLISH_SCRIPT = os.path.join(BASE_DIR, "batch_publish.py")
WECOM_SCRIPT = os.path.join(BASE_DIR, "send_wecom.py")
UPDATE_SCRIPT = os.path.join(BASE_DIR, "update_bf_question_once.py")
COOKIE_VALIDATE_URL = "https://admin.bufanshop.kcshop.vip/zonWwkqVHm.php/thinktank/question/add"


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def is_interactive_terminal() -> bool:
    return bool(sys.stdin and sys.stdin.isatty() and sys.stdout and sys.stdout.isatty())


def run_streaming(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    """运行子进程并实时转发输出，同时保留输出用于失败诊断。"""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
    )

    output_queue: queue.Queue[str] = queue.Queue()
    lines: list[str] = []

    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output_queue.put(line)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    start = time.monotonic()
    timed_out = False
    while process.poll() is None:
        while True:
            try:
                line = output_queue.get_nowait()
            except queue.Empty:
                break
            lines.append(line)
            print(line, end="", flush=True)

        if timeout is not None and time.monotonic() - start > timeout:
            timed_out = True
            process.kill()
            break

        time.sleep(0.1)

    reader.join(timeout=2)
    while True:
        try:
            line = output_queue.get_nowait()
        except queue.Empty:
            break
        lines.append(line)
        print(line, end="", flush=True)

    if timed_out:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        returncode = process.returncode if process.returncode is not None else 124
        log(f"子任务超时，已停止: {' '.join(cmd)}")
    else:
        returncode = process.returncode if process.returncode is not None else process.wait()

    return subprocess.CompletedProcess(cmd, returncode, "".join(lines), "")


def python_cmd(script: str, *args: str) -> list[str]:
    return [VENV_PYTHON, "-u", script, *args]


def run_cookie_refresh() -> subprocess.CompletedProcess:
    """交互式执行时直连终端，方便 OCR 失败后手动输入验证码。"""
    cmd = python_cmd(COOKIE_SCRIPT, "--headless")
    if is_interactive_terminal():
        cmd.append("--manual-captcha")
        return subprocess.run(cmd, timeout=None)

    return run_streaming(cmd, timeout=60)


def existing_cookie_is_usable() -> bool:
    """登录刷新失败时，验证配置中的旧 Cookie 是否仍可用于发文。"""
    try:
        import requests
        import config

        response = requests.get(
            COOKIE_VALIDATE_URL,
            headers=config.BACKEND_HEADERS,
            cookies=config.COOKIES,
            timeout=20,
        )
        final_url = response.url.lower()
        return (
            response.status_code == 200
            and "index/login" not in final_url
            and "__token__" in response.text
        )
    except Exception as exc:
        log(f"旧 Cookie 校验失败: {exc}")
        return False


def print_process_output(result: subprocess.CompletedProcess) -> None:
    out = (result.stdout or "") + (result.stderr or "")
    if out.strip():
        print(out, flush=True)


def main() -> int:
    log("=" * 50)
    log("每日自动发布任务启动")
    log(f"Python: {VENV_PYTHON}")
    log("=" * 50)

    log("Step 1/4: 登录获取 Cookie...")
    r1 = run_cookie_refresh()
    if r1.returncode != 0:
        log("Cookie 刷新失败，开始校验现有 Cookie...")
        if existing_cookie_is_usable():
            log("现有 Cookie 仍有效，继续执行本轮发布。")
        else:
            log("Cookie 获取失败且现有 Cookie 已失效")
            log("已停止后续发布步骤，避免使用过期 Cookie 继续发布。")
            return 1
    else:
        log("Cookie 已更新")

    log("Step 2/4: 运行批量发布...")
    # AI 优化/分类属于增强能力；外部模型不可用时必须保留原文继续发布，不能让日报任务整批失败。
    os.environ["BUFAN_SKIP_AI_OPTIMIZE"] = "1"
    os.environ["BUFAN_SKIP_AI_CLASSIFY"] = "1"
    os.environ["BUFAN_SKIP_AI_SORT"] = "1"
    r2 = run_streaming(
        python_cmd(
            PUBLISH_SCRIPT,
            "--zidonghua", "7",
            "--iotworld", "4",
            "--ithome", "4",
            "--zhidx", "3",
            "--qbitai", "2",
            "--tmtpost", "0",
        ),
        # 15 篇文章并行抓取、上传图片和提交审核，给足整体执行时间。
        timeout=2400,
    )
    if r2.returncode != 0:
        log(f"发布任务退出码: {r2.returncode}")
    else:
        log("发布任务完成")

    log("Step 3/4: 发送通知到企业微信群...")
    r3 = run_streaming(python_cmd(WECOM_SCRIPT), timeout=30)
    if r3.returncode != 0:
        log(f"通知任务退出码: {r3.returncode}")

    log("Step 4/4: 更新 bf_question 记录...")
    r4 = run_streaming(python_cmd(UPDATE_SCRIPT), timeout=60)
    if r4.returncode != 0:
        log(f"记录更新任务退出码: {r4.returncode}")

    log("=" * 50)
    log("每日自动发布任务结束")
    log("=" * 50)

    return 0 if r1.returncode == 0 and r2.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
