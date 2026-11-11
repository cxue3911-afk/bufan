# ArticleBridge v2 — Docker 部署包

## 项目简介

每天自动从 **自动化网（zidonghua.com.cn）** 和 **物联网世界（iotworld.com.cn）** 抓取最新技术文章，经 AI 分析、审核、改写后，发布到不凡商城后台，并通过企业微信群推送通知。

### 流水线流程

```
┌──────────────────────────────────────────────────────────────┐
│  daily_publish.py  ← 每天 08:40 定时触发                      │
├──────────────────────────────────────────────────────────────┤
│  Step 1  │  get_cookies.py      → Playwright 无头浏览器登录    │
│          │                       → OCR 识别验证码              │
│          │                       → 自动更新 config.py Cookie   │
├──────────┼───────────────────────────────────────────────────┤
│  Step 2  │  batch_publish.py    → 获取各站文章列表（翻页补齐）  │
│          │                      → AI 排序（DeepSeek）          │
│          │                      → 6 线程并行处理每个文章 slot  │
│          │    ├ step1/asyncio   → Playwright 抓取文章内容+图片 │
│          │    ├ ai/langchain    → 图片分析 + 内容审核 + 改写   │
│          │    └ step2/publish   → 上传图片 + 发布到后台        │
├──────────┼───────────────────────────────────────────────────┤
│  Step 3  │  send_wecom.py       → 推送成功文章到企业微信群     │
└──────────┴───────────────────────────────────────────────────┘
```

---

## 文件结构

```
articlebridge-docker/
├── Dockerfile                  # Docker 构建文件
├── entrypoint.sh               # 容器入口脚本（设置 cron + 启动守护进程）
├── config.py                   # 全局配置（Cookie、站点、超时、并行数）
├── daily_publish.py            # 每日任务入口
├── batch_publish.py            # 批量发��主程序
├── get_cookies.py              # 自动登录获取 Cookie（Playwright + ddddocr OCR）
├── send_wecom.py               # 企业微信群通知
├── update_bf_question_once.py  # 辅助脚本
├── step1/                      # 文章抓取模块
│   ├── step1_zidonghua.py      # 自动化网抓取
│   └── step1_iotworld.py       # 物联网世界抓取
├── step2/                      # 发布模块
│   └── step2_publish.py        # 图片上传 + 发布到后台
├── ai/                         # AI 优化模块
│   ├── workflow_langchain.py   # LangChain 工作流（586 行）
│   ├── config.json             # AI 模型配置（API Key / Base URL / Model）
│   ├── prompts.json            # AI 提示词模板
│   └── requirements.txt        # AI 模块 Python 依赖
└── README.md                   # 本文件
```

---

## 环境依赖

| 项目 | 要求 |
|------|------|
| Docker | 29.0+ (含 Docker Compose) |
| 磁盘空间 | ≥ 5 GB（镜像约 3GB） |
| 网络 | 能访问外网 AI API 和后台网站 |

### 外部服务依赖

| 服务 | 用途 | 配置位置 |
|------|------|----------|
| DeepSeek API | AI 排序、内容审核、改写 | `ai/config.json` → `text` |
| MiniMax VLM API | 图片视觉分析 | `ai/config.json` → `vision` |
| 不凡商城后台 | 发布目标 | `config.py` → `COOKIES` |
| 企业微信 Webhook | 通知推送 | `send_wecom.py` |

---

## 快速部署（Docker）

### 1. 构建镜像

```bash
# 进入项目目录
cd articlebridge-docker

# 构建镜像（首次约 3 分钟）
docker build -t articlebridge:latest .
```

### 2. 启动容器

```bash
# 启动（挂载 data 目录到宿主机以持久化发布记录）
docker run -d \
  --name articlebridge \
  --restart unless-stopped \
  -v "$(pwd)/data:/app/data" \
  articlebridge:latest
```

> **注意**：`data/` 目录存储 `publish_records.json`（发布历史去重）和运行日志，挂载后容器重启不会丢失。

### 3. 验证部署

```bash
# 查看容器状态
docker ps | grep articlebridge

# 查看运行日志
docker logs articlebridge

# 手动触发一次完整流水线
docker exec articlebridge python /app/daily_publish.py
```

### 4. 定时任务说明

容器内已通过 cron 配置每天 08:40（UTC 00:40）自动执行 `daily_publish.py`。

```bash
# 查看 cron 配置
docker exec articlebridge crontab -l
# 输出: 40 0 * * * cd /app && /usr/local/bin/python /app/daily_publish.py >> /app/data/daily_cron.log 2>&1

# 查看定时任务日志
tail -f data/daily_cron.log
```

---

## 配置说明

### config.py — 核心配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `COOKIES` | 后台登录凭证 | 首次部署需运行 `get_cookies.py` 获取 |
| `BACKEND_BASE_URL` | 后台地址 | `https://admin.bufanshop.kcshop.vip` |
| `SITES` | 文章来源 | 自动化网 + 物联网世界 |
| `articles_per_site` | 每站每天发布篇数 | 3 |
| `MAX_WORKERS` | 并行线程数 | 6 |
| `MAX_CANDIDATES` | 候选文章池大小 | 20 |
| `TIMEOUT_OPTIMIZE` | AI 优化超时（秒） | 600 |

