#!/usr/bin/env python3
"""
并行发布：同时处理各平台文章，每篇使用独立的隔离工作目录。
支持失败自动替补，每篇文章（slot）最多重试 3 次。

用法:
    python batch_publish.py                             # 默认各平台 3 篇
    python batch_publish.py --zidonghua 3 --iotworld 0  # 只发自动化网 3 篇
    python batch_publish.py --zidonghua 5               # 自动化网 5 篇，物联网世界默认 3 篇

依赖:
    pip install playwright openai requests
"""
import sys, os, re, json, time, subprocess, json, threading, argparse, queue
from datetime import date, timedelta
sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# 文件写入锁（多线程并发安全）
_record_lock = threading.Lock()
_log_lock = threading.Lock()
# venv 路径（一次解析，避免多线程竞态）
_resolved_venv = None
_resolved_venv_lock = threading.Lock()

# ── 从 config.py 导入配置 ────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from browser_runtime import browser_launch_options
from config import (
    WORK_DIR, DATA_DIR, YOUHUA_DIR, VENV_PYTHON, FALLBACK_VENV,
    SITES, RECORDS_FILE, OPTIMIZE_SCRIPT,
    MAX_CANDIDATES, MAX_CONSECUTIVE_FAILURES, MAX_ARTICLE_AGE_DAYS,
    TIMEOUT_STEP1, TIMEOUT_OPTIMIZE, TIMEOUT_PUBLISH,
    MAX_WORKERS
)

os.makedirs(DATA_DIR, exist_ok=True)

# 内容质量硬过滤：日报不再选展会、峰会、论坛、会议、路演等活动宣传稿。
EVENT_TITLE_KEYWORDS = (
    '展会', '博览会', '峰会', '论坛', '会议', '大会', '路演', '活动',
    '开幕', '闭幕', '展览', '参展', '同期活动', '高峰', '年会', '研讨会',
    '招展', '中博会', '发布会', '路演厅', '会见', '接待', '科创路演',
)
EVENT_URL_MARKERS = ('/exhibition/', '/event/', '/forum/', '/conference/')


def is_event_article(article: dict) -> bool:
    url = (article.get('url') or '').lower()
    if any(marker in url for marker in EVENT_URL_MARKERS):
        return True
    title = article.get('title', '')
    text = (title or '').replace(' ', '').replace('｜', '')
    return any(keyword in text for keyword in EVENT_TITLE_KEYWORDS)


TECHNICAL_TITLE_KEYWORDS = (
    '芯片', '半导体', '传感器', 'PLC', '工业', '自动化', '机器人', '设备',
    '材料', '工艺', '制造', '产线', '电源', '通信', 'AI', '模型', '算法',
    '系统', '平台', '方案', '技术', '应用', '量产', '研发', '测试', '软件',
)
PRODUCT_TITLE_KEYWORDS = ('发布', '推出', '亮相', '落地', '合作', '升级', '突破', '交付', '上市')


def local_relevance_score(article: dict) -> int:
    """无外部 AI 时的本地排序：工程技术/产品优先，活动稿降权但不删除。"""
    title = (article.get('title') or '').replace(' ', '')
    score = sum(3 for word in TECHNICAL_TITLE_KEYWORDS if word.lower() in title.lower())
    score += sum(2 for word in PRODUCT_TITLE_KEYWORDS if word in title)
    if is_event_article(article):
        score -= 6
    return score


def local_rank_articles(articles: list[dict]) -> list[dict]:
    return sorted(articles, key=local_relevance_score, reverse=True)


