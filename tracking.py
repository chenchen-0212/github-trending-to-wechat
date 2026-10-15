#!/usr/bin/env python3
"""榜单/深挖 双库追踪（SQLite，零依赖）。

两张表：
  daily_appearance  每天**文章实际收录**的项目 + 连续上榜天数（streak）
  deepdive_queue    深挖队列：连续上榜满 5 天自动入队；队列空了才去发现新项目

规则：
  - 连续上榜满 3 天 → 第 4 天起不再详写，降为一行简报（streak >= 4）
  - 连续上榜满 5 天 → 值得深挖，入队（streak >= 5）
  - streak 按「上一次实际记录过的日期」链式 +1：某天没跑流水线不会断；
    跑过但项目掉出文章 → 重置为 1

    python tracking.py --stats
    python tracking.py --backfill          # 从 data/articles/ + data/daily/ 回填历史
    python tracking.py --selftest
    python tracking.py --enqueue owner/name [--source manual]
    python tracking.py --requeue owner/name
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

DEFAULT_DB = os.path.join("data", "tracking.db")
BRIEF_FROM_STREAK = 4      # 连续第 4 天起转一行简报
DEEPDIVE_FROM_STREAK = 5   # 连续满 5 天 → 值得深挖
MAX_ATTEMPTS = 3           # 深挖失败重试上限，超过转 failed
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 文章里形如 `**owner/repo：卖点**`（简报还带「（连续上榜 N 天）」）
NAME_RE = re.compile(r"^\*\*([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)：")
# 深挖成稿文件名 <日期>-<owner>_<repo>.md（slug 把 / 换成了 _，不可逆）
FEATURE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")
GH_URL_RE = re.compile(r"https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)")

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_appearance (
    date       TEXT    NOT NULL,
    full_name  TEXT    NOT NULL,
    idx        INTEGER NOT NULL,
    role       TEXT    NOT NULL,
    rank       INTEGER,
    stars      INTEGER,
    delta      INTEGER,
    streak     INTEGER NOT NULL,
    seen_at    TEXT    NOT NULL,
    PRIMARY KEY (date, full_name)
);
CREATE INDEX IF NOT EXISTS idx_appear_name ON daily_appearance(full_name, date DESC);
CREATE INDEX IF NOT EXISTS idx_appear_date ON daily_appearance(date);

CREATE TABLE IF NOT EXISTS deepdive_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    max_streak    INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'pending',
    enqueued_at   TEXT    NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    note          TEXT,
    UNIQUE(full_name)
);
CREATE INDEX IF NOT EXISTS idx_queue_pick
    ON deepdive_queue(status, max_streak DESC, enqueued_at ASC, id ASC);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def connect(db_path=DEFAULT_DB):
    """打开库。WAL + busy_timeout 是日报(08:00)与深挖(每5分钟)并发开库的安全垫。"""
    d = os.path.dirname(db_path)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def memory_conn():
    """内存库，供自检与 dry-run 预览用。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


# ---------------------------------------------------------------- streak 引擎

def _prev_streaks(conn, date):
    """上一「实际记录过」的日期的 {full_name: streak}。返回 (映射, 那个日期)。"""
    row = conn.execute("SELECT MAX(date) AS d FROM daily_appearance WHERE date < ?",
                       (date,)).fetchone()
    prev_date = row["d"] if row else None
    if not prev_date:
        return {}, None
    rows = conn.execute("SELECT full_name, streak FROM daily_appearance WHERE date = ?",
                        (prev_date,))
    return {r["full_name"]: r["streak"] for r in rows}, prev_date