### ai/config.json — AI 模型配置

```json
{
  "text": {
    "base_url": "https://api.deepseek.com",
    "api_key": "sk-xxx",
    "model": "deepseek-v4-flash"
  },
  "vision": {
    "api_host": "https://api.minimaxi.com",
    "endpoint": "/v1/coding_plan/vlm",
    "api_key": "sk-xxx"
  },
  "workflow": {
    "skip_image_process": true,
    "skip_rewrite": true
  }
}
```

---

## 运维指南

### 日常操作

```bash
# 停止容器
docker stop articlebridge

# 启动容器
docker start articlebridge

# 重启容器
docker restart articlebridge

# 实时查看日志
docker logs -f articlebridge

# 手动执行发布
docker exec articlebridge python /app/daily_publish.py
```

### Cookie 刷新

Cookie 过期后发布会全部失败。`daily_publish.py` 的 Step 1 会自动尝试登录刷新，如 OCR 识别失败 3 次，需手动处理：

```bash
# 方法 1：等待下次定时任务自动重试（每 3 次尝试 1 次 OCR，有一定概率成功）

# 方法 2：在宿主机浏览器登录后台，手动获取 Cookie 后更新 config.py 并重建镜像
docker exec articlebridge python /app/get_cookies.py --headless
```

### 日志文件

| 文件 | 内容 |
|------|------|
| `data/daily_cron.log` | 定时任务输出日志 |
| `data/batch_publish.log` | 批量发布详细日志 |
| `data/publish_records.json` | 所有发布历史记录（JSON） |

### 数据备份

```bash
# 备份发布记录
cp data/publish_records.json data/publish_records_$(date +%Y%m%d).json

# 备份整个 data 目录
tar -czf data_backup_$(date +%Y%m%d).tar.gz data/
```

---

## 故障排查

| 现象 | 可能原因 | 解决方案 |
|------|----------|----------|
| 所有文章"发布请求失败" | Cookie 过期 | 运行 `get_cookies.py` 刷新 Cookie |
| "登录失败，验证码错误" ×3 | OCR 识别不准 | 等待下次自动重试，或本地手动登录 |
| Playwright 启动失败 | 系统库缺失 | 已验证 Docker 镜像包含所有依赖 |
| AI 排序/分析返回空 | API Key 过期或额度不足 | 更新 `ai/config.json` |
| 某个站点队列很快耗尽 | 首页文章已全部发布过 | 系统会自动翻页补充 |
| 图片分析失败 | MiniMax VLM API 问题 | 该文章标记后自动跳过 |

---

## 发布结果示例

```
==================================================
 汇总: 6/7 成功
==================================================
 [物联网世界] #2 ✅ 累计出货1.5亿片，这家芯片公司拿下近5亿融资 → 10468 [投融资讯]
 [物联网世界] #3 ✅ 中国海关强制要求婚纱出口采用RFID追溯 → 10469 [品质测试]
 [自动化网  ] #3 ✅ SurplusGLOBAL将亮相SEMICON KOREA 2026 → 10470 [行业展会]
 [物联网世界] #1#1 ✅ 又一UWB芯片厂商完成融资！ → 10471 [投融资讯]
 [自动化网  ] #1 ✅ 智驾下半场，美新半导体MEMS补齐感知关键一环 → 10472 [研发技术]
 [自动化网  ] #2 ✅ 美新半导体携手豪威科技集团 → 10473 [前沿科技资讯]
==================================================
```

---

## 镜像规格

| 项目 | 值 |
|------|-----|
| 基础镜像 | `python:3.12-slim` |
| 镜像大小 | ≈ 3.0 GB |
| 构建时间 | ≈ 3 分钟 |
| 运行内存 | ≈ 500 MB（含 Chromium） |

### 镜像分层说明

| 层 | 内容 | 大小 |
|----|------|------|
| Python 3.12-slim | 基础 Python 环境 | ~150 MB |
| 系统依赖 | Playwright 运行时库 + cron | ~200 MB |
| Python 包 | openai / langchain / ddddr / pillow 等 | ~600 MB |
| Playwright | Chromium + Chrome Headless Shell | ~300 MB |
| 项目代码 | 15 个源文件 | ~100 KB |

---

## 部署检查清单

- [ ] Docker 已安装（`docker --version`）
- [ ] 网络可访问 `api.deepseek.com`、`api.minimaxi.com`
- [ ] 网络可访问 `admin.bufanshop.kcshop.vip`
- [ ] `config.py` 中 Cookie 有效
- [ ] `ai/config.json` 中 API Key 有效
- [ ] 镜像构建成功（`docker build -t articlebridge .`）
- [ ] 容器启动正常（`docker run -d --name articlebridge ...`）
- [ ] 手动测试通过（`docker exec articlebridge python /app/daily_publish.py`）
- [ ] 企业微信群收到通知消息