def parse_listing_date(value: str):
    """解析列表页常见日期格式，返回 date；无法确认时返回 None。"""
    text = re.sub(r'\s+', ' ', (value or '').strip())
    if not text:
        return None
    today = date.today()
    if '前天' in text:
        return today - timedelta(days=2)
    if '昨天' in text:
        return today - timedelta(days=1)
    if '今天' in text or '刚刚' in text:
        return today

    match = re.search(r'(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})', text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    match = re.search(r'(?<!\d)(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?', text)
    if match:
        try:
            result = date(today.year, int(match.group(1)), int(match.group(2)))
            if result > today:
                result = date(today.year - 1, result.month, result.day)
            return result
        except ValueError:
            return None
    return None


def filter_recent_articles(articles: list[dict], site_name: str) -> list[dict]:
    """只保留今天、昨天、前天的文章；日期缺失也不放行。"""
    cutoff = date.today() - timedelta(days=MAX_ARTICLE_AGE_DAYS)
    recent = []
    skipped_old = 0
    skipped_unknown = 0
    for article in articles:
        published = parse_listing_date(article.get('time', ''))
        if published is None:
            skipped_unknown += 1
            continue
        if cutoff <= published <= date.today():
            article = dict(article)
            article['published_date'] = published.isoformat()
            recent.append(article)
        else:
            skipped_old += 1
    log(f'{site_name} 时效过滤：保留 {len(recent)} 篇，跳过旧文 {skipped_old} 篇，跳过无日期 {skipped_unknown} 篇')
    return recent


def find_stale_lead_date(md_file: str):
    """检查正文开头的主事件日期；避免今天发布、但正文明确报道数日前活动的稿件混入。"""
    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            text = f.read(1800)
    except OSError:
        return None
    # 只检查标题后的开头部分，避免把正文中的历史背景日期误判为文章日期。
    body = text.split('\n', 1)[1] if '\n' in text else text
    matches = re.findall(r'20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?', body[:1200])
    cutoff = date.today() - timedelta(days=MAX_ARTICLE_AGE_DAYS)
    for value in matches:
        parsed = parse_listing_date(value)
        if parsed is not None and parsed < cutoff:
            return value
    return None


# ── 工具函数 ──────────────────────────────────────────

def log(msg):
    ts = time.strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    with _log_lock:
        print(line, flush=True)
        with open(os.path.join(DATA_DIR, 'batch_publish.log'), 'a', encoding='utf-8') as f:
            f.write(f'{line}\n')


def run_streamed(cmd, timeout, tag):
    """运行子进程并实时显示输出，返回值保持 subprocess.CompletedProcess 形态。"""
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
        env=env,
    )

    output_queue = queue.Queue()
    lines = []

    def read_output():
        assert proc.stdout is not None
        for line in proc.stdout:
            output_queue.put(line)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    start = time.monotonic()
    timed_out = False

    while proc.poll() is None:
        while True:
            try:
                line = output_queue.get_nowait()
            except queue.Empty:
                break
            lines.append(line)
            text = line.rstrip()
            if text:
                display = text if len(text) <= 2000 else text[:1200] + ' ...[输出已截断]... ' + text[-300:]
                log(f'{tag} | {display}')

        if time.monotonic() - start > timeout:
            timed_out = True
            proc.kill()
            break

        time.sleep(0.1)

    reader.join(timeout=2)
    while True:
        try:
            line = output_queue.get_nowait()
        except queue.Empty:
            break
        lines.append(line)
        text = line.rstrip()
        if text:
            display = text if len(text) <= 2000 else text[:1200] + ' ...[输出已截断]... ' + text[-300:]
            log(f'{tag} | {display}')

    if timed_out:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        returncode = proc.returncode if proc.returncode is not None else 124
        log(f'{tag} 子任务超时，已停止')
    else:
        returncode = proc.returncode if proc.returncode is not None else proc.wait()

    return subprocess.CompletedProcess(cmd, returncode, ''.join(lines), '')


def resolve_venv():
    """解析 AI 优化包的 Python 路径（线程安全，仅解析一次）"""
    global _resolved_venv, VENV_PYTHON
    with _resolved_venv_lock:
        if _resolved_venv is not None:
            return _resolved_venv
        if os.path.exists(VENV_PYTHON):
            _resolved_venv = VENV_PYTHON
        elif os.path.exists(FALLBACK_VENV):
            _resolved_venv = FALLBACK_VENV
            log(f'  [venv] 使用备用: {_resolved_venv}')
        else:
            _resolved_venv = sys.executable
            log(f'  [venv] 未找到专用 venv，使用系统 Python')
    return _resolved_venv


DESKTOP_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36'


