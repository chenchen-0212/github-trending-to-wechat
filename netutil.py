#!/usr/bin/env python3
"""带退避重试的 HTTP 调用，专治间歇性网络抖动（SSL 中断 / 连接超时 / 5xx）。

只重试"瞬时错误"；4xx 这类确定性错误直接抛出，不浪费时间。
    python netutil.py      # 离线自检
"""
import time
import urllib.error
import urllib.request

DEFAULT_TRIES = 3


def with_retry(fn, tries=DEFAULT_TRIES, base_delay=2, on_retry=None):
    """反复调用 fn()，成功就返回；失败按 2s,4s,6s… 退避重试，用完仍失败则抛出。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code < 500:                     # 4xx：确定性错误，重试无意义
                raise
            last = e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last = e
        if i < tries - 1:
            wait = base_delay * (i + 1)
            if on_retry:
                on_retry(i + 1, wait, last)
            time.sleep(wait)
    raise last


def urlopen(req, timeout=30, tries=DEFAULT_TRIES, on_retry=None):
    return with_retry(lambda: urllib.request.urlopen(req, timeout=timeout),
                      tries=tries, on_retry=on_retry)


def _selftest():
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 3:
            raise urllib.error.URLError("boom")
        return "ok"

    assert with_retry(flaky, tries=3, base_delay=0) == "ok"
    assert state["n"] == 3, state

    def always_fail():
        raise urllib.error.URLError("nope")

    try:
        with_retry(always_fail, tries=2, base_delay=0)
        raise AssertionError("应当抛出")
    except urllib.error.URLError:
        pass

    def not_found():
        raise urllib.error.HTTPError("u", 404, "nf", {}, None)

    try:
        with_retry(not_found, tries=3, base_delay=0)
        raise AssertionError("4xx 不应重试")
    except urllib.error.HTTPError:
        pass
    print("netutil selftest ok")


if __name__ == "__main__":
    _selftest()
