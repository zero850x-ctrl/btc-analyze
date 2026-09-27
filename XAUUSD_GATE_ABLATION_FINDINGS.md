# XAUUSD gate 逐層 ablation — 發現

日期: 2026-09-27
腳本: `xauusd_gate_ablation.py` (XAUUSD 自家 backtest, 唯讀 monkey-patch)
配套: `btc_xauusd_gate.py` (同一方法套落 BTC), `xauusd_gate_breakdown.py`

## 問題

`analyze_v3.cron_push_eligible` 有 9 個條件串成 AND。從來冇人逐層拆開睇
邊層真正有貢獻。

## 方法

只換 XAUUSD `backtest.py` 嘅 `push_eligible` 一個函數, 其餘完全不變 →
deterministic, 差異 100% 來自 gate。

⚠️ XAUUSD repo 唯讀 (default agent cron 行緊), 只 import + monkey-patch。

## ⚠️ 方法限制 (自己揪出)

1. **`TRADE_COOLDOWN = 6` bars** → gate 一改, 交易序列唔同, cooldown 對位唔同,
   **唔係乾淨子集比較**。鐵證: G2(+quality) 120 單 > G1 119 單 (加 gate 反而多單)。
   → 所以另做**信號層乾淨版** (`btc_xauusd_gate.py`, 逐個 setup 獨立評估)。

2. **時間窗唔穩定** — 60d 同 180d 結果反轉 (見下)。

3. **730d 跑唔到** — XAUUSD `run_backtest` 用遞增窗口 (`df_bars.iloc[:i+1]`),
   O(n²), 730d H1 每個 gate 定義要 10+ 分鐘 → 放棄。

## 結果

### A. 累積層 (180d H1, 3418 bars)

| Gate | n | 勝率 | 總PnL | 每單 | PF | maxDD |
|---|---|---|---|---|---|---|
| G0 無 gate | 180 | 33.3% | −23.86 | −0.13 | 1.00 | **1623.20** |
| G2 +quality | 176 | 28.4% | −203.94 | −1.16 | 0.95 | 1412.83 |
| G3 +ALIGNED | 109 | 34.9% | −95.81 | −0.88 | 0.98 | 1395.72 |
| G5 +kline | 72 | 36.1% | +318.96 | +4.43 | 1.11 | 540.04 |
| **G7 =push 現行** | **28** | **57.1%** | **+707.37** | **+25.26** | **1.62** | **431.80** |

**最大發現 — 現行 gate 真正貢獻係「壓回撤」:**
- maxDD: 1623.20 → **431.80** (細 **3.8 倍**)
- 60d 同樣: 368.06 → 169.10 (細 2.2 倍)
→ 同代碼 docstring 寫嘅一致: **risk management guard, not an edge signal**。
  gate 唔係喺度揀好單, 係喺度**避開大回撤**。

### B. 單層 (對照 G0) — ⚠️ 60d vs 180d 反轉

| 層 | 60d PnL | 180d PnL | 穩定? |
|---|---|---|---|
| 只 quality | −58.62 | −203.94 | ✅ 皆負 |
| 只 **ALIGNED** | **−163.90** (最差) | **+673.98** (最好之一) | ❌ **反轉** |
| 只 **kline** | **+555.68** | **+645.97** | ✅ **皆正** |
| 只 priority | −23.16 | — | — |
| 只 session | −126.93 | — | — |
| 只 nospike | −141.24 | — | — |

**`kline_confirmed` 係唯一兩個時段都正嘅層。**

### C. 逐層移除 (180d, 由 G7 開始攞走)

| 攞走 | n | 總PnL | 對比 G5 (+317.51) |
|---|---|---|---|
| tp_sl | 72 | +317.51 | 冇變 |
| quality | 72 | +317.51 | 冇變 |
| aligned | 72 | +317.51 | 冇變 |
| priority | 72 | +317.51 | 冇變 |
| **kline** | **107** | **−64.83** | **由賺變蝕** |
| session | 72 | +195.16 | 少賺 |
| nospike | 72 | +441.31 | 多賺 |

→ `tp_sl / quality / aligned / priority` **零過濾作用** (移除後單數一模一樣)。
   `kline` 係唯一有實質影響嘅層。

### D. 信號層乾淨版 (GC=F 730d 1h, 442 setup, 無 cooldown confound)

**severity 分層 — gate 真正做緊咩:**

| Severity | n | meanR | t |
|---|---|---|---|
| ALIGNED | 247 | −0.064 | −0.85 |
| MILD | 184 | −0.073 | −0.91 |
| **SEVERE** | **11** | **−0.887** | **−6.84** |

→ **ALIGNED 同 MILD 統計上分唔開 (都係 ~−0.07)。**
   gate 嘅價值 = **篩走 SEVERE**, 唔係「ALIGNED 好過 MILD」= 純風險迴避。

   ⚠️ BTC 樣本**冇 SEVERE** (只有 ALIGNED/MILD) → 個 gate 喺 BTC 冇嘢好篩,
   只係白刪 MILD → **淨傷害**。呢個解釋咗兩市場為何一個有用一個有害。

**跨市場方向 (相反):**
- XAU: BUY −0.003 (平) / SELL −0.156 (t −2.17)
- BTC: BUY −0.059 / SELL +0.130 (t 1.81)
→ **唔可以互相搬。**

## 結論

1. **gate 嘅真正貢獻係壓回撤 (2-4 倍), 唔係提升回報** — 同 docstring 一致
2. **`kline_confirmed` 係唯一穩定有作用嘅層** (兩個時段都正, 移除後由賺變蝕)
3. **`aligned` 極不穩定** (60d 最差 ↔ 180d 最好) → 唔可信
4. **`tp_sl / quality / priority` 零過濾作用** (移除零變化)
5. **`nospike` 移除反而多賺** → 可能過緊
6. **冇任何一層統計顯著** (全部 |t| < 2, 最大 1.08)
7. **gate 係風控紀律, 唔係 alpha 來源** — 兩個市場都一樣

## 待辦 (若要繼續)

- 730d 需要先改 XAUUSD backtest 用固定窗口 (O(n)), 否則跑唔完
- `nospike` 疑似過緊, 值得單獨檢視
- `quality / priority` 若真係零 bind, 可以簡化 (但唔係緊急)
