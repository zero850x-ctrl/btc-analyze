#!/usr/bin/env python3
"""mtf_sweep.py — 高週期 (H4 / D1) 策略掃描: 驗證「成本佔 R」結構性假設.

動機 (2026-10-01):
  M30 上所有方向性策略實測全負。已診斷根因 **唔係** 進場邏輯, 而係經濟學:
      成本(R) ≈ COST × price / risk,  risk = k × ATR
  M30 ATR/price ≈ 0.4% → 3×ATR SL 只有價格 1.2% → 來回 0.2% 成本 = 0.17R
  (production SL 0.8×ATR 更窄 → 成本 0.5R/單)

  **週期越大 → ATR 越闊 → 成本佔 R 越細**。若毛利 ≈ 0 (打和),
  高週期就可能由負轉正。呢個係唯一未測、而且有結構性理由嘅維度。

重用 strategy_sweep.py 嘅 signal generator + run_single_slot (同一 harness
→ 同 M30 數字直接可比, 唔會因為換 code 而 artefact)。

用法:
  python3 mtf_sweep.py                 # 全部週期
  python3 mtf_sweep.py 4h              # 單一週期
  python3 mtf_sweep.py 4h 0.002 3.0    # 週期 成本 SL倍數
"""
import math
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import strategy_sweep as ss          # 重用同一 harness

OUT = os.path.expanduser("~/.hermes/reports")

# interval → (csv 檔名, max_hold bars, 描述)
# max_hold: 30m 用 96 (= 48 小時)。高週期用「時間等值」會太短
# (4h→12, 1d→2), 所以按策略邏輯俾最少 10-12 bars。
INTERVALS = {
    "30m": ("btc_bars_30m.csv", 96),
    "4h": ("btc_bars_4h.csv", 12),
    "1d": ("btc_bars_1d.csv", 10),
}

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
EXITS = ["trail", "tp3r"]

RNG = np.random.default_rng(20261001)


def load(iv):
    name, mh = INTERVALS[iv]
    df = pd.read_csv(os.path.join(OUT, name))
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.reset_index(drop=True), mh


def cost_in_r(df, atr, trades, sl_mult, cost):
    """實測每筆交易嘅成本佔幾多 R。"""
    if not trades:
        return np.nan
    vals = []
    for t in trades:
        i = t["idx"]
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0:
            continue
        risk = sl_mult * a
        px = df["close"].iloc[i]
        vals.append(cost * px / risk)
    return float(np.mean(vals)) if vals else np.nan


def boot_ci(vals, n_boot=10000, alpha=0.05):
    v = np.asarray(vals, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) < 10:
        return np.nan, np.nan
    idx = RNG.integers(0, len(v), size=(n_boot, len(v)))
    means = v[idx].mean(axis=1)
    return float(np.percentile(means, 100 * alpha / 2)), float(np.percentile(means, 100 * (1 - alpha / 2)))


