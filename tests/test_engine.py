"""
引擎行為測試（不需要真實資料）：python tests/test_engine.py

確認停損、跳空、收盤進出場、反手、手續費的處理和說明一致。
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lab import engine  # noqa: E402
from strategies.base import Decision, Strategy  # noqa: E402


class FakeMarket:
    def __init__(self, rows):
        idx = pd.date_range("2025-01-01", periods=len(rows), freq="1h", tz="UTC")
        self.df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)

    def bars(self, tf="1h", kind="perp"):
        return self.df


class Script(Strategy):
    """照劇本下單：plan = {bar: Decision}"""
    warmup = 0

    def __init__(self, plan, timeframe="1h"):
        super().__init__()
        self.plan, self.timeframe = plan, timeframe

    def prepare(self, m):
        return m.bars()

    def on_bar(self, i, ctx):
        return self.plan.get(i)


def run(rows, plan, fee=0.0, slip=0.0):
    return engine.run(Script(plan), FakeMarket(rows), fee=fee, slip=slip, capital=1000)


flat = [100, 101, 99, 100]

# 1. 停損在盤中被碰到 → 以停損價出場，而且停損從下一根才生效
r = run([flat, flat, [100, 100, 94, 96], flat], {1: Decision(enter=1, stop=95)})
t = r.trades[0]
assert t.exit_reason == "stop" and t.exit_price == 95, t
assert abs(t.R - (-1.0)) < 1e-9, t.R
assert r.trades[0].entry_time == pd.Timestamp("2025-01-01 02:00", tz="UTC")  # 第 1 根收盤

# 2. 進場那根本身碰到停損價 → 不算（停損下一根才掛上）
r = run([flat, [100, 101, 90, 100], flat, flat], {1: Decision(enter=1, stop=95)})
assert not r.trades and r.open_trade is not None

# 3. 開盤跳空穿過停損 → 用開盤價成交
r = run([flat, flat, [90, 92, 88, 91], flat], {1: Decision(enter=1, stop=95)})
assert r.trades[0].exit_price == 90

# 4. 空單停損
r = run([flat, flat, [100, 106, 99, 104], flat], {1: Decision(enter=-1, stop=105)})
assert r.trades[0].exit_price == 105 and r.trades[0].side == -1

# 5. 收盤平倉 + 損益
r = run([flat, flat, [100, 111, 99, 110], flat], {1: Decision(enter=1), 2: Decision(exit=True)})
t = r.trades[0]
assert t.exit_price == 110 and abs(t.pnl - 100) < 1e-9 and abs(r.equity.iloc[-1] - 1100) < 1e-9

# 6. 反手：多 → 空
r = run([flat, flat, [100, 101, 99, 105], [105, 106, 99, 100]], {1: Decision(enter=1), 2: Decision(enter=-1)})
assert r.trades[0].exit_reason == "reverse" and r.open_trade.side == -1
assert abs(r.equity.iloc[-1] - (1050 + 1050 / 105 * 5)) < 1e-6

# 7. 手續費 + 滑價
r = run([flat, flat, flat, flat], {1: Decision(enter=1), 2: Decision(exit=True)}, fee=0.001, slip=0.001)
assert r.equity.iloc[-1] < 1000 * (1 - 0.0039)

# 8. 停損移動：下一根才用新停損
r = run([flat, flat, [100, 101, 97, 100], [100, 101, 96, 100]],
        {1: Decision(enter=1, stop=90), 2: Decision(stop=98)})
assert r.trades and r.trades[0].exit_price == 98 and r.trades[0].exit_time == pd.Timestamp("2025-01-01 04:00", tz="UTC")

print("✅ 引擎測試全部通過")
