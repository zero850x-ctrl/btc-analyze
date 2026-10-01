# BTC 策略研究 — 總總結 (2026-08-31 → 2026-10-01)

分支: `research/design-overhaul` @ `cb075b9`（base `main` @ `17f2adf`）
數據: Binance BTCUSDT + 10 個 altcoin，M30 4 年 / H4 6 年 / D1 9 年

---

## 0. 一句話結論

**BTC 唔係冇策略，係冇市場中性 alpha。**
日線趨勢跟隨係真嘢（11/11 資產正、CI 排除 0、跨期間穩健），
但賺嘅係 crypto 長期上升嘅 **beta**（SHORT 完全冇 edge），
而且風險調整後唔勝 buy&hold，亦唔勝 60/40 再平衡。

---

## 1. 研究路線圖（點樣一步步排除）

| 階段 | 假設 | 結果 | 文檔 |
|---|---|---|---|
| ① RR gate 診斷 | 「冇單係因為 gate 太嚴」 | root cause 確認（RR TP1 恆 <1.0 vs gate 1.2 數學唔相交），但**修好都冇 edge** | RR_BLENDED_GATE_FINDINGS.md |
| ② 參數／出場 sweep | 「參數唔啱」 | 40/40 SL×出場全負；SL 0.8→8×ATR 單調改善但**永遠唔穿 0** | DESIGN_OVERHAUL_FINDINGS.md |
| ③ 換進場策略 | 「進場邏輯唔啱」 | 18/20 負；唯一候選 vol_break bootstrap CI 排除 0 = **0/40** | DESIGN_OVERHAUL_FINDINGS.md |
| ④ 形態（雙頂底／平行通道） | 「形態引擎唔夠」 | 自寫 detector，雙頂底 18/18 負；通道 230 組合 CI>0 只 6/230（**< 隨機期望 11.5**） | PATTERN_CHANNEL_FINDINGS.md |
| ⑤ 加 edge（條件過濾） | 「加 filter 就得」 | 12 特徵 × 5 分層 × TRAIN/TEST；82 過濾器 **0 通過**；最似 edge 嘅訊號係**成本 artifact** | EDGE_SEARCH_FINDINGS.md |
| ⑥ **高週期（D1/H4）** | 「成本佔 R 太貴」 | **假設成立** → 搵到真正期望值，但係 beta | MTF_FINDINGS.md |

---

## 2. 關鍵數字

### 2.1 根因：成本經濟學（唔係策略問題）

```
成本(R) ≈ COST × price / (k × ATR)
```

| 週期 | ATR/price | risk (3×ATR) | **成本/單** |
|---|---|---|---|
| 30m | 0.392% | 1.18% | **0.170R** |
| 4h | 1.494% | 4.48% | **0.045R** (3.8×) |
| 1d | 4.280% | 12.84% | **0.016R** (10.6×) |

production SL 只有 0.8×ATR → 成本實測 **≈ 0.35R/單**（價格 0.4% vs 來回 0.2%）。

### 2.2 M30 上全部方向性方法實測結果

| 方法 | 測試規模 | 結果 |
|---|---|---|
| 形態（Flag / Wedge / Triangle / DoubleTop / DoubleBottom / boundary） | 4 年 pool，4,869 setup | **全部負**，連 Flag 都 −0.427（t −14.71） |
| 平行通道突破 | 230 組合 | meanR −0.827~+1.179，CI>0 **6/230** < 隨機 11.5；Bonferroni 顯著 **0** |
| 雙頂／雙底 | 18 組合 | **18/18 負**，t 低至 −5.2 |
| 10 個獨立進場策略 | 20 組合 | 18 負；唯一候選 bootstrap CI 0/40 |
| 條件過濾（12 特徵） | 82 過濾器 | **0 通過**（隨機期望 4.1）；58 子集 TRAIN 為正 **0** |
| SL 闊度 | 40 組合 | 單調改善 −1.378 → −0.152，**永不穿 0** |
| 出場結構 | 8 種 | 散佈 **≤0.06R**（即出場唔重要） |

### 2.3 高週期 — 唯一正面結果

**成本拆解（證實毛利本身轉正）：**

| 週期 | 成本 0.2% | **零成本** |
|---|---|---|
| 30m | 3/20 正 | — |
| 4h | 11/20 正 | **18/20 正** |
| 1d | 16/20 正 | 16/20 正 |

