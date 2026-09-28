#!/usr/bin/env python3
"""martingale_cap_analysis.py — 深入查「4 注 cap」嗰層

背景 (2026-09-28, 用戶要求):
  馬丁 28 chain 淨 +$0.55, 但 7 筆 LOSS **全部**都係 4 注 chain (0 勝)。
  用戶要求查呢層。

## 關鍵發現 (code + log 對證)

btc_martingale.tick() 嘅分支:

    if net >= WIN_TARGET_USD:        → WIN
    elif chain["level"] >= MAX_LEVEL: → CAP LOSS  ← 冇價格條件!
    else:                             → ADD-ON (需 1×ATR)

即係 **一旦到 level 3 (4 注), 下一個 tick 就無條件平倉** (除非剛好夠 target)。
Cap **唔係停損**, 係一個 **計時器**。log 實證: 7/7 筆都係 note4 後 **剛好 15 分鐘**平倉。

而 ADD-ON 需要 1×ATR 逆向 → cap 同 add-on **設計唔一致**。

## 模擬

用 mainnet 15m klines (testnet 只保留 ~09-10 之後; 兩者價差 0.07%)。
tick 價 = 每個 15 分鐘 K 線嘅 **開盤價** (bot 每 15 分鐘 tick 一次讀市價)。

替代規則:
  CURRENT : note4 後下一個 tick 平倉 (現行)
  ATR_x   : 持有到 net>=target (WIN) 或 逆向 >= x×ATR (真停損)

用法: python3 martingale_cap_analysis.py
"""
import datetime
import importlib.util
import json
import os
import sys
import urllib.request

MART = os.path.expanduser("~/repos/btc-martingale/btc_martingale.py")
spec = importlib.util.spec_from_file_location("m", MART)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

TARGET = m.WIN_TARGET_USD
MAX_HOLD_TICKS = 96          # 24 小時後強制平倉


def mainnet(interval, start, end, limit=500):
    """mainnet public klines (免 auth) — testnet 冇舊歷史, 但價差 0.07%."""
    u = (f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT"
         f"&interval={interval}&startTime={start}&endTime={end}&limit={limit}")
    with urllib.request.urlopen(u, timeout=25) as r:
        return json.load(r)


def k15(start_ms, end_ms, limit=500):
    return mainnet("15m", start_ms, end_ms, limit)


def atr_from(bars):
    hg = [float(k[2]) for k in bars]
    lw = [float(k[3]) for k in bars]
    cl = [float(k[4]) for k in bars]
    return m._atr(hg, lw, cl, 14)


