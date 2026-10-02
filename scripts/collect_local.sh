#!/usr/bin/env bash
# 方案 B：在自己的電腦（台灣 IP）收集資料，再推上 GitHub。
# crontab -e 加一行：7 * * * * /path/to/btc-quant-lab/scripts/collect_local.sh
set -e
cd "$(dirname "$0")/.."
git pull --rebase
python3 collector/collect.py
git add data
git diff --cached --quiet && exit 0
git commit -m "data (local): $(date -u +%Y-%m-%dT%H:%MZ)"
git push
