---
name: github-trending-to-wechat
description: 采集 GitHub 增长热榜，用大模型生成公众号风格中文文章，自动建到微信公众号草稿箱。当用户要做「GitHub 热榜日报」「每日开源项目分享」「自动生成并推送公众号文章」这类定时内容流水线时使用。
---

# GitHub 增长热榜 → 微信公众号

把「采集 → 生成文章 → 优化标题 → 建草稿」串成一条每日可定时执行的流水线。
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
| 1/4 | `fetch_and_rank.py` | 拉 GitHub 候选（默认最近 30 天新建、Star>50），用昨日快照算 **Star 增量** | `data/daily/<日期>.md` `data/daily/<日期>.json` |
| 2/4 | `generate_article.py` | 大模型按公众号风格写中文文案 | `data/articles/<日期>.md` |
| 3/4 | `optimize_title.py` | 生成 6 个候选标题选优；有本号历史阅读数据时并入参考 | 就地改写文章首行标题 |
| 4/4 | `wechat_publish.py` | 逐图下载 → `uploadimg` 换微信托管 URL → 转内联样式 HTML → `draft/add` | 公众号草稿 |

一键执行：

```bash
python run_daily.py              # 全流程
python run_daily.py --skip-publish   # 只到生成文章，不碰微信
python run_daily.py --dry-run        # 只打印将要执行什么
```

单独验证微信凭证与权限：

```bash
python wechat_test.py            # 取token → 传图 → 建草稿 → 回读
python wechat_test.py --selftest # 离线自检
python notify.py --test          # 测试失败告警通道（Server酱）
```

## 失败告警

`run_daily.py` 任一步失败，会通过 `notify.py` 把「失败的步骤 + 报错尾部」推送到 Server酱（需要环境变量 `send_key`）。
零依赖，直接调 Server酱 HTTP 接口。未配置 `send_key` 时静默跳过，不影响主流程。

## 关键约束

1. **首次运行没有增量。** 只有一天快照时，`fetch_and_rank` 会回退为「按 Star 排序」，涨星行显示「目前约 N Star」；**第二天起**才是真正的增长榜（「今日新增约 N Star」）。
2. **只建草稿，不自动发表。** 官方发布接口 `freepublish/submit` 需要账号有对应权限；**个人未认证订阅号会返回 `48001`**。最终发布由人工在后台完成。
3. **快照是增量基石。** 别删 `data/snapshots/`，否则增量从头开始。
4. **依赖固定出口 IP。** IP 变了要在公众号后台重加白名单。

## 定时执行

以 Windows 任务计划为例（无人登录也运行）：

```powershell
$dir = 'C:\path\to\github-trending-to-wechat'
$arg = '/c cd /d "' + $dir + '" && python run_daily.py >> logs\run.log 2>&1'
$action  = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $arg -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00am
Register-ScheduledTask -TaskName 'GithubTrendingDaily' -Action $action -Trigger $trigger `
  -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable) `
  -Principal (New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U)
```

> 环境变量建议设为**机器级**（`[Environment]::SetEnvironmentVariable(..., 'Machine')`），S4U 会话才能读到。
