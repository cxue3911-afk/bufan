import html
import json
import re
import requests
import config

article_id = __import__('sys').argv[1]
session = requests.Session()
session.cookies.update(config.COOKIES)
url = f'{config.BACKEND_API_URL}/thinktank/question/edit/ids/{article_id}'
response = session.get(url, headers=config.BACKEND_HEADERS, timeout=20)
text = response.text
try:
    decoded = json.loads(text)
    if isinstance(decoded, str):
        text = decoded
except Exception:
    pass
match = re.search(r'<textarea[^>]*name="row\[content\]"[^>]*>(.*?)</textarea>', text, re.S)
content = html.unescape(match.group(1)) if match else ''
images = re.findall(r'<img[^>]+src="([^"]+)', content, re.I)
print(f'edit_status={response.status_code} html_len={len(text)} images={len(images)}')
for image in images[:5]:
    try:
        check = session.get(image, timeout=20)
        print(check.status_code, check.headers.get('content-type'), len(check.content), image)
    except Exception as exc:
        print('ERROR', image, exc)
