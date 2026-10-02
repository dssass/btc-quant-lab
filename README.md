# BTC Quant Lab

每天早上自動：**診斷 HSO 的弱點 → 上網找改良點子 → 做成新版本回測 → 贏過原版才凍結上架 → 和原版並排模擬交易 → 出報告**。
包含使用者的 HSO 策略（Hades × Swing × OrderFlow，4H、多空雙向）的 Python 版。

> 僅供研究與模擬交易，不構成投資建議，不會下任何真單。

---

## 架構

```
GitHub Actions（每小時）              Claude 排程任務（每天早上）
  collector/collect.py                  RESEARCH.md 的流程
  └─ Bybit / OKX 公開行情      ──►      ├─ lab/evaluate.py   評估所有策略
     現貨 15m、永續 15m、OI 1h  data/   ├─ 上網找 1 個新點子 → strategies/xxx.py
                                        ├─ lab/trial.py      偷看未來檢查 + IS/OOS + 門檻
                                        └─ 報告 Artifact + reports/
```

三段績效（最重要的是 FWD）：

| 段落 | 期間 | 意義 |
|---|---|---|
| IS 樣本內 | 資料起點 ~ 2024-07-01 | 策略可能是看著這段設計的 |
| OOS 樣本外 | 2024-07-01 ~ 凍結日 | 上架門檻看這段 |
| **FWD 前測** | 凍結日之後 | 策略寫好時還不存在的資料 = **真正的模擬交易** |

---

## 設定步驟

### 1. 建 repo 並推上程式碼
建一個**私人** repo（建議名稱 `btc-quant-lab`），把這個資料夾推上去。

### 2. 讓 GitHub Actions 開始收資料
- repo → **Actions** 分頁 → 如果有提示，按啟用
- 左邊點 **collect-market-data** → **Run workflow**，手動跑第一次
- 第一次會回補 2020 年至今的資料，約 10–30 分鐘
- 之後每小時自動跑；私人 repo 每月免費 2,000 分鐘，這個用量約 750 分鐘（每次不到 1 分鐘，但按分鐘計費）

### 3. 看第一次執行結果
- ✅ 綠勾勾：`data/manifest.json` 出現，記錄用哪家交易所
- ❌ 出現「所有來源都無法連線」：GitHub 的機器在美國，Bybit / OKX 可能封鎖美國 IP → 改用方案 B

### 方案 B：用自己的電腦收資料
在台灣的電腦上 `git clone` 這個 repo，然後：
- **Windows**：工作排程器 → 建立基本工作 → 每小時 → 執行 `scripts\collect_local.bat`
- **Mac / Linux**：`crontab -e` 加入 `7 * * * * /路徑/btc-quant-lab/scripts/collect_local.sh`

電腦關機時不會收資料，開機後下一次執行會自動補齊缺的部分。

### 4. 在 claude.ai 連上 GitHub
這樣 Claude 每天早上的排程任務才讀得到這個 repo。

---

## 常用指令

```bash
pip install -r requirements.txt

python -m lab.evaluate                       # 跑所有上架策略，產出 reports/latest.md
python -m lab.causal strategies.hso:HSO      # 偷看未來檢查
python -m lab.trial strategies.xxx:Xxx --tf 1h --source "網址" --note "說明" --register

python tests/test_engine.py                  # 引擎測試
python tests/test_collector.py               # 收集器測試（離線）
python tests/make_synthetic.py               # 產生合成資料，之後可加 --data tests/synthetic_data
```

## 檔案

| 路徑 | 內容 |
|---|---|
| `collector/collect.py` | 公開行情收集器（只用標準函式庫） |
| `lab/engine.py` | 逐根回測引擎，行為對齊 Pine `process_orders_on_close` |
| `lab/market.py` | 讀資料、轉週期、低週期 CVD、OI 對齊 |
| `lab/causal.py` | 偷看未來檢查（截斷資料重跑，結果必須一致） |
| `lab/trial.py` | 新策略／改良版試驗 + 上架門檻（`--baseline` 和原版比較） |
| `lab/diagnose.py` | 策略弱點診斷（多空、出場原因、MFE、獲利回吐） |
| `lab/evaluate.py` | 每日評估與報告 |
| `lab/config.py` | 手續費、樣本切分、門檻 |
| `strategies/hso.py` | HSO 的 Python 版（原版，永不修改） |
| `strategies/hso_v*.py` | HSO 改良版，每個檔案繼承原版、只改一個點 |
| `strategies/donchian.py` | 基準對照組 |
| `strategies/registry.json` | 上架策略與凍結時間（凍結後程式碼不能改） |
| `research/log.csv` | 所有測過的點子，包含失敗的 |
| `RESEARCH.md` | 每日研究流程與規則 |

## HSO Python 版和 TradingView 的差異

- 資料來自 Bybit（或 OKX），TradingView 依你圖表選的交易所，數字會有些微差異
- OI 用 1 小時快照；週期小於 1 小時時會比 TradingView 粗
- 滑價設 0.02%（Pine 的 2 ticks 幾乎等於 0），手續費同樣 0.05%
- CVD 只用 delta 的正負號，和 Pine 一樣，所以現貨/永續的成交量單位不影響結果
