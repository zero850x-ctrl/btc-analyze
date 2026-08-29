#!/usr/bin/env python3
"""backfill_oco_legs.py — 為舊 OCO_PLACED 紀錄補回 leg orderIds (one-off)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from binance_testnet_paper import _load_keys, _signed_request, load_log, save_log, SYMBOL, LOT_STEP


def main():
    key, secret = _load_keys()
    opens = _signed_request("GET", "/api/v3/openOrders", {"symbol": SYMBOL}, key, secret)
    log = load_log()
    fixed = 0
    for rec in log["orders"]:
        if rec.get("status") != "OCO_PLACED" or rec.get("oco_leg_ids"):
            continue
        exit_qty = int(rec["qty"] / LOT_STEP) * LOT_STEP
        legs = {}
        for o in opens:
            if o["side"] != "BUY" or abs(float(o["origQty"]) - exit_qty) > 1e-9:
                continue
            if o["type"] == "STOP_LOSS_LIMIT" and abs(float(o["stopPrice"]) - rec["planned_stop"]) < 0.01:
                legs["sl"] = o["orderId"]
            elif o["type"] == "LIMIT_MAKER" and abs(float(o["price"]) - round(rec["planned_tp1"] * 1.001, 2)) < 0.01:
                legs["tp"] = o["orderId"]
        if "sl" in legs and "tp" in legs:
            rec["oco_leg_ids"] = [legs["sl"], legs["tp"]]
            fixed += 1
            print(f"✅ backfill {rec['pattern']} qty={rec['qty']} legs={rec['oco_leg_ids']}")
        else:
            print(f"⚠️ {rec['pattern']} 只搵到 {legs}")
    save_log(log)
    print(f"done: {fixed} fixed")


if __name__ == "__main__":
    main()
