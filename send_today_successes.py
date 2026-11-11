#!/usr/bin/env python3
"""从当天发布记录中恢复成功文章，并发送企业微信通知。"""
import json
import os
from datetime import datetime

from send_wecom import RESULTS_FILE, main as send_wecom_main

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECORDS_FILE = os.path.join(BASE_DIR, "data", "publish_records.json")


def main():
    if not os.path.exists(RECORDS_FILE):
        print("没有发布记录，跳过兜底通知")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    with open(RECORDS_FILE, "r", encoding="utf-8") as f:
        records = json.load(f)

    seen = set()
    results = []
    for record in records:
        article_id = str(record.get("article_id") or "")
        if not article_id or article_id in seen:
            continue
        if not record.get("success"):
            continue
        if not str(record.get("publish_time") or "").startswith(today):
            continue
        seen.add(article_id)
        results.append(
            {
                "ok": True,
                "tag": "[今日兜底通知]",
                "title": record.get("title") or "已发布文章",
                "info": article_id,
                "category": "",
            }
        )

    if not results:
        print("当天没有成功发布记录，跳过兜底通知")
        return

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    send_wecom_main()


if __name__ == "__main__":
    main()
