#!/usr/bin/env python3
"""单项目深挖：挑一个成熟实用的开源项目 → 扒 README 配图 → 按 ARTICLE_STYLE.md 生成公众号文章。

    python feature_article.py                     # 自动选题
    python feature_article.py --repo owner/name   # 指定项目
    python feature_article.py --dry-run           # 只看选题与扒到的图，不调模型

环境变量:
    GITHUB_TOKEN                      可选，提升 GitHub 限流
    LLM_API_KEY / DEEPSEEK_API_KEY    必需（生成文章）
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import fetch_and_rank as fr
import generate_article as ga
import netutil
import wechat_test as wt

API = "https://api.github.com"
STYLE_PATH = "ARTICLE_STYLE.md"
IMG_DIR = os.path.join("examples", "features")
OUT_DIR = os.path.join("data", "features")
README_LIMIT = 6000

# 这些是徽章/统计图、图标、App 资源图，不是内容图
BAD_IMG = ("shields.io", "badge", "badgen.net", "travis-ci", "codecov", "coveralls",
           "npmjs.com", "bundlephobia", "opencollective", "camo.githubusercontent",
           "logo", "favicon", "/icon", "icon_", "-icon",
           "mipmap", "launcher", "xcassets", "drawable", "/res/", "splash",
           "/tests/", "/test/", "test_", "_test", "fixture", "sample_data", "dummy")
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
HINT_GOOD = ("screenshot", "screen", "demo", "preview", "showcase", "example", "shot", "overview")


def parse_json(text):
    t = text.strip()
    if t.startswith("```"):
        t = t[t.find("{"):]
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < 0:
        sys.exit("模型未返回 JSON:\n" + text[:400])
    return json.loads(t[i:j + 1])


def candidates(token, per_page=30):
    """拉一批成熟、仍在维护的项目当候选池（Star>=1500 且近 60 天有推送）。"""
    since = (datetime.now(timezone.utc) - timedelta(days=60)).date().isoformat()
    q = f"stars:>=1500 archived:false mirror:false pushed:>={since}"
    url = (f"{API}/search/repositories?q={urllib.parse.quote(q)}"
           f"&sort=stars&order=desc&per_page={per_page}")
    return fr._get(url, token).get("items", [])


def pick(repo_items):
    """让模型从候选里挑最适合写成公众号深度文的那个。"""
    lines = [f"- {r['full_name']} | ★{r['stargazers_count']} | {r.get('language') or '-'} | "
             f"{(r.get('description') or '')[:110]}" for r in repo_items]
    prompt = (
        "下面是 GitHub 上一批成熟项目。请挑出**最适合写一篇公众号深度介绍**的 1 个。\n"
        "标准：实用（真解决日常问题）、受众广（普通开发者/用户用得上）、有趣、"
        "功能已被真实用户验证过；避开过于人尽皆知、没有新鲜感的。\n"
        '只返回 JSON：{"full_name":"owner/name","reason":"一句话理由"}\n\n' + "\n".join(lines)
    )
    obj = parse_json(ga.chat([
        {"role": "system", "content": "你是开源项目选题编辑，判断克制、不追热点。"},
        {"role": "user", "content": prompt}]))
    return obj.get("full_name"), obj.get("reason", "")


def readme_text(full_name, token):
    data = fr._get(f"{API}/repos/{full_name}/readme", token)
    return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")


def extract_images(readme, repo):
    """从 README 抠内容图（markdown 与 HTML 两种写法），相对路径解析到 raw.githubusercontent.com。"""
    branch = repo.get("default_branch") or "HEAD"
    full = repo["full_name"]
    raw = [(alt, url) for alt, url in re.findall(r"!\[([^\]]*)\]\(([^)\s]+)", readme)]
    for tag in re.findall(r"<img[^>]*>", readme, flags=re.I):
        src = re.search(r'src=["\']([^"\']+)["\']', tag, flags=re.I)
        if not src:
            continue
        alt = re.search(r'alt=["\']([^"\']*)["\']', tag, flags=re.I)
        raw.append((alt.group(1) if alt else "", src.group(1)))
    seen, out = set(), []
    for alt, url in raw:
        low = url.lower()
        if any(b in low for b in BAD_IMG):
            continue
        if url.startswith("http"):
            real = url
        else:
            real = f"https://raw.githubusercontent.com/{full}/{branch}/{url.lstrip('./')}"
        if not re.search(r"\.(png|jpe?g|gif|webp)(\?|$)", real.lower()):
            continue
        if real in seen:
            continue
        seen.add(real)
        out.append({"alt": alt.strip(), "url": real,
                    "name": os.path.basename(real.split("?")[0])})
    return out


def scan_images(repo, token, limit=12):
    """扫仓库文件树找图片——README 没引用的截图也能捞到。"""
    branch = repo.get("default_branch") or "HEAD"
    full = repo["full_name"]
    try:
        tree = fr._get(f"{API}/repos/{full}/git/trees/{branch}?recursive=1", token)
    except SystemExit:
        return []
    paths = []
    for t in tree.get("tree", []):
        p = t.get("path", "")
        low = p.lower()
        if t.get("type") != "blob" or not low.endswith(IMG_EXT):
            continue
        if any(b in low for b in BAD_IMG):
            continue
        score = sum(2 for h in HINT_GOOD if h in low) + (1 if "/" in p else 0)
        paths.append((score, p))
    paths.sort(key=lambda x: -x[0])
    return [{"alt": "", "name": os.path.basename(p),
             "url": f"https://raw.githubusercontent.com/{full}/{branch}/{p}"}
            for _, p in paths[:limit]]


def collect_images(repo, readme, token, limit=6):
    """README 引用的优先，再用仓库里扫到的截图补齐。"""
    imgs = extract_images(readme, repo)
    seen = {i["url"] for i in imgs}
    for im in scan_images(repo, token):
        if im["url"] not in seen:
            seen.add(im["url"])
            imgs.append(im)
    if not imgs:                    # 仓库里确实没有可用图，退回 GitHub 自动生成的仓库卡片
        imgs = [{"alt": f"{repo['full_name']} 仓库卡片", "name": "repo-card.png",
                 "url": f"https://opengraph.githubassets.com/1/{repo['full_name']}"}]
    return imgs[:limit]


def download(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "gh-feature")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with netutil.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


def build_prompt(style, repo, readme, images):
    lic = (repo.get("license") or {}).get("spdx_id") or "-"
    facts = [
        f"仓库: {repo['full_name']}",
        f"描述: {repo.get('description') or '(无)'}",
        f"Star: {repo['stargazers_count']}  Fork: {repo['forks_count']}  未关闭 issue: {repo['open_issues_count']}",
        f"主语言: {repo.get('language') or '-'}  许可证: {lic}",
        f"创建于: {repo['created_at'][:10]}  最近推送: {repo['pushed_at'][:10]}",
        f"官网: {repo.get('homepage') or '-'}",
        f"仓库地址: {repo['html_url']}",
        f"今天日期: {datetime.now(timezone.utc).date().isoformat()}",
    ]
    if images:
        facts.append("可用配图（文件名往往暗示内容，插图时写 `图N` 占位）：")
        facts += [f"  图{i}: {im.get('name') or 'image'}"
                  + (f"  —  {im['alt']}" if im.get("alt") else "")
                  for i, im in enumerate(images, 1)]
    return (
        "请严格按下方《文章风格约束》，写一篇介绍这个开源项目的公众号文章。\n\n"
        f"===== 文章风格约束 =====\n{style}\n\n"
        "===== 项目事实（只能基于这些写，不许编造）=====\n" + "\n".join(facts) +
        f"\n\n===== 项目 README 节选 =====\n{readme[:README_LIMIT]}\n\n"
        "输出要求：\n"
        "1. 直接输出 Markdown 正文，**第一行必须是 `# 标题`**，然后按风格约束的骨架写。\n"
        "2. **必须至少插入 2 张配图**（除非一张都没有）：在相关段落附近单独成行写 "
        "`![说明](图N)`，N 是上面配图编号，说明要写清「这张图让你看到什么」；不要写其它图片地址。\n"
        "3. 每个数字标「截至 <日期>」，没把握的写「以官方文档为准」，绝不编造。\n"
        "4. 正文 2500–4000 字，短板至少 3 条且具体。"
    )


def apply_images(md, images):
    def repl(m):
        idx = int(m.group(2))
        if 1 <= idx <= len(images):
            return f"![{m.group(1)}]({images[idx - 1]['url']})"
        return ""
    return re.sub(r"!\[([^\]]*)\]\(图(\d+)\)", repl, md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", help="指定 owner/name；不指定则自动选题")
    ap.add_argument("--dry-run", action="store_true", help="只看选题与配图，不调模型")
    ap.add_argument("--out", help="输出 md 路径")
    ap.add_argument("--upload", action="store_true", help="生成后直接发到公众号草稿箱")
    args = ap.parse_args()

    if not os.path.exists(STYLE_PATH):
        sys.exit(f"缺少风格文档：{STYLE_PATH}")
    style = open(STYLE_PATH, encoding="utf-8").read()

    token = fr.gh_token()
    full_name = args.repo
    if not full_name:
        items = candidates(token)
        if not items:
            sys.exit("候选为空，检查 GITHUB_TOKEN 或稍后重试")
        print(f"候选 {len(items)} 个，交给模型选题…")
        full_name, reason = pick(items)
        print(f"选中: {full_name}  ← {reason}")
    if not full_name:
        sys.exit("选题失败")

    repo = fr._get(f"{API}/repos/{full_name}", token)
    print(f"项目: {repo['full_name']}  ★{repo['stargazers_count']}  "
          f"语言 {repo.get('language') or '-'}  最近推送 {repo['pushed_at'][:10]}")
    if repo.get("archived"):
        sys.exit("该项目已归档，换一个")

    readme = readme_text(full_name, token)
    images = collect_images(repo, readme, token)
    print(f"扒到 {len(images)} 张可用配图（README 引用 + 仓库截图）")

    if args.dry_run:
        for i, im in enumerate(images, 1):
            print(f"  图{i}: {(im['alt'] or '(无说明)')[:36]}  ->  {im['url'][:100]}")
        return

    slug = full_name.replace("/", "_")
    for i, im in enumerate(images, 1):
        ext = os.path.splitext(im["url"].split("?")[0])[1] or ".png"
        im["local"] = download(im["url"], os.path.join(IMG_DIR, slug, f"img{i}{ext}"))
    print(f"配图已存到 {os.path.join(IMG_DIR, slug)}")

    print("调用模型生成文章…")
    md = ga.chat([{"role": "system", "content": "你是资深科技公众号主笔，文风克制、重证据。"},
                  {"role": "user", "content": build_prompt(style, repo, readme, images)}])
    md = md.strip()
    if md.startswith("```"):
        md = re.sub(r"^```[a-z]*\n|\n```$", "", md).strip()
    md = apply_images(md, images)
    if images and "![" not in md:
        print("⚠️  模型没插入任何配图，建议重跑或手动插图")

    out = args.out or os.path.join(OUT_DIR, f"{datetime.now(timezone.utc).date().isoformat()}-{slug}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md + "\n")

    title = md.splitlines()[0].lstrip("# ").strip() if md else ""
    print(f"\n标题: {title}")
    print(f"正文: {len(md)} 字符")
    print(f"已写入: {out}")
    print(f"配图目录: {os.path.join(IMG_DIR, slug)}")

    if args.upload:
        import wechat_publish as wp
        appid = os.environ.get("WECHAT_APPID", "").strip()
        secret = os.environ.get("WECHAT_APPSECRET", "").strip()
        if not appid or not secret:
            sys.exit("缺少环境变量 WECHAT_APPID / WECHAT_APPSECRET")
        token = wt.get_token(appid, secret)
        print("发到公众号草稿箱…")
        media_id, _, _, _ = wp.publish(token, md)
        print(f"OK: 草稿已创建 media_id={media_id}")


if __name__ == "__main__":
    main()
