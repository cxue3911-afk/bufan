#!/usr/bin/env python3
"""汇总当天成功发布的文章，并推送到企业微信群。"""
import json
import os
import re
import sys
from datetime import datetime

import requests

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_FILE = os.path.join(BASE_DIR, "last_publish_results.json")
RECORDS_FILE = os.path.join(BASE_DIR, "data", "publish_records.json")
WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=0f3a0168-8bfe-4fe6-9df2-ee9cea5558be"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def group_by_site(results):
    groups = {}
    for item in results:
        tag = item.get("tag", "")
        match = re.search(r"\[(.+?)\]", tag)
        site = match.group(1).strip() if match else "其他"
        groups.setdefault(site, []).append(item)
    return groups


def send_markdown(content):
    resp = requests.post(
        WEBHOOK_URL,
        json={"msgtype": "markdown", "markdown": {"content": content}},
        timeout=10,
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if resp.status_code == 200 and payload.get("errcode") == 0:
        log("企业微信通知发送成功")
    else:
        log(f"企业微信通知发送失败: {resp.status_code} {resp.text[:200]}")
        raise SystemExit(1)


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    results = []
    if os.path.exists(RECORDS_FILE):
        with open(RECORDS_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
        results = [
            item for item in records
            if item.get("success") and str(item.get("publish_time", "")).startswith(today)
        ]
    elif os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            results = json.load(f)
    else:
        log("没有发布结果文件，跳过通知")
        return

    if not results:
        log("本次没有成功发布的文章")
        return

    # 一条通知只展示本次最近一批，避免历史补发累积后超过企业微信 4096 字节限制。
    results = results[-15:]

    groups = group_by_site(results)

    lines = [f"不凡智库自动发布日报"]
    lines.append(f"{today} | 成功发布 {len(results)} 篇")
    lines.append("")

    for site, items in groups.items():
        lines.append(f"**{site}**")
        for item in items:
            title = (item.get("title") or "已发布文章")[:80]
            article_id = item.get("info") or item.get("article_id") or "?"
            category = item.get("category") or ""
            category_suffix = f" [{category}]" if category else ""
            link = f"https://bftank.com/article_detail.html?id={article_id}"
            lines.append(f"- [{title}]({link}) ID:{article_id}{category_suffix}")
        lines.append("")

    lines.append("> 不凡商城后台自动发布")
    content = "\n".join(lines)

    if len(content.encode("utf-8")) > 3900:
        content = content.encode("utf-8")[:3500].decode("utf-8", errors="ignore") + "\n\n> 内容过长，已截断"

    send_markdown(content)


if __name__ == "__main__":
    main()
