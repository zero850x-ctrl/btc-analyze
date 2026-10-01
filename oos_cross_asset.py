#!/usr/bin/env python3
"""oos_cross_asset.py — 最強 out-of-sample 測試: BTC 上見到嘅正毛利, 喺其他資產出現嗎?

背景 (2026-10-01):
  M30 全負 → 診斷為成本問題 (成本 0.170R/單)。
  跑高週期之後 **結論升級**: 唔止係成本 —
    4h 零成本: 18/20 策略 meanR > 0
    1d 零成本: 16/20 策略 meanR > 0     ← 毛利本身已經正
  BTC 1d 零成本最好: vol_break +0.688 (t 2.63, n 18)

  ⚠️ 但呢啲全部係 in-sample (同一資產、同一期間)。1d 每策略只得 18-295 筆,
     多重比較後冇一個過 Bonferroni。**唔可以就咁信**。

真正嘅測試: **同一個策略邏輯, 換一個資產 (ETH / SOL) 跑。**
  - 若 BTC 1d 嘅「趨勢持續」alpha 係真 → ETH/SOL 1d 應該同樣正
  - 若只係 BTC 8 年嘅特定 regime → ETH/SOL 會反號或消失
  - 相關係數 (BTC meanR vs ETH meanR 跨策略) 係關鍵指標

用法: python3 oos_cross_asset.py [interval]
"""
import math
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import strategy_sweep as ss

OUT = os.path.expanduser("~/.hermes/reports")
COST = 0.002
SL_MULT = 3.0
MAX_HOLD = {"30m": 96, "4h": 12, "1d": 10}

STRATS = [
    ("donchian(20)", lambda df, atr: ss.sig_donchian(df, atr, 20)),
    ("donchian(50)", lambda df, atr: ss.sig_donchian(df, atr, 50)),
    ("donchian(100)", lambda df, atr: ss.sig_donchian(df, atr, 100)),
    ("momentum(20)", lambda df, atr: ss.sig_momentum(df, atr, 20)),
    ("momentum(50)", lambda df, atr: ss.sig_momentum(df, atr, 50)),
    ("ma_cross(10,50)", lambda df, atr: ss.sig_ma_cross(df, atr, 10, 50)),
    ("ma_cross(20,100)", lambda df, atr: ss.sig_ma_cross(df, atr, 20, 100)),
    ("rsi_mr(30,70)", lambda df, atr: ss.sig_rsi_mr(df, atr, 30, 70)),
    ("vol_break(1.5)", lambda df, atr: ss.sig_vol_break(df, atr, 1.5)),
    ("vol_break(2.0)", lambda df, atr: ss.sig_vol_break(df, atr, 2.0)),
]

RNG = np.random.default_rng(20261001)


def boot_ci(vals, n_boot=10000):
    v = np.asarray([x for x in vals if np.isfinite(x)], dtype=float)
    if len(v) < 10:
        return np.nan, np.nan
    idx = RNG.integers(0, len(v), size=(n_boot, len(v)))
    m = v[idx].mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def run_symbol(path, iv, cost=COST):
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.reset_index(drop=True)
    ss.COST = cost
    ss.SL_MULT = SL_MULT
    ss.TRAIL_MULT = 2.0
    ss.TP_R = 3.0
    ss.MAX_HOLD = MAX_HOLD[iv]
    atr = ss.atr_series(df)
    half = len(df) // 2
    rows = {}
    for name, fn in STRATS:
        sigs = fn(df, atr)
        if not sigs:
            continue
        for ex in ("trail", "tp3r"):
            trades = ss.run_single_slot(df, atr, sigs, ex)
            if len(trades) < 10:
                continue
            allr = [t["r"] for t in trades]
            st = ss.stats(allr)
            te = [t["r"] for t in trades if t["idx"] >= half]
            tr = [t["r"] for t in trades if t["idx"] < half]
            lo, hi = boot_ci(te)
            rows[f"{name}|{ex}"] = {
                "n": st["n"], "meanR": st["meanR"], "t": st["t"],
                "win": st["win"],
                "train": float(np.mean(tr)) if tr else np.nan,
                "test": float(np.mean(te)) if te else np.nan,
                "ci_lo": lo, "ci_hi": hi,
            }
    meta = {"bars": len(df),
            "start": str(df["datetime"].iloc[0]), "end": str(df["datetime"].iloc[-1]),
            "atr_pct": float((atr / df["close"]).median() * 100)}
    return rows, meta


