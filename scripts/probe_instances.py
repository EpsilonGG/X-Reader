#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Nitter 实例可用性探测。

为什么要单独一个脚本，而不是直接往 config.yaml 里加实例：
每个死实例都要付一次超时（默认 20s），而抓取是「每个账号 × 每个实例」的循环。
20 个账号 × 3 个死实例 = 20 分钟白等。所以先用这个脚本筛，只把活的写进 config。

判据不是 HTTP 200，而是「响应形状」：
实例被 Cloudflare / Anubis 拦下时同样返回 200，正文是验证页。
本脚本按 rss 里有没有 <item>、html 里有没有 timeline-item/tweet-content 判定，
并单独识别验证页关键词。

用法：
    python scripts/probe_instances.py                      # 用 config.yaml 里的实例
    python scripts/probe_instances.py --host a.com b.com   # 只测指定实例
    python scripts/probe_instances.py --handle re_re_tsubame --json out.json
    python scripts/probe_instances.py --from-candidates    # 测 config 里注释掉的候选

退出码：0 = 至少一个实例 rss 可用；1 = 全部失败（可直接接 CI 告警）。
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "config.yaml"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CHALLENGE = re.compile(
    rb"just a moment|cf-browser-verification|checking your browser|"
    rb"anubis|verifying you are human|enable javascript and cookies|"
    rb"<title>attention required|ddos-guard|access denied|"
    rb"rate limit|too many requests",
    re.I,
)

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def read_endpoints() -> list[str]:
    """从 config.yaml 里抠出 endpoints 列表（不引入 yaml 依赖）。"""
    if not CONFIG.exists():
        return []
    text = CONFIG.read_text(encoding="utf-8")
    block = re.search(r"^\s*endpoints:\s*$(.*?)(?=^\s{0,4}\w+:|\Z)", text, re.S | re.M)
    if not block:
        return []
    return re.findall(r"^\s*-\s*(https?://\S+)\s*$", block.group(1), re.M)


def read_candidates() -> list[str]:
    """config.yaml 里 candidates 段（人工维护的待测名单）。"""
    if not CONFIG.exists():
        return []
    text = CONFIG.read_text(encoding="utf-8")
    block = re.search(r"^\s*candidates:\s*$(.*?)(?=^\s{0,2}\w+:|\Z)", text, re.S | re.M)
    if not block:
        return []
    return re.findall(r"^\s*-\s*(https?://\S+)\s*$", block.group(1), re.M)


def fetch(url: str, timeout: int) -> tuple[int, bytes, str]:
    request = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=CTX) as response:
            return response.status, response.read(600_000), response.geturl()
    except urllib.error.HTTPError as error:
        try:
            body = error.read(60_000)
        except Exception:
            body = b""
        return error.code, body, url
    except Exception as error:  # 网络层失败（DNS、超时、TLS、代理 502）
        return -1, f"{type(error).__name__}: {error}".encode("utf-8", "ignore"), url


def classify_rss(status: int, body: bytes) -> str:
    if status == -1:
        return "unreachable"
    if CHALLENGE.search(body):
        return "challenge"
    if status != 200:
        return f"http-{status}"
    if b"<item" in body or b"<entry" in body:
        return "ok"
    if b"<rss" in body[:2000].lower() or b"<feed" in body[:2000].lower():
        return "empty-feed"
    return "not-feed"


def classify_html(status: int, body: bytes) -> str:
    if status == -1:
        return "unreachable"
    if CHALLENGE.search(body):
        return "challenge"
    if status != 200:
        return f"http-{status}"
    marks = sum(
        1 for mark in (b"timeline-item", b"tweet-content", b"tweet-link", b'class="timeline"')
        if mark in body
    )
    return "ok" if marks >= 2 else "no-timeline"


def probe(host: str, handle: str, timeout: int) -> dict:
    base = host.rstrip("/")
    status, body, final = fetch(f"{base}/{handle}/rss", timeout)
    items = len(re.findall(rb"<item[\s>]|<entry[\s>]", body))
    result = {
        "host": base,
        "rss_status": status,
        "rss_verdict": classify_rss(status, body),
        "rss_items": items,
        "rss_bytes": len(body),
        "redirected_to": final if final.rstrip("/") != base else "",
    }
    status, body, final = fetch(f"{base}/{handle}", timeout)
    result.update(
        {
            "html_status": status,
            "html_verdict": classify_html(status, body),
            "html_bytes": len(body),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 Nitter 实例的 rss / html 两条路线")
    parser.add_argument("--host", nargs="*", default=[], help="只测这些实例（可带 https://）")
    parser.add_argument("--handle", default="re_re_tsubame", help="用哪个账号探测")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--json", default="", help="把结果写成 JSON")
    parser.add_argument("--from-candidates", action="store_true", help="测 config 里的 candidates 段")
    args = parser.parse_args()

    hosts = args.host or (read_candidates() if args.from_candidates else read_endpoints())
    if not hosts:
        print("没有可测的实例：检查 config/config.yaml 的 endpoints / candidates 段。")
        return 1

    results = []
    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(probe, host, args.handle, args.timeout): host for host in hosts}
        for job in futures.as_completed(jobs):
            results.append(job.result())
    order = {host.rstrip("/"): index for index, host in enumerate(hosts)}
    results.sort(key=lambda item: order.get(item["host"], 999))

    print(f"{'实例':<34}{'RSS':<12}{'条':>4}{'KB':>6}  {'HTML':<14}{'KB':>6}")
    print("-" * 78)
    for item in results:
        print(
            f"{item['host'][:33]:<34}{item['rss_verdict']:<12}{item['rss_items']:>4}"
            f"{item['rss_bytes'] // 1024:>6}  {item['html_verdict']:<14}{item['html_bytes'] // 1024:>6}"
        )

    alive = [item for item in results if item["rss_verdict"] == "ok"]
    print()
    print(f"{len(alive)}/{len(results)} 个实例的 rss 可用。")
    if alive:
        print("建议顺序：" + " → ".join(item["host"] for item in alive))
    else:
        print("全部失败。先确认本机能不能直连外网，再怀疑实例。")

    if args.json:
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"结果写入 {args.json}")

    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