def plan_day(conn, date, ranked, top_n=10):
    """决定当天文章收录哪些项目、各自详写还是简报。

    ranked: data/daily/<date>.json 的数组（按名次排序）。
    返回 (entries, summary)。entries 每项含 index/role/streak/rank/... 字段。
    """
    prev, prev_date = _prev_streaks(conn, date)
    entries, details, seen = [], 0, set()
    for rank, it in enumerate(ranked, 1):
        name = it.get("full_name") or ""
        if not name or name in seen:          # 榜单理论上不重复，防一手
            continue
        seen.add(name)
        st = prev.get(name, 0) + 1
        if st >= BRIEF_FROM_STREAK:
            role = "brief"
        else:
            if details >= top_n:              # 详写收满，后面不再看
                break
            role = "detail"
            details += 1
        entries.append({
            "index": len(entries) + 1,
            "role": role,
            "streak": st,
            "rank": rank,
            "full_name": name,
            "url": it.get("url") or f"https://github.com/{name}",
            "description": it.get("description") or "",
            "stars": it.get("stars"),
            "delta": it.get("delta"),
            "language": it.get("language") or "",
            "created_at": it.get("created_at") or "",
        })
    summary = {
        "details": details,
        "briefs": sum(1 for e in entries if e["role"] == "brief"),
        "total": len(entries),
        "short": details < top_n,
        "prev_date": prev_date,
    }
    return entries, summary


def record_day(conn, date, entries):
    """落库当天收录。幂等：先删该日期再插，重跑同一天不会重复计数。"""
    now = _now()
    with conn:
        conn.execute("DELETE FROM daily_appearance WHERE date = ?", (date,))
        conn.executemany(
            "INSERT INTO daily_appearance"
            "(date, full_name, idx, role, rank, stars, delta, streak, seen_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            [(date, e["full_name"], e["index"], e["role"], e.get("rank"),
              e.get("stars"), e.get("delta"), e["streak"], now) for e in entries])
    return len(entries)


def enqueue_streak5(conn, date):
    """把当天达到「值得深挖」门槛的项目入队。返回新入队的名字列表。"""
    added = []
    rows = conn.execute(
        "SELECT full_name, streak FROM daily_appearance WHERE date = ? AND streak >= ?",
        (date, DEEPDIVE_FROM_STREAK)).fetchall()
    for r in rows:
        if enqueue_deepdive(conn, r["full_name"], "streak5", r["streak"]) == "pending":
            added.append(r["full_name"])
    return added


# ---------------------------------------------------------------- 深挖队列

def enqueue_deepdive(conn, full_name, source, max_streak=0):
    """入队（已存在则只抬优先级，不复活 done/skipped）。返回最终 status。"""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO deepdive_queue"
            "(full_name, source, max_streak, status, enqueued_at) VALUES(?,?,?,'pending',?)",
            (full_name, source, max_streak, _now()))
        conn.execute(
            "UPDATE deepdive_queue SET max_streak = MAX(max_streak, ?)"
            " WHERE full_name = ? AND status IN ('pending','in_progress')",
            (max_streak, full_name))
    row = conn.execute("SELECT status FROM deepdive_queue WHERE full_name = ?",
                       (full_name,)).fetchone()
    return row["status"] if row else None


def pending_deepdive(conn):
    """待深挖队列（优先级：连续越久越先，同分先入先出）。"""
    rows = conn.execute(
        "SELECT * FROM deepdive_queue WHERE status = 'pending'"
        " ORDER BY max_streak DESC, enqueued_at ASC, id ASC")
    return [dict(r) for r in rows]


def claim_deepdive(conn):
    """原子认领队首：pending → in_progress。没有可认领的返回 None。"""
    with conn:
        row = conn.execute(
            "SELECT id FROM deepdive_queue WHERE status = 'pending'"
            " ORDER BY max_streak DESC, enqueued_at ASC, id ASC LIMIT 1").fetchone()
        if not row:
            return None
        cur = conn.execute(
            "UPDATE deepdive_queue SET status='in_progress', started_at=?,"
            " attempt_count = attempt_count + 1 WHERE id = ? AND status = 'pending'",
            (_now(), row["id"]))
        if cur.rowcount != 1:               # 被别的进程抢走了
            return None
    got = conn.execute("SELECT * FROM deepdive_queue WHERE id = ?", (row["id"],)).fetchone()
    return dict(got) if got else None


