#!/usr/bin/env python3
"""把文章 md 转成微信公众号图文草稿（测试号）。

读 data/articles/<日期>.md，正文图片自动从 URL 下载、上传到微信换托管地址。
复用 wechat_test.py 里的凭证与上传函数。

    $env:WECHAT_APPID="..."; $env:WECHAT_APPSECRET="..."
    python wechat_publish.py               # 传图并建草稿
    python wechat_publish.py --dry-run     # 只本地拼 HTML，不联网
"""
import argparse
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import wechat_test as wt

IMG_DIR = os.path.join("examples", "images")


def local_path_for(url):
    """把图片 URL 映射到本地缓存文件名(owner_repo.png)。"""
    slug = "_".join(url.rstrip("/").split("/")[-2:])
    return os.path.join(IMG_DIR, slug + ".png")


def download_image(url):
    """把图片下载到本地缓存，已存在则跳过；带 429 退避重试。返回本地路径。"""
    path = local_path_for(url)
    if os.path.exists(path) and open(path, "rb").read(8) == b"\x89PNG\r\n\x1a\n":
        return path
    os.makedirs(IMG_DIR, exist_ok=True)
    for attempt in range(1, 6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                open(path, "wb").write(r.read())
            return path
        except urllib.error.HTTPError as e:
            time.sleep(10 * attempt if e.code == 429 else 2)
        except Exception:
            time.sleep(2)
    sys.exit(f"下载图片失败: {url}")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_html(token, md_text, dry_run):
    lines = md_text.splitlines()
    title = lines[0].lstrip("#").strip()

    # 公众号只认内联样式，这里把每种行映射成带 style 的 HTML
    body_p = "font-size:15px;line-height:1.75;color:#333;margin:0 0 16px;"
    num_p = "font-size:14px;color:#9a9a9a;letter-spacing:1px;margin:0 0 6px;"
    title_p = "font-size:17px;font-weight:bold;color:#07c160;line-height:1.5;margin:0 0 12px;"
    star_p = "font-size:13px;color:#9a9a9a;margin:0 0 12px;"
    card_s = ("background:#f7f7f7;border-radius:6px;padding:12px 14px;margin:0 0 18px;"
              "font-size:13px;line-height:1.6;color:#576b95;word-break:break-all;")
    img_p = "text-align:center;margin:0 0 18px;"
    hr_s = "border-top:1px dashed #e0e0e0;margin:2px 0 22px;"
    end_p = "font-size:13px;color:#9a9a9a;text-align:center;margin:26px 0 8px;"

    body, in_code, digest, n = [], False, None, 0
    for ln in lines[1:]:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code:                                   # 代码块内容（开源地址）→ 灰底卡片
            body.append(f'<section style="{card_s}">{esc(s)}</section>')
            continue
        if s == "---":
            continue
        m = re.match(r"!\[.*?\]\((.*?)\)", s)
        if m:
            if dry_run:
                wurl = "LOCAL:" + local_path_for(m.group(1))
            else:
                local = download_image(m.group(1))
                up = wt._upload(token, "media/uploadimg", local)
                if "url" not in up:
                    sys.exit(f"正文图上传失败 {local}: {up}")
                wurl = up["url"]
            body.append(f'<p style="{img_p}"><img src="{wurl}" '
                        f'style="max-width:100%;border-radius:6px;"/></p>')
            continue
        if re.fullmatch(r"\*\*\d+\*\*", s):           # 序号 01 → 灰色小字，条目间加分割线
            if n:
                body.append(f'<section style="{hr_s}"></section>')
            n += 1
            body.append(f'<p style="{num_p}">{s.strip("*")}</p>')
            continue
        mt = re.fullmatch(r"\*\*(.+)\*\*", s)         # 加粗整行：项目名 或 结尾
        if mt:
            inner = esc(mt.group(1))
            body.append(f'<p style="{end_p if "关注" in inner else title_p}">{inner}</p>')
            continue
        if s.startswith("目前约 ") or s.startswith("今日新增约 "):  # 涨星行 → 浅灰
            body.append(f'<p style="{star_p}">{esc(s)}</p>')
            continue
        body.append(f'<p style="{body_p}">{esc(s)}</p>')
        if digest is None:
            digest = s
    return title, "".join(body), (digest or title)


def main():
    today = datetime.now(timezone.utc).date().isoformat()
    ap = argparse.ArgumentParser()
    ap.add_argument("--article", default=os.path.join("data", "articles", today + ".md"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.article):
        sys.exit(f"找不到文章文件：{args.article}\n先跑 generate_article.py 生成文章。")
    md = open(args.article, encoding="utf-8").read()
    token = None
    if not args.dry_run:
        appid = os.environ.get("WECHAT_APPID", "").strip()
        secret = os.environ.get("WECHAT_APPSECRET", "").strip()
        if not appid or not secret:
            sys.exit("缺少环境变量 WECHAT_APPID / WECHAT_APPSECRET")
        token = wt.get_token(appid, secret)
        print("1) access_token:", wt._mask(token))

    title, html, digest = build_html(token, md, args.dry_run)
    print("2) 标题:", title)
    print("   摘要:", digest[:50])
    print("   正文长度:", len(html), "字符")
    if args.dry_run:
        print("---- HTML 预览(前 500 字) ----")
        print(html[:500])
        return

    cover = download_image(re.search(r"!\[.*?\]\((.*?)\)", md).group(1))
    mat = wt._upload(token, "material/add_material", cover, "&type=image")
    if "media_id" not in mat:
        sys.exit(f"封面图上传失败: {mat}")
    print("3) 封面 thumb_media_id:", mat["media_id"])

    article = {
        "title": title,
        "author": "",
        "digest": digest[:120],
        "content": html,
        "thumb_media_id": mat["media_id"],
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    d = wt.add_draft(token, [article])
    print("4) draft/add:", d)
    if "media_id" not in d:
        sys.exit(f"建草稿失败: {d}")

    drafts = wt.list_drafts(token)
    titles = [ni.get("title") for it in drafts.get("item", [])
              for ni in it.get("content", {}).get("news_item", [])]
    print("5) 草稿箱:", titles)
    print("\nOK: 10 个项目草稿已创建")


if __name__ == "__main__":
    main()
