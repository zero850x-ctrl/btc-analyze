# RR gate 結構性不相容 — `rr_1.0_lt_1.2` 擋單分析

日期: 2026-10-01
數據: `~/.hermes/reports/btc_rr_gate_diag.json` (593 個 setup, 2025-04-26 → 2026-09-22)
      `~/.hermes/reports/btc_gate_blocks.json` (8 日 live 擋單記錄)

## 問題

主引擎 09-20 重開之後，**27 日 0 新單**，而每個 tick 都掃到 raw setup。
今日 (10-01) 擋咗 60 個 raw setup，`rr_1.0_lt_1.2` 係第二大原因 (9 次)。
用戶問：呢個係刻意設計，定係 bug？

## 結論先講

**兩者都係，而且係同一個結構問題的兩面：**

1. Gate 係刻意嘅（`MIN_RR = 1.2`，有 live 15 單實證支持）
2. 但佢**同引擎嘅 TP 結構唔相容** → 數學上大部分 setup 永遠過唔到
3. ⚠️ **改 gate 唔會創造 edge** —— 呢點已經有兩個獨立研究結論支持（見下）

所以正確嘅講法唔係「改咗 gate 就有單做」，而係
「**主引擎實質上已經停擺；要唔要正式承認呢件事**」。

## 數據

### 擋單原因分佈（8 日 live，413 次）

| 類別 | 次數 | 佔比 |
|---|---|---|
| family（唔准開嘅形態家族）| 279 | 68% |
| RR < 1.2 | 134 | 32% |

### RR 值分佈（`btc_rr_gate_diag.json`，593 setup）

```
TP1 嘅 RR (gate 用呢個):  中位 0.75  平均 0.903  max 7.41
  → 過 1.2 嘅: 36 / 593 = 6.1%

TP2 嘅 RR (gate 完全唔睇): 中位 2.84  max 30.73
  → 過 1.2 嘅: 593 / 593 = 100%
```

**31% 嘅被擋 setup 係「剛好 1.00」** —— 掹 0.2 就過，但永遠掹唔到。

### 根因：TP1 同 TP2 差一個數量級

`analyze_v3.py:2897 _compute_tp1()`：
- **TP1** = fib extension **0.618**（pattern 高度嘅 0.618 倍）→ 貼近入場價
- **TP2** = fib extension **1.0 / 1.618** → 遠好多

而 `btc_engine.py:286` 嘅 gate **只計 TP1**：

```python
rr = abs(tp1 - limit_px) / risk
if rr < MIN_RR:      # MIN_RR = 1.2
    s["_gate_skip"] = f"rr_{s['rr_tp1']}_lt_{MIN_RR}"
```

但引擎嘅**實際出場結構係 3 注**（用戶偏好）：
- 第 1 份 → TP1
- 第 2 份 → TP2
- 尾倉 → breakeven + ATR trail

即係 gate 用「最細嗰注嘅 RR」去否決「整條腿嘅價值」。

### 按家族拆（TP1-only vs blended）

| family | n | TP1 過 1.2 | blended (rr1+rr2)/2 過 |
|---|---|---|---|
| **Flag**（唯一准開）| 274 | **5%** | **93%** |
| boundary | 153 | 0% | 100% |
| Triangle | 73 | 18% | 78% |
| DoubleBottom | 36 | 25% | 56% |
| DoubleTop | 33 | 0% | 64% |
| Wedge | 24 | 4% | 58% |

## ⚠️ 最重要嘅一點：改 gate 唔會創造 edge

呢個唔係推測，係**已經做過嘅實驗結論**：

1. **`feat/rr-blended-gate`（已寫好、未 merge）** — 將 gate 由 TP1-only 改成
   blended R：`(1/3)·rr1 + (1/3)·rr2 + (1/3)·尾倉`，門檻 1.2。
   **修法正確，但冇創造 edge** —— 結論係「merge = 蝕快 10 倍」
   （原本 0 單 = 0 蝕；改完開始落單，但 live 25 單 meanR **−0.317**）。
2. **整個 edge 研究（`RESEARCH_SUMMARY.md`）** — 六個階段、82 個過濾器、
   58 個 TRAIN 子集，**0 個通過** Bonferroni 門檻。
   結論：BTC 冇可證實嘅市場中性 alpha。

所以 gate 唔係「擋住賺錢機會」，而係**擋住一個已知負期望值嘅系統**。

## 選項（未實作，等用戶決定）

| | 做法 | 後果 | 風險 |
|---|---|---|---|
| **A** | **維持現狀** | 實質停擺；0 單 0 蝕 | 狀態隱形（報告仍顯示「跑緊」）|
| **B** | 改 blended gate | 開始落單，樣本上升 | 期望值仍負（研究已證）→ 蝕真錢 |
| **C** | 改 TP 結構（TP1 拉遠）| 可能過 gate | 改變策略本質；未經測試 |
| **D** | **正式停用 + 標記** | 狀態誠實 | 冇（純報告）|

**建議：D**（或者 A + D）。

理由：`btc_main_system_history.txt` 已經記錄「09-18 停用」，但 09-20 又重開，
而重開之後 0 單 —— 即係**同一個決定做咗兩次，第二次冇留記錄**。
報告而家仍然顯示「📡 主: 0 live 倉」，讀者會以為「跑緊、只係靜」。
加一個明確標記（似 `btc_main_system_history.txt` 嘅做法），
令「實質停擺」變成「明示停用」，先唔會誤導下一個讀報告嘅人。

## 相關

- `feat/rr-blended-gate`（branch，**唔好 merge**）— blended gate 實作 + `RR_BLENDED_GATE_FINDINGS.md`
- `RESEARCH_SUMMARY.md` — 六階段 edge 研究結論
- `~/.hermes/profiles/btc/skills/.../strategy-research-lessons.md` — 「引擎設計常數同 risk gate 無法交集 = 策略可行性發現，唔係門檻要調低」
