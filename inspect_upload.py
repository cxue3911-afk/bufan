from pathlib import Path
import requests
import config
import json
import re

asset = next(Path('data/work_008/data/article_assets').glob('*'))
with asset.open('rb') as handle:
    response = requests.post(
        f'{config.BACKEND_API_URL}/ajax/upload',
        files={'file': (asset.name, handle, 'image/png')},
        cookies=config.COOKIES,
        headers=config.BACKEND_HEADERS,
        timeout=30,
    )
print('status', response.status_code)
print('generic', response.text)

upload_url = f'{config.BACKEND_BASE_URL}/addons/alioss/index/upload'
page = requests.get(
    f'{config.BACKEND_API_URL}/thinktank/question/edit/ids/12785',
    cookies=config.COOKIES,
    headers=config.BACKEND_HEADERS,
    timeout=30,
).text
try:
    page = json.loads(page)
except Exception:
    pass
token_match = re.search(r'"aliosstoken":"([^"]+)"', page)
aliosstoken = token_match.group(1) if token_match else ''
print('token', bool(aliosstoken))
with asset.open('rb') as handle:
    oss_response = requests.post(
        upload_url,
        files={'file': (asset.name, handle, 'image/png')},
        data={'aliosstoken': aliosstoken},
        cookies=config.COOKIES,
        headers=config.BACKEND_HEADERS,
        timeout=30,
    )
print('oss', oss_response.status_code)
print(oss_response.text)
