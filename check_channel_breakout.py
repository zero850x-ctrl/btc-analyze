#!/usr/bin/env python3
"""check_channel_breakout.py — 嚴格驗證「平行通道突破」係真 edge 定噪音.

初步觀察 (4 年 M30, 69k bars):
    win=80  r2>=0.7  -0.105  →  r2>=0.85  +0.038
    win=150 r2>=0.7  +0.050  →  r2>=0.85  +0.178
    win=250 r2>=0.7  +0.353  →  r2>=0.85  +0.547   (TRAIN +0.587 / TEST +0.524)
單調 (win 同 r2 兩邊收緊都有改善) 係好徵兆 (§11), 但 n 細 (43-129)、t 只有 1.6。
雙頂/雙底同期係**一致負** (t 低至 -5.2), 組合亦負 → 唔係全域參數問題。

呢個 script 做五樣:
  1. 精細網格 (win × r2 × k_atr × 出場) + 單調性
  2. Bootstrap 95% CI
  3. 分段穩定性 (4 段, 唔止 TRAIN/TEST)
  4. 成本敏感度
  5. Bonferroni
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


def boot_ci(v, n_boot=5000, seed=42):
    rng = np.random.default_rng(seed)
    v = np.asarray(v, float)
    if len(v) < 5:
        return (None, None)
    ms = np.array([rng.choice(v, size=len(v), replace=True).mean() for _ in range(n_boot)])
    return float(np.percentile(ms, 2.5)), float(np.percentile(ms, 97.5))


def filt_channel(ch_raw, r2_min, resid_atr=None):
    """由 r2_min=0 / resid 寬鬆 嘅原始通道, 按幾何質素過濾.

    ⚠️ resid_atr 係**關鍵參數**, 唔可以隨手放鬆:
      resid <= 1.5×ATR → 只有 ~1% bar 有效 (真·貼線通道) → 唯一出現過正 meanR 嘅設定
      resid <= 5.0×ATR → 82-91% bar 有效 (等於冇過濾) → meanR 冧到 +0.15 以下
    """
    out = [None] * len(ch_raw)
    for i, c in enumerate(ch_raw):
        if c is None:
            continue
        if c["r2_up"] < r2_min or c["r2_dn"] < r2_min:
            continue
        if resid_atr is not None:
            lim = resid_atr * c["atr"]
            if c["res_up"] > lim or c["res_dn"] > lim:
                continue
        out[i] = c
    return out


def main():
    df = ss.load_bars()
    atr = ss.atr_series(df)
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    A = atr.values
    n = len(df)
    years = pd.to_datetime(df["datetime"]).dt.year.values
    print(f"[bars] {n}  {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}")
    print(f"[cost] 來回 {ss.COST * 100:.2f}%   SL {ss.SL_MULT}×ATR\n")

    SW = pd_.find_swings(H, L, 3)
    print(f"[swings] highs {len(SW[0])}, lows {len(SW[1])}")

    # 每個 win 只算一次 (幾何過濾全部放喺 filt_channel, 維度可掃)
    print("計通道 (r2_min=0, resid 寬鬆, 每個 win 一次)...", flush=True)
    RAW = {}
    for win in (150, 200, 250, 350, 500):
        RAW[win] = pd_.parallel_channel(H, L, A, win=win, lookback=3, r2_min=0.0,
                                        resid_max_atr=99.0, slope_tol_atr=0.05, swings=SW)
        v = sum(1 for x in RAW[win] if x)
        print(f"  win={win}: 原始有效 {v} ({v / n * 100:.1f}%)", flush=True)

    rows = []
    for win in (150, 200, 250, 350, 500):
        for r2 in (0.70, 0.80, 0.85, 0.90):
            for res in (0.5, 1.0, 1.5, 2.5):
                ch = filt_channel(RAW[win], r2, resid_atr=res)
                valid = sum(1 for x in ch if x)
                if valid == 0:
                    continue
                for k in (0.0, 0.25):
                    sigs = [(s["idx"], s["side"]) for s in
                            pd_.channel_signals(ch, H, L, C, A, mode="breakout", k_atr=k)]
                    if len(sigs) < 20:
                        continue
                    for emode in ("trail", "tp3r"):
                        tr = ss.run_single_slot(df, atr, sigs, emode)
                        allr = [x["r"] for x in tr]
                        st = ss.stats(allr)
                        if not st:
                            continue
                        ci_lo, ci_hi = boot_ci(allr)
                        seg = []
                        for q in range(4):
                            lo, hi = q * n // 4, (q + 1) * n // 4
                            s2 = ss.stats([x["r"] for x in tr if lo <= x["idx"] < hi])
                            seg.append(s2["meanR"] if s2 else None)
                        rows.append({"win": win, "r2": r2, "res": res, "k": k,
                                     "exit": emode, "n_sig": len(sigs), "n": st["n"],
                                     "meanR": st["meanR"], "t": st["t"],
                                     "win_pct": st["win"], "valid_bar": valid,
                                     "valid_pct": valid / n * 100,
                                     "ci_lo": ci_lo, "ci_hi": ci_hi,
                                     "q1": seg[0], "q2": seg[1], "q3": seg[2], "q4": seg[3]})

    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(REPO, f"channel_breakout_grid_cost{ss.COST}.csv"), index=False)

    print("\n" + "=" * 118)
    print("通道突破 — 完整網格 (trail 出場; 按 meanR 排)")
    print("=" * 118)
    hdr = f"{'win':>5} {'r2':>5} {'res':>5} {'k':>5} {'exit':>6} {'sig':>6} {'n':>5} " \
          f"{'meanR':>8} {'t':>7} {'勝%':>6} {'有效%':>6} {'95% CI':>18} " \
          f"{'Q1':>7} {'Q2':>7} {'Q3':>7} {'Q4':>7}"
    print(hdr)
    for _, r in R.sort_values("meanR", ascending=False).iterrows():
        ci = f"[{r['ci_lo']:+.2f},{r['ci_hi']:+.2f}]" if pd.notna(r["ci_lo"]) else "n/a"
        segs = " ".join(f"{r[q]:>+7.3f}" if pd.notna(r[q]) else f"{'n/a':>7}"
                        for q in ("q1", "q2", "q3", "q4"))
        print(f"{int(r['win']):>5} {r['r2']:>5} {r['res']:>5} {r['k']:>5} {r['exit']:>6} "
              f"{int(r['n_sig']):>6} {int(r['n']):>5} {r['meanR']:>+8.3f} {r['t']:>+7.2f} "
              f"{r['win_pct'] * 100:>5.1f} {r['valid_pct']:>5.1f} {ci:>18} {segs}")

    # 單調性
    print("\n" + "=" * 118)
    print("單調性檢查 (真效果應該平滑; 跳嚟跳去 = 噪音)")
    print("=" * 118)
    tr = R[R["exit"] == "trail"]
    for k in (0.0, 0.25):
      for res in (0.5, 1.0, 1.5, 2.5):
        sub = tr[(tr["k"] == k) & (tr["res"] == res)]
        if sub.empty:
            continue
        print(f"\n  k_atr={k}  resid<={res}×ATR")
        piv = sub.pivot_table(index="win", columns="r2", values="meanR")
        print("        " + "".join(f"  r2={c:<6}" for c in piv.columns))
        for w in piv.index:
            print(f"  win={w:<4}" + "".join(
                f"  {piv.loc[w, c]:>+8.3f}" if pd.notna(piv.loc[w, c]) else f"  {'n/a':>8}"
                for c in piv.columns))
        # 單調: r2 遞增時 meanR 唔應該亂跳
        mono = 0
        tot = 0
        for w in piv.index:
            vals = [piv.loc[w, c] for c in piv.columns if pd.notna(piv.loc[w, c])]
            for a, b in zip(vals, vals[1:]):
                tot += 1
                if b >= a:
                    mono += 1
        print(f"    R² 遞增時 meanR 上升: {mono}/{tot}")

    ncomp = len(R)
    from sweep_design import _norm_ppf
    bonf = _norm_ppf(1 - 0.05 / (2 * ncomp))
    print("\n" + "=" * 118)
    print(f"總結 ({ncomp} 組合, Bonferroni |t| > {bonf:.2f})")
    print("=" * 118)
    print(f"  meanR 範圍 {R['meanR'].min():+.3f} ~ {R['meanR'].max():+.3f}")
    print(f"  meanR > 0: {len(R[R['meanR'] > 0])}/{ncomp}")
    print(f"  4 段全部 > 0: {len(R[(R['q1'] > 0) & (R['q2'] > 0) & (R['q3'] > 0) & (R['q4'] > 0)])}/{ncomp}")
    ci_pos = R[R["ci_lo"] > 0]
    print(f"  bootstrap CI 完全 > 0: {len(ci_pos)}/{ncomp}")
    if len(ci_pos):
        print(ci_pos[["win", "r2", "k", "exit", "n", "meanR", "t", "ci_lo", "ci_hi"]].to_string(index=False))
    sig = R[R["t"] > bonf]
    print(f"  顯著正 (t > {bonf:.2f}): {len(sig)}")
    if len(sig):
        print(sig.to_string(index=False))

    # 成本敏感度 (最強組合)
    print("\n" + "=" * 118)
    print("成本敏感度 (最強組合)")
    print("=" * 118)
    best = R.sort_values("meanR", ascending=False).iloc[0]
    ch = filt_channel(RAW[int(best["win"])], best["r2"], resid_atr=best["res"])
    sigs = [(s["idx"], s["side"]) for s in
            pd_.channel_signals(ch, H, L, C, A, mode="breakout", k_atr=best["k"])]
    for cost in (0.0, 0.001, 0.002, 0.003, 0.004):
        ss.COST = cost
        tr2 = ss.run_single_slot(df, atr, sigs, best["exit"])
        st = ss.stats([x["r"] for x in tr2])
        if st:
            print(f"  來回 {cost * 100:.1f}%  n={st['n']:4}  meanR {st['meanR']:+.3f}  "
                  f"t {st['t']:+.2f}  勝 {st['win'] * 100:.1f}%")
    ss.COST = 0.002

    # 年度
    print("\n" + "=" * 118)
    print(f"年度穩定性 (best: win={int(best['win'])} r2={best['r2']} res={best['res']} k={best['k']} {best['exit']})")
    print("=" * 118)
    tr3 = ss.run_single_slot(df, atr, sigs, best["exit"])
    d = pd.DataFrame(tr3)
    d["year"] = years[d["idx"].values]
    for y, g in d.groupby("year"):
        v = g["r"].values
        m, sd = v.mean(), v.std(ddof=1) if len(v) > 1 else 0
        t = m / (sd / math.sqrt(len(v))) if sd > 0 else 0
        print(f"  {y}  n={len(v):4}  meanR {m:+.3f}  t {t:+5.2f}  勝 {(v > 0).mean() * 100:.1f}%")

    # 對照: 同類策略
    print("\n" + "=" * 118)
    print("對照基準 (同期同成本)")
    print("=" * 118)
    bh = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    print(f"  B&H 全期 {bh:+.1f}%")
    for N in (20, 50, 100):
        hi = df["high"].rolling(N).max().shift(1)
        lo = df["low"].rolling(N).min().shift(1)
        s2 = []
        for i in range(len(df)):
            if not np.isfinite(hi.iloc[i]):
                continue
            if C[i] > hi.iloc[i]:
                s2.append((i, "BUY"))
            elif C[i] < lo.iloc[i]:
                s2.append((i, "SELL"))
        tr4 = ss.run_single_slot(df, atr, s2, best["exit"])
        st = ss.stats([x["r"] for x in tr4])
        if st:
            print(f"  donchian({N}) {best['exit']:>6}  n={st['n']:5}  meanR {st['meanR']:+.3f}  t {st['t']:+.2f}")


if __name__ == "__main__":
    main()
