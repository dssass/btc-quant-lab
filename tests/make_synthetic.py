"""
產生合成行情（只給測試用），格式和收集器存的一模一樣。

  python tests/make_synthetic.py   → tests/synthetic_data/
"""

import csv
import json
import os
from datetime import datetime, timezone

import numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "synthetic_data")
SRC = "synthetic"
M15 = 15 * 60 * 1000


def write(series, header, rows):
    d = os.path.join(OUT, SRC, series)
    os.makedirs(d, exist_ok=True)
    by = {}
    for r in rows:
        m = datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc).strftime("%Y-%m")
        by.setdefault(m, []).append(r)
    for m, rs in by.items():
        with open(os.path.join(d, f"{m}.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rs)


def main(seed=7):
    rng = np.random.default_rng(seed)
    start = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(2026, 10, 2, 8, tzinfo=timezone.utc).timestamp() * 1000)
    ts = np.arange(start, end, M15)
    n = len(ts)
    # 有趨勢段落的隨機漫步：漂移每 ~2000 根換一次
    drift = np.repeat(rng.normal(0, 0.00006, n // 2000 + 1), 2000)[:n]
    r = drift + rng.normal(0, 0.004, n)
    close = 30000 * np.exp(np.cumsum(r))
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0015, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0015, n)))
    vol = np.abs(rng.normal(100, 30, n))
    basis = 0.0003 + np.cumsum(rng.normal(0, 0.00002, n)) * 0.1
    perp = lambda x: x * (1 + basis)
    spot_rows = [[int(t), round(o, 2), round(h, 2), round(l, 2), round(c, 2), round(v, 4)]
                 for t, o, h, l, c, v in zip(ts, open_, hi, lo, close, vol)]
    perp_rows = [[int(t), round(o, 2), round(h, 2), round(l, 2), round(c, 2), round(v * 3, 4)]
                 for t, o, h, l, c, v in zip(ts, perp(open_), perp(hi), perp(lo), perp(close), vol)]
    write("spot_15m", ["ts", "open", "high", "low", "close", "volume"], spot_rows)
    write("perp_15m", ["ts", "open", "high", "low", "close", "volume"], perp_rows)
    h = np.arange(start, end + 1, 4 * M15)
    oi = 50000 * np.exp(np.cumsum(rng.normal(0, 0.003, len(h))))
    # 故意挖一段 OI 缺漏，測試缺資料的處理
    keep = ~((h > start + 300 * 4 * M15) & (h < start + 400 * 4 * M15))
    write("oi_1h", ["ts", "oi"], [[int(t), round(v, 3)] for t, v, k in zip(h, oi, keep) if k])
    with open(os.path.join(OUT, "manifest.json"), "w") as fh:
        json.dump({"source": SRC, "symbol": "BTCUSDT"}, fh)
    print(f"寫入 {n} 根 15m K 棒到 {OUT}")


if __name__ == "__main__":
    main()
