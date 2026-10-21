#!/usr/bin/env python3
"""用大模型把当日榜单生成公众号文章 md。

输入: data/daily/<日期>.selected.json (select_daily.py 产出，含 role/连续天数)
      没有时就退回 data/daily/<日期>.json 的前 N 名
输出: data/articles/<日期>.md  (结构与 wechat_publish.py 期望一致)

role 为 brief 的项目是「连续上榜 3 天以上的霸榜项目」，只写一行卖点，不写正文、不配图。

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

INSTRUCTION = """为下面每个 GitHub 项目写中文介绍。每个项目带一个 role 字段，按它区分写法：
- role 为 "detail"：给一个「一句话卖点」(不超过 20 字，点出它解决什么问题)，以及 2~4 段短介绍。
- role 为 "brief"：只给一个「一句话卖点」(不超过 20 字)，paragraphs 必须是空数组 []。
其余要求：
- 每段一句话，口语化，第二人称「你」，说人话，不用 emoji，不堆营销词。
- detail 项目至少一段讲「怎么用」，可用「比如，」举例；再说明适合谁或注意事项。
- 安全/破解类项目必须加合规或授权提醒。
- 只依据给到的信息写，不要编造未提供的功能。

严格只返回 JSON 数组，不要 markdown 代码块、不要多余文字，格式：
[{"index":1,"pitch":"...","paragraphs":["...","..."]}, {"index":4,"pitch":"...","paragraphs":[]}]

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


def call_llm(key, items):
    slim = [{"index": it["index"], "role": it.get("role", "detail"),
             "full_name": it["full_name"], "description": it.get("description", ""),
             "stars": it.get("stars"), "delta": it.get("delta"),
             "language": it.get("language", "")} for it in items]
    return chat([{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": INSTRUCTION + json.dumps(slim, ensure_ascii=False)}], key)


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


def assemble(items, copies):
    n = len(items)
    out = [f"# 今天 GitHub 上 {n} 个顶顶顶的开源项目。", "",
           f"今天 GitHub 上又冒出来一批新项目，挑 {n} 个带你看一遍。", ""]
    for i, it in enumerate(items, 1):
        c = copies.get(i, {"pitch": "", "paragraphs": []})
        brief = it.get("role") == "brief"
        name = it["full_name"]
        url = it.get("url") or f"https://github.com/{name}"
        head = f"{name}：{c.get('pitch', '')}"
        out += [f"**{i:02d}**", ""]
        if brief:                       # 霸榜项目：一行卖点 + 连续天数，不写正文不配图
            out += [f"**{head}（连续上榜 {it.get('streak', 1)} 天）**", ""]
        else:
            out += [f"**{head}**", ""]
            for p in c.get("paragraphs") or []:
                out += [p, ""]
        d = it.get("delta")
        out.append(f"今日新增约 {d:,} Star。" if d else f"目前约 {(it.get('stars') or 0):,} Star。")
        out += ["", "```", f"开源地址：{url}", "```", ""]
        if not brief:
            out += [f"![{name}](https://opengraph.githubassets.com/1/{name})", ""]
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
    ap.add_argument("--stub", action="store_true")
    args = ap.parse_args()

    items = load_plan(args)
    if not items:
        sys.exit("榜单为空(还没有增量数据)，无可生成内容。")

    if args.stub:
        copies = {it["index"]: {"pitch": f"示例卖点 {it['index']}",
                                "paragraphs": ([] if it.get("role") == "brief"
                                               else ["第一段示例介绍。", "第二段示例介绍。"])}
                  for it in items}
    else:
        key = (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if not key:
            sys.exit("缺少环境变量 LLM_API_KEY (或 DEEPSEEK_API_KEY)")
        copies = {c["index"]: c for c in parse_json_array(call_llm(key, items))}

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(assemble(items, copies))
    print(f"已写入: {args.out}  ({len(items)} 个项目"
          f"，其中霸榜简报 {sum(1 for it in items if it.get('role') == 'brief')} 个)")


if __name__ == "__main__":
    main()
