#!/usr/bin/env python3
"""sweep_design.py — 設計大改: 掃 SL 闊度 × 出場結構 × TP 距離.

背景 (2026-09-28 回測):
  實況成本 (Binance spot 來回 0.2%) 下, 現行引擎 setup 全部失敗:
    無 gate  -0.459R/單 (t -13.5) | OLD rr1 gate -0.349R | NEW blended gate -0.357R
  零成本時 NEW gate ≈ -0.010R → **毛利 ≈ 0, 成本 ≈ 0.35R/單**。
  成本之所以咁重, 係因為 risk = 0.8×ATR ≈ 價格 0.4%, 而來回成本 0.2% ≈ 0.5R。

呢個 sweep 問三個問題:
  Q1 SL 加闊 (成本 R 值跌) 會唔會令期望值轉正?
  Q2 出場結構 (3 段 / 2 段 / 單 TP / 純 trail) 邊個好?
  Q3 TP 距離 (R 倍數) 有冇最佳值?

⚠️ 唔用引擎 TP1 值 — 佢被 _staged_targets() 夾到 1:1。全部 TP 由 R 倍數重新定義。

方法:
  - setup pool 由 build_setup_pool.py 快取 (含 limit_px / atr / side / fill_idx)
  - bar-by-bar 模擬, SL 優先 (同一根 bar 兩邊都中 → 當 SL, 保守)
  - 成本: entry *(1+c/2), 出場 *(1-c/2) → 總共扣 c
  - look-ahead: 由 fill_idx+1 開始 (唔用觸及嗰根 bar 嘅 high/low)

用法: python3 sweep_design.py [pool.json]
"""
import json
import math
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
REPO = os.path.dirname(os.path.abspath(__file__))

COST = float(os.environ.get("SWEEP_COST", "0.002"))   # 來回總成本 (Binance spot 0.1%/邊)
MAX_HOLD = 96          # 48h
BAR_HOURS = 0.5
TRAIL_MULT = float(os.environ.get("SWEEP_TRAIL", "2.0"))  # trail 距離 × ATR

BARS = None
O = H = L = C = None


def load_bars():
    global BARS, O, H, L, C
    p = os.path.expanduser("~/.hermes/reports/btc_bars_30m.csv")
    BARS = pd.read_csv(p)
    BARS["datetime"] = pd.to_datetime(BARS["datetime"])
    O = BARS["open"].values.astype(float)
    H = BARS["high"].values.astype(float)
    L = BARS["low"].values.astype(float)
    C = BARS["close"].values.astype(float)


# ── 出場結構定義 ────────────────────────────────────────────────────────
# segments: list of (tp_r or None, weight), 最後一段可以係 None = trail 到尾
STRUCTURES = {
    "staged3(1:1,2:1,trail)": [(1.0, 1 / 3), (2.0, 1 / 3), (None, 1 / 3)],
    "staged2(1.5:1,3:1)":     [(1.5, 0.5), (3.0, 0.5)],
    "staged3(1.5:3:trail)":   [(1.5, 1 / 3), (3.0, 1 / 3), (None, 1 / 3)],
    "staged3(2:4:trail)":     [(2.0, 1 / 3), (4.0, 1 / 3), (None, 1 / 3)],
    "single(1R)":             [(1.0, 1.0)],
    "single(2R)":             [(2.0, 1.0)],
    "single(3R)":             [(3.0, 1.0)],
    "trail_only":             [(None, 1.0)],
}


def simulate(entry, stop, atr, side, segs, fill_idx, trail_mult=TRAIL_MULT,
             be_after_first=True):
    """Bar-by-bar 分段出場. 回傳 R 倍數 (已扣成本).

    只讀 fill_idx+1 之後嘅 bar (觸及 limit 嗰根唔用 — 避免 look-ahead)。
    """
    n = len(O)
    end = min(fill_idx + 1 + MAX_HOLD, n)
    if end <= fill_idx + 1:
        return None
    is_sell = side == "SELL"
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    c = COST / 2.0
    # 成本模型: 信號價 (entry/stop/TP) 全部用**原始價**定義 (同 production 一致),
    # 成本只喺成交價上體現 — entry 買貴 c、出場賣平 c。risk 亦用原始價計。
    e_fill = entry * (1 - c) if is_sell else entry * (1 + c)

    tp_levels = []
    for tp_r, w in segs:
        tp_levels.append((None if tp_r is None else
                          (entry - tp_r * risk if is_sell else entry + tp_r * risk), w))

    remaining = [(tp, w) for tp, w in tp_levels]
    total_r = 0.0
    cur_stop = stop
    trail_on = False
    hit_first = False
    extreme = e_fill

    for i in range(fill_idx + 1, end):
        hi, lo, op = H[i], L[i], O[i]
        # 1) 檢查 stop (SL 優先 = 保守)
        stop_hit = (hi >= cur_stop) if is_sell else (lo <= cur_stop)
        if stop_hit:
            fill = max(op, cur_stop) if is_sell else min(op, cur_stop)
            fill *= (1 + c) if is_sell else (1 - c)
            r = ((e_fill - fill) / risk) if is_sell else ((fill - e_fill) / risk)
            for _, w in remaining:
                total_r += w * r
            return total_r
        # 2) 檢查 TP (逐段)
        new_rem = []
        for tp, w in remaining:
            if tp is None:
                new_rem.append((tp, w))
                continue
            tp_hit = (lo <= tp) if is_sell else (hi >= tp)
            if tp_hit:
                fill = min(op, tp) if is_sell else max(op, tp)
                fill *= (1 + c) if is_sell else (1 - c)
                r = ((e_fill - fill) / risk) if is_sell else ((fill - e_fill) / risk)
                total_r += w * r
                hit_first = True
            else:
                new_rem.append((tp, w))
        remaining = new_rem
        if not remaining:
            return total_r
        # 3) trail (尾倉段)
        has_trail = any(tp is None for tp, _ in remaining)
        if has_trail:
            if be_after_first and hit_first and not trail_on:
                # 第一段到價後 SL 推 breakeven
                cur_stop = e_fill
                trail_on = True
            extreme = max(extreme, hi) if is_sell else min(extreme, lo)
            if trail_on:
                cand = extreme + trail_mult * atr if is_sell else extreme - trail_mult * atr
                cur_stop = min(cur_stop, cand) if is_sell else max(cur_stop, cand)
    # 到期: 剩餘段用最後 close 平
    last = C[end - 1] * (1 + c) if is_sell else C[end - 1] * (1 - c)
    r = ((e_fill - last) / risk) if is_sell else ((last - e_fill) / risk)
    for _, w in remaining:
        total_r += w * r
    return total_r


