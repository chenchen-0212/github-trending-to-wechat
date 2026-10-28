# github-trending-to-wechat

> 每天自动把 GitHub 上的增长热榜，写成一篇中文公众号文章，并送进微信草稿箱。

一条 **零第三方依赖**（只用 Python 标准库）的内容流水线：

```
采集榜单 → 选榜·霸榜判定 → 大模型写文章 → 优化标题 → 建微信草稿
```

## 它是怎么工作的

```mermaid
flowchart TD
    A[定时触发<br/>Linux cron / Windows 任务计划] --> B[1. fetch_and_rank.py]
    B --> B1[GitHub Search API 拉候选]
    B1 --> B2[读昨日快照 → 算 Star 增量]
    B2 --> B3[无增量则回退按 Star 排序]
    B3 --> B4[2. select_daily.py]
    B4 --> B5[记入 data/tracking.db / 算连续上榜天数]
    B5 --> B6[第 2 次上榜转一行简报<br/>第 4 次起停更<br/>第 11 名起补满 10 篇详写]
    B6 --> C[3. generate_article.py]
    C -->     C1[大模型写 3 个重点 + 其余降级<br/>带开头钩子与可转发结论]
    C1 --> D[4. optimize_title.py]
    D --> D1[生成候选标题选优]
    D1 --> D2[5. article_lint.py<br/>结构/字数不过关就中止]
    D2 --> E[6. wechat_publish.py]
    E --> E1[配图 download → uploadimg 换微信URL]
    E1 --> E2[转内联样式 HTML → draft/add]
    E2 --> F[公众号草稿箱]
    F --> G[人工审核 → 手动发布]
```

## 快速开始

```bash
# 1. 配置环境变量（凭证只走环境变量，不落盘）
export GITHUB_TOKEN=ghp_xxx            # 可选：提升 GitHub 限流
export DEEPSEEK_API_KEY=sk-xxx         # 生成文章与标题（任一 OpenAI 兼容 key）
export LLM_BASE_URL=https://api.deepseek.com/v1   # 可选，默认 DeepSeek
export LLM_MODEL=deepseek-chat                     # 可选
export WECHAT_APPID=wx...              # 公众号
export WECHAT_APPSECRET=xxx            # 公众号（需在后台启用 + 加 IP 白名单）
export send_key=xxx                    # 可选：Server酱 sendkey，任一步失败时推送告警

# 2. 跑一遍
python run_daily.py        # 全流程，最后产出公众号草稿
```

Windows PowerShell 里用 `$env:GITHUB_TOKEN="..."`。

## 文件说明

| 文件 | 作用 |
|---|---|
| `fetch_and_rank.py` | 采集候选 + 用快照算 Star 增量，写 `data/daily/` |
| `select_daily.py` | 记榜入库、判定霸榜，产出 `data/daily/<日期>.selected.json` |
| `generate_article.py` | 大模型写文章，输出 `data/articles/` |
| `optimize_title.py` | 候选标题生成与选优 |
| `wechat_publish.py` | 转 HTML + 传图 + 建草稿 |
| `wechat_test.py` | 微信凭证/权限自检（含 `--selftest` 离线自检） |
| `tracking.py` | SQLite 追踪库：上榜记录 + 连续天数 + 深挖队列（stdlib `sqlite3`） |
| `article_lint.py` | 成稿自检：结构/字数/断点不过关就不建草稿（完读率闸门） |
| `run_daily.py` | 一键编排上面六步 |
| `notify.py` | 失败告警：把失败的步骤和报错推送到 Server酱（零依赖） |
| `netutil.py` | 带退避重试的 HTTP 调用，专治间歇性网络抖动 |
| `feature_article.py` | 单项目深挖：优先消费深挖队列 → 扒 README 图 → 按风格文档写文章 |
| `run_feature.sh` | 深挖的 72 小时时间闸（cron 每 5 分钟调用，满 72h 才真跑） |
| `ARTICLE_STYLE.md` | 「单项目深挖」文章风格约束（AI 生成时引用） |
| `ARTICLE_STYLE_DAILY.md` | 「每日榜单文」风格约束：3 个重点 + 其余降级、开头钩子、断点、字数 |
| `SKILL.md` | 作为 AI Agent Skill 使用的说明 |

