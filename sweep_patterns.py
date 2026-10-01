#!/usr/bin/env python3
"""sweep_patterns.py — 平行通道 / 雙頂底 / 兩者組合 的完整 sweep.

用戶要求: 「試下平行通道雙頂/底咁」

引擎幾乎唔出 Channel (pool 2394 之中只有 1 個), 所以用 pattern_detector.py
嘅獨立偵測。全部信號 bar close 確認 → 下一根 open 進場 (無 look-ahead)。

測:
  A. 雙頂/雙底 (頸線突破) — tol × depth 網格
  B. 平行通道 — breakout / meanrev, win × r2 網格
  C. 組合 — 雙頂/底 出現喺通道邊界 (confluence)

評估: 單槽 cap 1、SL 3×ATR、trail 2×ATR 或 TP 3R、來回成本 0.2%、
      TRAIN/TEST 對半、bootstrap 95% CI、Bonferroni。
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import strategy_sweep as ss
import pattern_detector as pd_

sys.path.insert(0, REPO)



def _f(x, fmt="{:+.3f}"):
    """安全格式化 — train/test 可能係 None (單邊樣本不足)。"""
    return "  n/a  " if x is None else fmt.format(x)

def boot_ci(v, n_boot=5000, seed=42):
    rng = np.random.default_rng(seed)
    v = np.asarray(v, float)
    if len(v) < 5:
        return None
    ms = np.array([rng.choice(v, size=len(v), replace=True).mean() for _ in range(n_boot)])
    return float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))


def eval_sigs(df, atr, sigs, half, label, rows, extra=None):
    if len(sigs) < 10:
        return None
    for emode in ("trail", "tp3r"):
        tr = ss.run_single_slot(df, atr, sigs, emode)
        allr = [x["r"] for x in tr]
        st = ss.stats(allr)
        if not st:
            continue
        tr_s = ss.stats([x["r"] for x in tr if x["idx"] < half])
        te_s = ss.stats([x["r"] for x in tr if x["idx"] >= half])
        ci = boot_ci(allr)
        row = {"label": label, "exit": emode, "n_sig": len(sigs), **st,
               "train": tr_s["meanR"] if tr_s else None,
               "test": te_s["meanR"] if te_s else None,
               "ci_lo": ci[0] if ci else None, "ci_hi": ci[1] if ci else None}
        if extra:
            row.update(extra)
        rows.append(row)
    return True


def main():
    df = ss.load_bars()
    atr = ss.atr_series(df)
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    A = atr.values
    n = len(df)
    half = n // 2
    print(f"[bars] {n}  {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}")
    print(f"[cost] 來回 {ss.COST * 100:.2f}%  SL {ss.SL_MULT}×ATR  trail {ss.TRAIL_MULT}×ATR  TP {ss.TP_R}R")
    print("[split] TRAIN 0-%d / TEST %d-%d" % (half, half, n))

    print("\n偵測 swing 點...", flush=True)
    SW = pd_.find_swings(H, L, 3)
    print(f"  swings: highs {len(SW[0])}, lows {len(SW[1])}")

    rows = []

    # ── A. 雙頂/雙底 ───────────────────────────────────────────────────
    print("\n" + "=" * 96)
    print("A. 雙頂/雙底 (頸線突破確認)")
    print("=" * 96)
    for tol in (0.3, 0.5, 0.8):
        for depth in (0.8, 1.5, 2.5):
            dtb = pd_.double_top_bottom(H, L, C, A, lookback=3, tol_atr=tol,
                                        depth_atr=depth, min_gap=5, max_gap=80,
                                        swings=SW)
            if not dtb:
                continue
            sigs = [(x["idx"], x["side"]) for x in dtb]
            lbl = f"DTB tol={tol} depth={depth}"
            r0 = len(rows)
            eval_sigs(df, atr, sigs, half, lbl, rows, {"grp": "A-dtb", "tol": tol, "depth": depth})
            if len(rows) > r0:
                a0 = [r for r in rows[r0:] if r["exit"] == "trail"][0]
                print(f"  {lbl:26} sig={len(sigs):5}  trail: n={a0['n']:4} "
                      f"meanR {a0['meanR']:+.3f} t {a0['t']:+5.2f}  "
                      f"[{_f(a0['train'])}/{_f(a0['test'])}]")

    # ── B. 平行通道 ────────────────────────────────────────────────────
    print("\n" + "=" * 96)
    print("B. 平行通道 (R² / 斜率平行 / 闊度 過濾)")
    print("=" * 96)
    for win in (80, 150, 250):
        for r2 in (0.70, 0.85):
            ch = pd_.parallel_channel(H, L, A, win=win, lookback=3, r2_min=r2,
                                      slope_tol_atr=0.05, swings=SW)
            valid = sum(1 for x in ch if x)
            print(f"\n  win={win} r2>={r2}: 有效 bar {valid}/{n} ({valid / n * 100:.1f}%)")
            for mode in ("breakout", "meanrev"):
                cs = pd_.channel_signals(ch, H, L, C, A, mode=mode)
                sigs = [(x["idx"], x["side"]) for x in cs]
                lbl = f"CH win={win} r2={r2} {mode}"
                r0 = len(rows)
                eval_sigs(df, atr, sigs, half, lbl, rows,
                          {"grp": "B-channel", "win": win, "r2": r2, "mode": mode})
                if len(rows) > r0:
                    a0 = [r for r in rows[r0:] if r["exit"] == "trail"][0]
                    print(f"    {mode:9} sig={len(sigs):5}  trail: n={a0['n']:4} "
                          f"meanR {a0['meanR']:+.3f} t {a0['t']:+5.2f}  "
                          f"[{_f(a0['train'])}/{_f(a0['test'])}]")

    # ── C. 組合 ────────────────────────────────────────────────────────
    print("\n" + "=" * 96)
    print("C. 組合 — 雙頂/底 出現喺通道邊界 (confluence)")
    print("=" * 96)
    ch_best = pd_.parallel_channel(H, L, A, win=150, lookback=3, r2_min=0.70,
                                   slope_tol_atr=0.05, swings=SW)
    dtb_base = pd_.double_top_bottom(H, L, C, A, lookback=3, tol_atr=0.5,
                                     depth_atr=1.0, min_gap=5, max_gap=80, swings=SW)
    for near in (0.5, 1.0, 2.0):
        cmb = pd_.combo_dtb_at_channel(H, L, C, A, ch_best, near_atr=near,
                                       dtbs=dtb_base)
        if not cmb:
            print(f"  near={near}: 冇信號")
            continue
        sigs = [(x["idx"], x["side"]) for x in cmb]
        lbl = f"COMBO near={near}"
        r0 = len(rows)
        eval_sigs(df, atr, sigs, half, lbl, rows, {"grp": "C-combo", "near": near})
        if len(rows) > r0:
            a0 = [r for r in rows[r0:] if r["exit"] == "trail"][0]
            print(f"  {lbl:20} sig={len(sigs):5}  trail: n={a0['n']:4} "
                  f"meanR {a0['meanR']:+.3f} t {a0['t']:+5.2f}  "
                  f"[{_f(a0['train'])}/{_f(a0['test'])}]")

    # ── 總結 ───────────────────────────────────────────────────────────
    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(REPO, f"pattern_sweep_cost{ss.COST}.csv"), index=False)
    print("\n" + "=" * 96)
    print("總結")
    print("=" * 96)
    ncomp = len(R)
    from sweep_design import _norm_ppf
    bonf = _norm_ppf(1 - 0.05 / (2 * ncomp))
    print(f"  組合 {ncomp}   Bonferroni |t| > {bonf:.2f}")
    print(f"  meanR 範圍 {R['meanR'].min():+.3f} ~ {R['meanR'].max():+.3f}")
    print(f"  meanR > 0: {len(R[R['meanR'] > 0])}/{ncomp}")
    print(f"  TRAIN 同 TEST 都 > 0: {len(R[(R['train'] > 0) & (R['test'] > 0)])}/{ncomp}")
    ci_pos = R[R["ci_lo"] > 0]
    print(f"  bootstrap 95% CI 完全 > 0: {len(ci_pos)}/{ncomp}")
    if len(ci_pos):
        print(ci_pos[["label", "exit", "n", "meanR", "t", "ci_lo", "ci_hi"]].to_string(index=False))
    print(f"\n  按組別平均 meanR:")
    for g, sub in R.groupby("grp"):
        print(f"    {g:12} n組={len(sub):3}  meanR {sub['meanR'].mean():+.3f}  "
              f"最好 {sub['meanR'].max():+.3f}  最差 {sub['meanR'].min():+.3f}")
    sig = R[R["t"].abs() > bonf]
    print(f"\n  顯著 |t| > {bonf:.2f}: {len(sig)}")
    if len(sig):
        print(sig[["label", "exit", "n", "meanR", "t"]].to_string(index=False))
    print(f"\n  Top 10 (按 meanR):")
    print(R.nlargest(10, "meanR")[["label", "exit", "n", "meanR", "t", "train", "test"]].to_string(index=False))


if __name__ == "__main__":
    main()
