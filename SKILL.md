---
name: github-trending-to-wechat
description: 采集 GitHub 增长热榜，用大模型生成公众号风格中文文章，自动建到微信公众号草稿箱。当用户要做「GitHub 热榜日报」「每日开源项目分享」「自动生成并推送公众号文章」这类定时内容流水线时使用。
---

# GitHub 增长热榜 → 微信公众号

把「采集 → 选榜·霸榜判定 → 生成文章 → 优化标题 → 建草稿」串成一条每日可定时执行的流水线。
**零第三方依赖**，只用 Python 标准库。

## 前置条件

### 环境变量

| 变量 | 用途 | 必需 |
|---|---|---|
| `GITHUB_TOKEN` | 提升 GitHub Search API 限流（也可用已登录的 `gh` CLI 兜底） | 建议 |
| `LLM_API_KEY`（或 `DEEPSEEK_API_KEY`） | 生成文章/标题，OpenAI 兼容接口 | 必需 |
| `LLM_BASE_URL` | 默认 `https://api.deepseek.com/v1`（通义/OpenAI 换这里） | 可选 |
| `LLM_MODEL` | 默认 `deepseek-chat` | 可选 |
| `WECHAT_APPID` / `WECHAT_APPSECRET` | 建草稿 | 必需 |
| `send_key` | Server酱 sendkey，任一步失败时推送告警 | 可选 |

### 微信公众号侧

- 在公众号后台把**运行机器的公网出口 IP** 加入 IP 白名单，否则报 `40164`。
- 接口需在**开发者平台**启用 AppSecret。

## 流程

| 步骤 | 脚本 | 做什么 | 产出 |
|---|---|---|---|
| 1/5 | `fetch_and_rank.py` | 拉 GitHub 候选（默认最近 30 天新建、Star>50），用昨日快照算 **Star 增量** | `data/daily/<日期>.md` `data/daily/<日期>.json` |
| 2/5 | `select_daily.py` | 记榜入库、算**连续上榜天数**；连续满 3 天的转一行简报，简报满 4 天停更，第 11 名起补满 10 篇详写 | `data/daily/<日期>.selected.json` + `data/tracking.db` |
| 3/5 | `generate_article.py` | 大模型按公众号风格写中文文案（简报项只写一行卖点） | `data/articles/<日期>.md` |
| 4/5 | `optimize_title.py` | 生成 6 个候选标题选优；有本号历史阅读数据时并入参考 | 就地改写文章首行标题 |
| 5/5 | `wechat_publish.py` | 逐图下载 → `uploadimg` 换微信托管 URL → 转内联样式 HTML → `draft/add` | 公众号草稿 |

一键执行：

```bash
python run_daily.py              # 全流程
python run_daily.py --skip-publish   # 不建草稿，只跑到优化标题
python run_daily.py --dry-run        # 只打印将要执行什么
```

单独验证微信凭证与权限：

```bash
python wechat_test.py            # 取token → 传图 → 建草稿 → 回读
python wechat_test.py --selftest # 离线自检
python notify.py --test          # 测试失败告警通道（Server酱）
```

单独操作追踪库：

```bash
python tracking.py --stats                    # 看记录天数、最长连续、队列状态
python tracking.py --backfill                 # 重建历史：日报上榜记录 + 深挖成稿补录
python tracking.py --selftest                 # 离线自检（连续天数/队列状态机）
python tracking.py --enqueue owner/name       # 手工塞一个进深挖队列
python tracking.py --requeue owner/name       # 把 failed/skipped 的重新放回队列
python select_daily.py --date 2026-10-13 --dry-run   # 预览某天的收录与霸榜判定
```

## 单项目深挖（另一条线）

和上面的「每日榜单」是**两条独立的线**：这条围绕**一个**成熟项目写一篇深度文。
由 `run_feature.sh` 控制成**精确每 72 小时**一次（cron 每 5 分钟调它一次，只有距上次成功满 72 小时才真跑；成功才写时间戳，失败下次即重试）。

```bash
python feature_article.py                     # 自动选题（深挖队列优先）
python feature_article.py --no-from-db        # 忽略队列，直接找新项目
python feature_article.py --repo owner/name   # 指定项目（不写队列）
python feature_article.py --dry-run           # 只看选题与扒到的图，不调模型、不写库
python feature_article.py --upload            # 生成后直接发到公众号草稿箱
```

