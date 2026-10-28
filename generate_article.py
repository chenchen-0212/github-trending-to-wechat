#!/usr/bin/env python3
"""用大模型把当日榜单生成公众号文章 md。

输入: data/daily/<日期>.selected.json (select_daily.py 产出，含 role/连续天数)
      没有时就退回 data/daily/<日期>.json 的前 N 名
输出: data/articles/<日期>.md  (结构与 wechat_publish.py 期望一致)

写法规矩见 ARTICLE_STYLE_DAILY.md（3 个重点 + 其余降级 + 开头钩子 + 可转发结论），
那份文件存在就拼进提示词；不存在则用内置规则。

role 为 brief 的项目是「第 2 次上榜起的霸榜项目」，压成文末速览表的一行，不写正文、不配图。

环境变量(OpenAI 兼容接口):
    LLM_API_KEY 或 DEEPSEEK_API_KEY   必需(二选一)
    LLM_BASE_URL   默认 https://api.deepseek.com/v1
    LLM_MODEL      默认 deepseek-chat

    python generate_article.py            # 调模型生成
    python generate_article.py --stub     # 不联网，用假文案验证拼装
"""
import argparse
import ast
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

import netutil

BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

SYSTEM = "你是公众号「逛逛GitHub」的编辑，专门把 GitHub 新项目写成通俗易懂的中文介绍。"

STYLE_PATH = "ARTICLE_STYLE_DAILY.md"

INSTRUCTION = """为下面每个 GitHub 项目写中文介绍，另外给全文写一个开头和一句结尾。

【结构：{focus} 个重点 + 其余降级】
列表按顺序排列，前 {focus} 个 role=detail 的项目是重点，必须写透；之后的 detail 写短介绍。
- 前 {focus} 个 detail（重点）：pitch 是一句话卖点（≤20 字，点出它替读者解决了什么麻烦），
  paragraphs 固定 3 段，顺序不能变：
  ① 这个项目针对的那个具体麻烦（写场景，不写形容词）
  ② 它怎么做到 / 你怎么用，用「比如，」举一个能看见画面的例子
  ③ 什么人不适合、有什么坑（安全、授权、平台限制、上手门槛）
- 第 {focus_plus} 个 detail 起（普通）：pitch 同上，paragraphs 1~2 段，只讲「它是什么、适合谁」，
  不展开用法。
- role 为 "brief"：只要一个 pitch（≤18 字），paragraphs 必须是空数组 []。

【开头 hook】
2~3 个短句，合计不超过 100 字，必须含：一句能让人对号入座的场景或痛点；
一句明确结论（这几 N 个里哪几个最值得看，点名）；一句路线指引（先说前 {focus} 个，其余在文末速览）。
禁止「今天又冒出来一批新项目」「挑 N 个带你看一遍」这类开场。

【结尾 takeaway】
一句话，不超过 30 字，要能被读者截图转发到群里：有判断、有反差，
不能是「快去试试」。例：租卡比赌一张显卡便宜。

其余要求：
- 每段一句话，口语化，第二人称「你」，说人话，不用 emoji，不堆营销词。
- 安全/破解类项目必须加合规或授权提醒。
- 只依据给到的信息写，不要编造未提供的功能。

严格只返回 JSON 对象，不要 markdown 代码块、不要多余文字，格式：
{{"hook":"...","takeaway":"...","items":{{"owner/repo":{{"pitch":"...","paragraphs":["...","...","..."]}}}}}}

items 的 key 必须是项目列表里原样照抄的 full_name，一个字都不要改、不要重新编号。

项目列表：
"""


def chat(messages, key=None, temperature=0.7):
    """通用 OpenAI 兼容调用；key 缺省时从环境变量取。"""
    key = key or (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        sys.exit("缺少环境变量 LLM_API_KEY (或 DEEPSEEK_API_KEY)")
    payload = json.dumps({"model": MODEL, "messages": messages,
                          "temperature": temperature}).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/chat/completions", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {key}")
    try:
        with netutil.urlopen(req, timeout=180) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"LLM 调用失败 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    if "choices" not in data:                  # 缺 choices 时直接抛 KeyError 看不出原因，这里说清楚
        sys.exit(f"LLM 返回异常(无 choices): {json.dumps(data, ensure_ascii=False)[:300]}")
    return data["choices"][0]["message"]["content"]


