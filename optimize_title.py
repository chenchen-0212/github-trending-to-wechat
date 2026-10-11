#!/usr/bin/env python3
"""存草稿前优化文章标题。

大模型生成多个候选并选优；若能连上微信 datacube（本号已发文章），
再把"本号阅读量最高的标题"作为风格参考喂给模型。

    python optimize_title.py                # 就地改 data/articles/<日期>.md 的首行标题
    python optimize_title.py --dry-run      # 只打印候选，不改文件
    python optimize_title.py --article X.md

环境变量:
    LLM_API_KEY / DEEPSEEK_API_KEY  必需
    WECHAT_APPID / WECHAT_APPSECRET 可选(拉本号历史标题；没有就跳过 A)
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import generate_article as ga
import wechat_test as wt

SYS = "你是公众号资深标题编辑，擅长写出高点击率、但绝不标题党的中文标题。"


def own_top_titles(n=5, days=7):
    """拉本号近 days 天已发文章，按阅读量取前 n 个标题。失败或无数据返回 []。"""
    appid = os.environ.get("WECHAT_APPID", "").strip()
    secret = os.environ.get("WECHAT_APPSECRET", "").strip()
    if not appid or not secret:
        return []
    try:
        token = wt.get_token(appid, secret)
    except SystemExit:
        return []
    reads = {}
    today = datetime.now(timezone.utc).date()
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        try:
            data = wt._post(
                f"https://api.weixin.qq.com/datacube/getarticletotal?access_token={token}",
                json.dumps({"begin_date": d, "end_date": d}).encode(),
                "application/json; charset=utf-8")
        except Exception:
            continue
        for art in data.get("list", []) or []:
            title = art.get("title")
            if not title:
                continue
            total = sum(r.get("read_num", 0) or 0
                        for r in (art.get("detail", {}) or {}).get("list", []) or [])
            reads[title] = max(reads.get(title, 0), total)
    return [t for t, _ in sorted(reads.items(), key=lambda kv: kv[1], reverse=True)][:n]


def build_prompt(old_title, topics, hist):
    parts = [f"当前标题：{old_title}", "", "本文涉及的 10 个项目：", topics, ""]
    if hist:
        parts += ["本号历史高阅读标题（按阅读量排序，参考其风格，不要照抄）："]
        parts += [f"- {t}" for t in hist]
        parts += [""]
    parts += [
        "请给出 6 个更吸引点击的候选标题，并选出最好的一个。要求：",
        "- 中文，不超过 24 字；口语化、有钩子，但不夸大、不做标题党。",
        "- 保留「GitHub / 开源」等核心信息，让人一眼知道是什么。",
        "- 只返回 JSON：{\"candidates\":[\"...\"],\"best\":\"...\"}，不要多余文字。",
    ]
    return "\n".join(parts)


def parse_obj(text):
    text = text.strip()
    if text.startswith("```"):
        text = text[text.find("{"):]
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        sys.exit("模型未返回 JSON:\n" + text[:400])
    return json.loads(text[i:j + 1])


def main():
    today = datetime.now(timezone.utc).date().isoformat()
    ap = argparse.ArgumentParser()
    ap.add_argument("--article", default=os.path.join("data", "articles", today + ".md"))
    ap.add_argument("--daily", default=os.path.join("data", "daily", today + ".json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.article):
        sys.exit(f"找不到文章文件：{args.article}\n先跑 generate_article.py。")
    lines = open(args.article, encoding="utf-8").read().splitlines()
    old_title = lines[0].lstrip("#").strip()

    topics = ""
    if os.path.exists(args.daily):
        topics = "\n".join(f"- {r['full_name']}: {(r.get('description') or '')[:60]}"
                           for r in json.load(open(args.daily, encoding="utf-8")))

    hist = own_top_titles()
    print("历史参考标题:", hist or "（无：本号暂无已发文章数据，走纯大模型）")

    obj = parse_obj(ga.chat([{"role": "system", "content": SYS},
                             {"role": "user", "content": build_prompt(old_title, topics, hist)}]))
    best = (obj.get("best") or old_title).strip()
    print("候选:", obj.get("candidates"))
    print("原标题:", old_title)
    print("新标题:", best)

    if args.dry_run or best == old_title:
        return
    lines[0] = "# " + best
    open(args.article, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("已写回:", args.article)


if __name__ == "__main__":
    main()