def run(iv, cost, sl_mult):
    df, max_hold = load(iv)
    ss.COST = cost
    ss.SL_MULT = sl_mult
    ss.TRAIL_MULT = 2.0
    ss.TP_R = 3.0
    ss.MAX_HOLD = max_hold
    atr = ss.atr_series(df)
    n = len(df)
    half = n // 2

    atr_pct = float((atr / df["close"]).median() * 100)
    risk_pct = sl_mult * atr_pct
    cost_r = cost / (risk_pct / 100) if risk_pct > 0 else np.nan

    print("\n" + "=" * 112)
    print(f"【{iv}】{n} 根  {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}"
          f"   成本 {cost * 100:.2f}%  SL {sl_mult}×ATR  max_hold {max_hold} bars")
    print(f"       ATR/price 中位 {atr_pct:.3f}%  → risk {risk_pct:.3f}% of price"
          f"  → **成本 ≈ {cost_r:.3f}R/單**")
    bh = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    bh_tr = (df["close"].iloc[half] / df["close"].iloc[0] - 1) * 100
    bh_te = (df["close"].iloc[-1] / df["close"].iloc[half] - 1) * 100
    print(f"       B&H 全期 {bh:+.1f}%  TRAIN {bh_tr:+.1f}%  TEST {bh_te:+.1f}%")
    print("-" * 112)
    print(f"{'策略':>18} {'出場':>6} | {'n':>5} {'meanR':>8} {'t':>7} {'win':>6} | "
          f"{'TRAIN':>9} {'TEST':>9} | {'boot_CI(TEST)':>20} | {'成本R':>7}")
    rows = []
    for name, fn in STRATS:
        sigs = fn(df, atr)
        if not sigs:
            continue
        for ex in EXITS:
            trades = ss.run_single_slot(df, atr, sigs, ex)
            if len(trades) < 10:
                continue
            allr = [t["r"] for t in trades]
            st = ss.stats(allr)
            tr = ss.stats([t["r"] for t in trades if t["idx"] < half])
            te = ss.stats([t["r"] for t in trades if t["idx"] >= half])
            te_vals = [t["r"] for t in trades if t["idx"] >= half]
            lo, hi = boot_ci(te_vals)
            cR = cost_in_r(df, atr, trades, sl_mult, cost)
            star = " ⭐" if (st["meanR"] > 0 and te and te["meanR"] > 0 and lo > 0) else ""
            print(f"{name:>18} {ex:>6} | {st['n']:>5} {st['meanR']:>+8.3f} {st['t']:>+7.2f} "
                  f"{st['win']:>6.1%} | {(tr['meanR'] if tr else float('nan')):>+9.3f} "
                  f"{(te['meanR'] if te else float('nan')):>+9.3f} | "
                  f"[{lo:>+7.3f},{hi:>+7.3f}] | {cR:>7.3f}{star}")
            rows.append({"interval": iv, "strategy": name, "exit": ex,
                         "cost": cost, "sl_mult": sl_mult, "n": st["n"],
                         "meanR": st["meanR"], "t": st["t"], "win": st["win"],
                         "train": tr["meanR"] if tr else np.nan,
                         "test": te["meanR"] if te else np.nan,
                         "ci_lo": lo, "ci_hi": hi, "cost_r": cR,
                         "n_test": len(te_vals)})
    return rows


def main():
    ivs = [sys.argv[1]] if len(sys.argv) > 1 else ["30m", "4h", "1d"]
    cost = float(sys.argv[2]) if len(sys.argv) > 2 else 0.002
    sl_mult = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0

    allrows = []
    for iv in ivs:
        allrows += run(iv, cost, sl_mult)

    R = pd.DataFrame(allrows)
    R.to_csv(os.path.join(REPO, f"mtf_sweep_cost{cost}_sl{sl_mult}.csv"), index=False)

    print("\n" + "=" * 112)
    print("總結")
    print("=" * 112)
    ntest = len(R)
    pos = (R["meanR"] > 0).sum()
    both = ((R["train"] > 0) & (R["test"] > 0)).sum()
    ci = (R["ci_lo"] > 0).sum()
    print(f"  測咗 {ntest} 組合")
    print(f"  meanR > 0            : {pos}/{ntest}")
    print(f"  TRAIN>0 & TEST>0     : {both}/{ntest}")
    print(f"  bootstrap CI 排除 0  : {ci}/{ntest}   (隨機期望 ≈ {ntest * 0.05:.1f})")
    if ntest:
        # Bonferroni 門檻 (雙尾)
        try:
            from sweep_design import _norm_ppf
            bonf = abs(_norm_ppf(0.025 / ntest))
            print(f"  Bonferroni |t| 門檻   : {bonf:.2f}")
            sig = (R["t"].abs() > bonf).sum()
            print(f"  過 Bonferroni 顯著    : {sig}/{ntest}"
                  f"  (其中正: {((R['t'].abs() > bonf) & (R['t'] > 0)).sum()})")
        except Exception as e:
            print(f"  (Bonferroni 計算失敗: {e})")
    print(f"\n  每個週期最好:")
    for iv in ivs:
        sub = R[R["interval"] == iv]
        if len(sub) == 0:
            continue
        b = sub.loc[sub["meanR"].idxmax()]
        print(f"    {iv:>4}: {b['strategy']:>18} {b['exit']:>6}  meanR {b['meanR']:+.3f} "
              f"(t {b['t']:+.2f}, n {b['n']:.0f}, TRAIN {b['train']:+.3f} TEST {b['test']:+.3f})")
    print(f"\n  saved mtf_sweep_cost{cost}_sl{sl_mult}.csv")


if __name__ == "__main__":
    main()
