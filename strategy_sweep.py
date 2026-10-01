#!/usr/bin/env python3
"""strategy_sweep.py — 設計大改: 完全唔用形態引擎, 試獨立進場邏輯.

動機 (2026-09-28 三重證據):
  1. validate_rr_blended_gate: 671 單, 實況成本 meanR -0.459 (t -13.5); 零成本 -0.212
  2. sweep_design: SL 由 0.8→6.4×ATR, meanR 由 -1.378 → -0.152, 全部 40 組合負
  3. 25 單 live: meanR -0.317, t = -2.12
  → 結論: 唔係 SL/TP/出場結構問題, 係**進場冇 edge**。

呢個 script 問: 換一個完全唔同嘅進場邏輯, 有冇 edge?

策略 (全部 bar close 確認, 下一根 open 進場 — 避免 look-ahead):
  - donchian(N):   突破過去 N 根高/低位
  - momentum(N):   過去 N 根回報符號
  - ma_cross(f,s): fast MA 上/下穿 slow MA
  - rsi_mr(p):     RSI 超買做空 / 超賣做多 (均值回歸)
  - roc_thresh(N,k): 過去 N 根回報 > k×ATR 才追

出場 (固定, 唔一齊 sweep 以免組合爆炸):
  - A: 3×ATR SL + 2×ATR trail
  - B: 3×ATR SL + 3R TP

單槽 (同時只 1 倉, 同 production cap 1 一致)。
TRAIN/TEST 各半 + Bonferroni。

用法: python3 strategy_sweep.py
"""
import math
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
COST = float(os.environ.get("SWEEP_COST", "0.002"))
SL_MULT = 3.0
TRAIL_MULT = 2.0
TP_R = 3.0
MAX_HOLD = 96


def load_bars():
    p = os.path.expanduser("~/.hermes/reports/btc_bars_30m.csv")
    df = pd.read_csv(p)
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.reset_index(drop=True)


