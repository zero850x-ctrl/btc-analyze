# 遺留提案：`needs_manual_reconcile` 死旗 + freeze 訊號強化

**Date:** 2026-09-27
**來源:** PR #7 review 期間發現（見 `PR7_REVIEW_GLM53FLASH_20260927.md`）
**狀態:** 提案，未實作
**範圍:** 純 local log / alert 層，**唔涉及落單邏輯**

---

## 問題

PR #7 加咗「rebuild 連續失敗 3 次 → freeze 交人手」。意圖正確，
但 freeze 訊號實際上冇傳達到人手。

### 三個疊埋嘅缺陷

**(1) 冇消費者**
```
$ grep -rn 'needs_manual_reconcile' btc_auto_trade_cycle.py
（0 命中）
```
`reconcile_alerts()` 只睇 `orphan_summary`（per-tick 計數），完全冇讀 record 上嘅
`needs_manual_reconcile`。冇 log 行、冇 TG 推送、冇 NOTABLE_KEYS。

**(2) 每 tick 被 pop**

`resolve_orphan_states` L295（`h is not None` 之後）：
```python
# FINDING 5b: 恢復正常時清走上一輪嘅 transient flag
rec.pop("needs_manual_reconcile", None)
rec.pop("unknown_holding_reason", None)
```
呢行每 tick 都行。freeze（L370）今個 tick 設 `True`，下個 tick 一開頭就被清走。

實測（`held_qty=0.001`、rebuild 永遠 raise）：
```
tick1 cnt=1 manual=False  summ={needs_legs:1, rebuild_failed:1}
tick2 cnt=2 manual=False  summ={needs_legs:1, rebuild_failed:1}
tick3 cnt=3 manual=True   summ={needs_legs:1, rebuild_failed:1}
tick4 cnt=3 manual=None   summ={needs_legs:1}
tick5 cnt=3 manual=None   summ={needs_legs:1}
```
`rebuild_frozen_note` 留低（可審計），但 `needs_manual_reconcile` 只亮一個 tick。

**(3) 警報訊息唔夠醒目**

tick4 summary 由 `{needs_legs:1, rebuild_failed:1}` → `{needs_legs:1}`，
`reconcile_alerts` 出一次：
```
⚠️ 孤兒對帳狀態變化: needs_legs=1 (之前: needs_legs=1, rebuild_failed=1)
```
之後每 6h 重提：
```
⏰ 孤兒對帳仍未處理 (每 6h 重提): needs_legs=1
```
兩條都冇講「已 freeze、停止自動重建、需人手」。

---

## 提案（三處小改）

### A. freeze 用獨立終態欄位（唔靠會被 pop 嘅 flag）

```python
if rec["rebuild_fail_count"] >= REBUILD_FAIL_FREEZE:
    rec["needs_manual_reconcile"] = True
    rec["rebuild_frozen"] = True          # ← 新：終態欄位，唔會被 pop
    rec["rebuild_frozen_note"] = (...)
```
`resolve_orphan_states` 頂部 freeze 判斷改讀 `rec.get("rebuild_frozen")`（而唔係 `rebuild_fail_count`）。
清除方法：人手刪 `rebuild_frozen` / `rebuild_fail_count`（同現有註釋一致）。

### B. `needs_manual_reconcile` 加消費者

`reconcile_alerts(summary, acct_btc, frozen=None)` 加第三參數，
由 `reconcile_cycle` 傳入「帶 `rebuild_frozen` 嘅記錄數」：

```python
if frozen:
    msgs.append(f"🚨 對帳重建已凍結 (需人手): {frozen} 筆 — "
                f"倉位可能冇止損, 檢查 rebuild_frozen_note")
```
放喺 `NOTABLE_KEYS`（現時 ⚠️ 已會推送，🚨 需確認有覆蓋）。

### C. 終態時清走殘留 flag

`resolve` 所有轉入 `DONE_STATUS` 嘅分支（L296 附近 CLOSED、L309 FLATTENED_LOW_FILL_RR、
及 existing 對帳 CLOSED 路徑）加：
```python
rec.pop("needs_manual_reconcile", None)
rec.pop("unknown_holding_reason", None)
rec.pop("rebuild_frozen", None)
```
理由：生產 log 現時有 **10 筆已終態 `FLATTENED_LOW_FILL_RR` 仍帶 `needs_manual_reconcile`**，
`r_multiple` 全部 `None`。佢哋唔會再 live，flag 純屬噪音，會干擾未來「數有幾多筆需人手」嘅查詢。

---

## 影響評估

| 項目 | 影響 |
|---|---|
| 落單行為 | **零** —— 全部係 log 欄位 + alert 訊息 |
| 現有 test | 需加 ~3 個斷言（freeze 後 flag 持久、alert 有 frozen 訊息、終態清 flag） |
| 生產 log | 可選擇性一次性清走 10 筆殘留 flag（純唯讀判斷，或寫一次 log）|

---

## 驗證方法（實作後）

```bash
cd ~/repos/btc-analyze
python3 test_btc_risk_guards.py    # 應該仍 221+ 全綠
python3 - <<'PY'   # freeze 後 flag 仍在
import sys, os, json, tempfile; sys.path.insert(0,'.'); os.chdir(os.path.expanduser('~/repos/btc-analyze'))
import binance_testnet_paper as btp, btc_auto_trade_cycle as cyc
tmp=tempfile.mkdtemp(); btp.LOG_PATH=os.path.join(tmp,"o.json"); cyc.RECONCILE_STATE=os.path.join(tmp,"rs.json")
json.dump({"orders":[{"order_id":1,"pattern":"A","side":"BUY","qty":0.001,"status":"OCO_FAILED","entry_fill":80000.0}],"history":[]}, open(btp.LOG_PATH,"w"))
log=btp.load_log()
def fail(r): raise RuntimeError("boom")
for t in range(1,6):
    _, summ = btp.resolve_orphan_states(log, 0.001, dust_eps=1e-5, rebuild=fail, acct_btc=0.001)
    print(t, "frozen=", log["orders"][0].get("rebuild_frozen"), "manual=", log["orders"][0].get("needs_manual_reconcile"), cyc.reconcile_alerts(summ, None))
PY
```
預期：tick3 起 `frozen=True` 一直保持，alert 每次講「已凍結需人手」。
