#!/usr/bin/env python3
"""每日一键流程：采集榜单 → 生成文章 → 优化标题 → 建微信草稿。

    python run_daily.py                  # 全流程
    python run_daily.py --skip-publish   # 只到生成文章
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
    ("1/4 采集榜单", [PY, "fetch_and_rank.py"]),
    ("2/4 生成文章", [PY, "generate_article.py"]),
    ("3/4 优化标题", [PY, "optimize_title.py"]),
    ("4/4 建草稿", [PY, "wechat_publish.py"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-publish", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

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
