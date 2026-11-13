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
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import wechat_test as wt

IMG_DIR = os.path.join("examples", "images")


def local_path_for(url):
    """把图片 URL 映射到本地缓存文件名(owner_repo.png)。"""
    slug = "_".join(url.rstrip("/").split("/")[-2:])
    return os.path.join(IMG_DIR, slug + ".jpg")


PROXY = "https://images.weserv.nl/?w=1080&output=jpg&q=80&url="


def _fetch(url, path, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if len(data) < 1024:
        raise OSError("图太小，可能是错误页")
    open(path, "wb").write(data)


def download_image(url, required=True):
    """把图片下载到本地缓存，已存在则跳过；直连不行就走图片代理。

    required=False 时下载失败返回 None（正文配图失败不该拖垮整篇文章），
    required=True 仍抛错退出（封面图没有就建不了草稿）。
    """
    path = local_path_for(url)
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return path
    os.makedirs(IMG_DIR, exist_ok=True)
    for attempt in range(1, 4):
        for target in (url, PROXY + urllib.parse.quote(url, safe="")):
            try:
                _fetch(target, path, timeout=45)
                return path
            except Exception:
                continue
        time.sleep(2 * attempt)
    if required:
        sys.exit(f"下载图片失败: {url}")
    print("  ! 图片下载失败，已跳过: " + url[:80])
    return None


def make_placeholder(path=os.path.join(IMG_DIR, "_placeholder_cover.png"), w=900, h=383):
    """纯 Python 生成纯色 PNG 当兜底封面（服务器没装 Pillow）。"""
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\x3a\x3a\x3a" * w for _ in range(h))

    def chunk(tag, data):
        body = struct.pack(">I", len(data)) + tag + data
        return body + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "wb").write(png)
    print("  ! 已生成兜底封面: " + path)
    return path


def pick_cover(md):
    """找一张能用的封面：按文章顺序试，全失败就用兜底图。"""
    for url in re.findall(r"!\[.*?\]\((.*?)\)", md):
        path = download_image(url, required=False)
        if path:
            return path
    return make_placeholder()


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
    # 霸榜简报标题（连续上榜 N 天）：蓝色，和详写标题的绿色区分开
    brief_p = "font-size:15px;font-weight:bold;color:#576b95;line-height:1.5;margin:0 0 12px;"
    card_s = ("background:#f7f7f7;border-radius:6px;padding:12px 14px;margin:0 0 18px;"
              "font-size:13px;line-height:1.6;color:#576b95;word-break:break-all;")
    img_p = "text-align:center;margin:0 0 18px;"
    hr_s = "border-top:1px dashed #e0e0e0;margin:2px 0 22px;"
    end_p = "font-size:13px;color:#9a9a9a;text-align:center;margin:26px 0 8px;"
    # 单项目深挖文章会用到的元素
    h2_p = ("font-size:17px;font-weight:bold;color:#1a1a1a;line-height:1.5;"
            "margin:26px 0 12px;border-left:4px solid #07c160;padding-left:10px;")
    quote_s = ("border-left:3px solid #d0d0d0;padding:6px 0 6px 12px;margin:0 0 16px;"
               "color:#666;font-size:14px;line-height:1.7;")
    li_p = "font-size:15px;line-height:1.75;color:#333;margin:0 0 8px;padding-left:16px;"
    table_t = "width:100%;border-collapse:collapse;margin:0 0 18px;font-size:13px;"
    th_t = "border:1px solid #e5e5e5;background:#f7f7f7;padding:6px 8px;text-align:left;color:#333;"
    td_t = "border:1px solid #e5e5e5;padding:6px 8px;color:#555;"

    body, in_code, digest, n = [], False, None, 0
    table_rows, code_lines = [], []

    def flush_code():
        """把攒下的代码行拼成一个卡片。原来每行一个卡片，多行代码会散成一堆。"""
        if not code_lines:
            return
        rendered = []
        for x in code_lines:
            pad = "&nbsp;" * (len(x) - len(x.lstrip()))   # 保留缩进
            rendered.append(pad + esc(x.strip()))
        body.append(f'<section style="{card_s}">' + "<br/>".join(rendered) + "</section>")
        code_lines.clear()

    def flush_table():
        """把攒下的表格行拼成 HTML 表格（丢掉 |---|---| 分隔行）。"""
        if not table_rows:
            return
        rows = [r for r in table_rows if not re.fullmatch(r"[\s|:\-]+", r)]
        parts = [f'<table style="{table_t}">']
        for k, row in enumerate(rows):
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            tag, style = ("th", th_t) if k == 0 else ("td", td_t)
            parts.append("<tr>" + "".join(
                f'<{tag} style="{style}">{esc(c)}</{tag}>' for c in cells) + "</tr>")
        parts.append("</table>")
        body.append("".join(parts))
        table_rows.clear()

    for ln in lines[1:]:
        s = ln.strip()
        if in_code:                                   # 代码块内容：攒起来最后拼成一个卡片
            if s.startswith("```"):
                flush_code()
                in_code = False
            else:
                code_lines.append(ln.rstrip())
            continue
        if not s:
            flush_table()
            continue
        if s.startswith("```"):
            flush_table()
            in_code = True
            continue
        if s == "---":
            flush_table()
            continue
        if s.startswith("|") and s.endswith("|"):     # 表格行，攒着一起渲染
            table_rows.append(s)
            continue
        flush_table()

        m = re.match(r"!\[.*?\]\((.*?)\)", s)
        if m:
            if dry_run:
                wurl = "LOCAL:" + local_path_for(m.group(1))
            else:
                local = download_image(m.group(1), required=False)
                if not local:
                    continue
                up = wt._upload(token, "media/uploadimg", local)
                if "url" not in up:
                    print("  ! 正文图上传失败，已跳过 " + local + ": " + str(up))
                    continue
                wurl = up["url"]
            body.append(f'<p style="{img_p}"><img src="{wurl}" '
                        f'style="max-width:100%;border-radius:6px;"/></p>')
            continue
        mh = re.match(r"#{1,6}\s*(.+)$", s)           # #/##/### 小标题（代码块内的 # 已在上面处理）
        if mh:
            heading = mh.group(1).strip().lstrip("#").strip()   # 容错：去掉标题里多写的 #
            if heading:
                body.append(f'<p style="{h2_p}">{esc(heading)}</p>')
                continue
        if s.startswith(">"):                         # 引用块
            body.append(f'<section style="{quote_s}">{esc(s.lstrip("> ").strip())}</section>')
            continue
        if re.match(r"^[-*]\s+", s):                  # 无序列表
            body.append(f'<p style="{li_p}">· {esc(s[2:].strip())}</p>')
            continue
        if re.fullmatch(r"\*\*\d+\*\*", s):           # 序号 01 → 灰色小字，条目间加分割线
            if n:
                body.append(f'<section style="{hr_s}"></section>')
            n += 1
            body.append(f'<p style="{num_p}">{s.strip("*")}</p>')
            continue
        mb = re.fullmatch(r"\*\*(.+（连续上榜 \d+ 天）)\*\*", s)   # 霸榜简报：一行，无正文无图
        if mb:
            body.append(f'<p style="{brief_p}">{esc(mb.group(1))}</p>')
            continue
        mt = re.fullmatch(r"\*\*(.+)\*\*", s)         # 加粗整行：项目名 或 结尾
        if mt:
            inner = esc(mt.group(1))
            body.append(f'<p style="{end_p if "关注" in inner else title_p}">{inner}</p>')
            continue
        if s.startswith("目前约 ") or s.startswith("今日新增约 "):  # 涨星行 → 浅灰
            body.append(f'<p style="{star_p}">{esc(s)}</p>')
            continue
        plain = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc(s))  # 行内加粗
        body.append(f'<p style="{body_p}">{plain}</p>')
        if digest is None:
            clean = re.sub(r"[*`#>|\[\]]", "", s).strip()
            if clean:
                digest = clean
    flush_table()
    flush_code()
    return title, "".join(body), (digest or title)


