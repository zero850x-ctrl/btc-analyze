# 設計大改 — 全 sweep 驗證報告 (research/design-overhaul)

日期: 2026-09-28
分支: `research/design-overhaul` (worktree `~/repos/btc-analyze-redesign`, base `main` @ `17f2adf`)
數據: Binance BTCUSDT M30, 540 日 (25,920 根, 2025-04-09 → 2026-10-01)
成本: 來回 0.2% (Binance spot 0.1%/邊)，除非另註明

---

## 0. 為什麼要大改

三重證據指向同一結論 —— **唔係參數問題，係進場冇 edge**：

| 證據 | 數字 |
|---|---|
| live 25 單 | meanR **−0.317**, t −2.12 (顯著負) |
| 540 日引擎 setup (671 單) | 實況成本 **−0.459R**, t −13.5；零成本 −0.212R |
| RR gate 換 metric | 新舊 gate per-trade 幾乎一樣 (−0.357 vs −0.349) |

成本 ≈ 0.35R/單（risk = 0.8×ATR ≈ 價格 0.4%，來回 0.2% ≈ 0.5R）→
毛利 ≈ 0 加上固定成本 = 必蝕。

呢次 sweep 問：**(1) 夾硬改參數會唔會轉正？(2) 換全新進場邏輯有冇 edge？**

---

## 1. SL 闊度 × 出場結構 (40 組合) — 全部負

`build_setup_pool.py` (2394 窗口 → 1035 setup, 2128 limit 成交) + `sweep_design.py`

風險重定義為 `entry ∓ k×ATR`，出場 8 種結構：

| SL | 最佳結構 | meanR | t |
|---|---|---|---|
| 0.8×ATR | staged3(2:4:trail) | **−1.362** | −31.3 |
| 1.5×ATR | trail_only | −0.661 | −12.0 |
| 2.0×ATR | trail_only | −0.504 | −10.7 |
| 3.0×ATR | trail_only | −0.308 | −7.6 |
| 4.0×ATR | staged3(2:4:trail) | −0.247 | −8.6 |

- **40/40 組合 meanR < 0**，範圍 **−1.402 ~ −0.247**
- Bonferroni 門檻 |t| > 5.19 → **40 個全部顯著（全部顯著負）**
- 趨勢：SL 越闊越好（攤薄固定成本），但**永遠唔會轉正**

再拉闊驗證（production stop 之上再乘）：

| SL | meanR | t |
|---|---|---|
| 0.8×ATR | −1.378 | −37.2 |
| 1.5×ATR | −0.894 | −29.1 |
| 3.0×ATR | −0.420 | −16.1 |
| 4.0×ATR | −0.328 | −13.3 |
| 6.0×ATR | −0.200 | −8.9 |
| 8.0×ATR（6.4×ATR 實際） | **−0.152** | **−7.5** |

→ 收斂去 0 但**唔會穿過 0**，而且 t 一直顯著負。
**呢個係「毛利 ≤ 0 + 固定成本」嘅典型形狀**，唔係參數未調好。

### 出場結構結論
8 種結構（3 段 / 2 段 / 單 TP 1R-3R / 純 trail）差異細（同一 k 之下散佈 ≤ 0.06R），
冇任何一種令負變正。**出場唔係問題所在。**

### Cross-validation
| 方法 | 0.2% 成本 | 0% 成本 |
|---|---|---|
| `validate_rr_blended_gate` (engine TP) | −0.459R | −0.212R |
| sweep harness (production stop) | −0.596R | −0.179R |

同號同量級 → harness 可信（差異來自 TP 定義：sweep 用 R 倍數，engine 用被 clamp 嘅 TP1）。

---

## 2. 完全唔同嘅進場邏輯 (20 組合) — 18 個負

`strategy_sweep.py`：單槽 event-driven、信號 bar 下一根 open 進場、SL 3×ATR。

| 策略 | n | meanR | t | TRAIN | TEST |
|---|---|---|---|---|---|
| **vol_break(1.5) [trail]** | 143 | **+0.310** | +1.12 | −0.049 | +0.707 |
| **vol_break(1.5) [tp3r]** | 149 | **+0.077** | +0.55 | +0.032 | +0.122 |
| donchian(100) [trail] | 258 | −0.086 | −0.68 | −0.137 | −0.032 |
| momentum(50) [trail] | 521 | −0.086 | −0.81 | −0.132 | −0.037 |
| ma_cross(10,50) [tp3r] | 321 | −0.111 | −1.27 | −0.213 | +0.004 |
| rsi_mr(30,70) [trail] | 479 | −0.130 | −1.18 | −0.176 | −0.085 |
| ma_cross(20,100) [tp3r] | 254 | −0.256 | −2.78 | −0.324 | −0.178 |
| donchian(20) [tp3r] | 477 | −0.294 | **−4.32** | −0.296 | −0.293 |
| rsi_mr(20,80) [tp3r] | 313 | −0.319 | **−4.13** | −0.331 | −0.306 |

