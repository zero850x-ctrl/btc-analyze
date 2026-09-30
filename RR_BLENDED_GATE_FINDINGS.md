# RR Gate metric 修正 — 驗證報告 (feat/rr-blended-gate)

日期: 2026-09-28
分支: `feat/rr-blended-gate` (worktree `~/repos/btc-analyze-rrfix`, base = `main` @ `17f2adf`)

---

## 1. 問題 (根因已定位到行數)

| 位置 | 事實 |
|---|---|
| `analyze_v3._staged_targets()` L3076-3084 | `tp1 = min(fib_tp, entry + risk)` (BUY) / `max(…entry − risk)` (SELL) → **TP1 被夾到最多 1:1** |
| `btc_engine.py` L45 / L288 | `MIN_RR = 1.2`；`if rr < MIN_RR: _gate_skip` |
| `binance_testnet_paper.py` L43 | `MIN_RR_EXEC = 1.2` (落單前第二道閘) |

引擎按設計把 TP1 擺喺 ~1:1，但 gate 要 1.2。
**唔係數學上完全唔相交**（早前講「永遠唔相交」係過頭）：2 年 274 個 Flag setup 有 3.7% ≥ 1.2，
所以係「幾乎永遠擋」而唔係「絕對擋」——2026-09-13~09-30 連續 17 日 0 單就係結果。

高 RR 嘅來源亦查清: 唔係「好 setup」，係 **SL floor 把 stop 移到貼近 limit**
令 risk 縮到極細 (例: raw risk $3 → floored $228，但 TP1 距離 1,138 → rr1 = 4.99)。

## 2. 修法 (用戶揀 A)

實際出場 = 1/3 @ TP1 + 1/3 @ TP2 + 1/3 尾倉。
所以 gate 應該用 **blended R** 而唔係 TP1 單段：

```
blended = (1/3)·rr1 + (1/3)·rr2 + (1/3)·tail        tail = TAIL_R_ASSUMED = 0.0
冇 TP2 → 第二份併入尾倉 (exec layer 行為) → blended = (rr1 + 2·tail) / 3
```

- 改 `btc_engine.py` (gate + 記 `rr_tp1`/`rr_tp2`/`rr_blended`)
- 改 `binance_testnet_paper.py` (`_blended_r` / `_rr_at` / `_compute_rr` + 3 處現價/fill 覆核)
- **門檻維持 1.2**，MIN_RR_EXEC 亦維持 1.2
- 新增 `test_rr_blended.py` 39 個斷言；`test_rr_gates.py` T3f 更新

## 3. 單元測試

| 測試 | 結果 |
|---|---|
| `test_rr_blended.py` (新) | **39/39 PASS** |
| `test_rr_gates.py` | 17/17 PASS |
| `test_gate_log.py` | 25/25 PASS |
| `test_btc_pattern_gate.py` | 45/45 PASS |
| `test_btc_risk_guards.py` | 231/231 PASS |
| `test_limit_entry.py` | 41/41 PASS |
| `test_gap_fill.py` / `test_spike_of.py` / `pen_test_btc.py` | PASS |
| py_compile | OK |

## 4. 回測驗證 — `validate_rr_blended_gate.py`

- 數據: Binance BTCUSDT M30, **540 日** (25,920 根), 每 32 根 (16h) 一個窗口
- 776 個窗口 → **682 個 Flag + MA50 過關 setup**，limit 成交 671 (11 個冇觸及 limit)
- 兩個 gate 用**完全相同** setup pool；出場用 production `_simulate_staged_exit`

### 4.1 結果（按成本情境）

| 成本 (來回) | 無 gate | OLD: rr1 ≥ 1.2 | NEW: blended ≥ 1.2 |
|---|---|---|---|
| 0% (gross) | n=671, **−0.212R**, t −4.49 | n=32, **+0.162R**, t +0.56 | n=317, **−0.010R**, t −0.13 |
| 0.1% | −0.288R, t −6.78 | +0.064R, t +0.24 | −0.115R, t −1.63 |
| **0.2% (Binance spot 實況)** | n=671, **−0.459R**, t −13.53 | n=32, **−0.349R**, t −1.39 | n=317, **−0.357R**, t −6.36 |

### 4.2 結論

1. ✅ **修法本身正確** — gate metric 終於同實際出場結構一致，唔再係量錯嘢。
2. ⚠️ **但冇 edge**。實況成本下 NEW gate = **−0.357R/單 (t = −6.36, n=317)**，
   同 OLD gate **−0.349R (t = −1.39, n=32)** 幾乎一樣 → **per-trade 期望值一樣係負**。
3. 🔴 **成本就係全部**。0% cost 時 NEW gate 係 −0.010R (≈ 打和)；加 0.2% 來回 → −0.357R。
   即係 **成本 ≈ 0.35R/單**（risk ≈ 0.8×ATR ≈ 價格 0.4% → 0.2% 來回 ≈ 0.5R）。
   毛利 ≈ 0，成本 0.35R → 必蝕。
4. ⚠️ **門檻敏感度非單調**（0.9 → −0.398 / 1.2 → −0.357 / 1.3 → −0.356 / 1.5 → −0.380）
   = 噪音特徵，冇任何門檻有 edge。
5. ⚠️ **尾倉假設越樂觀越差**（tail 0.0 → −0.357 / 0.5 → −0.369 / 1.0 → −0.409）
   → 樂觀假設只係放更多差單入嚟，唔會變好。

### 4.3 順帶揪到：舊 gate 係「揀到買入」而唔係「揀到好單」

OLD gate 嘅 32 單 **全部係 BUY，SELL 零單**。
原因 = SL floor 令 stop 貼近 limit 嘅 artifact（rr1 範圍 1.22–5.87，但設計上 TP1 係 1:1）。
所以嗰 32 單嘅「正回報」唔係揀單能力，係量度偏差 + 樣本細 (t=0.56)。

## 5. 方法論注意

- 回測**冇模擬同時持倉上限 (cap 1)**。per-trade 期望值唔受影響，
  但總計 (−113R) 會被高估；production cap 1 之下實際單數會少好多。
- 成本模型假設來回 0.2% 並全部以 entry 偏移套用（sim 本身唔計手續費）。
- 未模擬平倉滑價尾部（實證見過 1/28 次 −$1,050 wick 滑價）。

## 6. 建議

**唔建議 merge 入 `main`**（cron 行 main）。
實況成本下兩個 gate per-trade 期望值一樣（−0.35R），但 NEW gate 單數多 10 倍
→ 總虧損由 −11R 放大到 −113R。即係「修好咗但蝕得更快」。

若果仍然要 deploy（為咗收 live 數據），前提要先解決成本：
成本 ≈ 0.35R 係因為 risk 只有 ~0.8×ATR。
把 risk 拉闊（例如 SL 用 3×ATR）會令成本 R 值跌約 4 倍，但勝率/形態要重新驗證。

---

**檔案**
- `btc_engine.py`、`binance_testnet_paper.py` — 修改本體
- `test_rr_blended.py` (新, 39 斷言)、`test_rr_gates.py` (T3f 更新)
- `validate_rr_blended_gate.py` (新) + `btc_rr_gate_validation_cost{0.0,0.002}.json`
- `btc_rr_gate_diag.py` — 2 年 RR 分佈診斷
