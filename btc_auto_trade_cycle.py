#!/usr/bin/env python3
"""btc_auto_trade_cycle.py — 週末 auto-trade 一個完整循環 (5min cron 入口)

1. reconcile 上次 OCO 成交 → 計 R → close log
2. btc_engine.py 重新掃描 (真 BTC 數據)
3. 落新單 (dedup + 風控)
4. 心跳 log
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPO = "/tmp/btc-analyze"
sys.path.insert(0, REPO)
LOG_PATH = os.path.expanduser("~/.hermes/reports/btc_testnet_orders.json")
HEARTBEAT = os.path.expanduser("~/.hermes/reports/btc_auto_trade_heartbeat.txt")
HISTORY = os.path.expanduser("~/.hermes/reports/btc_testnet_closed_trades.json")


def sh(cmd):
    # 用同一個 interpreter (cron 環境 python3 可能冇 numpy/yfinance)
    cmd = cmd.replace("python3 ", f'"{sys.executable}" ', 1)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=REPO, timeout=280)
    return (r.stdout + r.stderr).strip()


def log(msg):
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | {msg}"
    print(line)
    os.makedirs(os.path.dirname(HEARTBEAT), exist_ok=True)
    with open(HEARTBEAT, "a") as f:
        f.write(line + "\n")
    # heartbeat file 只保留最後 500 行
    with open(HEARTBEAT) as f:
        lines = f.readlines()
    if len(lines) > 500:
        with open(HEARTBEAT, "w") as f:
            f.writelines(lines[-500:])


def reconcile_cycle(key, secret):
    """OCO legs 消失 = 其中一腿成交 → 對 myTrades 計入場/出場/fee → 計 R."""
    from binance_testnet_paper import _signed_request, load_log, save_log

    log_d = load_log()
    changed = []
    for rec in log_d["orders"]:
        if rec.get("status") != "OCO_PLACED" or not rec.get("oco_leg_ids"):
            continue
        opens = _signed_request("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"}, key, secret)
        open_ids = {o["orderId"] for o in opens}
        legs_gone = [i for i in rec["oco_leg_ids"] if i not in open_ids]
        if not legs_gone:
            continue
        # OCO 打咗 — 搵出場 trade
        trades = _signed_request("GET", "/api/v3/myTrades", {"symbol": "BTCUSDT"}, key, secret)
        t = sorted(trades, key=lambda x: x["time"])
        exit_trades = [x for x in t if x["orderId"] in legs_gone]
        if not exit_trades:
            continue
        exit_px = sum(float(x["price"]) * float(x["qty"]) for x in exit_trades) / sum(float(x["qty"]) for x in exit_trades)
        exit_qty = sum(float(x["qty"]) for x in exit_trades)
        exit_fee = sum(float(x["commission"]) for x in exit_trades)
        entry_px = rec["entry_fill"]
        # R 計法: 風險 = |entry - planned_stop| × qty (USDT), 獲利/虧損同理
        risk = abs(entry_px - float(rec["planned_stop"])) * exit_qty
        pnl = (exit_px - entry_px) * exit_qty * (-1 if rec["side"] == "SELL" else 1)
        r_mult = round(pnl / risk, 3) if risk > 0 else 0.0
        rec.update({
            "status": "CLOSED",
            "exit_fill": round(exit_px, 2),
            "exit_qty": exit_qty,
            "exit_fee": exit_fee,
            "pnl_usdt": round(pnl, 2),
            "r_multiple": r_mult,
            "closed_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "closed_via": "oco_leg_gone",
        })
        changed.append(rec)
    if changed:
        save_log(log_d)
        # 追加 closed history
        hist = json.load(open(HISTORY)) if os.path.exists(HISTORY) else {"trades": []}
        hist["trades"].extend(changed)
        with open(HISTORY, "w") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
    return changed


def main():
    from binance_testnet_paper import _load_keys
    key, secret = _load_keys()
    if not key or not secret:
        log("❌ 冇 testnet keys")
        return

    # 1. reconcile
    closed = reconcile_cycle(key, secret)
    for c in closed:
        log(f"🔒 CLOSED {c['side']} {c['pattern'][:18]} exit={c['exit_fill']} pnl={c['pnl_usdt']}USDT R={c['r_multiple']}")

    # 2. 引擎掃描
    out = sh("python3 btc_engine.py 2>&1 | tail -30")
    if "Trade Setups" not in out:
        log(f"⚠️ 引擎冇 setups (可能數據問題): {out[-120:]}")
        return

    # 3. 落單 (dedup + 風控內建)
    out2 = sh("python3 binance_testnet_paper.py 2>&1 | tail -10")
    if out2.strip():
        log(f"掃描結果: {out2.strip().splitlines()[-1]}")
    for line in out2.splitlines():
        if any(k in line for k in ("✅", "🚫", "❌", "[DRY]")):
            log(line.strip())

    # 4. 每日統計
    if closed:
        hist = json.load(open(HISTORY))
        rs = [t["r_multiple"] for t in hist["trades"]]
        log(f"📊 累計 {len(rs)} 平倉: sumR={sum(rs):+.2f} 勝率={sum(1 for r in rs if r > 0)}/{len(rs)}")


if __name__ == "__main__":
    main()
