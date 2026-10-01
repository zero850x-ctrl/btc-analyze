#!/usr/bin/env python3
"""check_vol_break.py — 跟進唯一候選 vol_break: 參數穩定性 + bootstrap CI.

策略掃描 (strategy_sweep.py) 20 個組合之中, 只有 vol_break(1.5) 兩個出場模式都正:
    trail: +0.310 (t 1.12, n 143)   tp3r: +0.077 (t 0.55, n 149)
但 t 低, n 細。呢個 script 做三件事判斷係真 edge 定噪音:
  1. 參數穩定性: 掃 N × k 網格 — 真 edge 應該平滑, overfit 會跳
  2. Bootstrap 95% CI — 包含 0 就唔可信
  3. TRAIN/TEST 一致性 + Bonferroni
"""
import math
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import strategy_sweep as ss


def sig_vol_break(df, atr, N, k):
    hi = df["high"].rolling(N).max().shift(1)
    lo = df["low"].rolling(N).min().shift(1)
    out = []
    for i in range(len(df)):
        a = atr.iloc[i]
        if not np.isfinite(a) or not np.isfinite(hi.iloc[i]) or not np.isfinite(lo.iloc[i]):
            continue
        c = df["close"].iloc[i]
        if c > hi.iloc[i] + k * a:
            out.append((i, "BUY"))
        elif c < lo.iloc[i] - k * a:
            out.append((i, "SELL"))
    return out


def boot_ci(v, n_boot=10000, seed=42):
    rng = np.random.default_rng(seed)
    v = np.asarray(v, float)
    if len(v) < 5:
        return None
    ms = np.array([rng.choice(v, size=len(v), replace=True).mean() for _ in range(n_boot)])
    return float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))


def main():
    df = ss.load_bars()
    atr = ss.atr_series(df)
    half = len(df) // 2
    print(f"[bars] {len(df)}  TRAIN 0-{half}  TEST {half}-{len(df)}")
    print(f"[cost] 來回 {ss.COST * 100:.2f}%  SL {ss.SL_MULT}×ATR  trail {ss.TRAIL_MULT}×ATR\n")

    rows = []
    for N in (10, 20, 40, 80):
        for k in (0.5, 1.0, 1.5, 2.0, 2.5):
            sigs = sig_vol_break(df, atr, N, k)
            for emode in ("trail", "tp3r"):
                tr = ss.run_single_slot(df, atr, sigs, emode)
                allr = [x["r"] for x in tr]
                st = ss.stats(allr)
                if not st:
                    continue
                tr_s = ss.stats([x["r"] for x in tr if x["idx"] < half])
                te_s = ss.stats([x["r"] for x in tr if x["idx"] >= half])
                ci = boot_ci(allr)
                rows.append({"N": N, "k": k, "exit": emode, "n": st["n"],
                             "meanR": st["meanR"], "t": st["t"], "win": st["win"],
                             "train": tr_s["meanR"] if tr_s else None,
                             "test": te_s["meanR"] if te_s else None,
                             "ci_lo": ci[0] if ci else None, "ci_hi": ci[1] if ci else None})
        print(f"  N={N} 掃完", flush=True)

    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(REPO, f"vol_break_grid_cost{ss.COST}.csv"), index=False)

    print("=" * 96)
    print("vol_break 參數網格 (N × k × 出場)")
    print("=" * 96)
    print(f"{'N':>4} {'k':>5} {'exit':>6} {'n':>5} {'meanR':>8} {'t':>7} {'勝%':>6} "
          f"{'TRAIN':>8} {'TEST':>8}  {'95% CI':>20}")
    for _, r in R.iterrows():
        ci = f"[{r['ci_lo']:+.3f},{r['ci_hi']:+.3f}]" if pd.notna(r["ci_lo"]) else "n/a"
        print(f"{int(r['N']):>4} {r['k']:>5} {r['exit']:>6} {int(r['n']):>5} "
              f"{r['meanR']:>+8.3f} {r['t']:>+7.2f} {r['win'] * 100:>5.1f} "
              f"{(r['train'] if pd.notna(r['train']) else float('nan')):>+8.3f} "
              f"{(r['test'] if pd.notna(r['test']) else float('nan')):>+8.3f}  {ci:>20}")

    ncomp = len(R)
    print(f"\n=== 判斷 ===")
    print(f"  組合數 {ncomp}  meanR 範圍 {R['meanR'].min():+.3f} ~ {R['meanR'].max():+.3f}")
    pos = R[R["meanR"] > 0]
    print(f"  meanR > 0: {len(pos)}/{ncomp}")
    both = R[(R["train"] > 0) & (R["test"] > 0)]
    print(f"  TRAIN 同 TEST 都 > 0: {len(both)}/{ncomp}")
    ci_pos = R[(R["ci_lo"] > 0)]
    print(f"  bootstrap 95% CI 完全喺 0 以上: {len(ci_pos)}/{ncomp}")
    if len(ci_pos):
        print(ci_pos[["N", "k", "exit", "n", "meanR", "t", "ci_lo", "ci_hi"]].to_string(index=False))
    from sweep_design import _norm_ppf
    bonf = _norm_ppf(1 - 0.05 / (2 * ncomp))
    print(f"  Bonferroni 門檻 |t| > {bonf:.2f}")
    sig = R[R["t"].abs() > bonf]
    print(f"  顯著: {len(sig)} — " + ("冇" if len(sig) == 0 else
          sig[["N", "k", "exit", "n", "meanR", "t"]].to_string(index=False)))


if __name__ == "__main__":
    main()