def claim_named(conn, full_name):
    """认领指定项目（发现新项目后入队即认领用）。已被别人认领返回 None。"""
    with conn:
        cur = conn.execute(
            "UPDATE deepdive_queue SET status='in_progress', started_at=?,"
            " attempt_count = attempt_count + 1 WHERE full_name = ? AND status = 'pending'",
            (_now(), full_name))
        if cur.rowcount != 1:
            return None
    got = conn.execute("SELECT * FROM deepdive_queue WHERE full_name = ?",
                       (full_name,)).fetchone()
    return dict(got) if got else None


def _set_status(conn, full_name, status, note):
    with conn:
        conn.execute(
            "UPDATE deepdive_queue SET status=?, finished_at=?, note=? WHERE full_name=?",
            (status, _now() if status in ("done", "failed", "skipped") else None,
             note, full_name))


def mark_deepdive_done(conn, full_name, note=""):
    _set_status(conn, full_name, "done", note)


def mark_deepdive_skipped(conn, full_name, note=""):
    _set_status(conn, full_name, "skipped", note)


def mark_deepdive_failed(conn, full_name, note="", max_attempts=MAX_ATTEMPTS):
    """失败：没到上限就回 pending（下次到点自动重试），到了就转 failed。"""
    row = conn.execute("SELECT attempt_count FROM deepdive_queue WHERE full_name = ?",
                       (full_name,)).fetchone()
    if not row:
        return None
    status = "failed" if row["attempt_count"] >= max_attempts else "pending"
    _set_status(conn, full_name, status, note)
    return status


def reset_stale_in_progress(conn):
    """把上次崩溃留下的 in_progress 复位成 pending。返回复位条数。"""
    with conn:
        cur = conn.execute(
            "UPDATE deepdive_queue SET status='pending' WHERE status='in_progress'")
    return cur.rowcount


def deepdived_names(conn):
    """已经在管（含已完成）的项目，用于发现新项目时避重。"""
    rows = conn.execute(
        "SELECT full_name FROM deepdive_queue WHERE status IN ('pending','in_progress','done')")
    return {r["full_name"] for r in rows}


def requeue(conn, full_name):
    """逃生口：把 failed/skipped 重新放回队列。"""
    with conn:
        cur = conn.execute(
            "UPDATE deepdive_queue SET status='pending', attempt_count=0,"
            " note=NULL, started_at=NULL, finished_at=NULL"
            " WHERE full_name = ? AND status IN ('failed','skipped')", (full_name,))
    return cur.rowcount


# ---------------------------------------------------------------- 历史回填

def names_from_article(path):
    """从文章 md 里按顺序取出收录的项目名。"""
    names = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            m = NAME_RE.match(ln.strip())
            if m:
                names.append(m.group(1))
    return names


def full_name_from_article(path, slug):
    """从深挖成稿正文里还原 owner/repo。

    文件名 slug 把 `/` 换成了 `_`（`owner_repo`），单靠它反推在有下划线的 owner 上会错，
    所以优先取正文里 GitHub 地址中「slug 形式与文件名一致」的那个；只有一个地址时直接用。
    """
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    cands = []
    for m in GH_URL_RE.finditer(text):
        name = m.group(1)
        if name.endswith(".git"):
            name = name[:-4]
        if name not in cands:
            cands.append(name)
    for c in cands:
        if c.replace("/", "_") == slug:
            return c
    return cands[0] if len(cands) == 1 else None


