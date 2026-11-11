#!/usr/bin/env python3
"""
ArticleBridge v2 全局配置文件
你可以自由修改此文件中各项参数的取值。
"""

import os

# ═══════════════════════════════════════════
# 一、基本路径
# ═══════════════════════════════════════════

# 项目根目录（一般不需要改）
WORK_DIR = os.path.dirname(os.path.abspath(__file__))

# 子目录
STEP1_DIR = os.path.join(WORK_DIR, 'step1')
STEP2_DIR = os.path.join(WORK_DIR, 'step2')
DATA_DIR = os.path.join(WORK_DIR, 'data')       # 中间产物目录
YOUHUA_DIR = os.path.join(WORK_DIR, 'ai')        # AI 优化包目录

# 图片缓存目录名（step1 生成 MD 时填充到图片路径中，一般不需要改）
ASSETS_DIR_NAME = 'article_assets'

# ═══════════════════════════════════════════
# 二、AI 优化包 Python 环境
# ═══════════════════════════════════════════

# 项目级统一 venv 路径（部署后：python -m venv .venv）
VENV_PYTHON = os.path.join(WORK_DIR, '.venv', 'Scripts', 'python.exe')
# 如果上面不存在，回退到 ai/ 下的旧 venv
FALLBACK_VENV = os.path.join(YOUHUA_DIR, '.venv', 'Scripts', 'python.exe')

# AI 优化包入口脚本（相对于 YOUHUA_DIR）
OPTIMIZE_SCRIPT = 'workflow_langchain.py'

# ═══════════════════════════════════════════
# 三、发布目标
# ═══════════════════════════════════════════

# 不凡商城后台 Cookies —— 过期后需重新从浏览器获取
COOKIES = {
    'keeplogin': os.getenv('BUFAN_KEEPLOGIN', ''),
    'PHPSESSID': os.getenv('BUFAN_PHPSESSID', ''),
}

# 后台基础 URL
BACKEND_BASE_URL = 'https://admin.bufanshop.kcshop.vip'
BACKEND_API_URL = f'{BACKEND_BASE_URL}/zonWwkqVHm.php'
# 图片上传接口返回后台域名，但前台展示必须使用 OSS 域名。
PUBLIC_UPLOAD_BASE_URL = 'https://oss.bufanshop.a-bf.cn'

BACKEND_HEADERS = {
    # 不凡智库内容管理已迁移到 thinktank 路由；旧 partner 路由会返回 404。
    'Referer': f'{BACKEND_API_URL}/thinktank/question',
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# ═══════════════════════════════════════════
# 四、文章来源
# ═══════════════════════════════════════════

SITES = {
    'zidonghua': {
        'step1': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'step1', 'step1_zidonghua.py'),
        'listing_urls': [
            'https://www.zidonghua.com.cn/news/semiconductor/',
            'https://www.zidonghua.com.cn/news/exhibition/',
            'https://www.zidonghua.com.cn/news/brand/',
        ],
        'selector': '.list-wrap',          # Playwright CSS 选择器
        'link_match': 'endsWithHtml',       # 链接匹配模式
        'sort_by_time': True,               # 从3个栏目合并后按时间排序取最新
        'name': '自动化网',
            'articles_per_site': 7,             # 每天发布篇数
    },
    'iotworld': {
        'step1': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'step1', 'step1_iotworld.py'),
        'listing_urls': ['https://www.iotworld.com.cn/News.html'],
        'page_url_format': 'https://www.iotworld.com.cn/IOTNews.aspx?page={page}',
        'selector': '',   # 空=扫全页面，配合 endsWithShtml + mobile排除 + URL去重
        'link_match': 'endsWithShtml',            # 桌面版链接格式：xxx.shtml
        'name': '物联网世界',
        'articles_per_site': 4,
    },
    'ithome': {
        'step1': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'step1', 'step1_generic.py'),
        'listing_urls': ['https://it.ithome.com/ityejie'],
        'selector': '',
        'link_match': 'ithome',
        'name': 'IT之家AI',
        'articles_per_site': 4,
    },
    'zhidx': {
        'step1': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'step1', 'step1_generic.py'),
        'listing_urls': ['https://www.zhidx.com/'],
        'selector': '',
        'link_match': 'zhidx',
        'name': '智东西',
        'articles_per_site': 3,
    },
    'qbitai': {
        'step1': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'step1', 'step1_generic.py'),
        'listing_urls': ['https://www.qbitai.com/'],
        'selector': '',
        'link_match': 'qbitai',
        'name': '量子位',
        'articles_per_site': 2,
    },
    'tmtpost': {
        'step1': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'step1', 'step1_generic.py'),
        'listing_urls': ['https://www.tmtpost.com/'],
        'selector': '',
        'link_match': 'tmtpost',
        'name': '钛媒体',
        'articles_per_site': 2,
    }
}

# ═══════════════════════════════════════════
# 五、处理参数
# ═══════════════════════════════════════════

# 每站每次获取的候选文章数
MAX_CANDIDATES = 20

# 时效窗口：允许今天、昨天、前天；更早的文章不进入发布队列。
MAX_ARTICLE_AGE_DAYS = 2

# 连续失败上限（到达后停止该站点）
MAX_CONSECUTIVE_FAILURES = 4

# 各步骤超时时间（秒）
TIMEOUT_STEP1 = 180       # 抓取文章
TIMEOUT_OPTIMIZE = 600    # AI 优化（最长 10 分钟）
# 图片上传需要逐张写入 OSS；含十几张图片的文章在网络较慢时可能超过 2 分钟。
TIMEOUT_PUBLISH = 420     # 发布（最长 7 分钟）

# ═══════════════════════════════════════════
# 六、并行发布配置
# ═══════════════════════════════════════════

# 并行工作线程数
MAX_WORKERS = 2

# ═══════════════════════════════════════════
# 七、文件路径（一般不需要改）
# ═══════════════════════════════════════════

LOCK_FILE = os.path.join(DATA_DIR, '.publish.lock')
STEP2 = os.path.join(STEP2_DIR, 'step2_publish.py')
RECORDS_FILE = os.path.join(DATA_DIR, 'publish_records.json')
LOG_FILE = os.path.join(DATA_DIR, 'daily_publish.log')
