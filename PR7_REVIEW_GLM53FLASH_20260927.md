# PR #7 Review — GLM 5.3 Flash 快速過一輪 + 遺留提案

**Date:** 2026-09-27
**PR:** #7 `fix(reconcile): phantom 連環市價平倉 — 5 項修復`
**Branch:** `fix/btc-reconcile-phantom-guards` → base `fix/btc-exit-symmetry`
**Head:** `30c075f`

> ⚠️ 本文件係**唯讀審查 + 提案**，未改任何代碼。Trade 相關操作已交尼古／靜江。

---

## 0. 審查方法（誠實聲明）

- **GLM 5.3 Flash**：經 opencode-go API 送 852 行 diff（分 2 段）。API 回應 **只有 `reasoning_content`，`content` 全程空白**
  ——3000 tokens 用盡都係 reasoning，`finish_reason=length`。加大到 16000 tokens 後 reasoning 去到 31K 字元仍然未完，
  **冇正式 review 正文**。以下 GLM 論點係我由 reasoning 內抽取佢嘅結論句，並非模型正式輸出。
- **我自己**：所有下面標「我實測」嘅項目，都係喺本機跑真 code / 真 testnet（唯讀或 sandbox log）驗證過。

---

## 1. 我親自實測（PR 本身有效）

| 項目 | 結果 |
|---|---|
| 8 個 test suite | 全綠：221 / 33 / 16 / 25 / 49 / 19 / 45 / 6 |
| 對帳 rebuild 會唔會落 MARKET 單 | **唔會**（真 testnet probe：openOrders 0、餘額 0.32669 不變、`flatten_skipped_reason` 有記錄）|
| 幽靈記錄（entry 唔喺 myTrades） | **被拒建 legs**，行 rebuild-fail → freeze 路徑，全程零落單 |
| `allow_flatten=True`（新成交路徑） | 仍然正常：OCO 失敗 → 1 張 MARKET → `FLATTENED_LOW_FILL_RR` → 釋放 cap |
| 生產 log 狀態 | 74 筆、**0 筆 live/孤兒**、`reconcile_state.json` orphan_summary 空、餘額 0.32669 |

---

## 2. 成立嘅問題（建議跟進）

### P1 — freeze flag 只存在一個 tick（我實測證實）· MED
`resolve_orphan_states` L295 每 tick 開頭 `rec.pop("needs_manual_reconcile")`，
之後第 3 次失敗（L370）才重新設 `True`。實測：

```
tick1 cnt=1 manual=False  summ={needs_legs:1, rebuild_failed:1}
tick2 cnt=2 manual=False  summ={needs_legs:1, rebuild_failed:1}
tick3 cnt=3 manual=True   summ={needs_legs:1, rebuild_failed:1}   ← freeze 個 tick
tick4 cnt=3 manual=None   summ={needs_legs:1}                     ← flag 被 pop 走
```

`rebuild_frozen_note` 仍在（可審計），但 **`needs_manual_reconcile` 訊號只亮一個 tick**。
→ 建議：freeze 後唔好再 pop（或者改用 `rebuild_frozen` 獨立欄位做終態判斷）。

### P2 — freeze 冇專屬警報（我實測證實）· MED
`reconcile_alerts` 只比對 summary。freeze 後 tick4 summary 由
`{needs_legs:1, rebuild_failed:1}` → `{needs_legs:1}` 會出一次 ⚠️，
但之後每 6h 重提只寫「孤兒對帳仍未處理: needs_legs=1」—— **冇講「已 freeze、需人手」**。
訊息強度弱過實際嚴重性。→ 建議 freeze 加一個專屬 summary key（`frozen_rebuild`）。

### P3 — `allow_flatten=False` 造成真倉裸倉窗口 · HIGH（風險）／設計取捨
已建 leg 會被 cancel，record 留 `OCO_FAILED`；要 3 tick（45 分鐘）後先 freeze 交人手。
期間**真倉完全冇 SL**。設計取捨係刻意（唔賣唔屬於自己嘅幣），但冇中間路。
→ 建議：flatten 前先核實 `free balance ≥ rec qty`，核實得過就准 flatten（同時保住「唔賣人哋嘅幣」）。

### P4 — entry 存在性檢查只用最近 1000 筆 myTrades · MED
`_rebuild` 用 `myTrades?limit=1000`（實測：356 筆、最早 2026-09-09）。
entry 跌出窗口 → 拒 rebuild → freeze → 裸倉。舊記錄（pre-09-09）修復後尤其高風險。
→ 建議：加 `fromId` 分頁，或改用 `/api/v3/order` order status 查。