def _extract_links_js():
    """返回通用的 JS 提取链接代码（在 page.evaluate 中执行）"""
    return r'''
        (opts) => {
            const sel = opts.selector;
            const match = opts.linkMatch;
            const containers = sel ? document.querySelectorAll(sel) : [document];
            if (!containers || containers.length === 0) return [];
            const seen = new Set();
            const results = [];
            for (const container of containers) {
                for (const a of container.querySelectorAll('a')) {
                    const href = (a.href || '').split('#')[0];
                    if (href.includes('mobile.')) continue;
                    const title = (a.title || a.textContent || (a.querySelector('img')?.alt) || '').trim();
                    let matched = false;
                    if (match === 'viewNews') {
                        matched = href.includes('/View.aspx/News-');
                    } else if (match === 'endsWithShtml') {
                        matched = href.endsWith('.shtml');
                    } else if (match === 'ithome') {
                        matched = /^https?:\/\/(?:www\.|it\.|m\.)?ithome\.com\/\d+\/\d+\/\d+\.(?:htm|html)$/.test(href);
                    } else if (match === 'zhidx') {
                        matched = /(^|\.)zhidx\.com$/.test(new URL(href).hostname)
                            && /\/p\/\d+\.html$/.test(new URL(href).pathname);
                    } else if (match === 'qbitai') {
                        matched = /^https?:\/\/(?:www\.)?qbitai\.com\/\d{4}\/\d{2}\/\d+\.html$/.test(href);
                    } else if (match === 'tmtpost') {
                        matched = /^https?:\/\/(?:www\.)?tmtpost\.com\/\d+\.html$/.test(href);
                    } else {
                        matched = href.endsWith('.html');
                    }
                    if (matched && title.length > 5 && !seen.has(href)) {
                        seen.add(href);
                        let timeText = '';
                        let parent = a.parentElement;
                        for (let d = 0; d < 5 && parent; d++) {
                            const dateEl = parent.querySelector('.date, time, [class*="date"]');
                            if (dateEl) {
                                timeText = dateEl.textContent.trim();
                                break;
                            }
                            // 部分来源（例如 IT之家）把“今日 08:21”直接写在列表项文本中，
                            // 没有 date/time 标签；只在较小的列表项中读取，避免拿到整页日期。
                            if ((parent.children?.length || 0) <= 8) {
                                const itemText = (parent.innerText || '').trim();
                                const inlineDate = itemText.match(/(?:今天|昨天|前天)\s*\d{1,2}:\d{2}|20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}|\d{1,2}[月./-]\d{1,2}日?/);
                                if (inlineDate) {
                                    timeText = inlineDate[0];
                                    break;
                                }
                            }
                            parent = parent.parentElement;
                        }
                        results.push({url: href, title: title, time: timeText});
                    }
                }
            }
            return results;
        }
    '''


def get_article_links_local(site_key):
    """获取首页文章列表（不加翻页），后续不够再补"""
    from playwright.async_api import async_playwright
    import asyncio

    site = SITES[site_key]
    selector = site.get('selector', 'a')
    link_match = site.get('link_match', 'endsWithHtml')

    async def _run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            all_links = []
            seen = set()

            for idx, url in enumerate(site['listing_urls'], 1):
                try:
                    page = await browser.new_page(user_agent=DESKTOP_UA)
                except Exception as e:
                    log(f'  [获取] ⚠️ 浏览器页面异常，自动重启: {e}')
                    browser = await p.chromium.launch(**browser_launch_options())
                    page = await browser.new_page(user_agent=DESKTOP_UA)
                try:
                    log(f'  [获取] 打开栏目页 {idx}/{len(site["listing_urls"])}: {url}')
                    await page.goto(url, wait_until='domcontentloaded', timeout=60000)
                    await page.wait_for_timeout(3000)

                    links = await page.evaluate(
                        _extract_links_js(),
                        {"selector": selector, "linkMatch": link_match}
                    )
                    log(f'  [获取] 栏目页 {idx}/{len(site["listing_urls"])} 提取 {len(links)} 条链接')

                    added = 0
                    for link in links:
                        if link['url'] not in seen:
                            seen.add(link['url'])
                            all_links.append(link)
                            added += 1
                    log(f'  [获取] 栏目页 {idx}/{len(site["listing_urls"])} 新增 {added} 条，累计 {len(all_links)} 条')

                except Exception as e:
                    log(f'  [获取] ⚠️ 栏目页获取失败: {url} - {e}')
                finally:
                    await page.close()

            await browser.close()

            # 按时间排序（如果配置了）
            if site.get('sort_by_time'):
                def _parse_date(link):
                    t = link.get('time', '')
                    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', t)
                    if m:
                        return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
                    return '0000-00-00'
                all_links.sort(key=_parse_date, reverse=True)

            log(f'  [获取] 首页共 {len(all_links)} 篇（含已发布）')
            return all_links

    return asyncio.run(_run())


