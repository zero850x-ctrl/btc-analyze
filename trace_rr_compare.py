#!/usr/bin/env python3
"""trace_rr_compare.py — 對比舊 code (zone 下緣 entry) vs 新 code (limit 價 entry) 嘅 RR."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
import numpy as np
import btc_engine as be

data = be.fetch_btc_data()
spec = importlib.util.spec_from_file_location(
    "av3", os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyze_v3.py"))
av3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(av3)

df = av3.add_indicators(data["m30"].copy())
atr = float(df["ATR"].iloc[-1])
px = float(df["Close"].iloc[-1])
ma50 = float(df["MA50"].iloc[-1])
pts = av3.find_swings_ordered(df["High"].values, df["Low"].values, lookback=3,
                              atr=df["ATR"].values, close=df["Close"].values)
pat = av3.detect_all_patterns(df, pts, atr=atr)
d = av3.analyze_daily_trend(data["day"])
h = av3.analyze_h1_trend(data["h1"])
raw = av3.generate_trade_setups(df, pat, pts, d, px, atr, h1_trend=h)

print(f"px={px:,.0f} ATR={atr:,.0f} MIN_RR={be.MIN_RR} SL_FLOOR_ATR_MULT={be.SL_FLOOR_ATR_MULT}")
print(f"SL floor 距離 = {be.SL_FLOOR_ATR_MULT * atr:,.0f}\n")

for s in raw:
    side = "SELL" if "SELL" in str(s.get("direction", "")) else "BUY"
    zone_low = be._parse_setup_level(s.get("entry_zone"))
    zone_hi_nums = [float(x.replace(",", "")) for x in be._LEVEL_NUM_RE.findall(
        str(s.get("entry_zone") or ""))]
    zone_hi = max(zone_hi_nums) if zone_hi_nums else zone_low
    lim = be.parse_entry_limit(s.get("entry_trigger"), s.get("entry_zone"))
    sl = be._parse_setup_level(s.get("stop_loss"))
    tp1 = be._parse_setup_level(s.get("tp1"))
    tp2 = be._parse_setup_level(s.get("tp2"))
    print(f"{side} {s.get('pattern')}")
    print(f"  zone={s.get('entry_zone')}  trig={s.get('entry_trigger')}")
    print(f"  zone 下緣={zone_low:,.0f}  zone 上緣={zone_hi:,.0f}  limit(新)={lim:,.0f}")
    print(f"  SL={sl:,.0f} TP1={tp1:,.0f} TP2={tp2 if tp2 is None else format(tp2, ',.0f')}")

    for label, entry in (("舊 code: zone 下緣", zone_low), ("新 code: limit 價", lim)):
        if entry is None or sl is None or tp1 is None:
            print(f"  {label}: 缺欄位")
            continue
        risk = abs(entry - sl)
        floor = be.SL_FLOOR_ATR_MULT * atr
        floored = risk < floor
        if floored:
            sl_eff = entry + floor if side == "SELL" else entry - floor
            risk = floor
        else:
            sl_eff = sl
        rr1 = abs(tp1 - entry) / risk
        line = f"  {label}: entry={entry:,.0f} risk={risk:,.0f} RR(TP1)={rr1:.2f}"
        if floored:
            line += f"  [SL floor 拉闊: {sl:,.0f}→{sl_eff:,.0f}]"
        print(line)
        if tp2:
            blend = (abs(tp1 - entry) / 3 + abs(tp2 - entry) * 2 / 3) / risk
            print(f"      3 段 blended RR (1/3 TP1 + 2/3 TP2) = {blend:.2f}"
                  f"{'  ✅過' if blend >= be.MIN_RR else '  ❌唔過'}")
    print()
