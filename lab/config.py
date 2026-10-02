"""全域設定：手續費、樣本切分、上架門檻。改這裡會影響所有策略，改之前先想清楚。"""

import pandas as pd

# 交易成本（每邊）
FEE = 0.0005        # 0.05%，對應 Pine 的 commission_value = 0.05
SLIPPAGE = 0.0002   # 0.02%，比 Pine 的 2 ticks 保守
CAPITAL = 10_000.0

# 樣本切分（固定日期，所有策略一致）
#   樣本內 IS：資料起點 ~ IS_END
#   樣本外 OOS：IS_END ~ 策略凍結日
#   前測 FWD（模擬交易）：凍結日之後新進來的資料
IS_END = pd.Timestamp("2024-07-01", tz="UTC")

# 新策略上架門檻（在 OOS 上檢查）
GATE = {
    "min_oos_trades": 15,      # 太少筆不能下結論
    "min_oos_pf": 1.2,         # Profit Factor
    "min_is_return": 0.0,      # IS 也要賺錢，避免只是 OOS 運氣好
    "min_oos_return": 0.0,
    "max_oos_dd": 0.45,        # 最大回撤上限
}

TIMEFRAMES = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}