- **18/20 負**；meanR 範圍 −0.319 ~ +0.310
- 唯二正 = `vol_break(1.5)` 兩個出場版本
- **冇任何正嘅組合統計顯著**（最高 t = 1.12，門檻 3.46）
- 唯一顯著嘅係**負嗰兩個**（Bonferroni 後）
- TRAIN 同 TEST 都 > 0：**1/20**

---

## 3. vol_break 跟進 (40 組合) — 確認係噪音

`check_vol_break.py`：N ∈ {10,20,40,80} × k ∈ {0.5,1.0,1.5,2.0,2.5} × 2 出場 + bootstrap 10,000 次

| 指標 | 結果 |
|---|---|
| meanR > 0 | **21/40** ← 隨機水平（擲毫） |
| TRAIN 同 TEST 都 > 0 | **15/40** ← 隨機水平 |
| **bootstrap 95% CI 完全喺 0 以上** | **0/40** |
| Bonferroni 後顯著 | **0** |
| meanR 範圍 | −0.187 ~ +0.621 |

**0/40 個組合嘅 95% CI 排除 0** → 冇任何證據支持 vol_break 有 edge。
正負分佈接近隨機（21/40）→ 典型噪音。

---

## 4. 總結

1. ❌ **調參數救唔到** — SL 由 0.8 → 8×ATR、8 種出場結構、40 個組合全部負。
   加闊 SL 只係攤薄固定成本（−1.378 → −0.152），毛利依然 ≤ 0。
2. ❌ **換策略救唔到** — 趨勢（donchian/momentum/ma_cross）、均值回歸（rsi_mr）、
   波動突破（vol_break）20 組合，18 個負，2 個正唔顯著。
3. ❌ **唯一候選係噪音** — vol_break 40 個參數組合，0/40 個 CI 排除 0。
4. ✅ **唯一統計顯著嘅係負** — donchian(20) t −4.32、rsi_mr(20,80) t −4.13。

**結論：BTC M30 上，形態 / 趨勢 / 均值回歸 / 突破四類進場，實況成本之下都冇 edge。**
同之前「16 策略 Bonferroni 0/16」嘅結果一致。

### 適用範圍（唔可以過度推廣）
- 週期: **M30 bar**，最長持倉 48h，單槽（cap 1）
- 成本: 來回 0.2%（Binance spot 標準；用 BNB 折或 VIP 會低啲，但差距唔足以扭轉 −0.2R 以上）
- 期間: 2025-04 → 2026-10（B&H 全期 +10.4%，TRAIN +20.4% / TEST −8.3%）
- 未測: 其他週期（M5/M15/H4/D）、其他市場（XAUUSD 已另測）、組合策略、日內時段過濾

---

## 5. 檔案

| 檔案 | 用途 |
|---|---|
| `build_setup_pool.py` | engine setup pool 快取 (2394 窗口 → 1035 setup) |
| `sweep_design.py` | SL × 出場結構 sweep (40 組合) + Bonferroni |
| `strategy_sweep.py` | 獨立策略進場 (10 策略 × 2 出場, 單槽) |
| `check_vol_break.py` | vol_break 參數網格 + bootstrap CI |
| `test_sweep_sim.py` | simulator 單元測試 **12/12 PASS** (look-ahead / SL 優先 / 成本 / trail) |
| `setup_pool.json` | pool 快取 |
| `sweep_design_cost0.002.csv`、`strategy_sweep_cost0.002.csv`、`vol_break_grid_cost0.002.csv` | 原始結果 |

### 過程中揪到嘅 sim bug（已修）
TP 水平原本由**成本調整後**嘅 entry 計（`e + tp_r*risk`），但 risk 用原始價計 →
TP 被推遠、成本被低估。已改為：信號價（entry/stop/TP）全部用原始價定義，
成本只喺成交價體現（entry 買貴 c、出場賣平 c）。`test_sweep_sim.py` F2 專門驗證呢點。