def atr_series(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def rsi_series(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# ── 信號產生器: 回傳 list of (idx, side) — idx = 進場 bar index ────────────
def sig_donchian(df, atr, N):
    hi = df["high"].rolling(N).max().shift(1)
    lo = df["low"].rolling(N).min().shift(1)
    out = []
    for i in range(len(df)):
        if not np.isfinite(hi.iloc[i]) or not np.isfinite(lo.iloc[i]):
            continue
        c = df["close"].iloc[i]
        if c > hi.iloc[i]:
            out.append((i, "BUY"))
        elif c < lo.iloc[i]:
            out.append((i, "SELL"))
    return out


def sig_momentum(df, atr, N):
    roc = df["close"].diff(N)
    out = []
    for i in range(len(df)):
        r = roc.iloc[i]
        if not np.isfinite(r) or r == 0:
            continue
        out.append((i, "BUY" if r > 0 else "SELL"))
    return out


def sig_ma_cross(df, atr, f, s):
    maf = df["close"].rolling(f).mean()
    mas = df["close"].rolling(s).mean()
    out = []
    for i in range(1, len(df)):
        a0, b0 = maf.iloc[i - 1], mas.iloc[i - 1]
        a1, b1 = maf.iloc[i], mas.iloc[i]
        if not all(np.isfinite([a0, b0, a1, b1])):
            continue
        if a0 <= b0 and a1 > b1:
            out.append((i, "BUY"))
        elif a0 >= b0 and a1 < b1:
            out.append((i, "SELL"))
    return out


def sig_rsi_mr(df, atr, lo=30, hi=70):
    r = rsi_series(df["close"])
    out = []
    for i in range(len(df)):
        v = r.iloc[i]
        if not np.isfinite(v):
            continue
        if v < lo:
            out.append((i, "BUY"))
        elif v > hi:
            out.append((i, "SELL"))
    return out


def sig_vol_break(df, atr, k=1.5):
    hi = df["high"].rolling(20).max().shift(1)
    lo = df["low"].rolling(20).min().shift(1)
    out = []
    for i in range(len(df)):
        a = atr.iloc[i]
        if not np.isfinite(a) or not np.isfinite(hi.iloc[i]):
            continue
        c = df["close"].iloc[i]
        if c > hi.iloc[i] + k * a:
            out.append((i, "BUY"))
        elif c < lo.iloc[i] - k * a:
            out.append((i, "SELL"))
    return out


def run_single_slot(df, atr, sigs, exit_mode):
    """單槽 event-driven: 有倉就唔開新倉 (同 production cap 1 一致).

    進場 = 信號 bar 嘅**下一根 open** (避免 look-ahead)。
    平倉後由 exit bar 之後才可以再開新倉。
    回傳 list of {"idx": 進場 bar index, "side", "r"}。
    """
    n = len(df)
    O = df["open"].values
    H = df["high"].values
    L = df["low"].values
    C = df["close"].values
    A = atr.values
    sig_map = {}
    for idx, side in sigs:
        sig_map.setdefault(idx, side)      # 同一根多個信號只取第一個
    c = COST / 2.0
    trades = []
    i = 0
    while i < n - 3:
        side = sig_map.get(i)
        if side is None:
            i += 1
            continue
        a = A[i + 1] if i + 1 < n and np.isfinite(A[i + 1]) else (A[i] if np.isfinite(A[i]) else np.nan)
        if not np.isfinite(a) or a <= 0:
            i += 1
            continue
        is_sell = side == "SELL"
        entry = float(O[i + 1])
        risk = SL_MULT * a
        stop = entry + risk if is_sell else entry - risk
        tp = None
        if exit_mode == "tp3r":
            tp = entry - TP_R * risk if is_sell else entry + TP_R * risk
        e_fill = entry * (1 - c) if is_sell else entry * (1 + c)
        extreme = e_fill
        r = None
        exit_idx = None
        for j in range(i + 2, min(i + 2 + MAX_HOLD, n)):
            hi, lo, op = H[j], L[j], O[j]
            if (hi >= stop) if is_sell else (lo <= stop):
                f = max(op, stop) if is_sell else min(op, stop)
                f *= (1 + c) if is_sell else (1 - c)
                r = ((e_fill - f) / risk) if is_sell else ((f - e_fill) / risk)
                exit_idx = j
                break
            if tp is not None and ((lo <= tp) if is_sell else (hi >= tp)):
                f = min(op, tp) if is_sell else max(op, tp)
                f *= (1 + c) if is_sell else (1 - c)
                r = ((e_fill - f) / risk) if is_sell else ((f - e_fill) / risk)
                exit_idx = j
                break
            # trail (只在無 TP 模式)
            if tp is None:
                extreme = max(extreme, hi) if is_sell else min(extreme, lo)
                cand = extreme + TRAIL_MULT * a if is_sell else extreme - TRAIL_MULT * a
                stop = min(stop, cand) if is_sell else max(stop, cand)
        if r is None:
            k = min(i + 2 + MAX_HOLD, n) - 1
            last = C[k] * (1 + c) if is_sell else C[k] * (1 - c)
            r = ((e_fill - last) / risk) if is_sell else ((last - e_fill) / risk)
            exit_idx = k
        trades.append({"idx": i, "side": side, "r": float(r), "exit_idx": int(exit_idx)})
        # 單槽: 持倉期間唔可以再開 — 由平倉 bar 之後繼續
        i = max(exit_idx + 1, i + 1)
    return trades


def stats(vals):
    v = np.asarray([x for x in vals if x is not None], dtype=float)
    if len(v) < 5:
        return None
    m, sd = v.mean(), v.std(ddof=1)
    t = m / (sd / math.sqrt(len(v))) if sd > 0 else 0.0
    return {"n": len(v), "meanR": float(m), "t": float(t),
            "win": float((v > 0).mean()), "sumR": float(v.sum())}


def main():
    df = load_bars()
    atr = atr_series(df)
    n = len(df)
    half = n // 2
    print(f"[bars] {n} 根  {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}")
    print(f"[cost] 來回 {COST * 100:.2f}%  SL {SL_MULT}×ATR  trail {TRAIL_MULT}×ATR  TP {TP_R}R")
    print(f"[split] TRAIN 0-{half}, TEST {half}-{n}")

    bh = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    print(f"[B&H] 全期 {bh:+.1f}%   TRAIN {(df['close'].iloc[half] / df['close'].iloc[0] - 1) * 100:+.1f}%"
          f"   TEST {(df['close'].iloc[-1] / df['close'].iloc[half] - 1) * 100:+.1f}%")

    STRATS = {
        "donchian(20)": lambda: sig_donchian(df, atr, 20),
        "donchian(50)": lambda: sig_donchian(df, atr, 50),
        "donchian(100)": lambda: sig_donchian(df, atr, 100),
        "momentum(20)": lambda: sig_momentum(df, atr, 20),
        "momentum(50)": lambda: sig_momentum(df, atr, 50),
        "ma_cross(10,50)": lambda: sig_ma_cross(df, atr, 10, 50),
        "ma_cross(20,100)": lambda: sig_ma_cross(df, atr, 20, 100),
        "rsi_mr(30,70)": lambda: sig_rsi_mr(df, atr, 30, 70),
        "rsi_mr(20,80)": lambda: sig_rsi_mr(df, atr, 20, 80),
        "vol_break(1.5)": lambda: sig_vol_break(df, atr, 1.5),
    }

    rows = []
    print("\n" + "=" * 88)
    print("全部策略 (單槽, 成本已扣)   —   對照基準: 全部負 = 冇 edge")
    print("=" * 88)
    for emode in ("trail", "tp3r"):
        print(f"\n--- 出場: {emode} ---")
        for name, fn in STRATS.items():
            try:
                sigs = fn()
            except Exception as e:
                print(f"  {name:20} 信號產生失敗: {e}")
                continue
            tr = run_single_slot(df, atr, sigs, emode)
            allst = stats([x["r"] for x in tr])
            tr_s = stats([x["r"] for x in tr if x["idx"] < half])
            te_s = stats([x["r"] for x in tr if x["idx"] >= half])
            if not allst:
                print(f"  {name:20} 樣本不足 ({len(tr)})")
                continue
            trs = f"{tr_s['meanR']:+.3f}" if tr_s else "n/a"
            tes = f"{te_s['meanR']:+.3f}" if te_s else "n/a"
            print(f"  {name:20} n={allst['n']:5}  meanR {allst['meanR']:+.3f}  "
                  f"t {allst['t']:+5.2f}  勝 {allst['win'] * 100:4.1f}%   "
                  f"[TRAIN {trs} / TEST {tes}]")
            rows.append({"exit": emode, "strat": name, **allst,
                         "train": tr_s["meanR"] if tr_s else None,
                         "test": te_s["meanR"] if te_s else None})

    R = pd.DataFrame(rows)
    R.to_csv(os.path.join(REPO, f"strategy_sweep_cost{COST}.csv"), index=False)
    if R.empty:
        return
    ncomp = len(R)
    thr = 1.96 + 1.5
    print(f"\n=== 總結 ===")
    print(f"  組合數 {ncomp}   meanR 範圍 {R['meanR'].min():+.3f} ~ {R['meanR'].max():+.3f}")
    print(f"  meanR > 0 嘅: {len(R[R['meanR'] > 0])}/{ncomp}")
    both_pos = R[(R["train"] > 0) & (R["test"] > 0)]
    print(f"  TRAIN 同 TEST 都 > 0: {len(both_pos)}/{ncomp}")
    if len(both_pos):
        print(both_pos[["exit", "strat", "n", "meanR", "t", "train", "test"]].to_string(index=False))
    print(f"  Bonferroni 門檻 |t| > {thr:.2f} (粗略):")
    sig = R[R["t"].abs() > thr]
    print(f"    顯著 {len(sig)} 個 — " +
          (sig[["exit", "strat", "t"]].to_string(index=False) if len(sig) else "冇"))


if __name__ == "__main__":
    main()
