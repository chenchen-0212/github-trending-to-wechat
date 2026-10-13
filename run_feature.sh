#!/bin/sh
# 精确「每 72 小时」运行一次单项目深挖。
# 由 cron 频繁调用（每 5 分钟），只有距上次成功满 72 小时才真正执行。
# 用成功时间戳而不是启动时间戳 —— 失败了下一次检查会立刻重试。
set -u

BASE=/opt/github-trending-to-wechat
STAMP="$BASE/data/.feature_last_run"
INTERVAL=259200                     # 72 * 3600
LOCK=/tmp/gh-trending-feature.lock

[ -f /etc/github-trending.env ] && . /etc/github-trending.env
cd "$BASE" || exit 0

now=$(date +%s)
last=$(cat "$STAMP" 2>/dev/null || echo 0)
case "$last" in ''|*[!0-9]*) last=0 ;; esac
[ $((now - last)) -lt "$INTERVAL" ] && exit 0

# 防止检查密集时重复启动
exec 9>"$LOCK"
flock -n 9 || exit 0

mkdir -p logs data
export PYTHONIOENCODING=utf-8

if /usr/bin/python3.11 feature_article.py --upload >> logs/feature.log 2>&1; then
    date +%s > "$STAMP"
    exit 0
fi

/usr/bin/python3.11 notify.py 深挖文章失败 见logs/feature.log >> logs/feature.log 2>&1
exit 1