def get_more_articles_local(site_key, existing_urls, need_count):
    """翻页获取更多文章，直到达到 need_count 或翻完"""
    from playwright.async_api import async_playwright
    import asyncio

    site = SITES[site_key]
    selector = site.get('selector', 'a')
    link_match = site.get('link_match', 'endsWithHtml')
    MAX_PAGES = 5

    async def _run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_launch_options())
            new_links = []
            seen = set(existing_urls)  # 已有 URL 不再重复

            for idx, url in enumerate(site['listing_urls'], 1):
                if len(new_links) >= need_count:
                    break

                try:
                    page = await browser.new_page(user_agent=DESKTOP_UA)
                except Exception as e:
                    log(f'  [翻页] ⚠️ 浏览器页面异常，自动重启: {e}')
                    browser = await p.chromium.launch(**browser_launch_options())
                    page = await browser.new_page(user_agent=DESKTOP_UA)
                try:
                    # 使用站点配置的分页格式，没有则 fallback 到通用格式
                    page_format = site.get('page_url_format')
                    for page_num in range(2, MAX_PAGES + 1):  # 从第2页开始
                        if len(new_links) >= need_count:
                            break

                        if page_format:
                            page_url = page_format.replace('{page}', str(page_num))
                        else:
                            base = url.rstrip('/')
                            page_url = f'{base}/index_{page_num}.html'
                        log(f'  [翻页] 打开栏目页 {idx}/{len(site["listing_urls"])} 第 {page_num} 页: {page_url}')
                        await page.goto(page_url, wait_until='domcontentloaded', timeout=60000)
                        await page.wait_for_timeout(2000)

                        links = await page.evaluate(
                            _extract_links_js(),
                            {"selector": selector, "linkMatch": link_match}
                        )
                        log(f'  [翻页] 第 {page_num} 页提取 {len(links)} 条链接')
                        if not links:
                            break  # 空页 = 到底了

                        added = 0
                        for link in links:
                            if link['url'] not in seen:
                                seen.add(link['url'])
                                new_links.append(link)
                                added += 1
                        log(f'  [翻页] 第 {page_num} 页新增 {added} 条，补获累计 {len(new_links)} 条')
                        if added == 0:
                            break  # 该页全部已见过，停
                except Exception as e:
                    log(f'  [翻页] ⚠️ {url} 翻页失败: {e}')
                finally:
                    await page.close()

            await browser.close()

            # 如果配置了按时间排序，补回来的也要排序
            if site.get('sort_by_time'):
                def _parse_date(link):
                    t = link.get('time', '')
                    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', t)
                    if m:
                        return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
                    return '0000-00-00'
                new_links.sort(key=_parse_date, reverse=True)

            log(f'  [翻页] 补获 {len(new_links)} 篇新文章')
            return new_links

    return asyncio.run(_run())


def ai_sort_articles_local(articles, site_name):
    """用 AI 对文章排序"""
    from openai import OpenAI

    if not articles:
        return []

    config_path = os.path.join(YOUHUA_DIR, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    text_cfg = cfg['text']
    client = OpenAI(api_key=text_cfg['api_key'], base_url=text_cfg['base_url'], timeout=text_cfg.get('timeout_s', 300))

    articles_text = '\n'.join([f"{i+1}. {a['title']}" for i, a in enumerate(articles)])

    prompt = f"""你是内容编辑，请从{site_name}以下文章中，按与"产品发布、行业解决方案、技术最新应用、行业资讯"主题的相关度从高到低排序。

要求：
- 排除纯政策文件、政府会议、人事任命等非技术内容
- 排除厂家推荐、厂家采购指南、检测仪器推荐等广告/推广类文章
- 优先选有技术细节、产品介绍、方案解析的文章
- 只输出 JSON，不要其他任何内容

文章列表：
{articles_text}

返回格式（只返回JSON数组，按符合程度从高到低排序，不要其他内容）：
[{{"index": 序号, "score": 相关度评分0-100, "category": "产品发布/行业解决方案/技术最新应用/行业资讯"}}]
"""

    log(f'  [AI排序] 排序 {site_name} {len(articles)} 篇文章，prompt 长度: {len(prompt)} 字符')

    # 排序仅用于优先级，不应阻塞整批发布；服务异常时快速回退到原始顺序。
    MAX_RETRIES = 1
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log(f'  [AI排序] 第 {attempt}/{MAX_RETRIES} 次调用...')
            resp = client.chat.completions.create(
                model=text_cfg['model'],
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                timeout=15
            )
            break  # 成功则跳出重试循环
        except Exception as e:
            log(f'  [AI排序] 第 {attempt} 次调用失败: {e}')
            if attempt < MAX_RETRIES:
                wait_s = attempt * 5  # 5s, 10s 递增等待
                log(f'  [AI排序] {wait_s}s 后重试...')
                time.sleep(wait_s)
            else:
                log(f'  [AI排序] 已重试 {MAX_RETRIES} 次，全部失败')
                return []

    raw = resp.choices[0].message.content.strip()
    # 提取 JSON（AI 可能包在 ```json 代码块里）
    if '```' in raw:
        raw = re.sub(r'^.*?```(?:json)?\s*', '', raw, flags=re.DOTALL)
        raw = re.sub(r'\s*```.*$', '', raw, flags=re.DOTALL)
    raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        log(f'  [AI排序] 返回内容非合法 JSON，尝试提取...')
        # 尝试在内容中找 JSON 数组
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group())
            except json.JSONDecodeError:
                log(f'  [AI排序] 无法解析 AI 返回: {raw[:200]}')
                return []
        else:
            log(f'  [AI排序] 无法解析 AI 返回: {raw[:200]}')
            return []

    if isinstance(result, dict):
        items = result.get('articles', result.get('selections', result.get('results', result.get('sorted', []))))
    else:
        items = result

    if not isinstance(items, list):
        items = [items]

    items.sort(key=lambda x: x.get('score', 0) if isinstance(x.get('score'), (int, float)) else 0, reverse=True)
    log(f'  [AI排序] 完成，有效 {len(items)} 篇')
    return items