**多資產驗證（11 資產 1d，獨立 TRAIN/TEST）：**

| 檢定 | 結果 |
|---|---|
| 資產層面 | **11/11 meanR > 0**（二項 p = 0.0005） |
| Block bootstrap（重抽資產） | CI **[+0.088, +0.146]** ✅ 排除 0 |
| 共同期間（2020-09-22+） | **11/11**，CI **[+0.074, +0.143]** ✅ 穩健 |
| TRAIN / TEST | +0.155 / +0.079 ✅ 都正 |
| Sanity check（rsi_mr 應負） | 10/11 負，p = 0.0059 ✅ 檢定有力 |
| Cross-asset（BTC→ETH/SOL） | 符號一致 **88%**，p = 0.002 |

### 2.4 ⚠️ 但拆 long/short 就爆

| | 正比例 | 中位 meanR | Bootstrap CI | t |
|---|---|---|---|---|
| **LONG** | 11/11 | **+0.250** | **[+0.167, +0.274]** ✅ | +16.20 |
| **SHORT** | 8/11 | **+0.011** | **[−0.014, +0.023]** ❌ 含 0 | **+0.34** |

- MA200 之上（牛市）+0.187 vs 之下（熊市）+0.039
- rsi_mr SHORT **11/11 負**（逆勢做空喺牛市必死）
- → **賺錢全部來自 long = crypto 長期上升 beta，唔係策略技巧**

### 2.5 同 Buy&Hold 比較（風險對齊後）

| | CAGR | maxDD | **Calmar** |
|---|---|---|---|
| BTC 策略（long-only 日線） | +4.7% | **−6.4%** | **0.74** |
| BTC B&H | +40.4% | −83.2% | 0.49 |
| **60/40 再平衡**（11 年） | **+48.0%** | −63.7% | **0.75** |

- Calmar 勝 B&H，但 **≈ 60/40 再平衡（0.75）**，而後者唔使做 ~20 筆/年交易
- 要追上 B&H 絕對回報需放大 **13×**（每筆風險 13% equity）→ 實務不可行

---

## 3. 其他發現（順帶）

### 3.1 馬丁格爾（BTC 已有 testnet live）
- 28 chains 淨 **+$0.55**（t 0.196 = 統計上係零）
- **cap 係計時器唔係止損**：7/7 大注 chain 都係 note4 後**剛好 15 分鐘**被無條件平倉 →
  7 筆 LOSS 全部集中喺 4 注 chain，0 勝（code 同 docstring 意圖唔一致）
- 對照 XAUUSD 馬丁 live：**53 筆 26勝／27負，equity −$78.28**（「有策略」但實蝕）

### 3.2 RR gate 修復（`feat/rr-blended-gate`，已 push 未 merge）
- 修法正確但**冇創造 edge**：新舊 gate per-trade 期望值幾乎一樣（−0.357 vs −0.349）
- merge 落 main = cron 即刻開始蝕快 10 倍（總虧 −113R vs −11R）
- **建議唔 merge**

---

## 4. ⚠️ 過程中修正嘅自身錯誤（4 次，全部由背景通知觸發核對）

| # | 錯誤 | 修正 |
|---|---|---|
| 1 | family ablation 期間標籤（540 日寫成 4 年） | 補跑 4 年 pool，結論一致 |
| 2 | §3.1 粗掃期間標籤（4 年寫成 540 日） | 由 CSV 反查修正 |
| 3 | CSV 檔名缺週期 → **靜默覆蓋**；§2 表數字抄錯 | 改名重跑；由 CSV 程式化核對 |
| 4 | 漏報資產期間差異（上市日差 3 年） | 補共同期間檢定 |

**根源全部一樣：人手由 console 抄數字 / 靠記憶寫報告。**
4 次核實後**最終結論都冇變**，但每次都會令報告唔可信。

→ 已寫入 skill `binance-testnet-paper-trading` / `references/strategy-research-lessons.md`
  教訓 **20-31**，核心紀律：
    - 每個數字要 traceable 到 CSV／metadata
    - 表格要**程式化產生**，唔好肉眼抄
    - 輸出檔名要包住所有掃描維度
    - 多資產要報每個資產期間 + 共同期間對照

---

## 5. 狀態 / 建議

### 現狀
- **cron worktree 完全未動**：`main` @ `17f2adf`，clean，5 個 cron job 全部 enabled
- 研究全部喺獨立 worktree，冇掂 production