def main():
    iv = sys.argv[1] if len(sys.argv) > 1 else "1d"
    print("=" * 108)
    print(f"Out-of-sample 跨資產驗證 — interval={iv}  成本 {COST * 100:.2f}%  SL {SL_MULT}×ATR")
    print("=" * 108)

    symbols = [("BTC", f"btc_bars_{iv}.csv"),
               ("ETH", f"btc_bars_ETHUSDT_{iv}.csv"),
               ("SOL", f"btc_bars_SOLUSDT_{iv}.csv")]
    res = {}
    for sym, fn in symbols:
        p = os.path.join(OUT, fn)
        out = run_symbol(p, iv)
        if out is None:
            print(f"\n⚠️ {sym}: 冇數據 ({fn})")
            continue
        rows, meta = out
        res[sym] = rows
        print(f"\n【{sym}】{meta['bars']} 根  {meta['start']} → {meta['end']}   "
              f"ATR/price {meta['atr_pct']:.3f}%")
        pos = sum(1 for r in rows.values() if r["meanR"] > 0)
        both = sum(1 for r in rows.values() if r["train"] > 0 and r["test"] > 0)
        ci = sum(1 for r in rows.values() if r["ci_lo"] > 0)
        print(f"     meanR>0: {pos}/{len(rows)}  TRAIN>0&TEST>0: {both}/{len(rows)}"
              f"  CI排除0: {ci}/{len(rows)}")

    if len(res) < 2:
        print("\n❌ 唔夠資產做比較")
        return

    # ── 跨資產對照 ──────────────────────────────────────────────
    print("\n" + "=" * 108)
    print("策略層面跨資產對照 (BTC 為基準, 因為策略係喺 BTC 上構思)")
    print("=" * 108)
    base = res.get("BTC")
    if base is None:
        print("❌ 冇 BTC 基準")
        return
    others = {k: v for k, v in res.items() if k != "BTC"}
    keys = [k for k in base if all(k in o for o in others.values())]
    print(f"{'策略|出場':>26} | {'BTC n':>6} {'meanR':>8} | "
          + " | ".join(f"{s:>6} {'meanR':>8}" for s in others) + " | 同號")
    agree = {s: 0 for s in others}
    for k in keys:
        b = base[k]
        cells = []
        signs = []
        for s, o in others.items():
            r = o[k]
            cells.append(f"{r['n']:>6} {r['meanR']:>+8.3f}")
            same = np.sign(r["meanR"]) == np.sign(b["meanR"])
            signs.append(same)
            if same:
                agree[s] += 1
        print(f"{k:>26} | {b['n']:>6} {b['meanR']:>+8.3f} | " + " | ".join(cells)
              + " | " + ("✅" if all(signs) else "❌"))

    print(f"\n  符號一致率 (相對 BTC):")
    for s in others:
        n = len(keys)
        pct = agree[s] / n * 100 if n else 0
        # 二項檢定: 純隨機同號率 50%
        p = sum(math.comb(n, i) for i in range(agree[s], n + 1)) / 2 ** n
        print(f"    BTC vs {s}: {agree[s]}/{n} = {pct:.0f}%   "
              f"二項 p(>=k) = {p:.3f}  {'✅ 顯著' if p < 0.05 else '❌ 唔顯著 (= 隨機)'}")

    # 相關
    bv = np.array([base[k]["meanR"] for k in keys])
    print(f"\n  跨資產 meanR 相關 (Pearson):")
    for s, o in others.items():
        ov = np.array([o[k]["meanR"] for k in keys])
        if len(bv) > 2 and bv.std() > 0 and ov.std() > 0:
            r = float(np.corrcoef(bv, ov)[0, 1])
            print(f"    BTC vs {s}: r = {r:+.3f}   {'✅ 正相關' if r > 0.3 else '❌ 唔相關'}")

    print("\n" + "=" * 108)
    print("判讀")
    print("=" * 108)
    print("  ✅ 若其他資產都正 + 同號率高 + 相關正 → BTC 1d 正毛利可能係真 alpha")
    print("  ❌ 若反號 / 同號率 ≈ 50% / 唔相關 → 之前嘅正數係 BTC 8 年特定 regime 噪音")


if __name__ == "__main__":
    main()
