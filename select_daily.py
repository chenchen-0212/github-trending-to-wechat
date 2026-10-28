#!/usr/bin/env python3
"""每日第 2 步：落库当天榜单、判定霸榜，产出 <日期>.selected.json。

规则（详见 tracking.py）：
  - 第 2 次上榜起降为一行简报（不写正文、不配图）
  - 简报最多挂 2 次 → 第 4 次上榜起不再收录（库里继续记，避免第二天重新被详写）
  - 那天还有它，榜单项目数就 +1：从第 11 名起往后补，直到凑满 10 篇详写
  - 连续满 5 天 → 入深挖队列

    python select_daily.py
    python select_daily.py --date 2026-10-13 --dry-run
    python select_daily.py --input data/daily/2026-10-12.json --out data/daily/2026-10-13.selected.json
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import tracking

try:
    import notify
except Exception:
    notify = None


def fallback(ranked, top):
    """追踪库不可用时的退化方案：简单取前 top 名，全部详写。"""
    out = []
    for rank, it in enumerate(ranked[:top], 1):
        out.append({
            "index": rank, "role": "detail", "streak": 1, "rank": rank,
            "full_name": it.get("full_name"), "url": it.get("url") or "",
            "description": it.get("description") or "", "stars": it.get("stars"),
            "delta": it.get("delta"), "language": it.get("language") or "",
            "created_at": it.get("created_at") or "",
        })
    return out


def plan(db, date, ranked, top, dry_run):
    """返回 (entries, summary, 新入深挖队列的项目名)。dry_run 时不写库。"""
    conn = tracking.connect(db)
    tracking.init_db(conn)
    entries, summary = tracking.plan_day(conn, date, ranked, top_n=top)
    # dropped 只落库不上文章：断了库里的连续天数，第二天它又会变回 streak 1 被详写
    live = [e for e in entries if e["role"] != "dropped"]
    for i, e in enumerate(live, 1):
        e["index"] = i
    if live and not summary["details"]:
        # 极端情况：当年榜单全是霸榜项目。至少留 1 篇详写，否则文章没有封面图发不出去。
        live[0]["role"] = "detail"
        summary["details"], summary["briefs"] = 1, summary["briefs"] - 1
        summary["short"] = False
    if dry_run:
        return live, summary, []
    tracking.record_day(conn, date, entries)
    return live, summary, tracking.enqueue_streak5(conn, date)


def main():
    today = datetime.now(timezone.utc).date().isoformat()
    ap = argparse.ArgumentParser(description="记榜 + 霸榜判定")
    ap.add_argument("--date", default=today)
    ap.add_argument("--input", help="默认 data/daily/<date>.json")
    ap.add_argument("--out", help="默认 data/daily/<date>.selected.json")
    ap.add_argument("--db", default=tracking.DEFAULT_DB)
    ap.add_argument("--top", type=int, default=10, help="详写篇数")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = args.input or os.path.join("data", "daily", args.date + ".json")
    out = args.out or os.path.join("data", "daily", args.date + ".selected.json")
    if not os.path.exists(src):
        sys.exit(f"找不到榜单文件：{src}\n先跑 fetch_and_rank.py。")
    ranked = json.load(open(src, encoding="utf-8"))
    if not ranked:
        sys.exit(f"榜单为空：{src}")

    try:
        entries, summary, added = plan(args.db, args.date, ranked, args.top, args.dry_run)
    except Exception as e:
        # 追踪库出问题不能拖垮日报：退化成取前 N 名，漏记的那天之后可补跑
        print(f"⚠️ 追踪库异常，本次退化为取前 {args.top} 名：{type(e).__name__}: {e}")
        print(f"   事后补记：python select_daily.py --date {args.date}")
        if notify:
            try:
                notify.send("GitHub日报告警 · 追踪库异常",
                            f"{type(e).__name__}: {e}\n已退化为取前 {args.top} 名，"
                            f"{args.date} 未记入库。", "服务器报警")
            except Exception:
                pass
        entries, added = fallback(ranked, args.top), []
        summary = {"details": len(entries), "briefs": 0, "total": len(entries),
                   "short": len(entries) < args.top, "prev_date": None}

    if args.dry_run:
        print(f"---- 预览 {args.date}（上一记录日 {summary['prev_date']}）----")
        for e in entries:
            print(f"  {e['index']:02d} [{'简报' if e['role'] == 'brief' else '详写'}]"
                  f" 名次{e['rank']:>2} 连续{e['streak']}天  {e['full_name']}")
        print(f"合计 {summary['total']} 项（详写 {summary['details']} + 简报 {summary['briefs']}）"
              + (f"，另有 {summary['dropped']} 项连续上榜满 4 次已停更" if summary.get("dropped") else ""))
        return

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"date": args.date, "top_n": args.top,
                   "summary": summary, "items": entries}, f,
                  ensure_ascii=False, indent=1)
    print(f"已记录 {summary['total']} 个上榜项目；详写 {summary['details']} 篇"
          f" + 霸榜简报 {summary['briefs']} 篇"
          + ("（榜单不足，未凑满 10 篇）" if summary["short"] else "")
          + (f"；{summary['dropped']} 项连续上榜满 4 次，本期起停更" if summary.get("dropped") else ""))
    if added:
        print(f"新入深挖队列 {len(added)} 个: {', '.join(added)}")
    print(f"已写入: {out}")


if __name__ == "__main__":
    main()