## 产出示例

`data/articles/<日期>.md`（3 个重点 + 中间简写 + 霸榜速览表）：

````markdown
# 今天 GitHub 上 12 个顶顶顶的开源项目。

（开头钩子：痛点场景 → 点名最值得看的 3 个 → 先说这 3 个，剩下在文末）

## 先说这 3 个

**01**

**owner/repo：一句话卖点**

这个项目针对的那个具体麻烦……
它怎么做到，比如你可以……
什么人不适合、有什么坑。

今日新增约 320 Star。

```
开源地址：https://github.com/owner/repo
```

![owner/repo](https://opengraph.githubassets.com/1/owner/repo)

**02** … **03**（同样 3 段 + 配图）

## 这几个也值得看

**04**

**owner/repo：一句话卖点**

它是什么、适合谁（1~2 段，不配图）。

今日新增约 88 Star。

## 另外 3 个，一句话带过

| 项目 | 一句话 | 今日新增 |
| --- | --- | --- |
| hot/repo（连榜 2 天）| 连续霸榜的卖点 | +120 |

> 一句能被截图转发到群里的判断。

**点击下方卡片，关注我们**
````

> 为什么长这样：本号实测完读率中位数 26%，且**不管阅读量是 16 还是 584 都卡在 23~32%**——
> 瓶颈不在选题，在结构。所以规则是：前 3 个写透（读者知道重点在哪），中间降级，
> 霸榜压成一行（读者看得到终点），全文控在 1800~2400 字，结尾留一句可转发的话。
> `article_lint.py` 会在建草稿前把不合格的稿子拦下来。

> 一个项目**第 2 次上榜起只保留这一行**——不写正文、不配图。这一行再挂 2 次后（即连续第 4 次上榜起）连这行也不再收录，只在追踪库里继续记连续次数。空出的篇幅由榜单**第 11 名起往后补**，保证当天仍有 10 篇详写，所以文章总数会变成 `10 + 霸榜数`。

## 已知边界（很重要）

- **首次运行没有增量。** 只有一天快照时按 Star 兜底排序；**第二天起**才是真正的「增长榜」。
- **只建草稿，不自动发表。** `freepublish/submit` 需要账号权限，**个人未认证订阅号会返回 `48001`**。发布由人工在后台完成。
- **数据统计接口同样不可用。** `datacube/*`（文章阅读量、用户分析等）在个人未认证号上同样返回 `48001`，拿不到阅读数据。
- **依赖固定出口 IP。** 运行机器的公网 IP 要在公众号后台白名单里，IP 变了会报 `40164`。
- **大模型文案需人工过目。** 生成的标题/描述建议发布前审一遍。
- **追踪库 `data/tracking.db` 是运行态，不入 git。** 它在被忽略的 `data/` 下，**不随代码部署**——生产那份在运行机器上（当前是 ECS），本地开发机是独立的一份。丢了可以重建：`python tracking.py --backfill`（日报上榜记录来自 `data/articles/` + `data/daily/`，深挖成稿来自 `data/features/`）。
- **连续上榜按「实际记录过的天」算。** 日期口径是 UTC；某天流水线没跑**不会**断连，跑过但项目掉出文章才重置为 1。第 2 次上榜起转一行简报，简报挂满 2 次后（第 4 次起）停更，连续满 5 天自动进入深挖队列。
- **深挖队列每 72 小时消费一个**（由 `run_feature.sh` 控时），按连续天数优先、同分先入先出；失败自动回队列，重试 3 次后转 `failed`，可用 `python tracking.py --requeue owner/name` 重开。
- **配图可能缺失，但不再拖垮成文。** 深挖要扒的图都在 `raw.githubusercontent.com` 上，而这个域名**在 ECS 上连不通**（实测 5/5 被 reset；`api.github.com` 与 `opengraph.githubassets.com` 都正常）。以前任何一张图下载失败都会把整篇作废（连败三天、每轮还推一条告警），现在改成**单张失败就跳过**，一张都没成时回退到仓库卡片。验证：`python feature_article.py --selftest`（离线）。

## License

MIT