def load_style():
    """读 ARTICLE_STYLE_DAILY.md；没有就只用内置规则，不让缺文档拖垮日报。"""
    if not os.path.exists(STYLE_PATH):
        print(f"提示: 没找到 {STYLE_PATH}，只用内置规则生成")
        return ""
    return "\n\n【写作风格约束】\n" + open(STYLE_PATH, encoding="utf-8").read()


def build_instruction(focus):
    return INSTRUCTION.format(focus=focus, focus_plus=focus + 1)


def call_llm(key, items, focus=3):
    slim = [{"index": it["index"], "role": it.get("role", "detail"),
             "full_name": it["full_name"], "description": it.get("description", ""),
             "stars": it.get("stars"), "delta": it.get("delta"),
             "language": it.get("language", "")} for it in items]
    prompt = build_instruction(focus) + load_style() + json.dumps(slim, ensure_ascii=False)
    return chat([{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": prompt}], key)


def parse_json_array(text):
    """解析模型返回的 JSON 数组；对常见的模型手误做兜底。

    依次尝试：原样 json.loads → 去掉尾随逗号 → ast.literal_eval(单引号/True/None)。
    都不行才退出，并把原文片段打出来。
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text).rstrip("`").strip()
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j < 0:
        sys.exit("模型未返回 JSON 数组:\n" + text[:500])
    body = text[i:j + 1]
    for candidate in (body, re.sub(r",\s*([}\]])", r"\1", body)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data
    try:                        # 模型偶尔吐 Python 字面量：'key': 'v' / True / None
        data = ast.literal_eval(body)
        if isinstance(data, list):
            return data
    except (ValueError, SyntaxError):
        pass
    sys.exit("模型返回的 JSON 无法解析:\n" + body[:500])


def split_hook(hook):
    """开头拆成短段；模型返回一整段时也照原样用。"""
    return [p.strip() for p in re.split(r"\n+", (hook or "").strip()) if p.strip()]


def item_block(it, copy, with_image):
    """一个详写条目：编号 → 标题 → 正文 → 涨星 → 地址 →（可选）配图。"""
    name = it["full_name"]
    url = it.get("url") or f"https://github.com/{name}"
    out = [f"**{it['index']:02d}**", "", f"**{name}：{copy.get('pitch', '')}**", ""]
    for p in copy.get("paragraphs") or []:
        out += [p, ""]
    d = it.get("delta")
    out.append(f"今日新增约 {d:,} Star。" if d else f"目前约 {(it.get('stars') or 0):,} Star。")
    out += ["", "```", f"开源地址：{url}", "```", ""]
    if with_image:
        out += [f"![{name}](https://opengraph.githubassets.com/1/{name})", ""]
    return out


def parse_payload(text):
    """解析模型返回的 {hook, takeaway, items}；退回旧格式(纯数组)也能用。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text).rstrip("`").strip()
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        body = text[i:j + 1]
        for cand in (body, re.sub(r",\s*([}\]])", r"\1", body)):
            try:
                data = json.loads(cand)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("items"):
                return (str(data.get("hook") or "").strip(),
                        str(data.get("takeaway") or "").strip(),
                        normalize_items(data["items"]))
    return "", "", parse_json_array(text)      # 退回旧格式：只有数组，没有开头和结论


def normalize_items(items):
    """统一成 [{full_name, pitch, paragraphs}]；模型返回 dict 时把 key 当 full_name。"""
    if isinstance(items, dict):
        return [dict(v, full_name=k) for k, v in items.items() if isinstance(v, dict)]
    return [c for c in items if isinstance(c, dict)]


def build_copies(raw, items):
    """按仓库全名对齐文案。

    曾经按 index 对齐，模型编号一漂移就把 A 的介绍贴到 B 名下（ub-mode 的正文
    挂到 word-atlas 底下），所以现在只认 full_name，index 仅作旧格式兜底。
    """
    by_name = {c["full_name"]: c for c in raw if c.get("full_name")}
    by_idx = {c["index"]: c for c in raw if c.get("index") is not None and not c.get("full_name")}
    copies, missing = {}, []
    for it in items:
        c = by_name.get(it["full_name"]) or by_idx.get(it["index"])
        if c is None:
            missing.append(it["full_name"])
            c = {"pitch": "", "paragraphs": []}
        copies[it["full_name"]] = c
    if missing:
        print(f"⚠️ {len(missing)} 个项目没拿到文案: {', '.join(missing)}")
    return copies


def assemble(items, copies, hook="", takeaway="", focus=3):
    """3 个重点写透 + 中间简写 + 霸榜压成速览表 + 一句可转发结论。

    完读率靠的就是这个骨架：读者开头就知道终点在哪，中途每 300 字有小标题或表格断点，
    结尾留一句能截图发群的话。字数目标 1800~2400。
    """
    n = len(items)
    detail_idx = [it["index"] for it in items if it.get("role") != "brief"]
    head_set = set(detail_idx[:focus])
    middle = [it for it in items if it.get("role") != "brief" and it["index"] not in head_set]
    briefs = [it for it in items if it.get("role") == "brief"]

    out = [f"# 今天 GitHub 上 {n} 个顶顶顶的开源项目。", ""]
    hook_paras = split_hook(hook)
    if hook_paras:
        for p in hook_paras:
            out += [p, ""]
    else:                                    # 模型没给开头时退回旧写法，至少不失焦
        out += [f"今天 GitHub 上又冒出来一批新项目，挑 {n} 个带你看一遍。", ""]

    if head_set:
        out += [f"## 先说这 {len(head_set)} 个", ""]
        for it in items:
            if it["index"] in head_set:
                out += item_block(it, copies[it["full_name"]], True)

    if middle:
        out += ["## 这几个也值得看", ""]
        for it in middle:
            out += item_block(it, copies[it["full_name"]], False)

    if briefs:
        out += [f"## 另外 {len(briefs)} 个，一句话带过", "",
                "| 项目 | 一句话 | 今日新增 |", "| --- | --- | --- |"]
        for it in briefs:
            c = copies[it["full_name"]]
            d = it.get("delta")
            star = f"+{d:,}" if d else f"{(it.get('stars') or 0):,}"
            streak = it.get("streak", 1)
            out.append(f"| {it['full_name']}（连榜 {streak} 天）| {c.get('pitch', '')} | {star} |")
        out.append("")

    if takeaway.strip():
        out += ["> " + takeaway.strip(), ""]

    out += ["**点击下方卡片，关注我们**", "",
            "每天为你整理 GitHub 上冒出来的新项目，不错过每一个值得看的开源作品。", ""]
    return "\n".join(out)


def load_plan(args):
    """优先用 select_daily 产出的 selected.json；没有就退回当天榜单前 N 名（全部详写）。"""
    if os.path.exists(args.selected):
        d = json.load(open(args.selected, encoding="utf-8"))
        items = d.get("items") or []
        if items:
            s = d.get("summary") or {}
            print(f"选品: {args.selected}"
                  f"（详写 {s.get('details', '?')} 篇 + 霸榜简报 {s.get('briefs', 0)} 篇）")
            return items
    if not os.path.exists(args.input):
        sys.exit(f"找不到榜单文件：{args.input}（也没有 {args.selected}）\n"
                 f"通常是因为今天还没跑 fetch_and_rank.py，或还没有增量数据(需两天快照才有榜)。")
    raw = json.load(open(args.input, encoding="utf-8"))[:args.top]
    print(f"选品: {args.input} 前 {len(raw)} 名（无 selected.json，全部按详写处理）")
    return [dict(it, index=i, role="detail", streak=1) for i, it in enumerate(raw, 1)]


def main():
    today = datetime.now(timezone.utc).date().isoformat()
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join("data", "daily", today + ".json"))
    ap.add_argument("--selected", default=os.path.join("data", "daily", today + ".selected.json"))
    ap.add_argument("--out", default=os.path.join("data", "articles", today + ".md"))
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--focus", type=int, default=3, help="重点写透的篇数(前 N 个详写项目)")
    ap.add_argument("--stub", action="store_true")
    args = ap.parse_args()

    items = load_plan(args)
    if not items:
        sys.exit("榜单为空(还没有增量数据)，无可生成内容。")

    if args.stub:
        copies = {it["full_name"]: {"pitch": f"示例卖点 {it['index']}",
                                "paragraphs": ([] if it.get("role") == "brief"
                                               else ["第一段示例介绍。", "第二段示例介绍。"])}
                  for it in items}
        hook, takeaway = "示例开头一。\n示例开头二。", "示例结论。"
    else:
        key = (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if not key:
            sys.exit("缺少环境变量 LLM_API_KEY (或 DEEPSEEK_API_KEY)")
        hook, takeaway, raw = parse_payload(call_llm(key, items, args.focus))
        copies = build_copies(raw, items)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(
        assemble(items, copies, hook, takeaway, args.focus))
    print(f"已写入: {args.out}  ({len(items)} 个项目"
          f"，其中霸榜简报 {sum(1 for it in items if it.get('role') == 'brief')} 个)")


if __name__ == "__main__":
    main()