def publish(token, md):
    """把文章 md 传成公众号草稿。返回 (media_id, title, html, digest)。"""
    title, html, digest = build_html(token, md, False)
    cover = pick_cover(md)
    mat = wt._upload(token, "material/add_material", cover, "&type=image")
    if "media_id" not in mat:
        sys.exit(f"封面图上传失败: {mat}")
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
    if "media_id" not in d and str(d.get("errcode")) == "53402":
        # 53402 = 封面裁剪失败，换兜底封面再试一次
        print("  ! 封面裁剪失败(53402)，换兜底封面重试")
        mat2 = wt._upload(token, "material/add_material", make_placeholder(), "&type=image")
        if "media_id" in mat2:
            article["thumb_media_id"] = mat2["media_id"]
            d = wt.add_draft(token, [article])
    if "media_id" not in d:
        sys.exit(f"建草稿失败: {d}")
    return d["media_id"], title, html, digest


def main():
    today = datetime.now(timezone.utc).date().isoformat()
    ap = argparse.ArgumentParser()
    ap.add_argument("--article", default=os.path.join("data", "articles", today + ".md"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.article):
        sys.exit(f"找不到文章文件：{args.article}\n先跑 generate_article.py 生成文章。")
    md = open(args.article, encoding="utf-8").read()

    if args.dry_run:
        title, html, digest = build_html(None, md, True)
        print("1) 标题:", title)
        print("   摘要:", digest[:50])
        print("   正文长度:", len(html), "字符")
        print("---- HTML 预览(前 500 字) ----")
        print(html[:500])
        return

    appid = os.environ.get("WECHAT_APPID", "").strip()
    secret = os.environ.get("WECHAT_APPSECRET", "").strip()
    if not appid or not secret:
        sys.exit("缺少环境变量 WECHAT_APPID / WECHAT_APPSECRET")
    token = wt.get_token(appid, secret)
    print("1) access_token:", wt._mask(token))

    media_id, title, html, digest = publish(token, md)
    print("2) 标题:", title)
    print("   摘要:", digest[:50])
    print("   正文长度:", len(html), "字符")
    print("3) draft/add media_id:", media_id)

    drafts = wt.list_drafts(token)
    titles = [ni.get("title") for it in drafts.get("item", [])
              for ni in it.get("content", {}).get("news_item", [])]
    print("4) 草稿箱:", titles)
    print("\nOK: 草稿已创建")


if __name__ == "__main__":
    main()
