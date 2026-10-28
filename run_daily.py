#!/usr/bin/env python3
"""每日一键流程：采集榜单 → 选榜·霸榜判定 → 生成文章 → 优化标题 → 建微信草稿。

    python run_daily.py                  # 全流程
    python run_daily.py --skip-publish   # 不建草稿（跑到优化标题）
    python run_daily.py --dry-run        # 只打印将要执行什么

需要环境变量：
    LLM_API_KEY(或 DEEPSEEK_API_KEY)   生成文章
    WECHAT_APPID / WECHAT_APPSECRET    建草稿
    send_key                           可选，失败时用 Server酱 推送告警
"""
import argparse
import subprocess
import sys

try:
    import notify          # send_key 未配置时不影响主流程
except Exception:
    notify = None

PY = sys.executable

STEPS = [
    ("1/6 采集榜单", [PY, "fetch_and_rank.py"]),
    ("2/6 选榜·霸榜判定", [PY, "select_daily.py"]),
    ("3/6 生成文章", [PY, "generate_article.py"]),
    ("4/6 优化标题", [PY, "optimize_title.py"]),
    ("5/6 完读率自检", [PY, "article_lint.py"]),
    ("6/6 建草稿", [PY, "wechat_publish.py"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-publish", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # --skip-publish 只跳过建草稿，自检照跑（自检本身不联网）
    steps = STEPS[:-1] if args.skip_publish else STEPS
    if args.dry_run:
        for name, cmd in steps:
            print("将执行:", name, "->", " ".join(cmd[1:]))
        return

    for name, cmd in steps:
        print(f"\n===== {name} =====", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        if r.returncode != 0:
            tail = ((r.stdout or "") + "\n" + (r.stderr or ""))[-1500:]
            if notify:
                ok, info = notify.send(f"GitHub日报失败 · {name}",
                                       f"退出码 {r.returncode}\n\n{tail}", "服务器报警")
            else:
                ok, info = False, "notify 未加载"
            sys.exit(f"{name} 失败(退出码 {r.returncode})，已中止；通知"
                     f"{'已发送' if ok else '失败: ' + str(info)}")
    print("\n全流程完成", flush=True)


if __name__ == "__main__":
    main()
