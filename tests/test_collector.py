"""
收集器離線測試（不連網）：python tests/test_collector.py
確認增量更新不重複、不存未收盤 K 棒、跨月分檔正確。
"""

import csv
import glob
import importlib.util
import os
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("collect", os.path.join(ROOT, "collector", "collect.py"))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

c.DATA = tempfile.mkdtemp()
c.time.sleep = lambda s: None
# 模擬交易所：回傳新到舊
c.bybit_klines = lambda cat, s, e: [[t, "1", "2", "0.5", "1.5", "3"] for t in range(s, e + 1, c.M15)][::-1]
c.bybit_oi = lambda s, e: [[t, "5"] for t in range(s, e + 1, c.H1)][::-1]

now = c.START_MS + 3000 * c.M15 + 5 * 60 * 1000  # 第 3000 根開盤後 5 分鐘（未收盤）
n1 = c.sync_klines("bybit", "perp", now)
n2 = c.sync_klines("bybit", "perp", now)
n3 = c.sync_klines("bybit", "perp", now + 2 * c.M15)
o1 = c.sync_oi("bybit", now)
o2 = c.sync_oi("bybit", now + c.H1)

rows = [r for f in sorted(glob.glob(os.path.join(c.DATA, "bybit/perp_15m/*.csv")))
        for r in list(csv.reader(open(f)))[1:]]
ts = [int(r[0]) for r in rows]
assert (n1, n2, n3) == (3000, 0, 2), (n1, n2, n3)
assert ts == sorted(set(ts)) and len(ts) == 3002 and ts[-1] == c.START_MS + 3001 * c.M15
assert (o1, o2) == (751, 1), (o1, o2)
print("✅ 收集器測試全部通過")
