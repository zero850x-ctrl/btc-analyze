#!/usr/bin/env python3
"""research_basket.py — B: 多資產 basket 能唔能夠提升再平衡溢價? (2026-09-19)

問題: BTC 唯一證實有結構優勢嘅係 60/40 再平衡 (Sharpe 1.18 vs B&H 1.13,
      maxDD 63.7% vs 83.4%)。分散化係「唯一免費午餐」—— 加資產應該擴大
      再平衡溢價。但加咗非加密資產 (GLD/SPY) 會**機械性降低加密曝險**,
      咁 Sharpe 升就可能純粹係減倉, 唔係分散化。

⚠️ 兩個陷阱 (呢個 script 就係為咗避開):
   (1) 減倉扮分散化 → 每個 basket 都要配「同風險」對照 (risk-matched twin):
       調總倉位令佢 realised vol == BTC-only 60/40 嘅 vol, 再比 Sharpe。
   (2) 窗口唔同 → SOL 2020-04 才有, 跨資產比較必須**同一窗口**重跑 BTC-only,
       唔可以拎 11 年 BTC 數字去比 6 年 basket。

可否證標準: basket 要喺**同風險**下 Sharpe 贏 BTC-only 60/40, 而且分段一致。

數據對齊注意: 加密 365 日都有價, 股票 weekend 冇 → 用 union + ffill
(股票休市時價格不變), 令加密嘅週末回報唔會被 drop 走。

用法:
  python3 research_basket.py
  python3 research_basket.py --selfcheck
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

from research_dca import FEE, metrics
from research_voltarget import block_boot_ci

FREQ = 90
BAND = 0.05
BASE_TARGET = 0.6          # 總 risk sleeve 佔比 (同 live 60/40 一致)


def load_multi(tickers):
    """回傳 DataFrame: 每日收盤, index=date (union grid + ffill)。"""
    import yfinance as yf

    cols = {}
    for t in tickers:
        h = yf.Ticker(t).history(period="max", interval="1d")
        if h.empty:
            raise SystemExit(f"冇數據: {t}")
        s = h["Close"].copy()
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        s = s[~s.index.duplicated(keep="last")]
        cols[t] = s
    df = pd.DataFrame(cols)
    df = df.sort_index()
    # union + ffill: 股票休市日價格沿用上一日 (加密該日照樣有回報)
    df = df.ffill()
    # 由所有資產都有數據嗰日開始 (最早共同起點)
    first = max(s.first_valid_index() for s in cols.values())
    df = df[df.index >= first].dropna()
    return df


def sim_basket(df, target=BASE_TARGET, freq_days=FREQ, band=BAND, fee=FEE):
    """等權 risk sleeve + 現金, 定期/band 再平衡。

    同 research_dca.sim_weight 同一套機制 (band / freq / fee / 即時成交),
    分別係 sleeve 內有 N 個資產, 每個目標 = target/N。
    """
    px = df.values.astype(float)
    n, k = px.shape
    tgt = target / k
    qty = np.array([tgt / px[0, j] for j in range(k)])
    cash = 1.0 - target
    eq = []
    last = 0
    for i in range(n):
        p = px[i]
        val = qty * p
        tot = val.sum() + cash
        if tot > 0:
            w = val / tot
            drift = np.abs(w - tgt).max()
            if (i - last >= freq_days) or drift > band:
                want = tot * tgt
                diff = want - val
                for j in range(k):
                    d = diff[j]
                    if abs(d) <= tot * 0.01 / k:
                        continue
                    if d > 0:
                        amt = min(d, cash)
                        if amt > 0:
                            qty[j] += amt * (1 - fee) / p[j]
                            cash -= amt
                    else:
                        sell = min(-d, val[j])
                        if sell > 0:
                            q = sell / p[j]
                            qty[j] -= q
                            cash += q * p[j] * (1 - fee)
                last = i
        eq.append(float((qty * p).sum() + cash))
    return np.array(eq)


def ann_vol(eq):
    r = np.diff(np.asarray(eq, float)) / np.asarray(eq, float)[:-1]
    r = r[np.isfinite(r)]
    return float(r.std(ddof=1) * np.sqrt(365)) if len(r) > 1 else 0.0


def match_risk(df, target_vol, lo=0.02, hi=1.0, iters=40):
    """搵總倉位 w 令 sim_basket 嘅 realised vol == target_vol (bisection)。"""
    va = ann_vol(sim_basket(df, lo))
    vb = ann_vol(sim_basket(df, hi))
    if va >= target_vol:
        return lo, va
    if vb <= target_vol:
        return hi, vb
    for _ in range(iters):
        mid = (lo + hi) / 2
        v = ann_vol(sim_basket(df, mid))
        if v < target_vol:
            lo = mid
        else:
            hi = mid
    w = (lo + hi) / 2
    return w, ann_vol(sim_basket(df, w))


def show(rows, title):
    print(f"\n{'=' * 112}\n{title}\n{'=' * 112}")
    print(f"{'策略':<40} {'淨值':>8} {'CAGR%':>7} {'maxDD%':>8} {'Sharpe':>7} "
          f"{'Calmar':>7} {'vol%':>6} {'vs B&H':>7}")
    print("-" * 112)
    for r in rows:
        print(f"{r['label']:<40} {r['final']:>8.3f} {r['CAGR%']:>7.1f} {r['maxDD%']:>8.1f} "
              f"{r['Sharpe']:>7.2f} {r['Calmar']:>7.2f} {r.get('vol%', 0):>6.1f} {r['vs_BH']:>7.2f}")


def _selfcheck():
    """不變式: (1) 恆價冇賺蝕; (2) 恆價+相同價格走勢, N=1 必須等於 sim_weight。"""
    from research_dca import sim_weight

    ok = True
    n = 400
    dates = pd.date_range("2020-01-01", periods=n, freq="D")

    # 1. 恆價
    df = pd.DataFrame({"A": np.full(n, 100.0), "B": np.full(n, 50.0)}, index=dates)
    v = sim_basket(df, 0.6)
    good = np.allclose(v, 1.0, atol=1e-9)
    ok &= good
    print(f"  {'OK ' if good else 'FAIL'} 恆價 2 資產: 淨值 {v[-1]:.10f} (要 1.0)")

    # 2. N=1 必須同 sim_weight 完全一致 (同機制)
    rng = np.random.default_rng(11)
    px = [100.0]
    for _ in range(600):
        px.append(px[-1] * float(np.exp(rng.normal(0, 0.02))))
    d1 = pd.DataFrame({"BTC": px}, index=pd.date_range("2020-01-01", periods=len(px), freq="D"))
    a = sim_basket(d1, 0.6)
    b = sim_weight(d1.rename(columns={"BTC": "close"}).reset_index(names="date"), 0.6,
                   FREQ, BAND, 1, FEE)
    good = np.allclose(a, b, rtol=1e-9, atol=1e-9)
    ok &= good
    print(f"  {'OK ' if good else 'FAIL'} N=1 等價 sim_weight: {a[-1]:.6f} vs {b[-1]:.6f}")

    # 3. N=2 相同走勢 → 必須等於全部押其中一隻 (冇分散化可言)
    d2 = pd.DataFrame({"A": px, "B": px}, index=d1.index)
    a2 = sim_basket(d2, 0.6)
    good = np.allclose(a2, b, rtol=1e-9, atol=1e-9)
    ok &= good
    print(f"  {'OK ' if good else 'FAIL'} N=2 完全相關: {a2[-1]:.6f} vs 單一 {b[-1]:.6f} (要相等)")

    print(f"\n{'✅ 全部通過' if ok else '❌ 有 FAIL'}")
    return 0 if ok else 1


CANDIDATES = [
    ("BTC only", ["BTC-USD"]),
    ("BTC+ETH", ["BTC-USD", "ETH-USD"]),
    ("BTC+ETH+SOL", ["BTC-USD", "ETH-USD", "SOL-USD"]),
    ("BTC+GLD", ["BTC-USD", "GLD"]),
    ("BTC+SPY", ["BTC-USD", "SPY"]),
    ("BTC+ETH+GLD+SPY", ["BTC-USD", "ETH-USD", "GLD", "SPY"]),
]


def main():
    for name, tickers in CANDIDATES:
        print(f"\n{'#' * 112}")
        print(f"# {name}   ({', '.join(tickers)})")
        print(f"{'#' * 112}")
        df = load_multi(tickers)
        k = df.shape[1]
        bench = df["BTC-USD"].values
        print(f"窗口: {len(df)} 日  {df.index[0].date()} → {df.index[-1].date()}  "
              f"({len(df)/365.25:.1f} 年)  BTC {bench[-1]/bench[0]-1:+.0%}")

        # 相關性診斷 (加密 vs 分散資產)
        if k > 1:
            rets = df.pct_change().dropna()
            cm = rets.corr()
            pairs = [f"{a.replace('-USD','')}~{b.replace('-USD','')}={cm.loc[a,b]:+.2f}"
                     for i, a in enumerate(df.columns) for b in df.columns[i+1:]]
            print("  相關性: " + "  ".join(pairs))

        eq_bh = (bench / bench[0])
        rows = [metrics(eq_bh, bench, "B&H BTC (100/0)")]
        if k > 1:
            eq_k = (df / df.iloc[0]).mean(axis=1).values
            rows.append(metrics(eq_k, bench, f"B&H 等權 {k} 資產"))

        # (1) 固定總倉位 60% (naive)
        eq60 = sim_basket(df, BASE_TARGET)
        m60 = metrics(eq60, bench, f"basket 60% 等權 (每季+band)")
        m60["vol%"] = ann_vol(eq60) * 100
        rows.append(m60)

        # (2) BTC-only 60/40 同窗口基準
        ref = None
        if k > 1:
            dfb = df[["BTC-USD"]]
            eqb = sim_basket(dfb, BASE_TARGET)
            ref = metrics(eqb, bench, "★ BTC-only 60/40 (同窗口基準)")
            ref["vol%"] = ann_vol(eqb) * 100
            rows.append(ref)

        # (3) 決定性: 同風險對照 (調倉位令 vol 對齊 BTC-only 60/40)
        w = mm = None
        if k > 1 and ref is not None:
            w, v = match_risk(df, ref["vol%"] / 100.0)
            eqm = sim_basket(df, w)
            mm = metrics(eqm, bench, f"basket 同風險 (倉位 {w:.0%}, vol {v*100:.1f}%)")
            mm["vol%"] = v * 100
            rows.append(mm)

        show(rows, f"B1. {name} — 同窗口比較")

        if k > 1 and ref is not None and mm is not None:
            print(f"\n  ● 決定性判定 (同風險 Sharpe):")
            print(f"      BTC-only 60/40 Sharpe = {ref['Sharpe']:.2f} (vol {ref['vol%']:.1f}%)")
            print(f"      basket   同風險 Sharpe = {mm['Sharpe']:.2f} (vol {mm['vol%']:.1f}%)")
            d = mm["Sharpe"] - ref["Sharpe"]
            print(f"      Δ = {d:+.2f}")

            # 配對 block bootstrap CI —— 同 A5 同一把尺:
            # Sharpe Δ 大唔等於真, 要睇日回報差嘅 CI 有冇排除 0。
            eb = sim_basket(df, w)
            er = sim_basket(df[["BTC-USD"]], BASE_TARGET)
            rb = np.diff(eb) / eb[:-1]
            rr = np.diff(er) / er[:-1]
            diff = rb - rr
            diff = diff[np.isfinite(diff)]
            ci = block_boot_ci(diff, block=FREQ, n=2000)
            lo, hi = ci[0] * 365 * 100, ci[1] * 365 * 100
            real = ci[0] > 0
            print(f"      配對日回報差: 年化 {diff.mean() * 365 * 100:+.2f}%  "
                  f"block CI 95% [{lo:+.2f}%, {hi:+.2f}%]")
            print(f"      → {'✅ CI 排除 0 = 分散化貢獻確立' if real else '❌ CI 包含 0 = 未確立'}"
                  + ("" if d > 0.05 else "  (ΔSharpe 亦低於門檻)"))

            # 分段 (4 段, 睇一致性)
            n = len(df)
            print(f"\n  ● 分段 (4 段) — basket 同風險 vs BTC-only 60/40:")
            print(f"    {'期間':<24} {'BTC':>9} | {'basket':>8} {'BTC-only':>9} {'Δ':>7}")
            print("    " + "-" * 70)
            for kk in range(4):
                i0, i1 = kk * n // 4, (kk + 1) * n // 4
                d0, d1 = df.index[i0], df.index[i1 - 1]      # 取日期要喺 reset 之前
                sub = df.iloc[i0:i1].reset_index(drop=True)
                if len(sub) < 120:
                    continue
                bc = sub["BTC-USD"].values
                mb = metrics(sim_basket(sub, w), bc, "")
                mr = metrics(sim_basket(sub[["BTC-USD"]], BASE_TARGET), bc, "")
                chg = bc[-1] / bc[0] - 1
                print(f"    {str(d0.date())}→{str(d1.date()):<11} "
                      f"{chg:>+8.0%} | {mb['Sharpe']:>8.2f} {mr['Sharpe']:>9.2f} "
                      f"{mb['Sharpe'] - mr['Sharpe']:>+7.2f}")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        sys.exit(_selfcheck())
    main()
