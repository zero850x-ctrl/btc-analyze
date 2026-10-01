#!/usr/bin/env python3
"""family_ablation.py — 被 gate 擋咗嘅形態家族, 如果放行會點?

背景: production `ALLOWED_PATTERN_FAMILIES = ("Flag",)` — 只做 Flag。
其餘家族 (DoubleTop/DoubleBottom/boundary/Triangle/Wedge) 一律被擋,
2026-09 診斷顯示 67.5% 被擋就係呢批。

呢個 script 用同一批 setup pool, **逐家族**跑同一套出場規則, 睇邊個家族有 edge。
比 production gate 準確, 因為:
  - 同一 pool、同一出場規則 → 家族之間可直接比較
  - 唔受 cooldown 序列效應影響 (每個 setup 獨立評估)
  - Bonferroni 修正

⚠️ 限制: pool 只有引擎定義嘅 entry (limit_px), 冇做 pattern-specific entry 重算。
所以呢個測嘅係「引擎對呢個家族嘅 entry 質素」, 唔係「呢個形態本身嘅潛力」。

用法: python3 family_ablation.py [pool.json]
"""
import json
import math
import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import sweep_design as sd


def main():
    pool_p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "setup_pool.json")
    with open(pool_p) as f:
        P = json.load(f)
    sd.load_bars()
    pool = [p for p in P["pool"] if p["fill_idx"] is not None]

    print(f"[pool] {len(pool)} 成交 setups  ({P['bars_start']} → {P['bars_end']})")
    print(f"[cost] 來回 {sd.COST * 100:.2f}%")

    SEGS = [(1.0, 1 / 3), (2.0, 1 / 3), (None, 1 / 3)]
    fams = sorted(set(p["family"] for p in pool))
    print(f"[families] {len(fams)}: {fams}\n")

    rows = []
    for k in (1.5, 3.0, 4.0):
        print("=" * 84)
        print(f"SL = {k}×ATR   出場 = 3段(1:2:trail)")
        print("=" * 84)
        print(f"  {'family':16} {'n':>5} {'meanR':>8} {'t':>7} {'勝%':>6} {'sumR':>9}")
        for fam in fams:
            sel = [p for p in pool if p["family"] == fam]
            if len(sel) < 10:
                continue
            vals = []
            for p in sel:
                e, atr = p["limit_px"], p["atr"]
                stop = e + k * atr if p["side"] == "SELL" else e - k * atr
                vals.append(sd.simulate(e, stop, atr, p["side"], SEGS, p["fill_idx"]))
            v = np.array([x for x in vals if x is not None])
            if len(v) < 10:
                continue
            m, s = v.mean(), v.std(ddof=1)
            t = m / (s / math.sqrt(len(v))) if s > 0 else 0.0
            print(f"  {fam:16} {len(v):>5} {m:>+8.3f} {t:>+7.2f} "
                  f"{(v > 0).mean() * 100:>5.1f} {v.sum():>+9.1f}")
            rows.append({"k": k, "family": fam, "n": len(v), "meanR": float(m),
                         "t": float(t), "win": float((v > 0).mean()), "sumR": float(v.sum())})
        # 對照: Flag (production 唯一放行)
        print()

    import pandas as pd
    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(REPO, f"family_ablation_cost{sd.COST}.csv"), index=False)

    ncomp = len(R)
    from sweep_design import _norm_ppf
    bonf = _norm_ppf(1 - 0.05 / (2 * ncomp))
    print("=" * 84)
    print(f"總結: {ncomp} 個 (family × SL) 組合   Bonferroni |t| > {bonf:.2f}")
    print("=" * 84)
    pos = R[R["meanR"] > 0]
    print(f"  meanR > 0: {len(pos)}/{ncomp}")
    if len(pos):
        print(pos.sort_values("meanR", ascending=False).to_string(index=False))
    sig = R[R["t"].abs() > bonf]
    print(f"\n  顯著 |t| > {bonf:.2f}: {len(sig)}")
    if len(sig):
        print(sig.sort_values("t").to_string(index=False))
    print(f"\n  meanR 範圍: {R['meanR'].min():+.3f} ~ {R['meanR'].max():+.3f}")

    # 家族匯總 (跨 SL 平均)
    print("\n  家族平均 (跨 SL):")
    g = R.groupby("family")[["n", "meanR", "t"]].mean().sort_values("meanR", ascending=False)
    for fam, r in g.iterrows():
        print(f"    {fam:16} n≈{int(r['n']):>5}  meanR {r['meanR']:+.3f}  t {r['t']:+.2f}")


if __name__ == "__main__":
    main()
