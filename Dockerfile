# ArticleBridge v2 — Docker 生产部署
# 每天 14:00 自动登录后台 → 抓取文章 → AI 优化 → 发布 → 群通知

FROM python:3.12-slim

WORKDIR /app

# ── 系统依赖 ──
RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
        libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 libdbus-1-3 libxcb1 libxkbcommon0 \
        libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
        libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
        libatspi2.0-0 libjpeg-dev libpng-dev \
        cron \
    && rm -rf /var/lib/apt/lists/*

# ── 复制项目文件（扁平结构，所有文件在 Dockerfile 同级目录）──
COPY config.py              /app/config.py
COPY daily_publish.py       /app/daily_publish.py
COPY batch_publish.py       /app/batch_publish.py
COPY get_cookies.py         /app/get_cookies.py
COPY send_wecom.py          /app/send_wecom.py
COPY update_bf_question_once.py /app/update_bf_question_once.py
COPY step1/                 /app/step1/
COPY step2/                 /app/step2/
COPY ai/                    /app/ai/
COPY entrypoint.sh          /app/entrypoint.sh

# ── 适配 Docker 环境（无 venv，用系统 Python）──
RUN sed -i "s|VENV_PYTHON = os.path.join.*|VENV_PYTHON = '/usr/local/bin/python'|" /app/config.py && \
    sed -i "s|FALLBACK_VENV = os.path.join.*|FALLBACK_VENV = '/usr/local/bin/python'|" /app/config.py && \
    sed -i 's|^VENV_PYTHON =.*|VENV_PYTHON = sys.executable|' /app/daily_publish.py

# ── 安装 Python 依赖 ──
RUN pip install --upgrade pip -q && \
    pip install -r /app/ai/requirements.txt -q && \
    pip install playwright ddddocr requests markdown html2text -q

# ── 安装 Playwright Chromium（无头模式）──
RUN python -m playwright install chromium 2>&1

# ── 初始化 ──
RUN mkdir -p /app/data && chmod +x /app/entrypoint.sh

ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