def backfill(conn, daily_dir=os.path.join("data", "daily"),
             articles_dir=os.path.join("data", "articles"),
             reset=True, dry_run=False):
    """用历史文章 + 榜单 json 播种 DB。返回每个日期的报告行。"""
    dates = set()
    for d, suffix in ((articles_dir, ".md"), (daily_dir, ".json")):
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(suffix) and DATE_RE.match(f[:-len(suffix)]):
                    dates.add(f[:-len(suffix)])
    report = []
    work = memory_conn() if dry_run else conn   # 预览走影子库：不写真实库，但连续天数能链上
    if reset and not dry_run:
        with work:
            work.execute("DELETE FROM daily_appearance")
    for date in sorted(dates):
        json_path = os.path.join(daily_dir, date + ".json")
        md_path = os.path.join(articles_dir, date + ".md")
        by_name, rank_of = {}, {}
        if os.path.exists(json_path):
            ranked = json.load(open(json_path, encoding="utf-8"))
            for i, it in enumerate(ranked, 1):
                by_name[it.get("full_name")] = it
                rank_of[it.get("full_name")] = i
        else:
            ranked = []
        if os.path.exists(md_path):
            names = names_from_article(md_path)
            src = "文章"
        else:
            names = [it.get("full_name") for it in ranked[:10]]
            src = "榜单前10"
        if not names:
            report.append(f"{date}: 跳过（既无文章也无榜单）")
            continue
        prev, _ = _prev_streaks(work, date)
        entries = []
        for i, name in enumerate(names, 1):
            it = by_name.get(name, {})
            st = prev.get(name, 0) + 1
            entries.append({
                "index": i,
                "role": "brief" if st >= BRIEF_FROM_STREAK else "detail",
                "streak": st,
                "rank": rank_of.get(name),
                "full_name": name,
                "stars": it.get("stars"),
                "delta": it.get("delta"),
            })
        top = max(e["streak"] for e in entries)
        record_day(work, date, entries)
        enqueue_streak5(work, date)
        report.append(f"{date}: {len(entries)} 项（{src}），最高连续 {top} 天"
                      + ("" if top < BRIEF_FROM_STREAK
                         else f"，其中 {sum(1 for e in entries if e['role'] == 'brief')} 项已转简报"))
    return report


def backfill_deepdive(conn, features_dir=os.path.join("data", "features"), dry_run=False):
    """把**已经写过的深挖成稿**补录进 deepdive_queue（status=done），免得再被深挖一遍。

    文件在 data/features/ 里但队列里没有记录的项目，靠这一步补上；
    队列里原来是 pending/in_progress 的，有成稿就以成稿为准置为 done。
    """
    if not os.path.isdir(features_dir):
        return []
    work = memory_conn() if dry_run else conn
    report = []
    for f in sorted(os.listdir(features_dir)):
        m = FEATURE_RE.match(f)
        if not m:
            continue
        date, slug = m.group(1), m.group(2)
        path = os.path.join(features_dir, f)
        name = full_name_from_article(path, slug) or slug.replace("_", "/", 1)
        when = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat()
        note = f"已有成稿：{path}"
        with work:
            row = work.execute("SELECT status FROM deepdive_queue WHERE full_name = ?",
                               (name,)).fetchone()
            if row:
                prev = row["status"]
                work.execute("UPDATE deepdive_queue SET status='done', finished_at=?, note=?"
                             " WHERE full_name = ?", (when, note, name))
                report.append(f"{date}  {name}  → done（原 {prev}，{f}）")
            else:
                work.execute(
                    "INSERT INTO deepdive_queue"
                    "(full_name, source, max_streak, status, enqueued_at, finished_at,"
                    " attempt_count, note) VALUES(?, 'backfill', 0, 'done', ?, ?, 0, ?)",
                    (name, when, when, note))
                report.append(f"{date}  {name}  → done（补录，{f}）")
    return report


def stats(conn):
    d = {}
    row = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT date) AS days, MIN(date) AS lo, MAX(date) AS hi"
        " FROM daily_appearance").fetchone()
    d["记录天数"] = row["days"]
    d["收录条数"] = row["n"]
    d["日期范围"] = f"{row['lo']} ~ {row['hi']}" if row["lo"] else "(空)"
    d["最长连续"] = [dict(r) for r in conn.execute(
        "SELECT full_name, streak, date FROM daily_appearance"
        " ORDER BY streak DESC, date DESC LIMIT 5")]
    d["深挖队列"] = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM deepdive_queue GROUP BY status")}
    return d


# ---------------------------------------------------------------- 自检

