"""
蒙地卡羅檢驗：HSO 的期望值有多少是運氣？

使用 research/universe_test.py 產出的 reports/universe_trades.csv（A 基準、13 個樣本外幣）。

三種模擬：
  1. 月份區塊重抽（block bootstrap）
     同一個月的交易一起抽（同時期的幣高度相關），重抽 10,000 次，
     得到期望值的 95% 信賴區間和「期望值 ≤ 0」的機率。
  2. 隨機進場對照（permutation / null model）
     每一筆 HSO 交易，換成「同一個幣、持有同樣久、但隨機時間進場」的交易，重複 2,000 次。
       2a 任何時間隨機做多      → HSO 有沒有比「隨便買」好？
       2b 只在綠區裡隨機做多     → HSO 的進場時機 + 停損出場，有沒有比「綠區裡隨便買」好？
     p 值 = 隨機版本的平均報酬 ≥ HSO 的比例。
  3. 交易順序重排（equity path）
     每筆固定冒 1% 風險（獲利 = 1% × R），把交易順序打亂 10,000 次，
     看最大回撤、最長連敗的分布（同樣的期望值，運氣不同時會經歷多大的回撤）。

用法：python research/montecarlo.py [--seed 7]
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

from lab.config import FEE, SLIPPAGE  # noqa: E402
from strategies.hso import HSO  # noqa: E402
from universe_test import UNSEEN, HourlyMarket  # noqa: E402

COST = 2 * (FEE + SLIPPAGE)
BAR = pd.Timedelta("4h")


def block_bootstrap(tr: pd.DataFrame, col: str, n: int, rng, freq: str = "M") -> dict:
    key = tr["entry"].dt.strftime("%Y-%m") if freq == "M" else tr["entry"].dt.year.astype(str) + "Q" + tr["entry"].dt.quarter.astype(str)
    tr = tr.assign(m=key)
    groups = [g[col].to_numpy() for _, g in tr.groupby("m")]
    sums = np.array([g.sum() for g in groups])
    cnts = np.array([len(g) for g in groups])
    k = len(groups)
    idx = rng.integers(0, k, size=(n, k))
    means = sums[idx].sum(1) / cnts[idx].sum(1)
    return {"mean": float(tr[col].mean()), "ci95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))],
            "p_le_0": float((means <= 0).mean()), "blocks": k}


def random_entry_test(tr: pd.DataFrame, n: int, rng) -> dict:
    """每筆交易換成同幣、同持有時間、隨機進場的交易。"""
    hso_ret, rand_any, rand_green = [], [], []
    per_coin = {}
    for coin, g in tr.groupby("coin"):
        m = HourlyMarket(os.path.join(ROOT, "data_universe", "okx", coin))
        s = HSO(timeframe="4h", mode="Long only")
        df = s.prepare(m)
        close = df["close"].to_numpy()
        # 收盤時間 → 索引
        t_close = df.index + BAR
        pos = pd.Series(np.arange(len(df)), index=t_close)
        ei = pos.reindex(g["entry"]).to_numpy()
        xi = pos.reindex(g["exit"]).to_numpy()
        ok = ~np.isnan(ei) & ~np.isnan(xi)
        dur = (xi[ok] - ei[ok]).astype(int)
        dur = np.maximum(dur, 1)
        hso_ret.append(g["ret_pct"].to_numpy()[ok] / 100)
        lo, hi = s.warmup, len(df) - 1
        green = np.where(df["zone"].to_numpy() == 1)[0]
        green = green[(green >= lo)]
        ra = np.empty((n, len(dur)))
        rg = np.empty((n, len(dur)))
        for j, d in enumerate(dur):
            starts = rng.integers(lo, hi - d, size=n)
            ra[:, j] = close[starts + d] / close[starts] - 1 - COST
            gs = green[green < hi - d]
            st = gs[rng.integers(0, len(gs), size=n)]
            rg[:, j] = close[st + d] / close[st] - 1 - COST
        rand_any.append(ra)
        rand_green.append(rg)
        per_coin[coin] = {"n": int(ok.sum()), "hso": float(np.mean(hso_ret[-1]) * 100),
                          "rand_any": float(ra.mean() * 100), "rand_green": float(rg.mean() * 100)}
    h = np.concatenate(hso_ret)
    ra = np.concatenate(rand_any, axis=1).mean(1)
    rg = np.concatenate(rand_green, axis=1).mean(1)
    return {
        "n_trades": int(len(h)), "hso_mean_pct": float(h.mean() * 100),
        "rand_any_mean_pct": float(ra.mean() * 100), "rand_any_p95_pct": float(np.percentile(ra, 95) * 100),
        "p_any": float((ra >= h.mean()).mean()),
        "rand_green_mean_pct": float(rg.mean() * 100), "rand_green_p95_pct": float(np.percentile(rg, 95) * 100),
        "p_green": float((rg >= h.mean()).mean()),
        "per_coin": per_coin,
    }


def equity_paths(r: np.ndarray, n: int, rng, risk: float = 0.01) -> dict:
    dds, streaks = np.empty(n), np.empty(n)
    for k in range(n):
        x = rng.permutation(r)
        eq = np.cumprod(1 + risk * x)
        peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
        dds[k] = ((peak - eq) / peak).max()
        loss = x <= 0
        best = cur = 0
        for v in loss:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        streaks[k] = best
    q = lambda a, p: float(np.percentile(a, p))  # noqa: E731
    return {"risk_per_trade": risk, "n_trades": int(len(r)),
            "max_dd_median": q(dds, 50), "max_dd_p95": q(dds, 95), "max_dd_p99": q(dds, 99),
            "loss_streak_median": q(streaks, 50), "loss_streak_p95": q(streaks, 95)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--trades", default=os.path.join(ROOT, "reports", "universe_trades.csv"))
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    tr = pd.read_csv(args.trades)
    for c in ("entry", "exit"):
        tr[c] = pd.to_datetime(tr[c], utc=True)
    out = {"seed": args.seed}
    for v in ["A 基準", "B 訂單流關", "C 結構失敗"]:
        sub = tr[(tr["variant"] == v) & (tr["coin"].isin(UNSEEN))].dropna(subset=["netR"])
        out[v] = {"bootstrap_month": block_bootstrap(sub, "netR", 10_000, rng, "M"),
                  "bootstrap_quarter": block_bootstrap(sub, "netR", 10_000, rng, "Q")}
    a = tr[(tr["variant"] == "A 基準") & (tr["coin"].isin(UNSEEN))].dropna(subset=["netR"])
    # 差異的區塊重抽：B−A、C−A 用同一組月份
    out["random_entry"] = random_entry_test(a, 2_000, rng)
    out["equity_1pct"] = equity_paths(a.sort_values("entry")["netR"].to_numpy(), 10_000, rng)

    path = os.path.join(ROOT, "reports", "montecarlo_result.json")
    with open(path, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in out.items() if k != "random_entry"}, ensure_ascii=False, indent=1))
    re_ = {k: v for k, v in out["random_entry"].items() if k != "per_coin"}
    print(json.dumps(re_, ensure_ascii=False, indent=1))
    print(pd.DataFrame(out["random_entry"]["per_coin"]).T.round(2).to_string())


if __name__ == "__main__":
    main()
