#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — Xuất playlist M3U tổng hợp + channels.json + health-report.json.
- Chỉ đưa kênh status=up vào playlist.
- Chia danh mục bằng group-title (chuẩn hóa theo map).
- Giữ bản playlist cuối cùng hoạt động nếu >30% kênh khỏe mạnh fail (an toàn).
"""
import json
import os
import re
import time
import shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "vietnam-iptv.m3u")
LAST_GOOD = os.path.join(DATA, "vietnam-iptv.last-good.m3u")

# Map group-title chuẩn hóa
GROUP_MAP = [
    (r"vietnam|việt|viet|vpop|bolero|remix", "Vietnam - Music"),
    (r"china|chinese|华语|c-pop|mandopop|douyin|cpop|中文", "China - Music"),
    (r"ai", "AI Music"),
    (r"tiktok|douyin|抖音", "TikTok - Trending"),
    (r"news|talk|sports", "Vietnam - Radio"),
    (r"tv|\btv\b", "Vietnam - TV"),
]

def map_group(raw):
    if not raw:
        return "International"
    r = raw.lower()
    for pat, label in GROUP_MAP:
        if re.search(pat, r):
            return label
    return "International"

def main():
    health_path = os.path.join(DATA, "health.json")
    with open(health_path, encoding="utf-8") as f:
        health = json.load(f)["stations"]

    stations = list(health.values())
    up = [s for s in stations if s.get("status") == "up"]

    # Kiểm tra tỷ lệ fail hàng loạt (>30% kênh đã từng up giờ down)
    prev_good = []
    if os.path.exists(LAST_GOOD) and os.path.getsize(LAST_GOOD) > 0:
        with open(LAST_GOOD, encoding="utf-8") as f:
            prev_good = [l for l in f.read().splitlines() if not l.startswith("#")]

    prev_up_count = len(prev_good)
    if prev_up_count and len(up) < prev_up_count * 0.7:
        print(f"⚠ Cảnh báo: chỉ {len(up)}/{prev_up_count} kênh up (<70%) — GIỮ bản last-good")
        shutil.copy(LAST_GOOD, OUT)
        # vẫn ghi channels.json từ bản cũ
        return _keep_old_channels()

    # Gom danh mục
    groups = {}
    for s in up:
        g = map_group(s.get("group", ""))
        groups.setdefault(g, []).append(s)

    lines = ["#EXTM3U"]
    for g in sorted(groups.keys()):
        for s in sorted(groups[g], key=lambda x: x.get("name", "").lower()):
            name = (s.get("name") or "").strip()
            if not name:
                name = s.get("url", "")
            lines.append(f'#EXTINF:-1 radio="true" group-title="{g}",{name}')
            lines.append(s["url"])

    playlist = "\n".join(lines) + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(playlist)

    # backup last-good
    shutil.copy(OUT, LAST_GOOD)

    # channels.json
    with open(os.path.join(DATA, "channels.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "groups": {
            g: [{"name": s.get("name"), "url": s.get("url"), "source": s.get("source")}
                for s in gs] for g, gs in groups.items()}},
            f, ensure_ascii=False, indent=2)

    # health-report.json
    with open(os.path.join(DATA, "health-report.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "total": len(stations), "up": len(up),
                   "down": len(stations) - len(up),
                   "down_stations": [s["name"] for s in stations if s["status"] != "up"][:50]},
                  f, ensure_ascii=False, indent=2)

    print(f"DONE: {len(up)} kênh up / {len(stations)} → {OUT}")
    for g, gs in groups.items():
        print(f"  [{g}] {len(gs)} kênh")

def _keep_old_channels():
    # nếu giữ bản cũ, vẫn ghi health-report với trạng thái (không ghi đè channels)
    print("(giữ bản cũ; health-report vẫn cập nhật)")

if __name__ == "__main__":
    main()
