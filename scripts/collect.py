#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collect.py — Thu thập & chuẩn hóa URL stream công khai (radio Việt/Trung Quốc/AI/TV VN).
Output: data/sources.json (candidates). Không touch mạng nội bộ (SSRF-safe).
"""
import csv
import json
import os
import re
import socket
import sys
import time
import ipaddress
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)

TIMEOUT = 10
MAX_WORKERS = 6

# ---------------------------------------------------------------- SSRF guard
# Dải IP bị cấm: private / loopback / link-local / reserved / metadata (169.254.169.254)
def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or str(ip) == "169.254.169.254")

def _host_is_safe(host: str) -> bool:
    # resolve tất cả địa chỉ, nếu bất kỳ cái nào bị chặn → không an toàn
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

def url_safe(url: str) -> bool:
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = p.hostname
    if not host:
        return False
    # localhost / 127.x / tên metadata
    if host == "localhost" or host.endswith(".local") or host == "metadata.google.internal":
        return False
    # nếu host là IP trần, check trực tiếp
    try:
        if _is_blocked_ip(host):
            return False
    except Exception:
        pass
    return _host_is_safe(host)

# ---------------------------------------------------------------- canonical URL
def canonical_url(url: str) -> str:
    """Chuẩn hóa: loại fragment, lowercase scheme/host, bỏ trailing slash."""
    try:
        p = urllib.parse.urlparse(url.strip())
    except Exception:
        return ""
    scheme = p.scheme.lower()
    host = p.hostname.lower() if p.hostname else ""
    if not scheme or not host:
        return ""
    try:
        port = p.port
    except ValueError:
        port = None
    netloc = host + (f":{port}" if port else "")
    path = re.sub(r"/+$", "", p.path) or "/"
    q = p.query
    return urllib.parse.urlunparse((scheme, netloc, path, "", q, ""))

# ---------------------------------------------------------------- fetch
def fetch(url: str, timeout: int = TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "vietnam-iptv-collect/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return r.read().decode("utf-8", errors="replace")

def parse_m3u(text: str, source: str):
    """Parse #EXTINF + URL pairs."""
    items = []
    lines = text.splitlines()
    cur = {}
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("#EXTINF"):
            m = re.search(r'group-title="([^"]*)"', ln)
            cur["group"] = m.group(1) if m else ""
            m = re.search(r'radio="([^"]*)"', ln)
            cur["radio"] = (m.group(1) if m else "true").lower()
            cur["name"] = ln.split(",", 1)[-1].strip() if "," in ln else ""
        elif ln and not ln.startswith("#") and cur:
            url = ln
            if url_safe(url):
                items.append({
                    "url": url, "canonical": canonical_url(url),
                    "name": cur.get("name", ""), "group": cur.get("group", ""),
                    "radio": cur.get("radio", "true"), "source": source,
                })
            cur = {}
    return items

# ---------------------------------------------------------------- Radio Browser
def fetch_radio_browser(params: dict, source: str):
    base = "https://de1.api.radio-browser.info/json/stations/search"
    qs = urllib.parse.urlencode({**params, "hidebroken": "true", "limit": 100})
    url = f"{base}?{qs}"
    try:
        txt = fetch(url, timeout=15)
        arr = json.loads(txt)
    except Exception as e:
        print(f"  [rb] {source} fail: {str(e)[:60]}")
        return []
    out = []
    for s in arr:
        u = s.get("url_resolved") or s.get("url") or ""
        if not url_safe(u):
            continue
        out.append({
            "url": u, "canonical": canonical_url(u),
            "name": s.get("name", ""), "group": source,
            "radio": "true", "source": f"radio-browser:{source}",
            "tags": s.get("tags", ""),
        })
    return out

# ---------------------------------------------------------------- main
def collect():
    candidates = {}
    seen_canon = set()

    def add(lst):
        for it in lst:
            c = it.get("canonical") or canonical_url(it.get("url", ""))
            if not c or c in seen_canon:
                continue
            seen_canon.add(c)
            it["canonical"] = c
            candidates[c] = it

    print("[1/5] AlonDrilich vn.m3u + cn.m3u ...")
    for cc in ("vn", "cn"):
        u = f"https://raw.githubusercontent.com/AlonDrilich/radio-playlists/main/countries/{cc}.m3u"
        try:
            add(parse_m3u(fetch(u), f"alondrilich:{cc}"))
        except Exception as e:
            print(f"  fail {cc}: {str(e)[:60]}")

    print("[2/5] iptv-org countries/vn.m3u (TV) ...")
    try:
        add(parse_m3u(fetch("https://iptv-org.github.io/iptv/countries/vn.m3u"), "iptv-org:vn-tv"))
    except Exception as e:
        print(f"  fail: {str(e)[:60]}")

    print("[3/5] iptv-org categories/music.m3u (music TV) ...")
    try:
        add(parse_m3u(fetch("https://iptv-org.github.io/iptv/categories/music.m3u"), "iptv-org:music"))
    except Exception as e:
        print(f"  fail: {str(e)[:60]}")

    print("[4/5] ChangeGod/viti listtvgiang ...")
    try:
        add(parse_m3u(fetch("https://raw.githubusercontent.com/ChangeGod/viti/main/listtvgiang"), "changegod:viti"))
    except Exception as e:
        print(f"  fail: {str(e)[:60]}")

    print("[5/5] Radio Browser API (VN / chinese / tiktok) ...")
    for params, tag in [
        ({"countrycode": "VN"}, "vn"),
        ({"tag": "chinese"}, "chinese"),
        ({"tag": "tiktok"}, "tiktok"),
    ]:
        add(fetch_radio_browser(params, tag))

    # AI radio leads (hand-verified trước đó)
    ai_leads = [
        ("botflix.tv AI radio", "https://hls.botflix.tv/radioo/index.m3u8"),
        ("mugenradio AI", "https://mugenradio.com/hls/live.m3u8"),
    ]
    for name, u in ai_leads:
        if url_safe(u):
            c = canonical_url(u)
            if c not in seen_canon:
                seen_canon.add(c)
                candidates[c] = {"url": u, "canonical": c, "name": name,
                                 "group": "AI Music", "radio": "true",
                                 "source": "ai-lead", "tags": "ai"}

    out = sorted(candidates.values(), key=lambda x: (x["source"], x["name"]))
    with open(os.path.join(DATA, "sources.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "candidates": out},
                  f, ensure_ascii=False, indent=2)

    print(f"\nDONE: {len(out)} candidates → data/sources.json")
    # thống kê nhanh theo nhóm
    grp = {}
    for it in out:
        grp[it["source"]] = grp.get(it["source"], 0) + 1
    for k, v in sorted(grp.items()):
        print(f"  {k}: {v}")

if __name__ == "__main__":
    collect()