def stats(vals, label, n_all=None):
    v = np.asarray([x for x in vals if x is not None], dtype=float)
    if len(v) == 0:
        print(f"  {label:34} n=0")
        return None
    m, sd = v.mean(), v.std(ddof=1) if len(v) > 1 else 0.0
    t = m / (sd / math.sqrt(len(v))) if sd > 0 else 0.0
    print(f"  {label:34} n={len(v):4}  sumR {v.sum():+8.1f}  meanR {m:+.3f}  "
          f"t {t:+5.2f}  勝率 {(v > 0).mean() * 100:5.1f}%")
    return {"n": len(v), "sumR": float(v.sum()), "meanR": float(m), "t": float(t),
            "win": float((v > 0).mean())}


def main():
    pool_p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "setup_pool.json")
    with open(pool_p) as f:
        P = json.load(f)
    load_bars()
    print(f"[cost] 來回 {COST * 100:.2f}%   trail {TRAIL_MULT}×ATR   hold ≤ {MAX_HOLD} 根")
    print(f"[pool] {P['n_windows']} windows   {len(P['pool'])} setups   "
          f"{P['bars_start']} → {P['bars_end']}")

    filled = [p for p in P["pool"] if p["fill_idx"] is not None]
    print(f"[pool] limit 成交 {len(filled)}")

    results = []
    # Q1+Q2+Q3: SL mult × structure
    print("\n" + "=" * 78)
    print("Q1/Q2/Q3: SL 闊度 × 出場結構 (風險以 entry∓k×ATR 重算, 唔用引擎 raw stop)")
    print("=" * 78)
    for k in (0.8, 1.5, 2.0, 3.0, 4.0):
        print(f"\n--- SL = {k}×ATR ---")
        for sname, segs in STRUCTURES.items():
            vals = []
            for p in filled:
                atr = p["atr"]
                e = p["limit_px"]
                stop = e + k * atr if p["side"] == "SELL" else e - k * atr
                r = simulate(e, stop, atr, p["side"], segs, p["fill_idx"])
                vals.append(r)
            st = stats(vals, sname)
            if st:
                st.update({"k": k, "structure": sname})
                results.append(st)

    df = pd.DataFrame(results)
    df.to_csv(os.path.join(REPO, f"sweep_design_cost{COST}.csv"), index=False)

    print("\n" + "=" * 78)
    print("最佳 12 個組合 (按 meanR)")
    print("=" * 78)
    top = df.sort_values("meanR", ascending=False).head(12)
    for _, r in top.iterrows():
        print(f"  k={r['k']:<4} {r['structure']:26} n={int(r['n']):4} "
              f"meanR {r['meanR']:+.3f}  t {r['t']:+5.2f}  勝 {r['win'] * 100:.1f}%")

    # Bonferroni: 掃咗幾個組合就除幾
    ncomp = len(df)
    thr = 1.96 + abs(_norm_ppf(0.05 / (2 * ncomp)))
    print(f"\nBonferroni: 掃 {ncomp} 個組合 → |t| 要 > {thr:.2f} 才算顯著")
    sig = df[df["t"].abs() > thr]
    print(f"  顯著: {len(sig)} 個" + ("" if len(sig) == 0 else f" — {sig[['k', 'structure', 't']].to_string(index=False)}"))
    print(f"  全部組合 meanR 範圍: {df['meanR'].min():+.3f} ~ {df['meanR'].max():+.3f}")
    print(f"  meanR > 0 嘅組合: {len(df[df['meanR'] > 0])}/{ncomp}")


def _norm_ppf(p):
    """標準常態分位數 (Acklam 近似) — 唔想 import scipy."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


if __name__ == "__main__":
    main()