def load_published_urls():
    """只加载成功发布的 URL；失败任务下次应继续重试。"""
    if not os.path.exists(RECORDS_FILE):
        return set()
    try:
        with open(RECORDS_FILE, 'r', encoding='utf-8') as f:
            records = json.load(f)
        return {r['url'] for r in records if r.get('success') is True}
    except (json.JSONDecodeError, IOError):
        return set()


def save_record(url, title, success, error_reason='', article_id=''):
    """保存发布记录到 JSON（线程安全）"""
    with _record_lock:
        records = []
        if os.path.exists(RECORDS_FILE):
            try:
                with open(RECORDS_FILE, 'r', encoding='utf-8') as f:
                    records = json.load(f)
            except (json.JSONDecodeError, IOError):
                records = []

        records.append({
            'url': url,
            'title': title,
            'publish_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'success': success,
            'error_reason': error_reason,
            'article_id': str(article_id) if article_id else ''
        })

        with open(RECORDS_FILE, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)


# ─── 单篇文章处理流水线 ────────────────────────────────

def process_one(params):
    """处理一篇文章的完整流水线（在独立工作目录中）"""
    url, step1_script, site_name, tag, step2_script, work_idx = params
    work_dir = os.path.join(DATA_DIR, f'work_{work_idx:03d}')
    data_dir = os.path.join(work_dir, 'data')
    os.makedirs(data_dir, exist_ok=True)

    md_file = os.path.join(data_dir, 'article.md')
    md_out = os.path.join(data_dir, 'article_out.md')

    log(f'{tag} 开始处理')

    # ── Step 1: 抓取 ──
    log(f'{tag} step1 抓取...')
    r = run_streamed(
        [sys.executable, '-u', step1_script, url, '--data-dir', data_dir],
        timeout=TIMEOUT_STEP1,
        tag=f'{tag} step1',
    )
    out = (r.stdout or '') + (r.stderr or '')
    if r.returncode != 0 or not os.path.exists(md_file) or os.path.getsize(md_file) < 50:
        err_msg = out[-500:] if len(out) > 500 else out
        log(f'{tag} step1 失败: {err_msg}')
        return False, url, '', 'step1_failed'

    # 从 md 文件读标题
    with open(md_file, 'r', encoding='utf-8') as f:
        first_line = f.readline().strip()
    title = first_line.replace('# ', '').strip() if first_line.startswith('# ') else url

    log(f'{tag} 标题: {title[:50]}')

    stale_lead_date = find_stale_lead_date(md_file)
    if stale_lead_date:
        log(f'{tag} 正文主事件日期 {stale_lead_date} 超出近三天窗口，跳过发布')
        return False, url, title, 'content_date_old'

    # ── Step 2: AI 优化 ──
    # 发布任务可通过环境变量跳过图片/文本优化，避免外部 AI 服务故障阻塞整批发布。
    if os.environ.get('BUFAN_SKIP_AI_OPTIMIZE') == '1':
        import shutil
        shutil.copy2(md_file, md_out)
        log(f'{tag} 跳过 AI 优化，保留原文和原图')
    else:
        venv_py = resolve_venv()
        log(f'{tag} AI 优化中...')
        r = run_streamed(
            [venv_py, '-u', os.path.join(YOUHUA_DIR, OPTIMIZE_SCRIPT),
             '--config', os.path.join(YOUHUA_DIR, 'config.json'),
             '--input', md_file,
             '--output', md_out,
             '--assets-dir', os.path.join(data_dir, 'article_assets')],
            timeout=TIMEOUT_OPTIMIZE,
            tag=f'{tag} AI',
        )
        out = (r.stdout or '') + (r.stderr or '')
        if 'BLOCKED' in out or '"blocked": true' in out:
            log(f'{tag} 审核未通过（含二维码/联系方式）')
            return False, url, title, 'audit_blocked'
        if '__AI_ANALYZE_FAILED__' in (r.stdout or ''):
            log(f'{tag} 图片AI分析失败，已记录到排除列表')
            return False, url, title, 'ai_analyze_failed'
        if r.returncode != 0 or not os.path.exists(md_out):
            err_msg = out[-500:] if len(out) > 500 else out
            log(f'{tag} 优化失败: {err_msg}')
            return False, url, title, 'optimize_failed'

    # 优化后的内容覆盖原文件（保持 step2 读取逻辑一致）
    if os.path.exists(md_out):
        import shutil
        shutil.copy2(md_out, md_file)

    # 从优化后的文件重新读取标题（AI 改写后的标题）
    with open(md_file, 'r', encoding='utf-8') as f:
        first_line = f.readline().strip()
    title = first_line.replace('# ', '').strip() if first_line.startswith('# ') else title

    log(f'{tag} 优化完成')

    # ── Step 3: 发布 ──
    log(f'{tag} 发布中...')
    r = run_streamed(
        [sys.executable, '-u', step2_script, '--data-dir', data_dir],
        timeout=TIMEOUT_PUBLISH,
        tag=f'{tag} 发布',
    )
    out = (r.stdout or '') + (r.stderr or '')
    if r.returncode != 0:
        log(f'{tag} 发布失败: {out[:200]}')
        return False, url, title, 'publish_failed'

    m = re.search(r'ID=([\d?]+)', out)
    article_id = m.group(1) if m else ''
    if not article_id:
        log(f'{tag} 发布失败：未获取到发布 ID')
        return False, url, title, 'publish_no_id'

    log(f'{tag} 发布成功 ID={article_id}')
    m_cat = re.search(r'分类=([\u4e00-\u9fff\w]+)', out)
    category = m_cat.group(1) if m_cat else ''
    return True, url, title, article_id, category


