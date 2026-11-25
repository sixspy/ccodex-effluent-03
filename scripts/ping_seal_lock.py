#!/usr/bin/env python3
"""effluent-03 并发脚本：双会话打同批封批，验批锁串行化。

用法：
  python scripts/ping_seal_lock.py                 # 默认打本地 http://127.0.0.1:8000
  python scripts/ping_seal_lock.py --base http://127.0.0.1:11600

口径：两路会话（writer / admin）同时对同一采样批抢封批。先到者持 sample 批锁，
后到者必须被 409 顶回，discharge_seals 正式区零增，contention_log 只留一条争用痕。
脚本退出码 0 表示锁语义符合预期，非 0 表示双进（无锁并发）事故。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import urllib.error
import urllib.request


def _post(base: str, path: str, token: str | None, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") or "{}"
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"detail": body}


def _get(base: str, path: str, token: str) -> dict:
    req = urllib.request.Request(base + path, method="GET")
    req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _login(base: str, username: str, password: str) -> str:
    _, data = _post(base, "/api/login", None, {"username": username, "password": password})
    return data["token"]


def main() -> int:
    parser = argparse.ArgumentParser(description="双会话打同批封批，验批锁")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--sample-code", default="SM-OF-E1-B-001")
    args = parser.parse_args()

    tok_writer = _login(args.base, "writer", "writer123")
    tok_admin = _login(args.base, "admin", "admin123")

    samples = _get(args.base, "/api/samples", tok_admin)["items"]
    target = next((s for s in samples if s["sample_code"] == args.sample_code), None)
    if target is None or target["status"] not in ("已裁定", "已准排") or target.get("bypass_open"):
        target = next(
            (s for s in samples if s["status"] in ("已裁定", "已准排") and not s.get("bypass_open")),
            None,
        )
    if target is None:
        print("[ping_seal_lock] 找不到可封批采样批（可先重置库）")
        return 2
    sample_id = target["id"]
    lock_key = f"sample:{sample_id}"
    print(f"[ping_seal_lock] 目标采样批 {target['sample_code']} (id={sample_id})")

    # writer 先占批锁不释放，模拟先到会话
    code, _ = _post(args.base, f"/api/locks/acquire?lock_key={lock_key}", tok_writer)
    print(f"[ping_seal_lock] writer 占锁 {lock_key} -> {code}")

    def hit(name: str, token: str) -> tuple[str, int, dict]:
        status, body = _post(
            args.base, "/api/seals", token, {"sample_id": sample_id}
        )
        return name, status, body

    before = {
        x["id"]
        for x in _get(args.base, "/api/locks", tok_admin)["contention"]
        if x["lock_key"] == lock_key
    }
    # 两路会话同时打同批封批
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args_: hit(*args_),
                [("writer", tok_writer), ("admin", tok_admin)],
            )
        )

    ok = True
    for name, status, body in results:
        print(f"[ping_seal_lock] {name} 封批 -> HTTP {status} {body}")
        if name == "admin" and status != 409:
            ok = False
    writer_status = next(s for n, s, _ in results if n == "writer")
    if writer_status != 200:
        ok = False

    locks = _get(args.base, "/api/locks", tok_admin)
    traces = [
        x
        for x in locks["contention"]
        if x["lock_key"] == lock_key and x["id"] not in before
    ]
    print(f"[ping_seal_lock] 本次新增争用痕 {len(traces)} 条: {traces}")
    if len(traces) != 1 or traces[0]["action"] != "争用失败":
        ok = False

    _post(args.base, f"/api/locks/release?lock_key={lock_key}", tok_writer)
    if ok:
        print("[ping_seal_lock] 批锁串行化符合预期：后到只记争用痕，无双进")
        return 0
    print("[ping_seal_lock] 失败：两路同批封批出现双进或争用痕缺失")
    return 1


if __name__ == "__main__":
    sys.exit(main())
