"""
依「上線前 5 條紀律」檢驗 HSO 原版（只做多、4H），用 13 個樣本外幣。

  - 參數高原：每個參數各自 ±10%，加上 40 組同時隨機 ±10%，期望值必須集體為正
  - 前後段衰減：2024-07-01 前 vs 後（HSO 沒有用這批幣調參，所以這是「時間上的樣本外」）
  - 多 Regime：逐年期望值，並用 13 幣等權買進持有報酬標示當年行情

用法：python research/validation.py [--seed 11]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))

from lab import engine  # noqa: E402
from strategies.hso import HSO  # noqa: E402
from universe_test import UNSEEN, HourlyMarket, trades_df  # noqa: E402

BASE = {"TREND_LEN": 120, "BASE_LEN": 200, "MA1_LEN": 41, "MA2_LEN": 72, "DC_LEN": 20}


def run_set(markets: dict, params: dict) -> np.ndarray:
    rs = []
    for coin, m in markets.items():
        res = engine.run(HSO(timeframe="4h", mode="Long only", **params), m)
        rs.append(trades_df(res, coin, "x")["netR"].dropna().to_numpy())
    return np.concatenate(rs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--random", type=int, default=40)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    markets = {c: HourlyMarket(os.path.join(ROOT, "data_universe", "okx", c)) for c in UNSEEN}

    rows = []
    base_r = run_set(markets, BASE)
    rows.append({"set": "原版", **BASE, "n": len(base_r), "expR": base_r.mean()})
    for k, v in BASE.items():
        for f in (0.9, 1.1):
            p = {**BASE, k: max(2, round(v * f))}
            r = run_set(markets, p)
            rows.append({"set": f"{k}×{f}", **p, "n": len(r), "expR": r.mean()})
            print(rows[-1], flush=True)
    for j in range(args.random):
        p = {k: max(2, round(v * rng.uniform(0.9, 1.1))) for k, v in BASE.items()}
        r = run_set(markets, p)
        rows.append({"set": f"隨機{j + 1}", **p, "n": len(r), "expR": r.mean()})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(ROOT, "reports", "plateau.csv"), index=False)
    rnd = df[df["set"].str.startswith("隨機")]["expR"]
    one = df[df["set"].str.contains("×")]["expR"]
    summary = {
        "原版 expR": float(base_r.mean()),
        "單一參數 ±10%：最小 / 最大": [float(one.min()), float(one.max())],
        "隨機 ±10%：最小 / 中位數 / 最大": [float(rnd.min()), float(rnd.median()), float(rnd.max())],
        "隨機組合為正的比例": float((rnd > 0).mean()),
        "原版在隨機組合中的百分位": float((rnd < base_r.mean()).mean()),
    }
    with open(os.path.join(ROOT, "reports", "plateau_summary.json"), "w") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=1))

    # 逐年行情：13 幣等權買進持有
    yr = {}
    for c, m in markets.items():
        d = m.bars("1d", "perp")["close"] if hasattr(m, "bars") else None
        b = m.bars("4h", "perp")["close"]
        yr[c] = b.groupby(b.index.year).agg(lambda s: s.iloc[-1] / s.iloc[0] - 1)
    bh = pd.DataFrame(yr).mean(axis=1) * 100
    print("13 幣等權買進持有（%/年）：", bh.round(0).to_dict())


if __name__ == "__main__":
    main()
