#!/bin/sh
set -e

echo "=========================================="
echo " ArticleBridge v2 — Docker 部署版启动"
echo "=========================================="
echo ""

# 创建必要的数据目录
mkdir -p /app/data

# 如果存在本地的 publish_records.json，保留它（通过 volume 挂载）
if [ ! -f /app/data/publish_records.json ]; then
    echo '[]' > /app/data/publish_records.json
    echo "ℹ️  初始化空的发布记录文件"
fi

# 设置 crontab（每天 08:40 CST = 00:40 UTC）
echo "40 0 * * * cd /app && /usr/local/bin/python /app/daily_publish.py >> /app/data/daily_cron.log 2>&1" > /tmp/crontab.txt
crontab /tmp/crontab.txt
rm /tmp/crontab.txt
echo "定时任务已配置:"
crontab -l | grep -v '^#'

echo ""
echo "启动 cron 守护进程..."
service cron start 2>/dev/null || cron

echo ""
echo "✅ 容器就绪。下次定时执行: 每天 08:40 (CST)"
echo ""

# 保持容器运行，同时输出日志
touch /app/data/daily_cron.log
exec tail -f /app/data/daily_cron.log
