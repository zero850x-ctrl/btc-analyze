# BTC engine vendor pin (XAUUSD analyze_v3)

BTC 唔自己寫形態引擎 — `analyze_v3.py` 係 XAUUSD repo 嘅 vendor snapshot，
`btc_engine.py` 經 importlib 讀佢（零改動照用）。本檔 pin 住每次 sync 嘅來源，
等日後 XAU 有修正可以對住跟，唔使靠記憶。

## 現行 pin

- XAU commit: `70c362031987bb1cca6ced49a3b7abf544dfc6c9` (main, 2026-10-11)
- XAU analyze_v3 sha256: `6158206d6253bc3a…c9170316`
- analyze_v3.py sha256: 6158206d6253bc3a3e331d7cf644330f9762314751caffc1dc9cf10335170816
  (機器讀行 —— backtest 交叉檢查用，唔好改格式)
- Sync 日期: 2026-10-24 (branch `feat/xau-engine-sync-c`)
- 內容: XAU `main:analyze_v3.py` 原樣 copy，零 BTC 改動

```
6158206d6253bc3a3e331d7cf644330f9762314751caffc1dc9cf10335170816
```

## 之前

- 舊 snapshot sha256: `51a4a4370c0290b6b…`（唔對應 XAU 任何已存 commit，
  30 個 analyze_v3.py 歷史版本逐個對過——估計係中間未 push 狀態 copy 過嚟）
- 缺 12 個 def（含 `push_eligible`、`rr_tp2_cell`、exit 單一入口等 09-24/09-28 修正）

## 下次 sync 流程

1. `git show <xau_commit>:analyze_v3.py | shasum -a 256` 對來源
2. copy 過嚟，`git diff --stat` 睇下多幾多函數
3. 跑 `test_xau_sync.py` + `test_limit_entry.py` + 全倉
   (引擎簽名一變即爆，最敏感係 btc_engine 嗰幾個；GLM R1: 新測試要入列)
4. 更新本檔 pin + 跑 Kimi review + PR（base main）
5. 唔部署（研究 code，主系統停用中，production cron 唔掂）
