#!/usr/bin/env python3
"""research_funding_carry.py — C: 期現基差 carry (delta-neutral) 到底值幾多? (2026-09-20)

⚠️ 先分清兩件完全唔同嘅嘢 —— 呢個 script 做嘅係第二件:
   (1) funding 當**方向信號** (「funding 極高 → 之後跌?」)
       → 2026-09-18 已測, IC ≈ -0.02 = 零預測力, 已否證。合理: funding 係市場
         microstructure 狀態, 唔係未來價格資訊。
   (2) funding 當**收益 (carry)**: long spot + short 永續, 價格對沖, 淨賺 funding。
       → **唔需要預測方向**, 只需要 funding 長期為正。本 script 量化呢個。
   (2) 之前係被排除 (research_funding.py docstring: 涉及 futures, 唔喺範圍),
       **唔係測完失敗**。呢個區分係關鍵。

本 script 答:
   a. 歷史上 funding 平均幾多 (年化), 正嘅比例幾高
   b. 資金效率: 對沖要佔抵押品 → 實際回報遠低於 funding APR (呢點最常被吹大)
   c. 淨手續費之後仲剩幾多, 打和要坐幾久
   d. 持有曲線嘅 maxDD (funding 轉負嘅時期)
   e. 一個簡單規則 (funding 連負就平倉) 有冇幫助
   f. 可行性: 冇 futures 就完全做唔到

⚠️ 本 script **唔會**模擬: 基差收斂/發散、爆倉、保證金追收、交易所風險、
   借幣成本。呢啲全部係真實風險, 唔係細節 —— 所以輸出要當「上限」睇, 唔係預期。

用法: python3 research_funding_carry.py
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd

CACHE = os.path.expanduser("~/.hermes/reports/btc_funding_history_full.json")
SPOT_TAKER = 0.001        # Binance spot taker 0.1% (同整個研究一致)
PERP_TAKER = 0.0005       # Binance USDT-M perp taker 0.05%
BOTH_LEGS_ONCE = 2 * (SPOT_TAKER + PERP_TAKER)   # 入+出一對腳 = 0.3%


def fetch_funding(start_ms, pages=60):
    """Binance fapi fundingRate — 由舊到新分頁 (公開數據, 唔需要 key)。"""
    out = []
    start = start_ms
    for p in range(pages):
        url = (f"https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT"
               f"&limit=1000&startTime={start}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
        if not d:
            break
        out += d
        last = d[-1]["fundingTime"]
        if last <= start:
            break
        start = last + 1
        time.sleep(0.2)
        if len(d) < 1000:
            break
    return out


def load_funding():
    if os.path.exists(CACHE):
        raw = json.load(open(CACHE))
    else:
        print("拎 funding history (BTCUSDT 永續 由 2019-09 開始, 公開數據)...")
        # 2019-09-01
        raw = fetch_funding(int(pd.Timestamp("2019-09-01").timestamp() * 1000))
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(raw, open(CACHE, "w"))
    df = pd.DataFrame([{"ts": x["fundingTime"], "rate": float(x["fundingRate"])}
                       for x in raw])
    df["dt"] = pd.to_datetime(df["ts"], unit="ms").dt.tz_localize(None)
    return df.drop_duplicates("dt").sort_values("dt").reset_index(drop=True)


def max_neg_streak(rates):
    """最長連續負 funding 嘅區間數 + 日數。"""
    best = cur = 0
    for x in rates:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best, best / 3.0


def carry_curve(rates, entry_fee=BOTH_LEGS_ONCE):
    """Delta-neutral: 每個 interval 收 rate (short perp 收正 funding)。

    回傳 (equity, 入場手續費已扣)。equity=1 起。
    """
    eq = np.cumprod(1.0 + np.asarray(rates, dtype=float))
    eq = eq * (1.0 - entry_fee)
    return eq


def maxdd(eq):
    peak = np.maximum.accumulate(eq)
    return float(((peak - eq) / peak).max() * 100)


def main():
    f = load_funding()
    n = len(f)
    span_days = (f["dt"].iloc[-1] - f["dt"].iloc[0]).total_seconds() / 86400
    yrs = span_days / 365.25
    r = f["rate"].values
    rp = r * 100

    print(f"funding 數據: {n} 筆  {f['dt'].iloc[0]} → {f['dt'].iloc[-1]}  "
          f"({yrs:.2f} 年, 理論 {int(span_days*3)} 筆)")
    print(f"每 8h funding%: mean={rp.mean():+.4f} median={np.median(rp):+.4f} "
          f"std={rp.std():.4f}")
    print(f"  p5={np.percentile(rp,5):+.4f} p25={np.percentile(rp,25):+.4f} "
          f"p75={np.percentile(rp,75):+.4f} p95={np.percentile(rp,95):+.4f}")
    print(f"  最小 {rp.min():+.4f}%  最大 {rp.max():+.4f}%")
    print(f"正 funding 比例: {(r > 0).mean()*100:.1f}%   "
          f"funding APR (mean×3×365): {rp.mean()*3*365:+.2f}%")

    st, sd = max_neg_streak(r)
    print(f"最長連續負 funding: {st} 個 interval ({sd:.1f} 日)")

    # ---- 按年 ----
    print(f"\n{'年':<6} {'筆數':>6} {'mean 8h%':>10} {'APR%':>8} {'正%':>7} {'最長負(日)':>11}")
    print("-" * 56)
    f["year"] = f["dt"].dt.year
    for y, g in f.groupby("year"):
        rr = g["rate"].values
        _, dd = max_neg_streak(rr)
        print(f"{y:<6} {len(rr):>6} {rr.mean()*100:>+10.4f} "
              f"{rr.mean()*3*365*100:>+8.2f} {(rr>0).mean()*100:>6.1f}% {dd:>11.1f}")

    # ---- 資金效率: 呢個先係最常被吹大嘅地方 ----
    # 對沖 $1 notional 需要: $1 spot + margin 做 $1 perp short。
    # margin 比例 m ∈ [0.2, 0.5] (視乎槓桿)。所以總資本 = 1 + m。
    print(f"\n{'=' * 78}")
    print("資金效率 — funding APR 係「按 notional 計」, 唔係「按出資計」")
    print('=' * 78)
    apr = rp.mean() * 3 * 365
    print(f"  funding APR (按 notional)                 : {apr:+.2f}%")
    for m in (0.2, 0.33, 0.5, 1.0):
        roc = apr / (1 + m)
        print(f"  按出資計 (spot 1.0 + perp margin {m:.2f}) : {roc:+.2f}%"
              f"   ← 總資本 {1+m:.2f}× notional")

    # ---- 持有曲線 + maxDD ----
    print(f"\n{'=' * 78}")
    print("持有曲線 (delta-neutral, 複利, 已扣入場手續費 {:.1%})".format(BOTH_LEGS_ONCE))
    print('=' * 78)
    eq = carry_curve(r)
    tot = (eq[-1] - 1) * 100
    cagr = ((eq[-1]) ** (1 / yrs) - 1) * 100
    print(f"  累積 {tot:+.1f}%   年化 {cagr:+.2f}%   maxDD {maxdd(eq):.2f}%")
    print(f"  打和 (手續費 {BOTH_LEGS_ONCE:.1%}): "
          f"{BOTH_LEGS_ONCE / (apr/100/365):.1f} 日" if apr > 0 else "  打和: 冇 (APR ≤ 0)")

    # ---- 簡單規則: funding 連負 N 個 interval 就平倉, 轉正返入場 ----
    print(f"\n{'=' * 78}")
    print("規則測試 — funding 連負 N 個 interval 就離場 (避開負 carry 期), 轉正即入返")
    print('=' * 78)
    print(f"{'N':>3} {'在場比例':>9} {'累積%':>9} {'年化%':>8} {'maxDD%':>8} {'vs 長坐':>9}")
    print("-" * 60)
    base = carry_curve(r)[-1]
    for N in (3, 6, 12, 24, 48):
        pos = np.zeros(len(r), dtype=bool)
        neg = 0
        hold = False
        for i, x in enumerate(r):
            if hold:
                if x < 0:
                    neg += 1
                    if neg >= N:
                        hold = False
                        neg = 0
                else:
                    neg = 0
            else:
                if x > 0:
                    hold = True
                    neg = 0
            pos[i] = hold
        # 只在在場時收 funding; 每次進出付 0.3%
        # ⚠️ 第一版呢度有 bug: maxDD 傳咗 [1.0, curve] 兩個元素嘅 array → 永遠 0.00 (假數字)。
        #    必須逐 interval 儲起 equity 再算 maxDD。
        eqs = np.empty(len(r))
        curve = 1.0
        prev = False
        for i in range(len(r)):
            if pos[i] and not prev:
                curve *= (1 - BOTH_LEGS_ONCE)
            if pos[i]:
                curve *= (1 + r[i])
            eqs[i] = curve
            prev = pos[i]
        ndays = span_days
        c = (curve ** (1 / yrs) - 1) * 100
        print(f"{N:>3} {pos.mean()*100:>8.1f}% {(curve-1)*100:>+9.1f} {c:>+8.2f} "
              f"{maxdd(eqs):>8.2f} {curve/base:>+9.3f}")

    # ---- 可行性 ----
    print(f"\n{'=' * 78}")
    print("可行性")
    print('=' * 78)
    print("  呢個策略**必須**有 USDT-M 永續 (futures) 先做得到。純 spot 做唔到:")
    print("   - funding 係永續合約機制, spot 冇")
    print("   - 冇一個 spot-only 產品等價 (staking/lending 係另一個風險溢價, 唔係呢個)")
    print(f"  舊約束 (research_funding.py, 2026-09-18): 香港地域 + 只做 spot testnet → 已排除")
    print("  所以要行 C, 第一步係確認帳戶/地區可否合法交易 perp —— 呢個係前提, 唔係細節。")

    print(f"\n⚠️ 本 script 未模擬 (全部都係真風險, 唔係細節):")
    print("   - 基差收斂/發散 (mark price 郁 → 對沖唔完美)")
    print("   - 保證金追收 / 爆倉 (short perp 遇急升)")
    print("   - 交易所對手風險 (資金長期放喺 perp)")
    print("   - 出入場滑價、資金費率之外嘅交易成本")
    print("   ⇒ 上面數字係**上限**, 唔係預期回報。")


if __name__ == "__main__":
    main()
