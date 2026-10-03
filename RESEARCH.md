# 每日研究流程（給每天早上的 Claude 排程任務看）

目標：**每天從網路找一個點子來改良使用者的 HSO 策略**，用「冠軍挑戰」的方式累積改良。

三個版本：
- **原版** `hso_v1`：使用者原本的腳本，永遠不動（`pine/HSO_original.pine`）
- **修改版**（冠軍）：目前最好的改良版，registry 裡 `role = champion`；還沒有人贏過原版時 = 原版
  （`pine/HSO_modified.pine`）
- **今日挑戰者**：繼承修改版、只多改一個點。贏過修改版就取代它，輸了修改版保持不變。

使用者是電機系研究生，寫過 Pine/Python 策略，報告用**繁體中文**，標題清楚、手機好讀。
使用者的觀點：趨勢型系統不該用勝率當優化目標，看 R 倍數、期望值、Profit Factor。

---

## 每日步驟

1. **準備環境**
   ```bash
   git pull
   pip install -r requirements.txt --break-system-packages -q
   python tests/test_engine.py
   ```

2. **檢查資料**
   - 最新 K 棒超過 3 小時前 → 報告最上面標示警告（GitHub Actions 可能被停用）。

3. **評估所有上架策略**
   ```bash
   python -m lab.evaluate
   ```

4. **診斷目前修改版的弱點**
   ```bash
   python -m lab.diagnose <修改版 id>   # registry 裡 role = champion 的那個；沒有就用 hso_v1
   ```
   看哪裡在虧：多 vs 空、哪種出場、MFE（進場後走不出去？）、獲利回吐、停損距離、哪一年特別差。
   **選一個最明顯的弱點**當今天的目標。

5. **上網找針對這個弱點的點子（一天只測一個）**
   - 找**規則明確**、有出處的做法：學術論文、經典交易系統（Wilder、Turtle、Chandelier Exit…）、
     有完整規則的文章或公開腳本。
   - 先看 `research/log.csv`，**不要重複**已經測過的點子。
   - 參數**照原始出處**，出處沒給就用業界常見值，在 `--note` 寫理由。
     **禁止**為了過門檻而反覆調參數重測：一個點子一天只跑一組設定。

6. **實作挑戰者並對決**
   - 新檔 `strategies/hso_vN_<slug>.py`，**繼承目前修改版的類別**（不是原版），只覆寫需要改的部分。
     範例：`strategies/hso_v3_regime.py`。版本號每天 +1，不管前一個有沒有贏。
   - 檔案開頭 docstring 寫清楚：診斷到的弱點、改了什麼、出處網址。
   - **永遠不要修改 `strategies/hso.py` 或任何已上架的策略檔**（code_sha 會檢查，繼承的父類別也算）。
   ```bash
   python -m lab.duel strategies.hso_vN_<slug>:<Class> \
       --params '{"新參數": 值}' --source "<網址>" --note "<弱點> → <改法>"
   ```
   `duel` 會自動：偷看未來檢查 → 原版 / 修改版 / 挑戰者三方比較 → 判定 → 更新 registry、
   `research/log.csv`、`reports/duel.json`。
   勝出條件：OOS PF 比修改版高 0.10 以上、平均 R 不變差、回撤不差超過 5 個百分點、IS PF ≥ 1，
   而且 OOS 前半、後半段報酬都不能輸修改版超過 2%。

   **挑戰者勝出時，一定要產出 Pine：**
   - 以 `pine/HSO_modified.pine` 為底，把今天的改動翻成 Pine v6，寫回 `pine/HSO_modified.pine`
   - 同一份複製到 `pine/history/<新 id>.pine`
   - 檔頭註解加上這一版的改動與出處；`strategy()` 標題改成新版本號
   - 只能用已收盤的資料（`request.security` 要搭配 `[1]` + `lookahead_on`），不能重繪
   - 在報告裡附上和前一版的差異（`diff --strip-trailing-cr`），並提醒使用者到 TradingView 編譯確認

7. **推回 GitHub**
   ```bash
   git add strategies research reports lab pine
   git commit -m "research: <日期> <版本 id> <通過/未通過>"
   git pull --rebase && git push
   ```

8. **發報告（Artifact）**
   - 用 `reports/latest.json` 做一頁儀表板，更新同一個 Artifact（網址記在 `reports/ARTIFACT_URL`，
     沒有就新建並把網址寫進去、commit）。
   - 內容順序：資料狀態 → HSO 各版本排行（前測報酬、前測筆數、OOS PF、平均 R、目前持倉）→
     HSO 今日狀態、持倉方向、停損 → 過去 24 小時平倉 → 今天的診斷、改良點子、出處、和原版的比較結果 →
     需要使用者決定的事。

---

## 規則

- **凍結就是凍結。** 上架後的策略程式碼和參數不能改。要改就做新版本、新 id。
- **前測（FWD）才是真的。** 改良版在 OOS 贏過原版只是入場券；前測也贏才值得建議使用者採用。
  前測少於 10 筆時，報告要明講「樣本不足，還不能下結論」。
- **修改版的可信度**：修改版是在 OOS 上一路挑出來的，挑越多次越可能只是適應了 OOS。
  所以報告要一直並列原版和修改版的**前測**；修改版前測 ≥ 15 筆後仍輸原版，要在報告提出警告。
- **淘汰建議**：前測 ≥ 20 筆且 PF < 0.8，或前測最大回撤超過 OOS 的 1.5 倍 → 建議淘汰，
  由使用者決定（決定後把 registry 裡的 `status` 改成 `retired`）。
- **失敗也要記錄。** 沒過門檻的點子照樣留在 log 和 strategies/ 裡，這是避免重複測試和自我欺騙的關鍵。
- 不要修改 `lab/` 裡的引擎或門檻，除非是修 bug；修 bug 要在報告中說明，並重跑 `tests/`。
- 這是模擬交易研究，**不下真單、不碰 API key**。

## 已知限制

- OKX 的 OI 歷史只有約 2 個月，所以 IS/OOS 期間訂單流的 OI 項大多是中性（0）。
  針對 OI 的改良點子在 IS/OOS 上測不出效果，要等前測累積。
