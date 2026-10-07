"""
預先登記的單一測試（2026-10-08）：現貨 CVD 頂背離 → 平多

規則（沒有新參數，沿用 HSO 的 Donchian 20 擺動點）：
  持有多單時，若本根 K 棒最高價 > 上一個已確認的擺動高點，
  但「累積現貨 CVD」在本根 < 上一個擺動高點那根 K 棒的累積現貨 CVD
  → 價格創高、現貨買盤沒跟上 = 頂背離 → 以收盤價平多。
  累積現貨 CVD = 1h 現貨 K 線 sign(close-open) × volume 的累加（和 HSO 的 CVD 算法相同，只是不截 24 根）。

判斷標準（和 universe_test 相同，跑之前寫死）：
  1. 13 個樣本外幣合併淨期望值（R）比 A 基準高
  2. 本版本合併期望值 t > 2
  3. ≥ 70% 的幣（10/13）期望值比 A 基準高
另外報告：同一筆進場的配對差、按月區塊重抽的配對差信賴區間。

用法：python research/cvd_divergence_test.py
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))

from lab import engine  # noqa: E402
from strategies.base import Ctx, Decision  # noqa: E402
from strategies.hso import HSO, LTF  # noqa: E402
from universe_test import SEEN, UNSEEN, HourlyMarket, summarize, trades_df, tstat  # noqa: E402


class HSOCvdDiv(HSO):
    id = "hso_cvd_div"

    def prepare(self, market) -> pd.DataFrame:
        df = super().prepare(market)
        tf = self.timeframe
        spot_d = market.ltf_delta(tf, LTF[tf], "spot").reindex(df.index)
        cum = spot_d.fillna(0.0).cumsum().where(spot_d.notna().cumsum() > 0)  # 現貨資料開始前 = NaN
        # 重算擺動高點「所在那根」的索引（HSO 只存價格）
        p = self.params
        dc_hi = df["high"].rolling(p["DC_LEN"], min_periods=p["DC_LEN"]).max().shift(1).to_numpy()
        dc_lo = df["low"].rolling(p["DC_LEN"], min_periods=p["DC_LEN"]).min().shift(1).to_numpy()
        hi, lo = df["high"].to_numpy(), df["low"].to_numpy()
        n = len(df)
        sw_hi_bar = np.full(n, -1)
        zz, ext, ext_b, cur_b = 1, np.nan, -1, -1
        for i in range(n):
            if zz == 1:
                if np.isnan(ext) or hi[i] >= ext:
                    ext, ext_b = hi[i], i
                if not np.isnan(dc_lo[i]) and lo[i] < dc_lo[i]:
                    cur_b = ext_b
                    zz, ext, ext_b = -1, lo[i], i
            else:
                if np.isnan(ext) or lo[i] <= ext:
                    ext, ext_b = lo[i], i
                if not np.isnan(dc_hi[i]) and hi[i] > dc_hi[i]:
                    zz, ext, ext_b = 1, hi[i], i
            sw_hi_bar[i] = cur_b
        cumv = cum.to_numpy()
        ref = np.array([cumv[b] if b >= 0 else np.nan for b in sw_hi_bar])
        div = (hi > df["swHi"].to_numpy()) & (cumv < ref)
        df["bearDiv"] = np.where(np.isnan(ref) | np.isnan(cumv), False, div)
        self.a["bearDiv"] = df["bearDiv"].to_numpy()
        return df

    def on_bar(self, i: int, ctx: Ctx) -> Decision | None:
        d = super().on_bar(i, ctx)
        if d is not None and not d.exit and ctx.position > 0 and self.a["bearDiv"][i]:
            d.exit, d.note, d.stop = True, "CVD 頂背離", None
        return d


def month_block_diff(m: pd.DataFrame, n: int = 10_000, seed: int = 3) -> dict:
    rng = np.random.default_rng(seed)
    key = m["entry"].dt.strftime("%Y-%m")
    g = m.assign(k=key).groupby("k")["diff"]
    sums, cnts = g.sum().to_numpy(), g.count().to_numpy()
    idx = rng.integers(0, len(sums), size=(n, len(sums)))
    means = sums[idx].sum(1) / cnts[idx].sum(1)
    return {"ci95": [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))], "p_diff_le_0": float((means <= 0).mean())}


def main() -> None:
    rows = []
    for coin in UNSEEN + SEEN:
        m = HourlyMarket(os.path.join(ROOT, "data_universe", "okx", coin))
        for name, cls in [("A 基準", HSO), ("D CVD 頂背離", HSOCvdDiv)]:
            res = engine.run(cls(timeframe="4h", mode="Long only"), m)
            rows.append(trades_df(res, coin, name))
        print(f"[{coin}] done", flush=True)
    tr = pd.concat(rows, ignore_index=True)
    tr["entry"] = pd.to_datetime(tr["entry"], utc=True)
    oos = tr[tr["coin"].isin(UNSEEN)]
    a, d = oos[oos["variant"] == "A 基準"], oos[oos["variant"] == "D CVD 頂背離"]
    pa, pd_ = summarize(a["netR"]), summarize(d["netR"])
    ca, cd = a.groupby("coin")["netR"].mean(), d.groupby("coin")["netR"].mean()
    better = int(sum(cd.get(c, -9) > ca.get(c, 9) for c in UNSEEN))
    m = d.merge(a, on=["coin", "entry"], suffixes=("_d", "_a"))
    m["diff"] = m["netR_d"] - m["netR_a"]
    changed = m[m["diff"].abs() > 1e-9]
    seen_d = summarize(tr[(tr["coin"].isin(SEEN)) & (tr["variant"] == "D CVD 頂背離")]["netR"])
    seen_a = summarize(tr[(tr["coin"].isin(SEEN)) & (tr["variant"] == "A 基準")]["netR"])
    out = {
        "A": pa, "D": pd_,
        "per_coin": {c: {"A": float(ca.get(c, np.nan)), "D": float(cd.get(c, np.nan))} for c in UNSEEN},
        "paired": {"n": int(len(m)), "changed": int(len(changed)), "mean_diff": float(m["diff"].mean()), "t": tstat(m["diff"].to_numpy()),
                   "changed_mean_diff": float(changed["diff"].mean()) if len(changed) else None,
                   "changed_win": int((changed["diff"] > 0).sum()), **month_block_diff(m)},
        "div_exits": int((d["reason"] == "CVD 頂背離").sum()),
        "div_exit_netR_mean": float(d[d["reason"] == "CVD 頂背離"]["netR"].mean()),
        "seen": {"A": seen_a, "D": seen_d},
        "decision": {"更高": bool(pd_["expR"] > pa["expR"]), "t>2": bool(pd_["t"] > 2), "變好幣數": f"{better}/{len(UNSEEN)}",
                     "≥70%": bool(better >= math.ceil(0.7 * len(UNSEEN)))},
    }
    out["decision"]["採用"] = all(out["decision"][k] for k in ("更高", "t>2", "≥70%"))
    with open(os.path.join(ROOT, "reports", "cvd_divergence_result.json"), "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
