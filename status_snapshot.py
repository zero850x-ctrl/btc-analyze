#!/usr/bin/env python3
"""status_snapshot.py — 一次性成績快照 (診斷用, 唔係 production)."""
import json
import os

H = os.path.expanduser("~/.hermes/reports")
px = 84342.01
START = {"btc": 1.00061, "usdt": 9951.02, "px": 78032.37}   # 09-18 20:32 再平衡上線時

start_v = START["btc"] * START["px"] + START["usdt"]
act_v = 0.538930 * px + 46859.90
print("=== 再平衡 60/40 (09-18 上線, 6 日) ===")
print("  起始: ${:,.0f}  (1.00061 BTC / 9,951 USDT)".format(start_v))
print("  現時: ${:,.0f}  (0.53893 BTC / 46,860 USDT)".format(act_v))
r = (act_v / start_v - 1) * 100
bh = (px / START["px"] - 1) * 100
print("  組合回報: {:+.2f}%".format(r))
print("  B&H 對照: {:+.2f}%   差距 {:+.2f}pp".format(bh, r - bh))

d = json.load(open(os.path.join(H, "btc_martingale_log.json")))
dl = d["daily"]
w = sum(v.get("wins", 0) for v in dl.values())
l = sum(v.get("losses", 0) for v in dl.values())
loss = sum(v.get("loss_usd", 0) for v in dl.values())
print("\n=== 馬丁格爾 ===")
print("  {}W/{}L = {} 單   蝕日合計 ${:.2f}".format(w, l, w + l, loss))
print("  贏單估算 +${:.2f}   淨估算 ${:+.2f}".format(w * 0.2, w * 0.2 - loss))
a = d.get("active")
if a:
    q = sum(float(e.get("qty") or 0) for e in a.get("entries", []))
    print("  當前 {} {} level {} ({}注) qty={:.6f}".format(
        a["id"], a["side"], a["level"], len(a.get("entries", [])), q))

t = json.load(open(os.path.join(H, "btc_testnet_closed_trades.json"))).get("trades", [])
print("\n=== 主系統 ===")
print("  {} 單, sumR {:+.2f}, PnL ${:+.2f}".format(
    len(t), sum((x.get("r_multiple") or 0) for x in t),
    sum((x.get("pnl_usdt") or 0) for x in t)))
new = [x for x in t if (x.get("closed_time") or "")[:10] >= "2026-09-20"]
print("  09-20 重開後: {} 單".format(len(new)))

led = json.load(open(os.path.join(H, "btc_rebalance_ledger.json")))
print("\n=== 帳本 ===\n  建立 {}  交易 {} 筆".format(
    led["created"][:16], len(led.get("trades", []))))