def ts_ms(iso):
    return int(datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def pnl_at(chain, px, upto=None):
    e = chain["entries"] if upto is None else chain["entries"][:upto + 1]
    q = sum(x["qty"] for x in e)
    avg = sum(x["qty"] * x["px"] for x in e) / q
    return (((px - avg) if chain["side"] == "BUY" else (avg - px)) * q), q, avg


def sim_rule(chain, bars_by_ts, note4_ts, atr, mult):
    """由 note4 之後第一個 tick 開始, 持有到 WIN 或 逆向 >= mult×ATR."""
    d = 1 if chain["side"] == "BUY" else -1
    last = chain["entries"][-1]["px"]
    ticks = sorted(t for t in bars_by_ts if t > note4_ts)[:MAX_HOLD_TICKS]
    for t in ticks:
        px = bars_by_ts[t]                     # tick 價 = K 線開盤
        net, q, avg = pnl_at(chain, px)
        adv = -d * (px - last)                 # 逆向量 (>0 = 蝕)
        if net >= TARGET:
            return px, net, "WIN", t
        if adv >= mult * atr:
            return px, net, "STOP", t
    if ticks:
        px = bars_by_ts[ticks[-1]]
        net, q, avg = pnl_at(chain, px)
        return px, net, "TIMEOUT", ticks[-1]
    return None, None, "NODATA", None


def main():
    log = json.load(open(os.path.expanduser(
        "~/.hermes/reports/btc_martingale_log.json")))
    ch = [c for c in log["chains"] if c.get("state") in ("WIN", "LOSS")]
    L = [c for c in ch if c.get("state") == "LOSS"]
    other = [c for c in ch if c.get("state") != "LOSS"]

    print(f"=== 4 注 cap 深入分析 ===")
    print(f"LOSS chain (全部 4 注): {len(L)}   其他 (1-3 注, 全部勝): {len(other)}")
    print(f"WIN target ${TARGET}  MAX_LEVEL={m.MAX_LEVEL}  最大持有 = {MAX_HOLD_TICKS} ticks (24h)\n")

    print("--- 驗證: tick 價 = K 線開盤? ---")
    for c in L:
        t4 = ts_ms(c["entries"][3]["time"]) // 900000 * 900000      # 對齊 15m 邊界
        tc = ts_ms(c["closed_time"]) // 900000 * 900000
        o4 = next((float(k[1]) for k in k15(t4 - 3600 * 1000, t4 + 3600 * 1000) if k[0] == t4), None)
        oc = next((float(k[1]) for k in k15(tc - 3600 * 1000, tc + 3600 * 1000) if k[0] == tc), None)
        f = lambda v: "None" if v is None else f"{v:,.0f}"
        print(f"  {c['id']} note4: K線開盤={f(o4)} log={c['entries'][3]['px']:,.0f} "
              f"(差 {0 if o4 is None else o4 - c['entries'][3]['px']:+,.0f}) | "
              f"平倉: K線開盤={f(oc)} log={c['closed_px']:,.0f} "
              f"(差 {0 if oc is None else oc - c['closed_px']:+,.0f})")

    print("\n--- 逐 chain 模擬 ---")
    results = {}
    for mult, label in ((0.5, "ATR_0.5"), (1.0, "ATR_1.0"),
                        (1.5, "ATR_1.5"), (2.0, "ATR_2.0")):
        results[label] = []

    total_cur = 0.0
    for c in L:
        t4 = ts_ms(c["entries"][3]["time"])
        bars = k15(t4 - 6 * 3600 * 1000, t4 + (MAX_HOLD_TICKS + 8) * 900 * 1000, limit=500)
        by_ts = {k[0]: float(k[1]) for k in bars}
        hist = [k for k in bars if k[0] < t4]        # 只用已收市 K 線 (k[0]==t4 係進行中)
        atr = atr_from(hist) if len(hist) >= 16 else 0.0

        cur, _, _ = pnl_at(c, c["closed_px"])
        total_cur += cur
        print(f"\n{c['id']} {c['side']}  ATR={atr:,.0f}  現行 {cur:+.2f}")

        for mult, label in ((0.5, "ATR_0.5"), (1.0, "ATR_1.0"),
                            (1.5, "ATR_1.5"), (2.0, "ATR_2.0")):
            px, net, why, t = sim_rule(c, by_ts, t4, atr, mult)
            if px is None:
                continue
            results[label].append(net)
            ts = datetime.datetime.fromtimestamp(t / 1000, datetime.timezone.utc).strftime("%H:%M")
            hold = (t - t4) // 900000
            print(f"    {label:<8} {why:<7} net={net:+7.2f} @{px:,.0f} ({ts}, 持有 {hold} ticks)")

    print(f"\n--- 合計 (7 筆 LOSS chain) ---")
    print(f"  現行 (cap = 下一個 tick)          {total_cur:+8.2f}")
    for label in ("ATR_0.5", "ATR_1.0", "ATR_1.5", "ATR_2.0"):
        v = results[label]
        print(f"  {label:<8} (真 {label[4:]} 停損)          {sum(v):+8.2f}   "
              f"改善 {sum(v) - total_cur:+.2f}  (n={len(v)})")

    print(f"\n--- 對整體嘅影響 ---")
    oth = sum(pnl_at(c, c["closed_px"])[0] for c in other)
    print(f"  21 筆其他 chain (不受影響)        {oth:+8.2f}")
    print(f"  現行總淨                          {oth + total_cur:+8.2f}")

    # 敏感度: 更多 multiplier
    print(f"\n--- 敏感度: 停損 = x×ATR (逐筆改善) ---")
    imp = []
    for mult in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0):
        tag = f"ATR_{mult}"
        if tag not in results:
            results[tag] = []
            for c in L:
                t4 = ts_ms(c["entries"][3]["time"])
                bars = k15(t4 - 6 * 3600 * 1000, t4 + (MAX_HOLD_TICKS + 8) * 900 * 1000, limit=500)
                by_ts = {k[0]: float(k[1]) for k in bars}
                hist = [k for k in bars if k[0] < t4]
                atr = atr_from(hist) if len(hist) >= 16 else 0.0
                px, net, why, t = sim_rule(c, by_ts, t4, atr, mult)
                if px is not None:
                    results[tag].append(net)
        d = sum(results[tag]) - total_cur
        imp.append(d)
        print(f"  x={mult:<5} 總淨 {oth + sum(results[tag]):+8.2f}   7筆改善 {d:+6.2f}")

    # 顯著性: 逐筆改善量做單樣本 t 檢定
    print(f"\n--- 顯著性 (逐筆改善, n=7) ---")
    import math
    for tag in ("ATR_0.5", "ATR_1.0", "ATR_1.5"):
        diffs = []
        for i, c in enumerate(L):
            t4 = ts_ms(c["entries"][3]["time"])
            bars = k15(t4 - 6 * 3600 * 1000, t4 + (MAX_HOLD_TICKS + 8) * 900 * 1000, limit=500)
            by_ts = {k[0]: float(k[1]) for k in bars}
            hist = [k for k in bars if k[0] < t4]
            atr = atr_from(hist) if len(hist) >= 16 else 0.0
            mult = float(tag.split("_")[1])
            px, net, why, t = sim_rule(c, by_ts, t4, atr, mult)
            cur, _, _ = pnl_at(c, c["closed_px"])
            diffs.append(net - cur)
        n = len(diffs)
        mu = sum(diffs) / n
        sd = math.sqrt(sum((x - mu) ** 2 for x in diffs) / (n - 1))
        tt = mu / (sd / math.sqrt(n)) if sd else 0.0
        print(f"  {tag:<8} 平均改善 {mu:+.3f}  sd {sd:.3f}  t {tt:+.2f}  "
              f"{'✅ 過 2.45' if abs(tt) > 2.45 else '唔顯著 (門檻 2.45, df=6)'}")

    print("\n⚠️ 嚴重限制:")
    print("  1. 反事實只用 mainnet 價 (同 testnet 差 0.07%); 冇模擬平倉滑價")
    print("  2. 冇模擬「chain 早啲平倉 → 單槽空出 → 新 chain 提早開始」序列效應")
    print("  3. n=7 → 任何『揀最好 multiplier』都係 in-sample overfit (同一批數據)")
    print("  4. 我只知『呢 7 筆會點』, 唔知喺其他時段/市況會點")


if __name__ == "__main__":
    main()
