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

# 号主总结的四个起标题公式。候选标题必须落在其中一种上，别自由发挥。
FORMULAS = """四个最常用的起标题公式（每个候选都要明确属于其中一种）：
1. 数字+结果：标题里放一个具体数字，再告诉读者看完能得到什么。数字让人信，结果让人有期待。
   例：我用这3个方法，一个月瘦了10斤 / 坚持这5个习惯，我戒掉了熬夜
2. 痛点+解决方案：先说出读者心里正在烦的事，再暗示本文能帮他解决。痛点是"这不就是在说我吗"。
   例：为什么你越努力越焦虑？答案可能和你想的不一样 / 天天加班还是没成绩？问题出在这3个细节上
3. 悬念+反转：制造悬念或给出反常识的观点，打破大家习以为常的想法。
   例：我劝你，不要做一个太合群的人 / 那个从不发朋友圈的同事，升职了
4. 对比+反差：把两个有落差的数字或处境并排放，利用攀比心理。
   例：同样工作5年，为什么别人月薪3万，你还在拿8千 / 她30岁退休环游世界，你30岁还在还房贷"""

# 四个公式的总结，既是判断标准，也得体现在标题本身
SUMMARY = """好标题其实没那么玄乎，无非是让读者觉得两件事：这事儿跟我有关，看完我有收获。
所以标题里要能看见「我」（你/你的处境）和「收获」（一个结果、一个答案、一个判断），
只是报菜名式地罗列项目名，这两样一个都不占，不算合格。"""


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
        FORMULAS,
        "",
        SUMMARY,
        "",
        "请给出 6 个候选标题，并选出最好的一个。要求：",
        "- 前 4 个分别套用公式 1~4 各一个，剩下 2 个可组合两种公式；优先用本文项目里真实出现的数字。",
        "- 每条候选都用上面的总结自检：能不能看出「这跟我有关」和「看完我有什么收获」，缺一个就重写。",
        "- 中文，不超过 24 字；口语化、有钩子，但不夸大、不做标题党。",
        "- 数字只能来自给定的项目数据，不许编；反差只能来自项目本身的事实。",
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
