#!/usr/bin/env python3
"""成稿自检：按「完读率」规则卡一遍，不合格就不建草稿。

规则来自 ARTICLE_STYLE_DAILY.md，这里只做能自动判断的那几条。
硬性项不过直接退出 1，run_daily.py 会中止并告警，省得又发一篇 25% 完读的。

    python article_lint.py                  # 检查 data/articles/<今天>.md
    python article_lint.py --article X.md
    python article_lint.py --warn           # 只报告，永远退出 0
"""
import argparse
import os
import re
import sys
from datetime import datetime, timezone

# 目标是 1800~2400；超到 3200 说明完全没压缩，按硬伤处理
GOOD_MIN, GOOD_MAX = 1800, 2600
HARD_MAX = 3200
MIN_IMAGES = 2

OLD_OPENINGS = (
    "又冒出来一批新项目",
    "挑 ",
    "今天给大家介绍",
)


def strip_code(md):
    """去掉代码块，避免把开源地址算进正文字数。"""
    return re.sub(r"^```.*?^```", "", md, flags=re.S | re.M)


def word_count(text):
    """中文字数：去掉空白和 markdown 记号后再数。"""
    return len(re.sub(r"[\s*#>`|\-]", "", text))


def lint(md):
    """返回 (hard, warn, info)。hard 非空就代表不该发。"""
    hard, warn, info = [], [], {}
    body = strip_code(md)
    lines = [ln.strip() for ln in body.splitlines()]
    paras = [ln for ln in lines if ln and not ln.startswith("#")
             and not ln.startswith("|") and not ln.startswith("**")
             and not ln.startswith("今日新增约") and not ln.startswith("目前约")]

    words = word_count(body)
    images = len(re.findall(r"^!\[", md, re.M))
    heads = re.findall(r"^##\s+(.+)$", body, re.M)
    focus = re.search(r"^##\s+先说这\s*(\d+)\s*个", body, re.M)
    quote = re.search(r"^>\s*(.+)$", body, re.M)
    items = len(re.findall(r"^\*\*\d+\*\*$", body, re.M))
    rows = len([ln for ln in lines if ln.startswith("|") and not
                re.fullmatch(r"[\s|:\-]+", ln)])

    info.update(words=words, images=images, items=items, table_rows=rows,
                sections=len(heads), focus=int(focus.group(1)) if focus else 0,
                takeaway=(quote.group(1).strip() if quote else ""))

    opening = paras[0] if paras else ""
    info["opening"] = opening

    # ---- 硬性：结构没生效就不发 ----
    if not focus:
        hard.append("没有「## 先说这 N 个」重点小节——3+其余的结构没生效")
    if any(k in opening for k in OLD_OPENINGS):
        hard.append(f"开头还是旧句式：{opening[:40]}")
    if words > HARD_MAX:
        hard.append(f"正文 {words} 字，超过 {HARD_MAX}，等于没压缩")
    if items == 0:
        hard.append("没有找到 **01** 这类条目编号，wechat_publish 会排错")

    # ---- 提示：不阻断，但值得改 ----
    if not quote:
        warn.append("没有 > 引用块的可转发结论，少了传播入口")
    if words < GOOD_MIN or words > GOOD_MAX:
        warn.append(f"正文 {words} 字，目标区间 {GOOD_MIN}~{GOOD_MAX}")
    if images < MIN_IMAGES:
        warn.append(f"配图只有 {images} 张，目标 ≥{MIN_IMAGES}（重点项目各一张）")
    if len(opening) > 120:
        warn.append(f"开头 {len(opening)} 字，建议压到 100 字以内")
    if not rows:
        warn.append("没有速览表，霸榜项目没被压成一行")
    if info["focus"] and info["items"] and info["focus"] > info["items"]:
        warn.append(f"重点 {info['focus']} 个 > 条目 {info['items']} 个")

    return hard, warn, info


def report(md, path):
    hard, warn, info = lint(md)
    print(f"成稿: {path}")
    print(f"  字数 {info['words']} | 条目 {info['items']} | 重点 {info['focus']}"
          f" | 小标题 {info['sections']} | 配图 {info['images']} | 表格行 {info['table_rows']}")
    print(f"  开头: {info['opening'][:60]}")
    print(f"  结论: {info['takeaway'][:60] or '（缺）'}")
    for h in hard:
        print(f"  [硬伤] {h}")
    for w in warn:
        print(f"  [建议] {w}")
    if not hard and not warn:
        print("  全部通过")
    return hard


def main():
    today = datetime.now(timezone.utc).date().isoformat()
    ap = argparse.ArgumentParser()
    ap.add_argument("--article", default=os.path.join("data", "articles", today + ".md"))
    ap.add_argument("--warn", action="store_true", help="只报告，不阻断")
    args = ap.parse_args()

    if not os.path.exists(args.article):
        sys.exit(f"找不到成稿：{args.article}\n先跑 generate_article.py。")
    hard = report(open(args.article, encoding="utf-8").read(), args.article)
    if hard and not args.warn:
        sys.exit(1)


if __name__ == "__main__":
    main()
