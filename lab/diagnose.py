"""
策略弱點診斷：把交易按 IS/OOS × 多空 × 出場原因拆開，加上 MFE/MAE 分析。
每天改良 HSO 前先跑這個，改良點子要針對這裡看到的弱點。

  python -m lab.diagnose hso_v1
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import engine, registry
from .config import IS_END, TIMEFRAMES
from .market import Market


def diagnose(sid: str, data_dir: str | None = None) -> str:
    e = next(x for x in registry.load() if x["id"] == sid)
    res = engine.run(registry.instantiate(e), Market.load(data_dir))
    width = pd.Timedelta(TIMEFRAMES[e["timeframe"]])
    bars = res.bars
    rows = []
    for t in res.trades:
        w = bars[(bars.index + width > t.entry_time) & (bars.index + width <= t.exit_time)]
        risk = abs(t.entry_price - (t.init_stop or t.entry_price)) or np.nan
        if t.side > 0:
            mfe, mae = (w.high.max() - t.entry_price) / risk, (t.entry_price - w.low.min()) / risk
        else:
            mfe, mae = (t.entry_price - w.low.min()) / risk, (w.high.max() - t.entry_price) / risk
        rows.append(dict(seg="IS" if t.entry_time < IS_END else "OOS", side="多" if t.side > 0 else "空",
                         reason=t.exit_reason.replace("Hades 平多", "Hades").replace("Hades 平空", "Hades"),
                         R=t.R, ret=t.ret, mfe=mfe, mae=mae, bars=(t.exit_time - t.entry_time) / width,
                         risk_pct=risk / t.entry_price * 100))
    T = pd.DataFrame(rows)

    def g(d):
        return pd.Series({"筆數": len(d), "勝率%": (d.ret > 0).mean() * 100, "平均R": d.R.mean(), "總R": d.R.sum(),
                          "複利報酬%": ((1 + d.ret).prod() - 1) * 100, "持有根數中位": d.bars.median(),
                          "MFE中位R": d.mfe.median()})

    out = [f"# {sid} 弱點診斷", "", "## 多空", T.groupby(["seg", "side"]).apply(g).round(2).to_string(),
           "", "## 出場原因", T.groupby(["seg", "reason"]).apply(g).round(2).to_string(),
           "", "## 獲利回吐（MFE ≥ 1.5R 但最後 < 0.5R）",
           T.assign(giveback=(T.mfe >= 1.5) & (T.R < 0.5)).groupby("seg").giveback.mean().mul(100).round(0).to_string() + " %",
           "", f"## 初始風險（停損距離）中位數：{T.risk_pct.median():.2f}%",
           "", "## OOS R 分布", str(np.round(np.sort(T[T.seg == "OOS"].R.values), 2))]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("--data")
    a = ap.parse_args()
    print(diagnose(a.id, a.data))


if __name__ == "__main__":
    main()