### P5 — `flatten_ok=True` 未核實成交 · MED
現時語意 = 「MARKET 單 accepted」。若 accepted 但未成交，record 已係終態，
而 **balance alert 門檻 0.05 BTC，單筆 0.00258 唔會觸發** → 冇任何 catcher。
（檔內註釋已有記載，係已知殘留風險。）→ 建議 flatten 後查 order status / myTrades 核實。

### P6 — `integration_check_limit.py` 仍然直寫生產 log · MED
`atexit` 唔涵蓋 SIGKILL / SIGTERM（cron timeout kill）→ 極端情況仍會留測試記錄，
即係今次事故嘅同一條路。→ 建議：改沙盒（temp copy + redirect `btp.LOG_PATH`），
測試完再對交易所 openOrders（而唔係靠還原生產 log）。

---

## 3. GLM 提出但**我證偽**嘅（唔成立）

| GLM 論點 | 實測結果 |
|---|---|
| `HOLDING_STATUS` 含 `WIPED` → double-count 持倉 | 生產過濾係 `is_live_rec(r) and status in HOLDING_STATUS`（cycle:410）。`WIPED` 已 DONE → `is_live_rec` False → **唔會入扣減**。 |
| `build_exit_legs` 回傳 tuple → `resolve` 會 AttributeError | `resolve_orphan_states` L347-352 已有 `isinstance(new_rec, tuple)` 拆包，test F3f 亦鎖死。 |
| freeze 後 summary 唔變 → 冇警報 | 實測 summary `{needs_legs, rebuild_failed}` → `{needs_legs}` **會**觸發一次變化警報。 |

---

## 4. 遺留提案（F2）

### R1 — `needs_manual_reconcile` 係死旗（新發現）· MED
三個問題疊埋：
1. **冇任何消費者** —— `grep -rn 'needs_manual_reconcile' btc_auto_trade_cycle.py` = 0 命中，
   冇 log、冇推送、冇 alert key。
2. **每 tick 被 pop**（同 P1）。
3. **生產 log 殘留** —— 而家 10 筆 `FLATTENED_LOW_FILL_RR` 仍帶此 flag，
   佢哋已係終態（`is_live_rec` False、`r_multiple` 全部 `None`），但 flag 冇清走。

→ 提案（one-liner 級）：
- `resolve_orphan_states` 尾段：record 一旦轉入 `DONE_STATUS` 就清 `needs_manual_reconcile` / `unknown_holding_reason`。
- freeze 改用獨立 `rebuild_frozen` 欄位（唔靠會被 pop 嘅 flag）。
- `reconcile_alerts` 加 `frozen_rebuild` summary key（解 P2）。

### R2 — WIPED 註釋同 tuple 唔一致（文檔級）· LOW
`HOLDING_STATUS` 仍然包含 `WIPED`，靠 call site 嘅 `is_live_rec` 過濾先唔會出錯。
`ORPHAN_STATUS` 就移走咗。兩個 tuple 對同一個狀態採取唔同策略，靠註釋解釋。
→ 提案：`WIPED` 一併移出 `HOLDING_STATUS`（反正 DONE 唔會 live），少一個隱式依賴。

---

## 5. 結論

**結論: 可合併但需跟進。**

PR 本身嘅 5 項修復我實測有效（尤其「對帳 rebuild 禁市價平倉」——
真 testnet probe 確認零落單、零賣出）。上面 P1–P6 冇一項係會即時造成損失嘅 blocker，
但 P1/P2/P3 疊埋會令「freeze 交人手」呢個安全網實際上**唔夠醒目**，
而 P3 係真倉裸倉窗口 —— 建議合併後即刻開 follow-up PR 處理 P1+P2（small diff），P3 單獨評估。

---

## 附：本次審查嘅技術附註

- opencode-go `/chat/completions` 需要 **`x-opencode-session` header**，
  否則回 `MissingSessionID`；冇正確 UA 會被 Cloudflare 擋（403 error 1010）。
- `glm-5.3-flash` 經呢個 endpoint 對長 diff 會 **reasoning-only** 輸出（`content` 空）。
  短 prompt 一樣有此傾向（20 tokens 全 reasoning）。用於長 code review 唔可靠。