- **选题**：**先消费深挖队列**（连续上榜满 5 天的项目，按连续天数优先）；队列空了才去 GitHub Search 拉一批成熟项目（Star≥1500 且近 60 天有推送）让大模型挑——一次只加一个，入队后立即深挖
- **队列**：认领即置 `in_progress`，成功置 `done`；中途失败自动退回 `pending`（下次到点重试），重试 3 次转 `failed`。已在 `data/features/` 有成稿的项目会被跳过
- **配图**：从 README 引用 + 仓库文件树里扒，滤掉徽章 / 图标 / App 资源 / 测试夹具；确实没有就退回仓库卡片
- **风格**：由 `ARTICLE_STYLE.md` 约束——单项目深挖骨架、短板必须 ≥3 条且具体、每个数字标日期
- **产出**：`data/features/<日期>-<owner>_<repo>.md`，配图存 `examples/features/<owner>_<repo>/`

## 失败告警

`run_daily.py` 任一步失败，会通过 `notify.py` 把「失败的步骤 + 报错尾部」推送到 Server酱（需要环境变量 `send_key`）。
零依赖，直接调 Server酱 HTTP 接口。未配置 `send_key` 时静默跳过，不影响主流程。

## 网络重试

所有 HTTP 调用（GitHub / 大模型 / 微信）都走 `netutil.urlopen`，对**瞬时错误**
（连接中断、超时、5xx）自动退避重试 3 次（2s/4s/6s）；4xx 这类确定性错误立即抛出，不做无谓重试。

## 关键约束

1. **首次运行没有增量。** 只有一天快照时，`fetch_and_rank` 会回退为「按 Star 排序」，涨星行显示「目前约 N Star」；**第二天起**才是真正的增长榜（「今日新增约 N Star」）。
2. **只建草稿，不自动发表。** 官方发布接口 `freepublish/submit` 需要账号有对应权限；**个人未认证订阅号会返回 `48001`**。最终发布由人工在后台完成。
3. **快照是增量基石。** 别删 `data/snapshots/`，否则增量从头开始。
4. **依赖固定出口 IP。** IP 变了要在公众号后台重加白名单。
5. **数据统计接口不可用。** `datacube/*`（文章阅读量、用户分析等）在个人未认证号上同样返回 `48001`，拿不到阅读数据。
6. **追踪库是运行态。** `data/tracking.db` 在被忽略的 `data/` 下，**不随代码部署**；生产那份在运行机器上，与开发机互相独立。丢了用 `python tracking.py --backfill` 重建。
7. **连续上榜按「实际记录过的天」算。** 日期口径 UTC；某天流水线没跑不会断连，跑过但项目掉出文章才重置为 1。满 3 天（第 4 天起）转一行简报，简报满 4 天不再收录（库里仍记），满 5 天入深挖队列。
8. **两个 cron 会同时开库。** 日报 08:00 与深挖（每 5 分钟检查）都会读写 `tracking.db`；`run_feature.sh` 的 `flock` 只防自己，跨任务并发靠 SQLite 的 `journal_mode=WAL` + `busy_timeout=5000` 兜底。

## 定时执行

### Linux cron（推荐，云服务器常驻）

密钥放 root-only 文件，cron 里 `source` 进来：

```bash
cat > /etc/github-trending.env <<'EOF'
export DEEPSEEK_API_KEY='...'
export WECHAT_APPID='...'
export WECHAT_APPSECRET='...'
export send_key='...'
EOF
chmod 600 /etc/github-trending.env

# 每天 08:00 执行（自动保留已有 crontab 条目）
( crontab -l 2>/dev/null; \
  echo '0 8 * * * . /etc/github-trending.env; cd /opt/github-trending-to-wechat && mkdir -p logs && python3 run_daily.py >> logs/run.log 2>&1' \
) | crontab -
```

### Windows 任务计划（本机跑）

```powershell
$dir = 'C:\path\to\github-trending-to-wechat'
$arg = '/c cd /d "' + $dir + '" && python run_daily.py >> logs\run.log 2>&1'
$action  = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $arg -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00am
Register-ScheduledTask -TaskName 'GithubTrendingDaily' -Action $action -Trigger $trigger `
  -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable) `
  -Principal (New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U)
```

> 环境变量建议设为**机器级**（`[Environment]::SetEnvironmentVariable(..., 'Machine')`），S4U 会话才能读到。
> 云服务器出口 IP 比家用宽带稳定，能少踩公众号 IP 白名单的坑——长期无人值守优先选它。
