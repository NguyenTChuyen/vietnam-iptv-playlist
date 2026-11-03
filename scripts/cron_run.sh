#!/usr/bin/env bash
# Cron mỗi ngày: 06:00 validate + build; 18:00 discovery (collect).
# Dùng git pull trước, commit/push chỉ khi có thay đổi. Không in token.
set -euo pipefail

REPO="/home/ubuntu/github/vietnam-iptv-playlist"
cd "$REPO"

git pull --rebase --quiet origin main 2>/dev/null || true

MODE="${1:-validate}"
if [ "$MODE" = "discovery" ]; then
  python3 scripts/collect.py
fi
python3 scripts/validate.py
python3 scripts/build.py

# Commit/push chỉ khi file thay đổi
if git diff --quiet && git diff --cached --quiet; then
  echo "KHÔNG CÓ THAY ĐỔI — skip push"
  exit 0
fi

git add -A
git commit -q -m "cron($MODE): cập nhật playlist $(date -u +%Y-%m-%dT%H:%MZ)"
git push --quiet origin main
echo "ĐÃ PUSH $(git log --oneline -1)"
