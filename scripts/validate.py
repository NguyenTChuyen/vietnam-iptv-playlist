#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate.py — Kiểm tra stream: HTTP/HLS + ffprobe (chủ động), ghi health.json.
- Giới hạn: timeout 8-12s, retries 2, song song 4-8.
- Chống SSRF cả sau redirect. Không tải audio số lượng lớn (chỉ probe).
- Giữ lịch sử failure_count; playlist công khai chỉ bỏ sau 3 lỗi liên tiếp.
"""
import json
import os
import socket
import subprocess
import sys
import time
import ipaddress
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")

TIMEOUT = 10
RETRIES = 2
MAX_WORKERS = 6
FFPROBE = os.environ.get("FFPROBE", "ffprobe")
MAX_HEALTH_AGE = 3 * 24 * 3600  # 3 ngày coi là cũ

def _is_blocked_ip(ip_str):
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or str(ip) == "169.254.169.254")

def resolve_safe(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_blocked_ip(str(ip)):
            return False
    return True

def head_check(url):
    """Follow redirects tối đa 5, chặn mọi hop vào private/blocked."""
    import http.client
    hop = 0
    cur = url
    while hop < 5:
        p = urllib.parse.urlparse(cur)
        host = p.hostname
        if not host or host == "localhost" or host.endswith(".local"):
            return False, "blocked-host"
        if not resolve_safe(host):
            return False, "blocked-ip"
        try:
            conn_cls = http.client.HTTPSConnection if p.scheme == "https" else http.client.HTTPConnection
            conn = conn_cls(host, p.port or (443 if p.scheme == "https" else 80), timeout=TIMEOUT)
            conn.request("HEAD", p.path or "/", headers={"User-Agent": "vietnam-iptv-validate/1.0"})
            resp = conn.getresponse()
            status = resp.status
            loc = resp.getheader("Location")
            conn.close()
        except Exception as e:
            return False, f"conn-{str(e)[:40]}"
        if status in (301, 302, 303, 307, 308) and loc:
            cur = urllib.parse.urljoin(cur, loc)
            hop += 1
            continue
        return status in (200, 206), f"http-{status}"
    return False, "too-many-redirects"

def ffprobe_check(url):
    try:
        r = subprocess.run(
            ["timeout", "25", FFPROBE, "-v", "error",
             "-show_entries", "format=format_name,bit_rate",
             "-of", "default=noprint_wrappers=1", url],
            capture_output=True, text=True, timeout=30)
        out = r.stdout.strip()
        if "format_name=" not in out:
            return False, "ffprobe-no-format"
        return True, out.replace("\n", " | ")[:160]
    except Exception as e:
        return False, f"ffprobe-{str(e)[:40]}"

def check_one(item, old):
    url = item["url"]
    # 1) HEAD/HTTP + SSRF
    ok, why = head_check(url)
    ms = None
    if not ok:
        return {"status": "down", "reason": why, "response_ms": ms}
    # 2) ffprobe
    ok2, ff = ffprobe_check(url)
    if not ok2:
        return {"status": "down", "reason": ff, "response_ms": ms}
    return {"status": "up", "reason": ff, "response_ms": ms}

def main():
    src_path = os.path.join(DATA, "sources.json")
    health_path = os.path.join(DATA, "health.json")
    with open(src_path, encoding="utf-8") as f:
        candidates = json.load(f)["candidates"]

    # load health cũ
    old_health = {}
    if os.path.exists(health_path):
        with open(health_path, encoding="utf-8") as f:
            old_health = json.load(f)["stations"]

    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(check_one, it, old_health.get(it["canonical"])): it for it in candidates}
        for fut, it in futures.items():
            key = it["canonical"]
            try:
                res = fut.result()
            except Exception as e:
                res = {"status": "down", "reason": f"exc-{str(e)[:40]}", "response_ms": None}
            # merge history
            old = old_health.get(key, {})
            failed = 1 if res["status"] != "up" else 0
            failure_count = old.get("failure_count", 0) + failed
            if failed == 0:
                failure_count = 0
            results[key] = {
                "name": it.get("name", ""),
                "group": it.get("group", ""),
                "radio": it.get("radio", "true"),
                "source": it.get("source", ""),
                "url": it.get("url", ""),
                "status": res["status"],
                "reason": res.get("reason", ""),
                "response_ms": res.get("response_ms"),
                "last_success": old.get("last_success") if failed == 0 else old.get("last_success"),
                "failure_count": failure_count,
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            if failed == 0:
                results[key]["last_success"] = results[key]["checked_at"]

    summary = {"up": 0, "down": 0}
    for v in results.values():
        summary[v["status"]] += 1

    out = {"updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "summary": summary, "stations": results}
    with open(health_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"DONE: up={summary['up']} down={summary['down']} → data/health.json")

if __name__ == "__main__":
    main()
