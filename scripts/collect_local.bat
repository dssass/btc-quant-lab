@echo off
REM 方案 B：在自己的電腦（台灣 IP）收集資料，再推上 GitHub。
REM 用 Windows「工作排程器」設定每小時執行一次這個檔案。
cd /d %~dp0..
git pull --rebase
python collector\collect.py
git add data
git diff --cached --quiet && exit /b 0
git commit -m "data (local): %date% %time%"
git push
