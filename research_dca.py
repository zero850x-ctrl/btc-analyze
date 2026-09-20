#!/usr/bin/env python3
"""research_dca.py — 「40% 現金分批入市」到底會發生咩事？(2026-09-19)

背景：60/40 再平衡 09-18 上線，14 個鐘之後組合 +2.21% vs B&H +3.26% —— 差 $930，
全部係 20:32 一次過賣 0.324 BTC @78,032（換現金）之後 BTC 升 3.7% 嘅機會成本。
用戶想「加 DCA，40% 現金分批入市，保留再平衡框架」。

⚠️ 呢條問題 research_premium.py 答唔到：佢個 sim_dca 係「由 0 開始每月存 $100」，
   起始 100% 現金、6 年都投唔完 → 條曲線幾乎係現金，唔可以同「已有倉位」比較。

本 script 答嘅係：喺**已經係 60/40** 嘅前提下，改 target / 加入分批，長期會點？
每條策略都用同一個起點 (target weight)、同一段數據、同一 fee。

關鍵張力（本 script 要量化）：
  「把 40% 現金投埋入去」= target 由 60% 移到 100% = 變返 B&H
  → CAGR 升、maxDD 升 —— 即係 60/40 唯一嘅優勢 (回撤細 ~20pp) 被自願放棄。
  所以真正嘅選擇唔係「DCA 有冇用」，而係「你想要邊個 target」。

用法: python3 research_dca.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

FEE = 0.001          # Binance spot taker 0.1%
YEARS = 11


def load_daily(years=YEARS):
    import yfinance as yf
    df = yf.Ticker("BTC-USD").history(period=f"{years}y", interval="1d")
    if df.empty:
        raise SystemExit("冇數據")
    df = df.reset_index()
    for a in ("Date", "Datetime"):
        if a in df.columns:
            df = df.rename(columns={a: "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    for col in ("close", "open", "high", "low"):
        for alt in (col.capitalize(), col.upper()):
            if alt in df.columns and col not in df.columns:
                df[col] = df[alt]
    return df[["date", "close"]].dropna().reset_index(drop=True)


def metrics(eq, bench, label):
    eq = np.asarray(eq, dtype=float)
    n = len(eq)
    yrs = n / 365.25
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100 if eq[-1] > 0 else -100.0
    peak = np.maximum.accumulate(eq)
    mdd = ((peak - eq) / peak).max() * 100
    rets = np.diff(eq) / eq[:-1]
    rets = rets[np.isfinite(rets)]
    sharpe = (rets.mean() / rets.std() * np.sqrt(365)) if len(rets) > 1 and rets.std() > 0 else 0.0
    bh = bench[-1] / bench[0]
    return {"label": label, "final": eq[-1], "CAGR%": cagr, "maxDD%": mdd,
            "Sharpe": sharpe, "Calmar": (cagr / mdd if mdd > 0 else 99.0),
            "vs_BH": eq[-1] / bh}


def sim_bh(d):
    return (d["close"] / d["close"].iloc[0]).values


def sim_weight(d, target=0.6, freq_days=90, band=0.05, spread_days=1, fee=FEE):
    """(重)平衡到 target，可選：偏離 band 即做、交易日分批 spread_days 日完成。

    spread_days=1 = 即時成交（現行 live 行為）。
    spread_days>1 = 由**下一個交易日**開始，每日執行總量嘅 1/n，按當日價成交。

    ⚠️ 呢個函數第一版有 bug（賣出嘅 BTC 被扣兩次 → 假 98% maxDD）。
       所以加咗 `_selfcheck()`：喺**常數價格**下，分批必須同一次過得出同一結果。
       改呢個函數之前，跑一跑 `python3 research_dca.py --selfcheck`。
    """
    px = np.asarray(d["close"], dtype=float)
    n = len(px)
    btc = target / px[0]
    cash = 1.0 - target
    eq = []
    last = 0
    plan = []                                  # [(執行日, 'b'/'s', usd)]
    for i in range(n):
        p = px[i]
        # 1. 執行今日到期嘅分批（按今日價成交）
        if plan:
            nxt = []
            for (day, side, usd) in plan:
                if i < day:
                    nxt.append((day, side, usd))
                    continue
                if side == "b":
                    amt = min(usd, cash)
                    if amt > 0 and p > 0:
                        btc += amt * (1 - fee) / p
                        cash -= amt
                else:
                    qty = min(usd / p, btc) if p > 0 else 0.0
                    if qty > 0:
                        btc -= qty
                        cash += qty * p * (1 - fee)
            plan = nxt
        # 2. 決定（唔喺已有計劃未完成時再開新計劃）
        tot = btc * p + cash
        if tot > 0 and not plan:
            drift = abs(btc * p / tot - target)
            if (i - last >= freq_days) or drift > band:
                diff = tot * target - btc * p
                if abs(diff) > tot * 0.01:
                    if spread_days <= 1:
                        if diff > 0:
                            buy = min(diff, cash)
                            btc += buy * (1 - fee) / p
                            cash -= buy
                        else:
                            sell = min(-diff, btc * p)
                            btc -= sell / p
                            cash += sell * (1 - fee)
                    elif diff > 0:
                        buy = min(diff, cash)
                        plan = [(i + 1 + k, "b", buy / spread_days) for k in range(spread_days)]
                    else:
                        sell = min(-diff, btc * p)
                        plan = [(i + 1 + k, "s", sell / spread_days) for k in range(spread_days)]
                last = i
        eq.append(btc * p + cash)
    return np.array(eq)


def _selfcheck():
    """想驗嘅唔變式（唔靠價格路徑）。

    ① 常數價格下，分批 = 一次過（唯一乾淨嘅等價測試）—— 會捉到重複扣減、
       漏 fee、plan 未執行等會計 bug。第一版就係喺呢度 fail。
    ② 唔可以出現負現金／負 BTC（捉 sign error）。
    ③ 同一價格序列下，lump 同 spread 嘅成交量應該同一數量級（唔可以差天共地）。

    ⚠️ 刻意**唔**斷言「分批一定要差過／好過一次過」：分批嘅成本係中間嘅市場暴露，
       方向取決於價格路徑（升市分批賣 = 賣得更高 = 更好）。呢個係我自己推理錯過嘅位。
    """
    import pandas as pd

    for spread in (1, 2, 5, 20):
        d = pd.DataFrame({"close": np.full(400, 100.0)})
        a = sim_weight(d, 0.6, 90, 0.05, 1)[-1]
        b = sim_weight(d, 0.6, 90, 0.05, spread)[-1]
        ok = abs(a - b) < 1e-9
        print(f"  ① spread={spread:<3} 一次過={a:.6f} 分批={b:.6f}  {'✅' if ok else '❌ 唔等價'}")
        if not ok:
            raise SystemExit(f"selfcheck ① FAIL: spread={spread}")

    for name, path in (("升市", np.linspace(100.0, 300.0, 400)),
                       ("跌市", np.linspace(300.0, 100.0, 400)),
                       ("V 型", np.concatenate([np.linspace(200, 60, 200),
                                               np.linspace(60, 200, 200)]))):
        d = pd.DataFrame({"close": path})
        for spread in (1, 20):
            eq = sim_weight(d, 0.6, 30, 0.05, spread)
            bad = (~np.isfinite(eq)).sum() + (eq <= 0).sum()
            if bad:
                raise SystemExit(f"selfcheck ② FAIL: {name} spread={spread} 有 {bad} 個壞值")
        print(f"  ② {name:<4} 淨值恆正／有限  ✅")
    print("  selfcheck 全過")


if __name__ == "__main__" and "--selfcheck" in sys.argv:
    _selfcheck()
    raise SystemExit(0)


def sim_glide(d, start_w=0.6, end_w=0.8, glide_days=180, freq_days=30, band=0.05, fee=FEE):
    """target 由 start_w 線性移到 end_w（glide_days 日），之後固定 end_w。

    保留再平衡框架（所以仍然有回撤保護），但慢慢減現金拖累。
    """
    eq = []
    px0 = float(d["close"].iloc[0])
    btc = start_w / px0
    cash = 1.0 - start_w
    last = 0
    for i in range(len(d)):
        px = float(d["close"].iloc[i])
        prog = min(1.0, i / glide_days) if glide_days > 0 else 1.0
        target = start_w + (end_w - start_w) * prog
        tot = btc * px + cash
        want = (i - last >= freq_days) or (abs(btc * px / tot - target) > band if tot > 0 else False)
        if want and tot > 0:
            diff = tot * target - btc * px
            if abs(diff) > tot * 0.01:
                if diff > 0:
                    buy = min(diff, cash)
                    btc += buy * (1 - fee) / px
                    cash -= buy
                else:
                    sell = min(-diff, btc * px)
                    btc -= sell / px
                    cash += sell * (1 - fee)
            last = i
        eq.append(btc * px + cash)
    return np.array(eq)


def show(rows, title):
    print(f"\n{'=' * 104}\n{title}\n{'=' * 104}")
    print(f"{'策略':<34} {'淨值':>9} {'CAGR%':>7} {'maxDD%':>8} {'Sharpe':>7} {'Calmar':>7} {'vs B&H':>7}")
    print("-" * 104)
    for r in rows:
        print(f"{r['label']:<34} {r['final']:>9.3f} {r['CAGR%']:>7.1f} {r['maxDD%']:>8.1f} "
              f"{r['Sharpe']:>7.2f} {r['Calmar']:>7.2f} {r['vs_BH']:>7.2f}")


STRATS = [
    ("B&H (100/0)", lambda x: sim_bh(x)),
    ("60/40 每季", lambda x: sim_weight(x, 0.6, 90, 0.0, 1)),
    ("60/40 每季+band5% (live)", lambda x: sim_weight(x, 0.6, 90, 0.05, 1)),
    ("60/40 每季+band5% 分批5日", lambda x: sim_weight(x, 0.6, 90, 0.05, 5)),
    ("60/40 每季+band5% 分批20日", lambda x: sim_weight(x, 0.6, 90, 0.05, 20)),
    ("70/30 每季+band5%", lambda x: sim_weight(x, 0.7, 90, 0.05, 1)),
    ("80/20 每季+band5%", lambda x: sim_weight(x, 0.8, 90, 0.05, 1)),
    ("90/10 每季+band5%", lambda x: sim_weight(x, 0.9, 90, 0.05, 1)),
    ("glide 60→80 (180日)", lambda x: sim_glide(x, 0.6, 0.8, 180)),
    ("glide 60→90 (180日)", lambda x: sim_glide(x, 0.6, 0.9, 180)),
    ("glide 60→100 (180日)", lambda x: sim_glide(x, 0.6, 1.0, 180)),
]


def main():
    d = load_daily()
    print(f"數據: {len(d)} 日  {d['date'].iloc[0].date()} → {d['date'].iloc[-1].date()}")
    print(f"BTC: ${d['close'].iloc[0]:,.0f} → ${d['close'].iloc[-1]:,.0f}  "
          f"({(d['close'].iloc[-1] / d['close'].iloc[0] - 1) * 100:+.0f}%)")

    show([metrics(fn(d), d["close"].values, n) for n, fn in STRATS], f"全期 ({YEARS} 年)")

    n = len(d)
    for k in range(5):
        a, b = k * n // 5, (k + 1) * n // 5
        sub = d.iloc[a:b].reset_index(drop=True)
        chg = (sub["close"].iloc[-1] / sub["close"].iloc[0] - 1) * 100
        show([metrics(fn(sub), sub["close"].values, nm) for nm, fn in STRATS],
             f"第 {k+1}/5 段  {sub['date'].iloc[0].date()} → {sub['date'].iloc[-1].date()} "
             f"(BTC {chg:+.0f}%)")


if __name__ == "__main__":
    main()
