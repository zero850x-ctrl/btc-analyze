#!/usr/bin/env python3
"""research_premium.py — 風險溢價型策略研究 (唔靠方向預測).

測: Buy&Hold / DCA (定期定額) / 定期再平衡 / Dip-buying
指標: CAGR / maxDD / Sharpe / Calmar / 最終淨值

⚠️ 誠實警告: BTC 2020-2026 係大牛市, 任何 long-biased 策略都會靚。
   所以一定要睇 (a) 分段穩定性 (b) 熊市段表現 (c) 同 B&H 嘅相對表現。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd


def load_daily(years=6):
    import yfinance as yf
    df = yf.Ticker("BTC-USD").history(period=f"{years}y", interval="1d")
    if df.empty:
        raise SystemExit("冇數據")
    df = df.reset_index()
    for a in ("Date", "Datetime"):
        if a in df.columns:
            df = df.rename(columns={a: "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    for col in ("open", "high", "low", "close"):
        for alt in (col.capitalize(), col.upper()):
            if alt in df.columns and col not in df.columns:
                df[col] = df[alt]
    return df[["date", "open", "high", "low", "close"]].dropna().reset_index(drop=True)


def metrics(eq, bench_close, label):
    """eq: 淨值序列 (開始=1). bench_close: 同期 BTC 價 (計 B&H 對照)."""
    eq = np.asarray(eq, dtype=float)
    n = len(eq)
    yrs = n / 365.25
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100 if eq[-1] > 0 else -100.0
    peak = np.maximum.accumulate(eq)
    mdd = ((peak - eq) / peak).max() * 100
    rets = np.diff(eq) / eq[:-1]
    rets = rets[np.isfinite(rets)]
    sharpe = (rets.mean() / rets.std() * np.sqrt(365)) if len(rets) > 1 and rets.std() > 0 else 0.0
    calmar = cagr / mdd if mdd > 0 else 99.0
    bh = bench_close[-1] / bench_close[0]
    return {"label": label, "final": round(eq[-1], 3), "CAGR%": round(cagr, 1),
            "maxDD%": round(mdd, 1), "Sharpe": round(sharpe, 2), "Calmar": round(calmar, 2),
            "vs_BH": round(eq[-1] / bh, 2), "days": n}


def sim_bh(d):
    return (d["close"] / d["close"].iloc[0]).values


def sim_dca(d, monthly_usd=100.0, start_capital=10000.0):
    """每月投入固定金額 (由 cash 池), 其餘現金唔生息 (保守)."""
    dt = pd.to_datetime(d["date"])
    months = ((dt.dt.year - dt.dt.year.iloc[0]) * 12 + (dt.dt.month - dt.dt.month.iloc[0]))
    btc = 0.0
    cash = start_capital
    eq = []
    prev_m = -1
    for i in range(len(d)):
        m = int(months.iloc[i])
        if m != prev_m and cash >= monthly_usd:
            px = float(d["close"].iloc[i])
            btc += monthly_usd / px
            cash -= monthly_usd
            prev_m = m
        eq.append(btc * float(d["close"].iloc[i]) + cash)
    eq = np.array(eq) / start_capital
    return eq


def sim_rebalance(d, btc_pct=0.6, freq_days=90, fee=0.001):
    """定期再平衡 BTC/cash 比例到 target (收 fee)."""
    eq = []
    px0 = float(d["close"].iloc[0])
    btc = btc_pct / px0
    cash = 1.0 - btc_pct
    last = 0
    for i in range(len(d)):
        px = float(d["close"].iloc[i])
        if i - last >= freq_days:
            tot = btc * px + cash
            tgt_btc_val = tot * btc_pct
            cur_btc_val = btc * px
            diff = tgt_btc_val - cur_btc_val
            if abs(diff) > tot * 0.01:
                if diff > 0:
                    buy_val = min(diff, cash)
                    btc += buy_val * (1 - fee) / px
                    cash -= buy_val
                else:
                    sell_val = min(-diff, cur_btc_val)
                    btc -= sell_val / px
                    cash += sell_val * (1 - fee)
            last = i
        eq.append(btc * px + cash)
    return np.array(eq)


def sim_dip(d, dip_pct=10.0, chunk=0.1, start_capital=10000.0):
    """由高點跌 X% 就買入一份, 最多用完現金."""
    btc = 0.0
    cash = start_capital
    peak = 0.0
    armed = True
    eq = []
    for i in range(len(d)):
        px = float(d["close"].iloc[i])
        peak = max(peak, px)
        dd = (px - peak) / peak * 100
        if dd <= -dip_pct and armed and cash > 0:
            amt = min(cash, start_capital * chunk)
            btc += amt / px
            cash -= amt
            armed = False
        if dd > -dip_pct * 0.4:
            armed = True
        eq.append(btc * px + cash)
    return np.array(eq) / start_capital


def show(rows, title):
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")
    print(f"{'策略':<28} {'淨值':>9} {'CAGR%':>7} {'maxDD%':>8} {'Sharpe':>7} {'Calmar':>7} {'vs B&H':>7}")
    print("-" * 100)
    for r in rows:
        print(f"{r['label']:<28} {r['final']:>9.3f} {r['CAGR%']:>7.1f} {r['maxDD%']:>8.1f} "
              f"{r['Sharpe']:>7.2f} {r['Calmar']:>7.2f} {r['vs_BH']:>7.2f}")


def main():
    d = load_daily(6)
    print(f"數據: {len(d)} 日  {d['date'].iloc[0].date()} → {d['date'].iloc[-1].date()}")
    print(f"BTC: ${d['close'].iloc[0]:,.0f} → ${d['close'].iloc[-1]:,.0f}  "
          f"({(d['close'].iloc[-1]/d['close'].iloc[0]-1)*100:+.0f}%)")

    STRATS = [
        ("Buy&Hold", lambda x: sim_bh(x)),
        ("DCA $100/月", lambda x: sim_dca(x, 100)),
        ("DCA $200/月", lambda x: sim_dca(x, 200)),
        ("再平衡 60/40 每季", lambda x: sim_rebalance(x, 0.6, 90)),
        ("再平衡 80/20 每季", lambda x: sim_rebalance(x, 0.8, 90)),
        ("再平衡 60/40 每月", lambda x: sim_rebalance(x, 0.6, 30)),
        ("Dip-buy -10% ×10%", lambda x: sim_dip(x, 10, 0.1)),
        ("Dip-buy -20% ×25%", lambda x: sim_dip(x, 20, 0.25)),
    ]

    # 全期
    rows = [metrics(fn(d), d["close"].values, name) for name, fn in STRATS]
    show(rows, "全期 (6 年)")

    # 分 4 段
    n = len(d)
    for k in range(4):
        a, b = k * n // 4, (k + 1) * n // 4
        sub = d.iloc[a:b].reset_index(drop=True)
        rows = [metrics(fn(sub), sub["close"].values, name) for name, fn in STRATS]
        chg = (sub["close"].iloc[-1] / sub["close"].iloc[0] - 1) * 100
        show(rows, f"第 {k+1}/4 段  {sub['date'].iloc[0].date()} → {sub['date'].iloc[-1].date()} "
                   f"(BTC {chg:+.0f}%)")


if __name__ == "__main__":
    main()
