import html
import json
import os
import re

import markdown
import requests

import config

BASE = config.BACKEND_BASE_URL
API = config.BACKEND_API_URL
DATA = os.path.join(os.path.dirname(__file__), 'data', 'repair_12739b')
ARTICLE_ID = '12739'

session = requests.Session()
session.cookies.update(config.COOKIES)
headers = config.BACKEND_HEADERS.copy()

edit_url = f'{API}/thinktank/question/edit/ids/{ARTICLE_ID}'
page = session.get(edit_url, headers=headers, timeout=30)
page.raise_for_status()
raw = page.json() if page.text.startswith('"') else page.text
token_match = re.search(r'name="__token__" value="([^"]+)"', raw)
if not token_match:
    raise RuntimeError(f'无法获取编辑 token: {raw[:200]}')
token = token_match.group(1)

with open(os.path.join(DATA, 'article.md'), encoding='utf-8') as f:
    md_text = f.read()
title = md_text.splitlines()[0].removeprefix('# ').strip()
assets = os.path.join(DATA, 'article_assets')
uploaded = {}

for ref in sorted(set(re.findall(r'!\[[^]]*\]\(([^)]+)\)', md_text))):
    path = os.path.join(DATA, ref.replace('/', os.sep))
    if not os.path.exists(path):
        print(f'缺少图片: {ref}')
        continue
    with open(path, 'rb') as f:
        resp = session.post(
            f'{API}/ajax/upload',
            files={'file': (os.path.basename(path), f, 'application/octet-stream')},
            headers=headers,
            timeout=60,
        )
    data = resp.json()
    if data.get('code') != 1:
        raise RuntimeError(f'图片上传失败 {ref}: {resp.text[:300]}')
    url = data['data']['url']
    if not url.startswith('http'):
        url = BASE + url
    uploaded[ref] = url
    print(f'图片上传成功: {ref} -> {url}')

content = markdown.markdown(md_text, extensions=['extra'])
for ref, url in uploaded.items():
    content = content.replace(f'src="{ref}"', f'src="{url}"')

payload = {
    '__token__': token,
    'row[class_id]': '142',
    'row[question_type]': '2',
    'row[title]': title,
    'row[sku_id]': '',
    'row[content]': content,
    'row[tag_ids]': '14,16,17',
    'row[reward]': '0',
    'row[is_open]': '1',
    'row[status]': '1',
    'row[is_collect]': '0',
}
result = session.post(edit_url, data=payload, headers={**headers, 'Referer': edit_url}, timeout=60)
print(f'编辑响应: {result.status_code} {result.text[:500]}')
if result.status_code != 200 or '"code":1' not in result.text:
    raise RuntimeError('文章更新失败')
print(f'文章 {ARTICLE_ID} 图片已补传并更新')
