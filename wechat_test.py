#!/usr/bin/env python3
"""微信公众号(测试号)上传流程验证：取 token → 传正文图 → 传永久封面 → 建草稿。

凭证从环境变量读取，不落盘、不打印：
    set WECHAT_APPID=你的appid
    set WECHAT_APPSECRET=你的appsecret
    python wechat_test.py

测试号没有发布权限，本脚本只到「建草稿」为止，不会发布/群发。
    python wechat_test.py --selftest   # 只跑离线自检，不联网
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.request
import uuid

import netutil

API = "https://api.weixin.qq.com/cgi-bin"


def build_multipart(path):
    """构造 multipart/form-data，字段名固定为 media。返回 (boundary, body)。"""
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        content = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="media"; filename="{os.path.basename(path)}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return boundary, body


def _post(url, data, ctype):
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", ctype)
    with netutil.urlopen(req, timeout=60) as r:
        return json.load(r)


def _upload(token, endpoint, path, extra=""):
    boundary, body = build_multipart(path)
    return _post(f"{API}/{endpoint}?access_token={token}{extra}", body,
                 f"multipart/form-data; boundary={boundary}")


def get_token(appid, secret):
    url = f"{API}/token?grant_type=client_credential&appid={appid}&secret={secret}"
    with netutil.urlopen(url, timeout=30) as r:
        data = json.load(r)
    if "access_token" not in data:
        sys.exit(f"取 token 失败: {data}")
    return data["access_token"]


def add_draft(token, articles):
    return _post(f"{API}/draft/add?access_token={token}",
                 json.dumps({"articles": articles}, ensure_ascii=False).encode("utf-8"),
                 "application/json; charset=utf-8")


def list_drafts(token):
    return _post(f"{API}/draft/batchget?access_token={token}",
                 json.dumps({"offset": 0, "count": 5, "no_content": 1}).encode(),
                 "application/json; charset=utf-8")


def _mask(t):
    return t[:6] + "..." + t[-4:] if len(t) > 12 else "***"


def selftest():
    path = "examples/images/tobi_disktree.png"
    assert os.path.exists(path), f"缺少测试图片 {path}"
    boundary, body = build_multipart(path)
    assert boundary.encode() in body
    assert b'name="media"' in body
    assert body.endswith(b"--\r\n")
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="examples/images/tobi_disktree.png")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return

    appid = os.environ.get("WECHAT_APPID", "").strip()
    secret = os.environ.get("WECHAT_APPSECRET", "").strip()
    if not appid or not secret:
        sys.exit("缺少环境变量 WECHAT_APPID / WECHAT_APPSECRET")

    token = get_token(appid, secret)
    print("1) access_token:", _mask(token))

    up = _upload(token, "media/uploadimg", args.image)
    print("2) media/uploadimg:", up)
    if "url" not in up:
        sys.exit("正文图上传失败，见上方返回")
    body_url = up["url"]

    mat = _upload(token, "material/add_material", args.image, "&type=image")
    print("3) material/add_material:", mat)
    if "media_id" not in mat:
        sys.exit("永久封面图上传失败，见上方返回")

    article = {
        "title": "上传流程测试",
        "author": "test",
        "digest": "验证：token → 传图 → 草稿",
        "content": f'<p>这是一次上传流程测试。</p><p><img src="{body_url}"/></p>',
        "thumb_media_id": mat["media_id"],
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    d = add_draft(token, [article])
    print("4) draft/add:", d)
    if "media_id" not in d:
        sys.exit("建草稿失败，见上方返回")

    drafts = list_drafts(token)
    titles = [ni.get("title") for it in drafts.get("item", [])
              for ni in it.get("content", {}).get("news_item", [])]
    print("5) draft/batchget 草稿箱标题:", titles)
    print("\nOK: 流程走通，草稿已创建。")


if __name__ == "__main__":
    main()
