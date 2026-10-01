#!/usr/bin/env python3
"""feature_edge_search.py — 「加 edge」: 系統性搜尋條件變數, 睇有冇正期望值子集.

背景: 已經證明 raw 進場（形態/趨勢/均值回歸/突破）全部負。
呢個 script 問: **加上條件過濾之後, 有冇任何子集係正?**

方法論紀律 (唔做就會變成 overfit 自欺):
  1. 所有特徵只用 **win_idx 及之前** 嘅 bar 計算 (無 look-ahead)
  2. 條件搜尋**只在 TRAIN 做**; TEST 只做驗證, 唔准回頭揀
  3. 每個特徵看**整個 quintile 序列** — 真效果應該單調, 唔係「某一格靚」
  4. 報 Bonferroni 門檻 + 「隨機之下會有幾多假陽性」

特徵 (全部 signal-time 可得):
  atr_pct      波動水平 (ATR/price %)
  atr_pctl     波動相對位置 (近 500 根內嘅百分位)
  ma50_ext     離 MA50 距離 %
  ma200_ext    離 MA200 距離 %
  ma200_slope  MA200 斜率 (50 根變化 %)
  rsi14        RSI(14)
  compress     壓縮度 (20 根區間 / 100 根區間)
  pos_in_range 在 100 根區間內嘅位置
  run_len      連續同向 bar 數
  vol_z        成交量 z-score
  hour         bar 嘅 UTC 小時
  dow          星期幾 (0=Mon)

用法: python3 feature_edge_search.py [pool.json]
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import sweep_design as sd

N_QUANT = 5


def build_features(df):
    H, L, C, V = (df["high"].values, df["low"].values,
                  df["close"].values, df["volume"].values)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    A = tr.rolling(14).mean().values
    ma50 = df["close"].rolling(50).mean().values
    ma200 = df["close"].rolling(200).mean().values
    d = df["close"].diff()
    up = d.clip(lower=0).rolling(14).mean()
    dn = (-d.clip(upper=0)).rolling(14).mean()
    RSI = (100 - 100 / (1 + up / dn.replace(0, np.nan))).values
    dt = pd.to_datetime(df["datetime"])
    return dict(H=H, L=L, C=C, V=V, A=A, ma50=ma50, ma200=ma200, RSI=RSI,
                hour=dt.dt.hour.values, dow=dt.dt.dayofweek.values)


def feats_at(i, F):
    """只用索引 <= i 嘅資料。資訊不足回 None。"""
    if i < 200:
        return None
    A, C, H, L, V = F["A"], F["C"], F["H"], F["L"], F["V"]
    a = A[i]
    px = C[i]
    if not np.isfinite(a) or a <= 0 or not np.isfinite(px):
        return None
    w = A[max(0, i - 500):i + 1]
    w = w[np.isfinite(w)]
    hi20, lo20 = H[i - 19:i + 1].max(), L[i - 19:i + 1].min()
    hi100, lo100 = H[i - 99:i + 1].max(), L[i - 99:i + 1].min()
    rng100 = hi100 - lo100
    vw = V[max(0, i - 100):i]
    vs = vw.std()
    # 連續同向 bar
    run = 0
    for k in range(i, max(0, i - 20), -1):
        if C[k] > C[k - 1]:
            if run >= 0:
                run += 1
            else:
                break
        elif C[k] < C[k - 1]:
            if run <= 0:
                run -= 1
            else:
                break
        else:
            break
    f = {
        "atr_pct": a / px * 100,
        "atr_pctl": float((w <= a).mean()) if len(w) > 50 else np.nan,
        "ma50_ext": (px / F["ma50"][i] - 1) * 100 if np.isfinite(F["ma50"][i]) else np.nan,
        "ma200_ext": (px / F["ma200"][i] - 1) * 100 if np.isfinite(F["ma200"][i]) else np.nan,
        "ma200_slope": ((F["ma200"][i] / F["ma200"][i - 50] - 1) * 100
                        if np.isfinite(F["ma200"][i]) and np.isfinite(F["ma200"][i - 50])
                        else np.nan),
        "rsi14": F["RSI"][i],
        "compress": (hi20 - lo20) / rng100 if rng100 > 0 else np.nan,
        "pos_in_range": (px - lo100) / rng100 if rng100 > 0 else np.nan,
        "run_len": run,
        "vol_z": (V[i] - vw.mean()) / vs if len(vw) > 20 and vs > 0 else np.nan,
        "hour": F["hour"][i],
        "dow": F["dow"][i],
    }
    return f


def main():
    pool_p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "setup_pool.json")
    with open(pool_p) as f:
        P = json.load(f)
    sd.load_bars()
    print(f"[pool] {len(P['pool'])} setups  {P['bars_start']} → {P['bars_end']} ({P['days']} 日)")
    print(f"[cost] 來回 {sd.COST * 100:.2f}%")

    import pandas as _pd
    df = _pd.read_csv(os.path.expanduser("~/.hermes/reports/btc_bars_30m.csv"))
    df["datetime"] = _pd.to_datetime(df["datetime"])
    F = build_features(df)

    SEGS = [(1.0, 1 / 3), (2.0, 1 / 3), (None, 1 / 3)]
    SL_K = 3.0

    rows = []
    for p in P["pool"]:
        if p["fill_idx"] is None:
            continue
        f = feats_at(p["win_idx"], F)
        if f is None:
            continue
        e, atr = p["limit_px"], p["atr"]
        stop = e + SL_K * atr if p["side"] == "SELL" else e - SL_K * atr
        r = sd.simulate(e, stop, atr, p["side"], SEGS, p["fill_idx"])
        if r is None:
            continue
        rows.append({"r": r, "side": p["side"], "family": p["family"],
                     "win_idx": p["win_idx"], **f})
    D = pd.DataFrame(rows)
    n = len(D)
    half = n // 2
    D = D.sort_values("win_idx").reset_index(drop=True)
    tr = D.iloc[:half]
    te = D.iloc[half:]
    print(f"[sample] 可用 {n}  (TRAIN {len(tr)} / TEST {len(te)})")
    print(f"[base] TRAIN meanR {tr['r'].mean():+.3f} | TEST meanR {te['r'].mean():+.3f}\n")

    FEATS = ["atr_pct", "atr_pctl", "ma50_ext", "ma200_ext", "ma200_slope",
             "rsi14", "compress", "pos_in_range", "run_len", "vol_z", "hour", "dow"]

    print("=" * 104)
    print(f"特徵 quintile 分析 (TRAIN 揀; TEST 驗證)   n_quant={N_QUANT}")
    print("=" * 104)
    print(f"{'feature':>14} | {'Q1':>8} {'Q2':>8} {'Q3':>8} {'Q4':>8} {'Q5':>8} | "
          f"{'ρ_train':>8} {'ρ_test':>8} | {'TEST Q5-Q1':>11}")
    out = []
    for feat in FEATS:
        v = tr[feat].replace([np.inf, -np.inf], np.nan)
        if v.notna().sum() < 200:
            continue
        try:
            b_tr, edges = pd.qcut(v, N_QUANT, labels=False, retbins=True, duplicates="drop")
        except ValueError:
            continue
        nb = int(b_tr.max()) + 1
        b_te = pd.cut(te[feat].replace([np.inf, -np.inf], np.nan), bins=edges,
                      labels=False, include_lowest=True)
        tr_means, te_means, ns = [], [], []
        for q in range(nb):
            a = tr.loc[b_tr == q, "r"]
            b = te.loc[b_te == q, "r"]
            tr_means.append(a.mean() if len(a) >= 10 else np.nan)
            te_means.append(b.mean() if len(b) >= 10 else np.nan)
            ns.append(len(a))
        # Spearman: bucket rank vs meanR
        good = ~np.isnan(tr_means)
        rho_tr = (np.corrcoef(np.arange(nb)[good], np.array(tr_means)[good])[0, 1]
                  if good.sum() >= 3 else np.nan)
        good2 = ~np.isnan(te_means)
        rho_te = (np.corrcoef(np.arange(nb)[good2], np.array(te_means)[good2])[0, 1]
                  if good2.sum() >= 3 else np.nan)
        spread = (te_means[-1] - te_means[0]) if not np.isnan(te_means[-1]) and not np.isnan(te_means[0]) else np.nan
        cells = " ".join(f"{m:>+8.3f}" if not np.isnan(m) else f"{'n/a':>8}" for m in tr_means)
        print(f"{feat:>14} | {cells} | {rho_tr:>+8.2f} {rho_te:>+8.2f} | {spread:>+11.3f}")
        out.append({"feature": feat, "rho_train": rho_tr, "rho_test": rho_te,
                    **{f"tra_q{q+1}": tr_means[q] for q in range(nb)},
                    **{f"tes_q{q+1}": te_means[q] for q in range(nb)},
                    "test_spread": spread})

    print("\n" + "=" * 104)
    print("單調性判讀 (|ρ| >= 0.7 且 TRAIN/TEST 同號 才算候選)")
    print("=" * 104)
    cands = []
    for o in out:
        same_sign = (np.isfinite(o["rho_train"]) and np.isfinite(o["rho_test"])
                     and np.sign(o["rho_train"]) == np.sign(o["rho_test"]))
        strong = (abs(o["rho_train"]) >= 0.7 and abs(o["rho_test"]) >= 0.7)
        tag = "✅ 候選" if (same_sign and strong) else ("~ 弱" if same_sign else "❌ 反號/噪音")
        print(f"  {o['feature']:>14}  ρ_train {o['rho_train']:+.2f}  ρ_test {o['rho_test']:+.2f}  {tag}")
        if same_sign and strong:
            cands.append(o["feature"])

    # ── 揀最好 quintile 組合 (只准用 TRAIN 揀) ───────────────────────────
    print("\n" + "=" * 104)
    print("組合過濾 (TRAIN 揀最好 quintile → TEST 驗證)  [⚠️ 多重比較陷阱區]")
    print("=" * 104)
    print(f"  {'filter':>34} {'n_tr':>6} {'meanR_tr':>9} {'n_te':>6} {'meanR_te':>9} {'t_te':>7}")
    tested = 0
    surv = []
    for feat in FEATS:
        v = tr[feat].replace([np.inf, -np.inf], np.nan)
        if v.notna().sum() < 200:
            continue
        try:
            b_tr, edges = pd.qcut(v, N_QUANT, labels=False, retbins=True, duplicates="drop")
        except ValueError:
            continue
        b_te = pd.cut(te[feat].replace([np.inf, -np.inf], np.nan), bins=edges,
                      labels=False, include_lowest=True)
        sub_tr = tr.loc[b_tr.notna()].copy()
        sub_tr["_b"] = b_tr[b_tr.notna()]
        # 每個 quintile
        for q in sorted(sub_tr["_b"].unique()):
            sel_tr = sub_tr.loc[sub_tr["_b"] == q, "r"]
            sel_te = te.loc[b_te == q, "r"]
            if len(sel_tr) < 30 or len(sel_te) < 30:
                continue
            tested += 1
            m_te = sel_te.mean()
            t_te = m_te / (sel_te.std(ddof=1) / math.sqrt(len(sel_te))) if sel_te.std(ddof=1) > 0 else 0
            flag = ""
            if sel_tr.mean() > 0 and m_te > 0 and t_te > 2:
                flag = "  ⭐"
                surv.append({"filter": f"{feat} q{int(q)+1}", "feat": feat,
                             "n_tr": len(sel_tr), "mean_tr": sel_tr.mean(),
                             "n_te": len(sel_te), "mean_te": m_te, "t_te": t_te})
            print(f"  {f'{feat} q{int(q)+1}':>34} {len(sel_tr):>6} {sel_tr.mean():>+9.3f} "
                  f"{len(sel_te):>6} {m_te:>+9.3f} {t_te:>+7.2f}{flag}")
        # 上下半 (極端組合)
        for lbl, mask_tr, mask_te in (
                (f"{feat} Q1+Q2 (低)", b_tr.isin([0, 1]), b_te.isin([0, 1])),
                (f"{feat} Q4+Q5 (高)", b_tr.isin([nb - 1, nb - 2]), b_te.isin([nb - 1, nb - 2]))):
            sel_tr = tr.loc[mask_tr.fillna(False), "r"]
            sel_te = te.loc[mask_te.fillna(False), "r"]
            if len(sel_tr) < 30 or len(sel_te) < 30:
                continue
            tested += 1
            m_te = sel_te.mean()
            t_te = m_te / (sel_te.std(ddof=1) / math.sqrt(len(sel_te))) if sel_te.std(ddof=1) > 0 else 0
            flag = ""
            if sel_tr.mean() > 0 and m_te > 0 and t_te > 2:
                flag = "  ⭐"
                surv.append({"filter": lbl, "feat": feat, "n_tr": len(sel_tr),
                             "mean_tr": sel_tr.mean(), "n_te": len(sel_te),
                             "mean_te": m_te, "t_te": t_te})
            print(f"  {lbl:>34} {len(sel_tr):>6} {sel_tr.mean():>+9.3f} "
                  f"{len(sel_te):>6} {m_te:>+9.3f} {t_te:>+7.2f}{flag}")

    bonf = abs(_norm_ppf(0.05 / (2 * max(tested, 1))))
    exp_fp = tested * 0.05
    print(f"\n  測咗 {tested} 個過濾器   Bonferroni |t| 門檻 ≈ {bonf:.2f}")
    print(f"  純隨機之下預期假陽性 (t>2 約 5%): {exp_fp:.1f} 個")
    print(f"  實際通過 (TRAIN>0 & TEST>0 & t>2): {len(surv)} 個")
    for s in surv:
        print(f"    {s['filter']:>34}  TRAIN {s['mean_tr']:+.3f} (n={s['n_tr']})  "
              f"TEST {s['mean_te']:+.3f} (n={s['n_te']}, t={s['t_te']:+.2f})")
    if len(surv) <= exp_fp:
        print("  ⚠️ 通過數 <= 隨機期望 → 冇證據有 edge")
    else:
        print("  ⚠️ 通過數 > 隨機期望, 但要跑 Bonferroni: "
              + ("冇一個過" if all(abs(s["t_te"]) < bonf for s in surv) else "有過門檻者!"))

    pd.DataFrame(out).to_csv(os.path.join(REPO, "feature_quintiles.csv"), index=False)
    if surv:
        pd.DataFrame(surv).to_csv(os.path.join(REPO, "feature_survivors.csv"), index=False)
    print(f"\nsaved feature_quintiles.csv")
    return D


def _norm_ppf(p):
    from sweep_design import _norm_ppf as f
    return f(p)


if __name__ == "__main__":
    main()
