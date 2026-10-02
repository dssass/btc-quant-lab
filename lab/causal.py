"""
偷看未來檢查：把資料截在幾個時間點重跑，
截斷前的指標和交易必須和完整資料跑出來的一模一樣。
不一樣就代表策略用到了「當時還不知道」的資料。

用法：python -m lab.causal strategies.hso:HSO --tf 1h --params '{}'
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from . import engine
from .config import TIMEFRAMES
from .market import Market
from .registry import get_class


def _same(a: pd.Series, b: pd.Series) -> bool:
    if a.dtype == bool or b.dtype == bool:
        return bool((a.astype(bool).to_numpy() == b.astype(bool).to_numpy()).all())
    x, y = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
    return bool(np.allclose(x, y, rtol=1e-9, atol=1e-9, equal_nan=True))


def check(make, market: Market, n_cuts: int = 3) -> tuple[bool, list[str]]:
    full_s = make()
    full = engine.run(full_s, market)
    width = pd.Timedelta(TIMEFRAMES[full_s.timeframe])
    idx = full.bars.index
    start = max(full_s.warmup + 50, int(len(idx) * 0.4))
    if len(idx) - start < n_cuts + 2:
        return False, ["資料太少，無法檢查"]
    cuts = np.linspace(start, len(idx) - 2, n_cuts).astype(int)
    problems = []
    for k in cuts:
        T = idx[k] + width
        part_s = make()
        part = engine.run(part_s, market.truncate(T))
        a = full.bars.loc[part.bars.index]
        if len(part.bars) != (idx + width <= T).sum():
            problems.append(f"截在 {T}：K 棒數量不一致")
            continue
        for col in part.bars.columns:
            if col in a.columns and not _same(a[col], part.bars[col]):
                bad = part.bars.index[~np.isclose(a[col].astype(float), part.bars[col].astype(float), equal_nan=True)][:1]
                problems.append(f"截在 {T}：欄位 {col} 不一致（最早在 {list(bad)}）")
        ft = [(t.entry_time, round(t.entry_price, 6), t.side) for t in full.trades if t.entry_time <= T]
        if full.open_trade is not None and full.open_trade.entry_time <= T:
            ft.append((full.open_trade.entry_time, round(full.open_trade.entry_price, 6), full.open_trade.side))
        pt = [(t.entry_time, round(t.entry_price, 6), t.side) for t in part.trades]
        if part.open_trade is not None:
            pt.append((part.open_trade.entry_time, round(part.open_trade.entry_price, 6), part.open_trade.side))
        if ft != pt:
            problems.append(f"截在 {T}：進場紀錄不一致（完整 {len(ft)} 筆 vs 截斷 {len(pt)} 筆）")
    return (not problems), problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cls")
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--data")
    args = ap.parse_args()
    cls = get_class(args.cls)
    params = json.loads(args.params)
    ok, probs = check(lambda: cls(timeframe=args.tf, **params), Market.load(args.data))
    print("✅ 沒有偷看未來" if ok else "❌ 偷看未來：\n  " + "\n  ".join(probs))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
