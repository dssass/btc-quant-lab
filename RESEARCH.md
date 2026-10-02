# 每日研究流程（給每天早上的 Claude 排程任務看）

目標：每天找一個新的 BTC 交易策略，**誠實地**測試它，好的才上架做模擬交易，
並回報所有上架策略（包含使用者的 HSO）的模擬交易狀況。

使用者看得懂程式和交易（電機系、寫過 Pine/Python 策略），報告用**繁體中文**，標題清楚、手機好讀。

---

## 每日步驟

1. **準備環境**
   ```bash
   git pull
   pip install -r requirements.txt --break-system-packages -q
   python tests/test_engine.py
   ```

2. **檢查資料**
   - `data/manifest.json` 不存在 → 資料管線還沒跑起來。仍然做第 3、4 步的研究和寫程式，
     但跳過回測，在 `research/log.csv` 記一筆 `note=待資料`，報告第一行寫明「資料還沒進來」。
   - 最新 K 棒超過 3 小時前 → 報告最上面標示警告（GitHub Actions 可能被停用或被交易所封鎖）。

3. **評估所有上架策略**
   ```bash
   python -m lab.evaluate
   ```

4. **研究一個新點子（一天只測一個）**
   - 上網找**規則明確**的策略：學術論文、知名交易系統、有完整進出場規則的文章。
     沒有明確規則的（「看感覺」、需要人工判斷形態的）跳過。
   - 先看 `research/log.csv`，**不要重複**已經測過的點子。
   - 參數**照原始出處**，出處沒給就用業界常見值，在 `--note` 寫理由。
     **禁止**為了過門檻而反覆調參數重測：一個點子一天只跑一組設定。
   - 每週最多一次，可以針對 HSO 提出改良版（新檔 `strategies/hso_v2.py`、新 id），
     理由必須來自 HSO 的 OOS 或前測弱點分析，不能只因為回測比較好看。
     **永遠不要修改 `strategies/hso.py`。**

5. **實作 + 測試**
   - 預設用 **4h**（使用者的 HSO 週期），除非出處明確指定別的週期。
   - 新檔放 `strategies/<slug>.py`，繼承 `strategies/base.py` 的 `Strategy`（看 `donchian.py` 當範例）。
   - 只用 `market.bars()`、`market.ltf_delta()`、`market.oi_close()` 拿資料，
     `on_bar(i)` 只能讀第 i 根以前。
   ```bash
   python -m lab.trial strategies.<slug>:<Class> --tf 4h \
       --source "<網址>" --note "<一句話說明>" --register
   ```
   `trial` 會自動做偷看未來檢查、IS/OOS 績效、門檻判定，結果寫進 `research/log.csv`。
   通過門檻才會上架（凍結時間 = 最新資料時間）。

6. **推回 GitHub**
   ```bash
   git add strategies research reports
   git commit -m "research: <日期> <策略 id> <通過/未通過>"
   git pull --rebase && git push
   ```

7. **發報告（Artifact）**
   - 用 `reports/latest.json` 做一頁儀表板，更新同一個 Artifact（網址記在 `reports/ARTIFACT_URL`，
     沒有就新建並把網址寫進去、commit）。
   - 內容順序：資料狀態 → 排行榜（前測報酬、前測筆數、OOS PF、目前持倉）→
     HSO 今日狀態與持倉/停損 → 過去 24 小時平倉 → 今天研究的點子與結果（含出處）→ 需要使用者決定的事。

---

## 規則

- **凍結就是凍結。** 上架後的策略程式碼和參數不能改（`code_sha` 會檢查）。要改就做新版本、新 id。
- **前測（FWD）才是真的。** 排名以前測為主，OOS 為輔；前測筆數少於 10 筆時，報告要明講「樣本不足，還不能下結論」。
- **淘汰建議**：前測 ≥ 20 筆且 PF < 0.8，或前測最大回撤超過 OOS 的 1.5 倍 → 在報告中建議淘汰，
  由使用者決定（決定後把 registry 裡的 `status` 改成 `retired`）。
- **失敗也要記錄。** 沒過門檻的點子照樣寫進 log，這是避免重複測試和自我欺騙的關鍵。
- 不要修改 `lab/` 裡的引擎或門檻，除非是修 bug；修 bug 要在報告中說明，並重跑 `tests/`。
- 這是模擬交易研究，**不下真單、不碰 API key**。
