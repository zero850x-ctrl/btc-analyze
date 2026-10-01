#!/usr/bin/env python3
"""edge_combo_search.py — 跟進: 單調特徵係真 edge 抑或成本 artifact? 組合能否轉正?

發現 (feature_edge_search.py): 3 個特徵單調且 TRAIN/TEST 一致 —
    compress     ρ_train +0.98  ρ_test +0.94   (TEST Q5-Q1 +0.144)
    atr_pctl     ρ_train +0.93  ρ_test +0.94   (+0.189)
    atr_pct      ρ_train +0.85  ρ_test +0.89   (+0.143)
**但所有 quintile 都仍然負** (最好 -0.201)。

疑點: 呢 3 個特徵本質都係「波動高」。
     成本(R) ≈ COST × price / risk, risk = 3×ATR → **ATR 越闊, 成本佔 R 越少**。
     所以「高波動 = 冇咁差」可能純粹係機械成本效應, 唔係預測力 (skill §13)。

呢個 script 答三條:
  Q1 零成本之下, 單調梯度會唔會消失? (消失 = 成本 artifact)
  Q2 3 個單調特徵嘅交集 (高波動 + 擴張 + 高百分位) 能否轉正?
  Q3 全部特徵 2-3 重組合窮舉 (TRAIN 揀 / TEST 驗) 有冇任何一個轉正?
"""
import itertools
import json
import math
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import sweep_design as sd
import feature_edge_search as fes

NQ = 5
MONO = ["compress", "atr_pctl", "atr_pct"]


def load_dataset(cost):
    """回傳 DataFrame(r, side, family, win_idx, <features>) — 指定成本重算。"""
    with open(os.path.join(REPO, "setup_pool.json")) as f:
        P = json.load(f)
    sd.load_bars()
    sd.COST = cost
    df = pd.read_csv(os.path.expanduser("~/.hermes/reports/btc_bars_30m.csv"))
    df["datetime"] = pd.to_datetime(df["datetime"])
    F = fes.build_features(df)
    SEGS = [(1.0, 1 / 3), (2.0, 1 / 3), (None, 1 / 3)]
    rows = []
    for p in P["pool"]:
        if p["fill_idx"] is None:
            continue
        f = fes.feats_at(p["win_idx"], F)
        if f is None:
            continue
        e, atr = p["limit_px"], p["atr"]
        stop = e + 3.0 * atr if p["side"] == "SELL" else e - 3.0 * atr
        r = sd.simulate(e, stop, atr, p["side"], SEGS, p["fill_idx"])
        if r is None:
            continue
        rows.append({"r": r, "side": p["side"], "family": p["family"],
                     "win_idx": p["win_idx"], **f})
    D = pd.DataFrame(rows).sort_values("win_idx").reset_index(drop=True)
    sd.COST = 0.002
    return D


def quintile_bounds(df, feat):
    """由 TRAIN 定 bin 邊界 (唔准用 TEST 揀)。"""
    v = df[feat].replace([np.inf, -np.inf], np.nan)
    try:
        b, edges = pd.qcut(v, NQ, labels=False, retbins=True, duplicates="drop")
    except ValueError:
        return None, None
    return b, edges


