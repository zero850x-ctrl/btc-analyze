# 幽靈記錄連環市價平倉事故 + 5 項修復 (2026-09-27)

> 事故: 2026-09-20 ~ 09-27，每 15 分鐘一筆 0.00258 BTC 市價賣出，共 **270 筆 = 0.6966 BTC**
> (testnet ≈ $58.7k)，帳戶 BTC 由 0.61 → 0.327。全程**零警報**。
> 止血: 09-27 16:27 (log 備份 + 人手對帳標終態)。修復: 本分支 (fix/btc-reconcile-phantom-guards)。

## 根因鏈 (4 個缺陷疊加)

1. **測試 fixture 寫入生產 log** — `test_rr_gates.py` T2 用 fake fill (orderId 999001)
   直寫 `~/.hermes/reports/btc_testnet_orders.json`，記錄**冇真實 entry 成交**但
   有 qty/planned 價位。(09-13 起每次跑 test 都寫一筆。)
2. **狀態錯標 (L629)** — `place_signal_order` 低 RR flatten：市價平倉**成功**
   (flatten_ok=True) 都被無條件標 `FLATTENED_OCO_FAILED` = 「平倉未確認」= 仍然
   live + 入對帳 rebuild 路徑 (殭屍狀態)。
3. **rebuild 失敗 fallback 市價平倉** — 對帳 rebuild 對呢筆假記錄建 OCO legs，
   用舊價位 → OCO -2010 失敗 → fallback「唔可以裸倉」即市價平倉 → 賣走**唔屬於
   呢筆嘅幣** (帳戶實際 BTC)。status 唔變 → 下個 tick 重試 → 循環 7 日。
4. **零警報** — `🩹 孤兒狀態對帳` 唔喺 cron `NOTABLE_KEYS`；fail-closed 令
   flatten_ok=True 記錄永久佔 cap 1 → 引擎 7 日冇開新倉都冇人知。

## 修復 (5 項)

| # | 修復 | 檔案 |
|---|------|------|
| ① | 測試沙盒化 | `test_rr_gates.py` (LOG_PATH redirect + T6/T6b 防回歸斷言), `test_limit_entry.py` (LOG_PATH + cyc.HEARTBEAT/RECONCILE_STATE redirect), `integration_check_limit.py` (atexit 還原 — 中途 crash 都唔留測試記錄) |
| ② | L629 狀態改終態 | `binance_testnet_paper.py`: flatten 成功 → 保持 `FLATTENED_LOW_FILL_RR` (終態)；只有未確認先標 `FLATTENED_OCO_FAILED` |
| ③ | rebuild 禁市價平倉 | `build_exit_legs(..., allow_flatten=)` — 對帳 rebuild 一律 `allow_flatten=False`；`resolve_orphan_states` 見 `flatten_ok=True` 即判終態 (唔 rebuild)；rebuild 連續失敗 3 次 → freeze 交人手 (`REBUILD_FAIL_FREEZE`) |
| ④ | 對帳/餘額警報 | `btc_auto_trade_cycle.py` `reconcile_alerts()`: 孤兒 summary 變化 + 餘額 high-water 跌 ≥0.05 BTC → ⚠️ (只喺變化先出聲; 走 NOTABLE_KEYS → TG) |
| ⑤ | 10 筆 frozen 逐筆對帳 | 10 筆 pre-09-09 記錄 (order 已唔喺 allOrders + openOrders=0 + 帳戶重置) → `FLATTENED_LOW_FILL_RR` + `resolved_via=testnet_reset_no_position`；另 2 筆 999001 test artifact 標 `resolved_via=test_artifact` |

附加 (同類安全): HISTORY 只收有 `r_multiple` 嘅 CLOSED (對帳 CLOSED 唔污染統計)；統計行 `.get()` 防禦；`DONE_STATUS` / `resolve` 註釋更新。

## 驗證

- **8 個 test suite 全綠**: test_rr_gates 16/16 (T3 恢復 pass、T6/T6b 沙盒斷言)、
  test_btc_risk_guards 213/213 (新增 F3b/c/d/e: flatten 終態、rebuild 計數 freeze、
  allow_flatten=False 零 MARKET 單、allow_flatten=True 出 1 張)、test_limit_entry 31/31、
  其餘 4 suite 全過。
- **交易所對證**: 10 筆孤兒 order 全部唔喺 `/api/v3/allOrders` (最早 09-09 23:00)，
  openOrders=0 → 冇倉；現 log 0 筆孤兒。
- **止血後實測**: 16:15 之後零賣出 (16:30 起每個 tick 驗證)。
- **警報實測** (probe): 狀態變化出聲 / 無變靜默 / 餘額跌出一次 / 查唔到餘額唔 crash。
- 09:05:37 UTC 假警報「餘額 0.00000」= test_limit_entry 未沙盒化 call reconcile_cycle
  (fake /account 無 BTC balance) — 已加固 (cyc.HEARTBEAT/RECONCILE_STATE redirect)，
  實際餘額 0.32669 完好。

## 遺留 / 後續

- **WIPED 狀態**: `WIPED` 唔喺 DONE_STATUS 但註釋/log 訊息都話「cap 1 已釋放」—
  下次 testnet 重置時 WIPED 記錄會仍然 live + 入 rebuild (同今次同類)。
  建議 one-liner: WIPED 加入 DONE_STATUS + 移出 ORPHAN_STATUS/HOLDING_STATUS (待批)。
- `estimate_held_for` 嘅「帳戶總額」語意 (孤兒 vs 未入 log 持倉) — 未在本次範圍。
- 本分支 merge 前 cron 行 worktree 現時分支 (未 merge 都行到新 code); merge 後還原。

## 備份

- `<reports>/btc_testnet_orders.json.bak-20260927-1627-phantom999001` (止血前)
- `<reports>/btc_testnet_orders.json.bak-20260927-1706-fix5-cleanup` (對帳前)
- 事故記錄: `<reports>/btc_incident_20260927_phantom999001.md`