### 未 merge 嘅分支
| 分支 | 狀態 | 建議 |
|---|---|---|
| `feat/rr-blended-gate` (`~/repos/btc-analyze-rrfix`) | 2 commits ahead | **唔 merge**（冇 edge，只會蝕快） |
| `research/design-overhaul` (`~/repos/btc-analyze-redesign`) | 研究記錄 | 保留做文檔 |
| `fix/btc-pattern-gate-explicit`、`fix/btc-risk-guards-parity`、`research/btc-edge-voltarget-basket` | **已全部喺 main 內** | 可清理 worktree |

### 建議下一步（三選一）
- **A. 收檔** — 接受「BTC 冇方向性 alpha」，cron 維持現狀（testnet 收樣本）
- **B. 60/40 再平衡做主力** — 唯一有結構性優勢嘅方向（Sharpe 1.18 / Calmar 0.75）
- **C. 修馬丁 cap bug** — 令第 4 注真正按價止損（實質工作，獨立 branch 可做）

**唔建議**：再試新嘅方向性策略／再加過濾層。已測過嘅維度：
形態、趨勢、均值回歸、突破、SL 闊度、8 種出場、12 個條件變數 × 5 分層 × TRAIN/TEST、
M30/H4/D1 三個週期、11 個資產 — **全部唔係冇 alpha 就係 beta**。

---

## 6. 研究產出檔案

| 檔案 | 內容 |
|---|---|
| `RR_BLENDED_GATE_FINDINGS.md` | RR gate root cause + 修復 + 回測（在 rrfix worktree） |
| `DESIGN_OVERHAUL_FINDINGS.md` | SL 闊度 × 出場結構 + 10 個獨立進場策略 |
| `PATTERN_CHANNEL_FINDINGS.md` | 平行通道／雙頂底 detector + 230 組合細網格 |
| `EDGE_SEARCH_FINDINGS.md` | 條件過濾搜尋（12 特徵 / 82 過濾器） |
| `MTF_FINDINGS.md` | **高週期研究（最重要）** |
| `MARTINGALE_CAP_FINDINGS.md` | 馬丁 4 注 cap 分析 |
| `XAUUSD_GATE_ABLATION_FINDINGS.md` | XAUUSD gate ablation |

腳本: `pattern_detector.py`、`sweep_patterns.py`、`check_channel_breakout.py`、
`feature_edge_search.py`、`edge_combo_search.py`、`mtf_sweep.py`、`oos_cross_asset.py`、
`pooled_validation.py`、`longshort_decomp.py`、`equity_sim.py`、`common_period_check.py`、
`strategy_sweep.py`、`sweep_design.py` + 單元測試 (`test_pattern_detector.py` 26/26、
`test_sweep_sim.py` 12/12 全 PASS)

---

## 7. ⚠️ 收檔時發現嘅 cron 風險（未修，需確認）

4 個 BTC cron script 共用同一個 repo path（`~/repos/btc-analyze`），但 BRANCH_PIN 唔一致：

| script | BRANCH_PIN |
|---|---|
| `btc_weekend_cron.py`（落單） | `main` ✅ |
| `btc_rebalance_cron.py`（再平衡） | **`fix/btc-exit-symmetry`** ⚠️ 落後 main **7 commits** |
| `btc_martingale_cron.py` | （無 pin，用現狀） |
| `btc_dual_report.py` | （無 pin，用現狀） |

**風險鏈**：repo 損壞時（macOS clean-tmps 有清過檔嘅先例）→ 邊個 cron 先撞到就由佢復原：
若 rebalance 先 → `git clone -b fix/btc-exit-symmetry` 落**共用** path →
repo 變成落後 7 commits、**缺少 PR#7 phantom-sell 修復**
（`binance_testnet_paper.py` 少 222 行、`btc_auto_trade_cycle.py` 少 202 行）→
之後落單 cron 嘅 `_repo_healthy()` **只檢查檔案存在 + HEAD 可解析，唔檢查 branch** →
當佢健康 → **靜默跑舊 code 落單**。

**現時未觸發**（repo 實際喺 `main` @ `17f2adf`，clean），屬**潛在**風險。
建議修法（需用戶確認才做，因涉及 cron）：
把 `btc_rebalance_cron.py` 嘅 BRANCH_PIN 改為 `main`，
並在 `_repo_healthy()` 加 `git rev-parse --abbrev-ref HEAD == "main"` 驗證。
