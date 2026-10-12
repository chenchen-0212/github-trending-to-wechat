#!/usr/bin/env python3
"""失败通知：把流水线结果推送到 Server酱（ServerChan）。

sendkey 从环境变量读取，变量名 `send_key`。零第三方依赖，直接调 Server酱
HTTP 接口（等价于 serverchan-sdk，但不用 pip install）。

    python notify.py --test               # 发一条测试通知
    python notify.py "标题" "内容"         # 手动发一条
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

import netutil

API = "https://sctapi.ftqq.com/{key}.send"
TITLE_MAX = 32


def sendkey():
    """读环境变量 send_key（Windows 下大小写不敏感，这里遍历兜底）。"""
    for k, v in os.environ.items():
        if k.lower() == "send_key" and v.strip():
            return v.strip()
    return ""


def send(title, desp="", tags=None):
    """发通知。返回 (ok, 说明)。没配 sendkey 时返回 False，不抛异常。"""
    key = sendkey()
    if not key:
        return False, "未配置环境变量 send_key"
    data = {"title": title[:TITLE_MAX], "desp": desp}
    if tags:
        data["tags"] = tags
    req = urllib.request.Request(API.format(key=key),
                                 data=urllib.parse.urlencode(data).encode(),
                                 method="POST")
    try:
        with netutil.urlopen(req, timeout=15, tries=2) as r:
            j = json.load(r)
        return (j.get("code") == 0), j
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("title", nargs="?", default="")
    ap.add_argument("desp", nargs="?", default="")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    if args.test:
        ok, r = send("流水线通知测试", "收到这条说明 send_key 配置正常。", "服务器报警")
    elif args.title:
        ok, r = send(args.title, args.desp)
    else:
        sys.exit('用法: python notify.py "标题" "内容"  或  python notify.py --test')

    print("发送" + ("成功" if ok else "失败"), "->", r)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