def selftest():
    fresh = memory_conn

    def ent(i, name, streak):
        return {"index": i, "role": "detail", "streak": streak, "rank": i,
                "full_name": name, "stars": 1, "delta": 1}

    def ranked(n, first="o1/r"):
        out = [{"full_name": first, "url": "u", "description": "d", "stars": 1,
                "delta": 1, "language": "Go", "created_at": "c"}]
        out += [{"full_name": f"x{i}/r", "url": "u", "description": "d", "stars": 1,
                 "delta": 1, "language": "Go", "created_at": "c"} for i in range(2, n + 1)]
        return out

    # 1) 连续三天（每天文章里只有它）→ streak 1,2,3
    c = fresh()
    for d, want in (("2026-01-01", 1), ("2026-01-02", 2), ("2026-01-03", 3)):
        entries, _ = plan_day(c, d, ranked(1))
        assert entries[0]["full_name"] == "o1/r" and entries[0]["streak"] == want, (d, entries[0])
        assert entries[0]["role"] == "detail"
        record_day(c, d, entries)

    # 2) 第 4 天 → 转简报；详写仍收满 10 篇
    entries, summary = plan_day(c, "2026-01-04", ranked(20))
    assert entries[0]["streak"] == 4 and entries[0]["role"] == "brief", entries[0]
    assert summary["details"] == 10 and summary["total"] == 11, summary
    record_day(c, "2026-01-04", entries)

    # 3) 连续满 5 天 → 自动入队
    entries, _ = plan_day(c, "2026-01-05", ranked(20))
    record_day(c, "2026-01-05", entries)
    assert enqueue_streak5(c, "2026-01-05") == ["o1/r"], pending_deepdive(c)

    # 4) 中间某天没跑流水线 → 连续天数不断
    c = fresh()
    record_day(c, "2026-02-01", [ent(1, "a/b", 1)])
    entries, _ = plan_day(c, "2026-02-03", ranked(3, "a/b"))
    assert entries[0]["streak"] == 2, entries[0]

    # 5) 跑过但项目掉出文章 → 重置为 1
    c = fresh()
    record_day(c, "2026-03-01", [ent(1, "a/b", 1)])
    record_day(c, "2026-03-02", [ent(1, "z/z", 1)])
    entries, _ = plan_day(c, "2026-03-03", ranked(3, "a/b"))
    assert entries[0]["streak"] == 1, entries[0]

    # 6) 同一天重跑 → 幂等
    c = fresh()
    e, _ = plan_day(c, "2026-04-01", ranked(5))
    record_day(c, "2026-04-01", e)
    record_day(c, "2026-04-01", e)
    assert c.execute("SELECT COUNT(*) n FROM daily_appearance").fetchone()["n"] == len(e)
    assert c.execute("SELECT streak FROM daily_appearance WHERE date='2026-04-01'").fetchone()["streak"] == 1

    # 7) 队列状态机：认领 → 完成；失败 3 次转 failed
    c = fresh()
    enqueue_deepdive(c, "a/b", "manual", 9)
    assert enqueue_deepdive(c, "a/b", "manual", 9) == "pending"
    row = claim_deepdive(c)
    assert row["full_name"] == "a/b" and row["status"] == "in_progress", row
    mark_deepdive_done(c, "a/b", "ok")
    assert "a/b" not in [q["full_name"] for q in pending_deepdive(c)]
    assert "a/b" in deepdived_names(c)

    enqueue_deepdive(c, "c/d", "manual")
    assert claim_deepdive(c)["full_name"] == "c/d"
    assert mark_deepdive_failed(c, "c/d") == "pending"      # 第 1 次失败 → 回队列
    assert pending_deepdive(c)[0]["full_name"] == "c/d"
    claim_deepdive(c)
    mark_deepdive_failed(c, "c/d")
    claim_deepdive(c)
    assert mark_deepdive_failed(c, "c/d") == "failed"       # 第 3 次 → 终态
    assert "c/d" not in [q["full_name"] for q in pending_deepdive(c)]
    assert requeue(c, "c/d") == 1 and pending_deepdive(c)[0]["full_name"] == "c/d"
    assert enqueue_deepdive(c, "a/b", "manual", 99) == "done"   # 不复活已完成

    # 8) 崩溃残留复位
    c = fresh()
    enqueue_deepdive(c, "e/f", "manual")
    claim_deepdive(c)
    assert reset_stale_in_progress(c) == 1
    assert pending_deepdive(c)[0]["full_name"] == "e/f"

    # 9) 队列优先级：连续越久越先
    c = fresh()
    enqueue_deepdive(c, "low/x", "manual", 5)
    enqueue_deepdive(c, "high/y", "manual", 8)
    assert claim_deepdive(c)["full_name"] == "high/y"

    # 10) 文章项目名解析（含简报行）
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# 标题\n\n**01**\n\n**storytold/photocraft：纯 Rust 照片编辑器**\n\n正文。\n\n"
                "**02**\n\n**a/b：卖点（连续上榜 4 天）**\n\n今日新增约 1 Star。\n\n"
                "**点击下方卡片，关注我们**\n")
        tmp = f.name
    assert names_from_article(tmp) == ["storytold/photocraft", "a/b"], names_from_article(tmp)
    os.unlink(tmp)

    # 11) 深挖成稿还原 owner/repo：正文混有同类项目地址时要挑 slug 对得上的那个
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# 标题\n\n和 https://github.com/other/textract 比一下。\n\n"
                "项目地址：https://github.com/microsoft/markitdown\n"
                "装它：git clone https://github.com/microsoft/markitdown.git\n")
        tmp = f.name
    assert full_name_from_article(tmp, "microsoft_markitdown") == "microsoft/markitdown", \
        full_name_from_article(tmp, "microsoft_markitdown")
    # owner 带下划线时，正文里只有一个地址也能认出来（slug 反推会错）
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("项目地址：https://github.com/my_org/tool\n")
        tmp2 = f.name
    assert full_name_from_article(tmp2, "my_org_tool") == "my_org/tool"
    os.unlink(tmp)
    os.unlink(tmp2)

    print("tracking selftest ok")


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="榜单/深挖追踪库")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--no-reset", action="store_true", help="backfill 时保留已有记录")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--enqueue", metavar="OWNER/NAME")
    ap.add_argument("--requeue", metavar="OWNER/NAME")
    ap.add_argument("--source", default="manual")
    ap.add_argument("--max-streak", type=int, default=0)
    ap.add_argument("--daily-dir", default=os.path.join("data", "daily"))
    ap.add_argument("--articles-dir", default=os.path.join("data", "articles"))
    ap.add_argument("--features-dir", default=os.path.join("data", "features"))
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    conn = connect(args.db)
    init_db(conn)

    if args.backfill:
        prefix = "预览: " if args.dry_run else ""
        print(f"{prefix}[日报] 上榜记录")
        for line in backfill(conn, args.daily_dir, args.articles_dir,
                             reset=not args.no_reset, dry_run=args.dry_run):
            print(prefix + line)
        print(f"{prefix}[深挖] 已有成稿")
        rows = backfill_deepdive(conn, args.features_dir, args.dry_run)
        for line in rows:
            print(prefix + line)
        if not rows:
            print(prefix + "（data/features/ 下没有成稿）")
        return
    if args.enqueue:
        print(f"{args.enqueue} → {enqueue_deepdive(conn, args.enqueue, args.source, args.max_streak)}")
    if args.requeue:
        print(f"{args.requeue} 复位 {requeue(conn, args.requeue)} 条")
    if args.stats or not (args.enqueue or args.requeue):
        d = stats(conn)
        for k in ("记录天数", "收录条数", "日期范围"):
            print(f"{k}: {d[k]}")
        print("深挖队列:", d["深挖队列"] or "(空)")
        for r in d["最长连续"]:
            print(f"  最长连续: {r['full_name']} {r['streak']} 天（截至 {r['date']}）")
        queue = pending_deepdive(conn)
        if queue:
            print(f"待深挖 {len(queue)} 个，队首 {queue[0]['full_name']}"
                  f"（连续 {queue[0]['max_streak']} 天）")


if __name__ == "__main__":
    main()
