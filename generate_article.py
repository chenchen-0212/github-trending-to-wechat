#!/usr/bin/env python3
"""用大模型把当日榜单生成公众号文章 md。

输入: data/daily/<日期>.json   (fetch_and_rank.py 产出的结构化榜单)
输出: data/articles/<日期>.md  (结构与 wechat_publish.py 期望一致)

环境变量(OpenAI 兼容接口):
    LLM_API_KEY 或 DEEPSEEK_API_KEY   必需(二选一)
    LLM_BASE_URL   默认 https://api.deepseek.com/v1
    LLM_MODEL      默认 deepseek-chat

    python generate_article.py            # 调模型生成
    python generate_article.py --stub     # 不联网，用假文案验证拼装
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

SYSTEM = "你是公众号「逛逛GitHub」的编辑，专门把 GitHub 新项目写成通俗易懂的中文介绍。"

INSTRUCTION = """为下面每个 GitHub 项目写中文介绍。要求：
- 每个项目给一个「一句话卖点」(不超过 20 字，点出它解决什么问题)，以及 2~4 段短介绍。
- 每段一句话，口语化，第二人称「你」，说人话，不用 emoji，不堆营销词。
- 至少一段讲「怎么用」，可用「比如，」举例；再说明适合谁或注意事项。
- 安全/破解类项目必须加合规或授权提醒。
- 只依据给到的信息写，不要编造未提供的功能。

严格只返回 JSON 数组，不要 markdown 代码块、不要多余文字，格式：
[{"index":1,"pitch":"...","paragraphs":["...","..."]}, ...]

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
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"LLM 调用失败 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    return data["choices"][0]["message"]["content"]


def call_llm(key, items):
    return chat([{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": INSTRUCTION + json.dumps(items, ensure_ascii=False)}], key)


def parse_json_array(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text).rstrip("`").strip()
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j < 0:
        sys.exit("模型未返回 JSON 数组:\n" + text[:500])
    return json.loads(text[i:j + 1])


def assemble(items, copies):
    n = len(items)
    out = [f"# 今天 GitHub 上 {n} 个顶顶顶的开源项目。", "",
           f"今天 GitHub 上又冒出来一批新项目，挑 {n} 个带你看一遍。", ""]
    for i, it in enumerate(items, 1):
        c = copies.get(i, {"pitch": "", "paragraphs": []})
        out += [f"**{i:02d}**", "", f"**{it['full_name']}：{c['pitch']}**", ""]
        for p in c["paragraphs"]:
            out += [p, ""]
        d = it.get("delta")
        out.append(f"今日新增约 {d:,} Star。" if d else f"目前约 {it['stars']:,} Star。")
        out += ["", "```", f"开源地址：{it['url']}", "```", "",
                f"![{it['full_name']}](https://opengraph.githubassets.com/1/{it['full_name']})", ""]
    out += ["**点击下方卡片，关注我们**", "",
            "每天为你整理 GitHub 上冒出来的新项目，不错过每一个值得看的开源作品。", ""]
    return "\n".join(out)


def main():
    today = datetime.now(timezone.utc).date().isoformat()
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.path.join("data", "daily", today + ".json"))
    ap.add_argument("--out", default=os.path.join("data", "articles", today + ".md"))
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--stub", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"找不到榜单文件：{args.input}\n"
                 f"通常是因为今天还没跑 fetch_and_rank.py，或还没有增量数据(需两天快照才有榜)。")
    items = json.load(open(args.input, encoding="utf-8"))[:args.top]
    if not items:
        sys.exit("榜单为空(还没有增量数据)，无可生成内容。")

    if args.stub:
        copies = {i: {"pitch": f"示例卖点 {i}", "paragraphs": ["第一段示例介绍。", "第二段示例介绍。"]}
                  for i in range(1, len(items) + 1)}
    else:
        key = (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if not key:
            sys.exit("缺少环境变量 LLM_API_KEY (或 DEEPSEEK_API_KEY)")
        copies = {c["index"]: c for c in parse_json_array(call_llm(key, items))}

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    open(args.out, "w", encoding="utf-8").write(assemble(items, copies))
    print(f"已写入: {args.out}  ({len(items)} 个项目)")


if __name__ == "__main__":
    main()
