#!/usr/bin/env python3
"""采集 GitHub 候选项目并计算每日 Star 增量，输出「增长热榜」。

用法:
    python fetch_and_rank.py                      # 读环境变量 GITHUB_TOKEN
    GITHUB_TOKEN=xxx python fetch_and_rank.py
    python fetch_and_rank.py --selftest           # 只跑内置自检，不联网

零第三方依赖。无 token 也能跑，但 Search API 未认证限流很严，候选池拉不全。
首次运行没有昨日快照，delta 全为空是正常的，第二天才有榜单。
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import netutil

API = "https://api.github.com/search/repositories"


def gh_token():
    """GITHUB_TOKEN 未设置时，从已登录的 gh CLI 取（token 留在系统 keyring，不落盘）。"""
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _get(url, token):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "gh-trending-hot")
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with netutil.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"HTTP {e.code} {url}\n{body}")


def fetch_candidates(token, window_days, pages, per_page=100):
    """新建且已获得一定 star 的仓库 —— 「增长热榜」的候选池。"""
    since = (datetime.now(timezone.utc).date() - timedelta(days=window_days)).isoformat()
    q = urllib.parse.quote(f"created:>={since} stars:>50")
    out = []
    for page in range(1, pages + 1):
        url = f"{API}?q={q}&sort=stars&order=desc&per_page={per_page}&page={page}"
        items = _get(url, token).get("items", [])
        if not items:
            break
        for it in items:
            out.append({
                "full_name": it["full_name"],
                "url": it["html_url"],
                "description": (it.get("description") or "").strip(),
                "stars": it["stargazers_count"],
                "language": it.get("language") or "",
                "created_at": it.get("created_at", ""),
            })
    return out


def compute_deltas(current, prev_stars):
    """只对昨日快照里也存在的仓库算增量，按增量降序。prev_stars: {full_name: stars}"""
    rows = []
    for r in current:
        prev = prev_stars.get(r["full_name"])
        if prev is None:
            continue
        r = dict(r)
        r["delta"] = r["stars"] - prev
        rows.append(r)
    rows.sort(key=lambda r: (r["delta"], r["stars"]), reverse=True)
    return rows


def load_prev_stars(data_dir, today):
    snap_dir = os.path.join(data_dir, "snapshots")
    if not os.path.isdir(snap_dir):
        return {}
    days = sorted(f[:10] for f in os.listdir(snap_dir)
                  if f.endswith(".json") and f[:10] < today.isoformat())
    if not days:
        return {}
    with open(os.path.join(snap_dir, days[-1] + ".json"), encoding="utf-8") as fh:
        return json.load(fh)["repos"]


def save_snapshot(data_dir, today, repos):
    path = os.path.join(data_dir, "snapshots", f"{today.isoformat()}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"date": today.isoformat(),
                   "repos": {r["full_name"]: r["stars"] for r in repos}},
                  fh, ensure_ascii=False, indent=2)
    return path


def render_markdown(rows, top, day):
    lines = [f"# GitHub 增长热榜 · {day.isoformat()}", ""]
    for i, r in enumerate(rows[:top], 1):
        desc = f" — {r['description']}" if r["description"] else ""
        d = r.get("delta")
        tag = f"+{d} ⭐ " if d else ""
        lines.append(f"{i}. [{r['full_name']}]({r['url']}) {tag}(共 {r['stars']}){desc}")
    return "\n".join(lines)


def selftest():
    cur = [
        {"full_name": "a/b", "url": "", "description": "", "stars": 150,
         "language": "", "created_at": ""},
        {"full_name": "c/d", "url": "", "description": "", "stars": 90,
         "language": "", "created_at": ""},
        {"full_name": "e/f", "url": "", "description": "", "stars": 100,
         "language": "", "created_at": ""},  # 昨日不在榜 -> 应被排除
    ]
    prev = {"a/b": 100, "c/d": 80}
    rows = compute_deltas(cur, prev)
    assert [r["full_name"] for r in rows] == ["a/b", "c/d"], rows
    assert [r["delta"] for r in rows] == [50, 10], rows
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default="",
                    help="留空则依次尝试 GITHUB_TOKEN 环境变量、gh CLI")
    ap.add_argument("--window-days", type=int, default=30,
                    help="候选池只看最近这么多天内新建的仓库")
    ap.add_argument("--pages", type=int, default=3, help="每页100条，拉几页")
    ap.add_argument("--top", type=int, default=20, help="榜单条数")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    today = datetime.now(timezone.utc).date()
    token = args.token or os.environ.get("GITHUB_TOKEN", "") or gh_token()
    if not token:
        print("警告: 无 GITHUB_TOKEN 且 gh 未登录，Search API 限流很严，候选可能不全",
              file=sys.stderr)

    current = fetch_candidates(token, args.window_days, args.pages)
    print(f"采集候选: {len(current)} 个")

    prev = load_prev_stars(args.data_dir, today)
    rows = compute_deltas(current, prev)
    seed = not rows  # 首日无增量：回退为按 Star 排序的候选，保证下游有内容
    if seed:
        rows = [dict(r, delta=None) for r in
                sorted(current, key=lambda x: x["stars"], reverse=True)]
    save_snapshot(args.data_dir, today, current)

    md = render_markdown(rows, args.top, today)
    out_dir = os.path.join(args.data_dir, "daily")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{today.isoformat()}.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(md + "\n")

    # 结构化榜单，供文章生成器消费
    json_path = os.path.join(out_dir, f"{today.isoformat()}.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(rows[:args.top], fh, ensure_ascii=False, indent=2)

    if not prev:
        print("首次运行: 已保存快照，明日才有增量榜单")
    if seed:
        print("本次无增量，榜单回退为按 Star 排序的候选(首日正常行为)")
    print(md if rows else "(本次无数据)")
    print(f"\n已写入: {out_path}")
    print(f"已写入: {json_path}")


if __name__ == "__main__":
    main()
