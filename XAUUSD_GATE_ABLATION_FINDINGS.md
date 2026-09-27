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

## ⚠️ 方法論修正 (2026-09-27 追加, 查 nospike 時發現)

**上面「逐層移除」嘅數字唔可以歸因任何一層。** 我當初已警告 cooldown
confound, 但報告時仍然寫咗「移除 nospike 反而多賺 +441.31」— 呢個係錯嘅。

證據 (180d H1, 三組都係 72 單但 PnL 唔同):
| gate 組合 | 單數 | 總PnL |
|---|---|---|
| G5 = tp_sl+quality+aligned+priority+kline | 72 | +317.51 |
| 移除 nospike = G5 **+session** | 72 | +441.31 |
| 移除 session = G5 **+nospike** | 72 | +195.16 |

→ G5 vs 「移除 nospike」只差 **session** 層 → 嗰 +$124 係 **session** 嘅效果。
→ 加 nospike 令 +317.51 → +195.16 (−$122)。
→ 單數一樣但 PnL 唔同 = 交易序列唔同 = **cooldown 序列效應**, 唔係層嘅貢獻。

**結論: cooldown-based backtest ablation 唔可以用嚟判斷 gate 層貢獻。**
正確方法 = ① 逐 setup 獨立評估 ② 直接數 fire 次數。

---

## nospike 深入查 (2026-09-27, 用戶要求)

機制 (`analyze_v3._post_spike_state`):
```
move = close[-2] - close[-2-W]      # W = SPIKE_WINDOW_BARS = 4 (已收市 bar)
|move| > MULT × ATR  → spike, 方向 = move 符號
setup 方向同 spike 方向一致 → post_spike_blocked = True (唔推送)
MULT = 3.0 (09-08 由 2.0 調高 — 2.0 喺普通趨勢延續都 fire; 動機事件 ≈9.5 ATR)
```

### 新工具

- `btc_xauusd_gate.spike_of()` — 複製官方公式 (BTC repo 嘅 av3 冇呢個函數,
  post-spike gate 係 XAUUSD repo 09-04 加、未同步)。**`test_spike_of.py` 19 PASS / 0 FAIL** —
  同 XAUUSD 原函數逐字對比 (300 組隨機 + 邊界 + 4 個參數組合), 100% 一致。
- `count_nospike.py` — monkey-patch `bt._inject_push_metadata` 直接數 fire 次數。
- `xauusd_nospike_deep.py` — 離線掃 MULT × WINDOW (backtest 只需跑一次)。

### 發現 1: nospike 確實 fire, 但對最終交易零影響

| 數據 | setups | blocked | fire 率 | 產出交易 |
|---|---|---|---|---|
| 60d M30 | 3,543 | 46 | 1.30% | 26 |
| 180d H1 | 7,502 | 100 | 1.33% | 28 |

但 60d M30 ablation「移除 L_nospike」→ **48 單 −45.53, 同 G6 一模一樣**
→ fire 咗但**零影響** (被其他層或 priority 排序蓋過) = **冗餘層**。

### 發現 2: 方向對, 但感知度極低 (GC=F 730d 1h 乾淨版, 442 setup)

- 被擋 **5 單 (1.1%)**, meanR **−1.004**, 勝率 **0%** (全滅)
- 通過 437 單, meanR −0.078
- TRAIN −1.005 / TEST −1.000 → ✅ 兩段同號
- → **方向正確** (擋走嘅係蝕單), 但 n=5 → 唔顯著
- 放行呢 5 單 → sumR −39.07 → −44.09 (**更差**)

### 發現 3: ❌ 唔應該放寬 (敏感度, GC=F 1h)

| MULT | W | 被擋 n | 被擋 meanR | 放行 meanR | vs 唔加 (−0.088) |
|---|---|---|---|---|---|
| 1.5 | 2 | 18 | −0.61 | −0.07 | +0.022 |
| 2.0 | 2 | 11 | −0.64 | −0.07 | +0.014 |
| **3.0** | **4** | **5** | **−1.00** | **−0.08** | **+0.010 ←官方** |
| 3.0 | 6 | 20 | **+0.11** | −0.10 | −0.010 |
| 1.5 | 6 | 104 | **+0.04** | −0.13 | **−0.038 ❌** |
| 2.0 | 6 | 74 | **+0.01** | −0.11 | −0.021 ❌ |

→ **放寬 (細 MULT / 長 window) 會令被擋嘅 meanR 變正** = 開始誤殺賺錢單。
  官方 (3.0, 4) 剛好喺「方向對 + 誤殺最少」嘅位置。
- Bonferroni: 測 24 組合 → 門檻 |t| > 2.6 → 最佳 |t| = 2.04 **唔過**。

### 發現 4: 🔄 BTC 上完全相反 (570 setup, 1h)

- 被擋 **15 單 (2.6%)**, meanR **+0.589**, 勝率 **66.7%**, t **+2.15**
- 放行 555 單 meanR +0.058
- TRAIN +0.869 / TEST +0.170 → 兩段**同號皆正** → 一致地誤殺
- spike 反方向放行 (搏反彈) 3 單 meanR **+0.850** (100% 勝)
- 敏感度: **所有** MULT×WINDOW 組合被擋 meanR 都係正 (除 n=4 兩格)
- → **BTC 上呢層方向係反嘅**: 急升/急跌後追同方向**賺錢** (動量延續),
  唔係黃金嗰種 V 型反彈陷阱。

### 結論: nospike 係咪過緊?

**唔係。** 佢:
1. 感知度低 (1.3%), 對最終交易**零影響** (60d M30 移除零變化) = 冗餘層
2. 方向**正確** (擋走嘅係蝕單), 放寬反而會誤殺
3. 官方參數 (3.0, 4) 已喺合理位置
4. 邊際貢獻 ≈ 0 → 保留成本近零, 但唔應該期望佢貢獻 alpha

⚠️ 之前「移除 nospike 多賺 +$124」係 cooldown artifact (見上方法論修正)。

### ⚠️ 限制

- GC=F 1h 乾淨版同官方 M30 設計有 timeframe mismatch (yfinance 30m 只 60 日
  → WARMUP=1000 令 M30 樣本不足) → M30 乾淨版做唔到
- 全部 |t| 遠低於 Bonferroni 門檻 → 唔顯著
- nospike fire 率用 GC=F(黃金期貨) 代理 XAUUSD spot, 兩者可能唔同

## 待辦 (若要繼續)

- 730d 需要先改 XAUUSD backtest 用固定窗口 (O(n)), 否則跑唔完
- `nospike` 已查完 (見上)
- `quality / priority` 若真係零 bind, 可以簡化 (但唔係緊急)