# ─── 错误码 → 中文描述 ────────────────────────────────

ERROR_REASONS = {
    'audit_blocked': '审核未通过（含二维码/联系方式）',
    'optimize_failed': 'AI优化失败',
    'ai_analyze_failed': '图片AI分析失败',
    'publish_no_id': '发布后未获取到文章ID',
    'publish_failed': '发布请求失败',
    'step1_failed': '文章抓取失败',
    'content_date_old': '正文主事件日期超过三天',
}


# ─── 屏幕显示宽度工具 ──────────────────────────────────

def cjk_width(s):
    """计算字符串的近似显示宽度（CJK 字符按 2 列算）"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


# ─── 主流程 ────────────────────────────────────────────

def main():
    # ── 解析 CLI 参数 ──
    parser = argparse.ArgumentParser(description='批量发布文章到不凡商城后台')
    parser.add_argument('--zidonghua', type=int, default=None, help='自动化网发布篇数（默认3）')
    parser.add_argument('--iotworld', type=int, default=None, help='物联网世界发布篇数（默认3）')
    parser.add_argument('--ithome', type=int, default=None, help='IT之家AI发布篇数（默认3）')
    parser.add_argument('--zhidx', type=int, default=None, help='智东西发布篇数（默认2）')
    parser.add_argument('--qbitai', type=int, default=None, help='量子位发布篇数（默认2）')
    parser.add_argument('--tmtpost', type=int, default=None, help='钛媒体发布篇数（默认2）')
    args = parser.parse_args()
    # 按 CLI 参数覆盖各平台篇数
    override_counts = {}
    if args.zidonghua is not None:
        override_counts['zidonghua'] = args.zidonghua
    if args.iotworld is not None:
        override_counts['iotworld'] = args.iotworld
    for site_key in ('ithome', 'zhidx', 'qbitai', 'tmtpost'):
        value = getattr(args, site_key, None)
        if value is not None:
            override_counts[site_key] = value

    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

    log('=' * 50)
    log('并行发布 batch_publish 启动')
    log('=' * 50)

    published_urls = load_published_urls()
    log(f'已发布记录: {len(published_urls)} 条')

    # ── 构建每站队列（串行获取 + AI排序）──
    site_queues = {}  # site_key -> {queue, success_count, in_flight, slots, ...}
    for site_key, site in SITES.items():
        log(f'\n--- {site["name"]} ---')
        publish_need = override_counts.get(site_key, site.get('articles_per_site', 3))
        if publish_need <= 0:
            log(f'--{site_key} 0 篇，跳过此平台')
            continue

        log(f'获取文章列表（排除已发布 {len(published_urls)} 条）...')

        articles = filter_recent_articles(get_article_links_local(site_key), site['name'])
        if not articles:
            log(f'未获取到文章')
            continue

        # 排除已发布
        fresh = [a for a in articles if a['url'] not in published_urls]
        filtered_events = len([a for a in fresh if is_event_article(a)])
        if filtered_events:
            log(f'活动类候选降权而非删除: {filtered_events} 篇')
        if not fresh:
            log(f'全部文章都已发布过')
            continue

        # 不够 20 篇？翻页补
        if len(fresh) < MAX_CANDIDATES:
            need_more = MAX_CANDIDATES - len(fresh)
            log(f'candidate articles less than {MAX_CANDIDATES}, fetch {need_more} more...')
            more = get_more_articles_local(site_key, published_urls | {a['url'] for a in fresh}, need_more)
            fresh.extend(filter_recent_articles(more, site['name']))

        # 截取前 MAX_CANDIDATES 篇
        if len(fresh) > MAX_CANDIDATES:
            fresh = fresh[:MAX_CANDIDATES]

        log(f'候选文章: {len(fresh)} 篇')

        if os.environ.get('BUFAN_SKIP_AI_SORT') == '1':
            log('跳过 AI 排序，使用本地工程相关性排序')
            sorted_articles = local_rank_articles(fresh)
        else:
            sorted_articles = ai_sort_articles_local(fresh, site['name'])
        if not sorted_articles:
            log(f'AI 排序失败，使用原始顺序')
            sorted_articles = local_rank_articles(fresh)
        else:
            # AI 只返回 {index, score, category}，映射回完整文章数据
            idx_map = {i: a for i, a in enumerate(fresh)}
            enriched = []
            for item in sorted_articles:
                idx = item.get('index', 0) - 1  # AI 从 1 开始编号
                if idx in idx_map:
                    a = dict(idx_map[idx])
                    enriched.append(a)
            if enriched:
                sorted_articles = enriched
            else:
                log(f'AI 排序结果无法映射，使用原始顺序')
                sorted_articles = local_rank_articles(fresh)

        site_queues[site_key] = {
            'queue': list(sorted_articles),
            'need': publish_need,
            'success_count': 0,
            'in_flight': 0,
            # 每个 slot 表示一个"想要发布成功"的位置
            # state: 'ready'(可提交) / 'in_flight'(处理中) / 'done'(已成功) / 'dead'(重试3次放弃)
            # retries: 已重试次数（0 = 首次提交，1 = 第1次重试，...）
            'slots': [{'state': 'ready', 'retries': 0} for _ in range(publish_need)],
            'name': site['name'],
            'step1_script': site['step1'],
            'step2_script': os.path.join(WORK_DIR, 'step2', 'step2_publish.py'),
        }

    if not site_queues:
        log('\n没有需要处理的站点')
        return

    # 计算站点名对齐宽度（CJK 等宽对齐）
    max_name_cjk = max(cjk_width(sq['name']) for sq in site_queues.values())

    total_need = sum(sq['need'] for sq in site_queues.values())
    log(f'\n🔁 共需 {total_need} 篇文章，启动 {MAX_WORKERS} 个并行任务...')

    # ── 并行处理（支持失败替补）──
    global_work_idx = 0       # 用于目录命名，全局递增保证唯一
    pending = {}              # future -> (site_key, slot_idx, tag, url, article_title_orig)
    results = []
    site_order = list(site_queues.keys())

    def fmt_tag(sq, slot_idx, retries):
        """生成显示标签，如 '#3' 或 '#3#1'；站点名自动等宽对齐"""
        pad = ' ' * (max_name_cjk - cjk_width(sq['name']))
        name_padded = sq['name'] + pad
        slot_str = f'#{slot_idx + 1}'
        if retries > 0:
            slot_str += f'#{retries}'
        return f'[{name_padded}] {slot_str}'

    def try_submit(site_key):
        """从站点队列取下一篇提交，返回是否提交成功"""
        nonlocal global_work_idx
        sq = site_queues[site_key]

        # 不再需要更多文章
        if sq['success_count'] + sq['in_flight'] >= sq['need']:
            return False
        # 队列空了
        if not sq['queue']:
            return False

        # 找一个可用的 slot
        slot_idx = None
        for i, s in enumerate(sq['slots']):
            if s['state'] == 'ready':
                slot_idx = i
                break
        if slot_idx is None:
            return False

        article = sq['queue'].pop(0)
        global_work_idx += 1
        sq['in_flight'] += 1
        sq['slots'][slot_idx]['state'] = 'in_flight'
        retries = sq['slots'][slot_idx]['retries']
        tag = fmt_tag(sq, slot_idx, retries)

        job = (article['url'], sq['step1_script'], sq['name'], tag, sq['step2_script'], global_work_idx)
        future = executor.submit(process_one, job)
        pending[future] = (site_key, slot_idx, tag, article['url'], article.get('title', ''))
        return True

    def fill_workers():
        """轮转所有站点，填满并行槽位，避免新增站点只抓取不发布。"""
        progress = True
        while len(pending) < MAX_WORKERS and progress:
            progress = False
            for site_key in site_order:
                if len(pending) >= MAX_WORKERS:
                    break
                if try_submit(site_key):
                    progress = True

    def fail_slot(sq, slot_idx, tag):
        """slot 处理失败：递增重试计数，判断是否放弃"""
        slot = sq['slots'][slot_idx]
        slot['retries'] += 1
        if slot['retries'] >= 3:
            slot['state'] = 'dead'
            log(f'{tag} 连续失败 3 次，停止替补')
            return True  # 已放弃
        else:
            slot['state'] = 'ready'
            return False  # 可再试

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 初始提交：填满 worker（各站轮流）
        fill_workers()

        while pending:
            done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                site_key, slot_idx, tag, url, article_title = pending.pop(future)
                sq = site_queues[site_key]
                sq['in_flight'] -= 1

                try:
                    result = future.result()
                    # 失败时返回4个值(False, url, title, reason)，成功时返回5个值(True, url, title, id, category)
                    if len(result) >= 5:
                        ok, res_url, res_title, info, category = result
                    else:
                        ok, res_url, res_title, info = result
                        category = ''
                except Exception as e:
                    log(f'{tag} 💥 异常: {e}')
                    fail_slot(sq, slot_idx, tag)
                    results.append((False, tag, article_title or '', str(e)))
                    # 尝试补位
                    fill_workers()
                    continue

                if ok:
                    sq['success_count'] += 1
                    sq['slots'][slot_idx]['state'] = 'done'
                    save_record(res_url, res_title, True, '', info)
                    log(f'{tag} ✅ 发布成功 ID={info}')
                    results.append((True, tag, res_title, info, category))
                else:
                    save_record(res_url, res_title, False, info, '')
                    reason = ERROR_REASONS.get(info, info)
                    dead = fail_slot(sq, slot_idx, tag)
                    if not dead:
                        log(f'{tag} ❌ 失败: {reason}')
                    results.append((False, tag, res_title, reason))

                # 尝试补位（检查是否需要更多文章）
                fill_workers()

    # ── 汇总 ──
    ok_count = sum(1 for r in results if r[0])
    log('\n' + '=' * 50)
    log(f'📊 汇总: {ok_count}/{len(results)} 成功')
    for r in results:
        ok, tag, title, info = r[0], r[1], r[2], r[3]
        icon = '✅' if ok else '❌'
        cat = f' [{r[4]}]' if ok and len(r) > 4 and r[4] else ''
        log(f'{tag} {icon} {title[:40]} → {info}{cat}')
    log('=' * 50)

    # ── 保存本次运行结果（供通知推送使用）──
    last_file = os.path.join(WORK_DIR, 'last_publish_results.json')
    try:
        success_results = [{'ok': r[0], 'tag': r[1], 'title': r[2], 'info': r[3], 'category': r[4] if len(r) > 4 else ''}
                           for r in results if r[0]]
        json.dump(success_results, open(last_file, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception:
        pass


if __name__ == '__main__':
    main()