def main():
    FEATS = ["atr_pct", "atr_pctl", "ma50_ext", "ma200_ext", "ma200_slope",
             "rsi14", "compress", "pos_in_range", "run_len", "vol_z", "hour", "dow"]

    print("=" * 100)
    print("Q1. 成本拆解 — 單調梯度係真 edge 定機械成本效應?")
    print("=" * 100)
    table = {}
    for cost in (0.002, 0.0):
        D = load_dataset(cost)
        h = len(D) // 2
        tr, te = D.iloc[:h], D.iloc[h:]
        print(f"\n  成本 {cost * 100:.1f}%   TRAIN meanR {tr['r'].mean():+.3f} | "
              f"TEST meanR {te['r'].mean():+.3f}")
        print(f"  {'feature':>10} | " + " ".join(f"{'Q' + str(q + 1):>8}" for q in range(NQ))
              + f" | {'Q5-Q1':>8}")
        for feat in MONO:
            b_tr, edges = quintile_bounds(tr, feat)
            if b_tr is None:
                continue
            b_te = pd.cut(te[feat].replace([np.inf, -np.inf], np.nan), bins=edges,
                          labels=False, include_lowest=True)
            ms = []
            for q in range(NQ):
                sub = te.loc[b_te == q, "r"]
                ms.append(sub.mean() if len(sub) >= 10 else np.nan)
            print(f"  {feat:>10} | " + " ".join(
                f"{m:>+8.3f}" if not np.isnan(m) else f"{'n/a':>8}" for m in ms)
                + f" | {ms[-1] - ms[0]:>+8.3f}")
            table[(cost, feat)] = ms
    # 比較梯度大細
    print("\n  梯度 (Q5-Q1) 對比:")
    print(f"  {'feature':>10} | {'成本 0.2%':>10} | {'零成本':>10} | 結論")
    for feat in MONO:
        a = table[(0.002, feat)]
        b = table[(0.0, feat)]
        da, db = a[-1] - a[0], b[-1] - b[0]
        if abs(da) > 1e-9 and abs(db) < abs(da) * 0.4:
            verdict = "梯度大幅收窄 → 成本 artifact"
        elif abs(db) >= abs(da) * 0.7:
            verdict = "梯度保留 → 唔係純成本"
        else:
            verdict = "部分收窄"
        print(f"  {feat:>10} | {da:>+10.3f} | {db:>+10.3f} | {verdict}")

    print("\n" + "=" * 100)
    print("Q2/Q3. 組合掃描 (TRAIN 揀 → TEST 驗證, 成本 0.2%)")
    print("=" * 100)
    D = load_dataset(0.002)
    h = len(D) // 2
    tr, te = D.iloc[:h].reset_index(drop=True), D.iloc[h:].reset_index(drop=True)

    # 為每個特徵建 TRAIN-derived bin, 標記 TEST
    bincode = {}
    for feat in FEATS:
        b_tr, edges = quintile_bounds(tr, feat)
        if b_tr is None:
            continue
        b_te = pd.cut(te[feat].replace([np.inf, -np.inf], np.nan), bins=edges,
                      labels=False, include_lowest=True)
        bincode[feat] = (b_tr.astype("float"), b_te.astype("float"))

    print(f"\n  (a) 3 個單調特徵嘅高分交集")
    print(f"  {'組合':>44} {'n_tr':>6} {'meanR_tr':>9} {'n_te':>6} {'meanR_te':>9} {'t_te':>7}")
    for k in (1, 2, 3):
        for combo in itertools.combinations(MONO, k):
            for lv in ("hi", "lo"):
                if lv == "hi":
                    m_tr = np.ones(len(tr), bool); m_te = np.ones(len(te), bool)
                    for feat in combo:
                        b_tr, b_te = bincode[feat]
                        m_tr &= (b_tr >= NQ - 2).values
                        m_te &= (b_te >= NQ - 2).values
                    lbl = " ∩ ".join(combo) + " [Q4+Q5]"
                else:
                    m_tr = np.ones(len(tr), bool); m_te = np.ones(len(te), bool)
                    for feat in combo:
                        b_tr, b_te = bincode[feat]
                        m_tr &= (b_tr <= 1).values
                        m_te &= (b_te <= 1).values
                    lbl = " ∩ ".join(combo) + " [Q1+Q2]"
                if m_tr.sum() < 30 or m_te.sum() < 30:
                    continue
                s_tr, s_te = tr.loc[m_tr, "r"], te.loc[m_te, "r"]
                t_te = (s_te.mean() / (s_te.std(ddof=1) / math.sqrt(len(s_te)))
                        if s_te.std(ddof=1) > 0 else 0)
                tag = " ⭐" if (s_tr.mean() > 0 and s_te.mean() > 0) else ""
                print(f"  {lbl:>44} {len(s_tr):>6} {s_tr.mean():>+9.3f} "
                      f"{len(s_te):>6} {s_te.mean():>+9.3f} {t_te:>+7.2f}{tag}")

    print(f"\n  (b) 全部 12 特徵 × {NQ} quintile 窮舉 (揀 TRAIN meanR 最高 10 個看 TEST)")
    print(f"  {'filter':>30} {'n_tr':>6} {'meanR_tr':>9} {'n_te':>6} {'meanR_te':>9} {'t_te':>7}")
    cand = []
    tested = 0
    for feat in FEATS:
        if feat not in bincode:
            continue
        b_tr, b_te = bincode[feat]
        for q in range(NQ):
            m_tr = (b_tr == q).values
            m_te = (b_te == q).values
            if m_tr.sum() < 30 or m_te.sum() < 30:
                continue
            tested += 1
            s_tr, s_te = tr.loc[m_tr, "r"], te.loc[m_te, "r"]
            t_te = (s_te.mean() / (s_te.std(ddof=1) / math.sqrt(len(s_te)))
                    if s_te.std(ddof=1) > 0 else 0)
            cand.append({"filter": f"{feat} q{q+1}", "n_tr": len(s_tr),
                         "mean_tr": s_tr.mean(), "n_te": len(s_te),
                         "mean_te": s_te.mean(), "t_te": t_te})
    cand.sort(key=lambda x: -x["mean_tr"])
    surv = 0
    for c in cand[:10]:
        ok_tr = c["mean_tr"] > 0
        ok_te = c["mean_te"] > 0
        tag = " ⭐" if (ok_tr and ok_te) else ""
        if ok_tr and ok_te:
            surv += 1
        print(f"  {c['filter']:>30} {c['n_tr']:>6} {c['mean_tr']:>+9.3f} "
              f"{c['n_te']:>6} {c['mean_te']:>+9.3f} {c['t_te']:>+7.2f}{tag}")
    exp_fp = tested * 0.05
    print(f"\n  測咗 {tested} 個; 隨機期望 (TRAIN>0 & TEST>0 約 25%) ≈ "
          f"{tested * 0.25:.1f} 個; 實際 TRAIN>0 嘅: "
          f"{sum(1 for c in cand if c['mean_tr'] > 0)} 個")
    print(f"  TRAIN 揀最好 10 個之中, TEST 仍然正: {surv}/10")

    # Q2 結論
    print("\n" + "=" * 100)
    print("Q1/Q2/Q3 結論")
    print("=" * 100)
    allbest = max([c["mean_te"] for c in cand if c["mean_tr"] > 0], default=None)
    print(f"  任何單特徵子集 TEST 最好 meanR: {allbest:+.3f}" if allbest else "  冇 TRAIN 為正嘅子集")
    print(f"  → {'仍然全部負 = 冇 edge' if (allbest or -1) < 0 else '有正值, 需進一步驗證!'}")


if __name__ == "__main__":
    main()
