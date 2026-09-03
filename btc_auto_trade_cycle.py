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


def _pnl_of(rec, exit_px, qty, fee):
    """單一段 exit 嘅 pnl (fee 為 BTC 單位 → 扣 fee×exit_px 轉 USD)."""
    direction = -1 if rec["side"] == "SELL" else 1
    return (exit_px - rec["entry_fill"]) * qty * direction - fee * exit_px


def _trail_leg(rec, open_map, key, secret):
    """尾倉 SL (l3) 按 ATR step 推 — 每行 +1 ATR 利潤 → SL 追 1 ATR (鎖 1 ATR 浮動).

    先 POST 新 SL order 成功先 DELETE 舊 (避免 naked 窗口); 失敗留待下個 tick 再試.
    """
    from binance_testnet_paper import _signed_request, current_price

    l3_id = rec.get("l3_id")
    if not l3_id or l3_id not in open_map:
        return
    atr = rec.get("atr")
    if not atr or atr <= 0:
        return
    entry = rec["entry_fill"]
    side = rec["side"]
    px = current_price()
    old_stop = float(open_map[l3_id]["stopPrice"])
    if side == "BUY":
        steps = int((px - entry) / atr)
        new_stop = entry + max(0, steps - 1) * atr
    else:
        steps = int((entry - px) / atr)
        new_stop = entry - max(0, steps - 1) * atr
    if steps < 1:
        return  # 未行夠 1 ATR
    new_stop = round(new_stop, 2)
    if (side == "BUY" and new_stop <= old_stop) or (side == "SELL" and new_stop >= old_stop):
        return  # 唔更有利
    qty = float(open_map[l3_id]["origQty"])
    try:
        if side == "BUY":
            r = _signed_request("POST", "/api/v3/order", {
                "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_LOSS_LIMIT",
                "quantity": f"{qty:.5f}", "stopPrice": f"{new_stop:.2f}",
                "price": f"{new_stop * 0.9985:.2f}", "timeInForce": "GTC"}, key, secret)
        else:
            r = _signed_request("POST", "/api/v3/order", {
                "symbol": "BTCUSDT", "side": "BUY", "type": "STOP_LOSS_LIMIT",
                "quantity": f"{qty:.5f}", "stopPrice": f"{new_stop:.2f}",
                "price": f"{new_stop * 1.0015:.2f}", "timeInForce": "GTC"}, key, secret)
        _signed_request("DELETE", "/api/v3/order",
                        {"symbol": "BTCUSDT", "orderId": l3_id}, key, secret)
        rec["l3_id"] = r["orderId"]
        rec.setdefault("exit_leg_ids", []).append(r["orderId"])   # 新 SL leg 都要 reconcile
        rec["trail_stop"] = new_stop
        rec.setdefault("trails", []).append({
            "to": new_stop,
            "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    except Exception as e:
        rec.setdefault("trail_errors", []).append(str(e)[:120])


def reconcile_cycle(key, secret):
    """3 段 exit reconcile + 尾倉 trailing.

    1. exit leg 消失 → myTrades 對應 fill → 累計 realized (已計 legs 唔重複)
    2. 全部 qty 已實現 → CLOSED, 計 R (扣 fee)
    3. 尾倉 SL 仲開住 + 價格行咗 >=1 ATR → 推 SL (trailing)
    """
    from binance_testnet_paper import _signed_request, load_log, save_log

    log_d = load_log()
    changed = []
    opens = _signed_request("GET", "/api/v3/openOrders", {"symbol": "BTCUSDT"}, key, secret)
    open_ids = {o["orderId"] for o in opens}
    open_map = {o["orderId"]: o for o in opens}
    for rec in log_d["orders"]:
        if rec.get("status") != "OCO_PLACED" or not rec.get("exit_leg_ids"):
            continue
        legs_gone = [i for i in rec["exit_leg_ids"] if i not in open_ids]
        done_ids = set(rec.get("closed_leg_ids") or [])
        new_gone = [i for i in legs_gone if i not in done_ids]
        if new_gone:
            trades = _signed_request("GET", "/api/v3/myTrades", {"symbol": "BTCUSDT"}, key, secret)
            t = sorted(trades, key=lambda x: x["time"])
            exit_trades = [x for x in t if x["orderId"] in new_gone]
            if exit_trades:
                exit_qty = sum(float(x["qty"]) for x in exit_trades)
                exit_px = sum(float(x["price"]) * float(x["qty"]) for x in exit_trades) / exit_qty
                exit_fee = sum(float(x["commission"]) for x in exit_trades)
                rec["closed_leg_ids"] = sorted(done_ids | set(new_gone))
                rec["realized_qty"] = round((rec.get("realized_qty") or 0.0) + exit_qty, 8)
                rec["realized_pnl"] = round((rec.get("realized_pnl") or 0.0)
                                            + _pnl_of(rec, exit_px, exit_qty, exit_fee), 2)
                rec["exit_fee"] = round((rec.get("exit_fee") or 0.0) + exit_fee, 8)
                rec.setdefault("realized_parts", []).append({
                    "qty": exit_qty, "price": round(exit_px, 2), "fee": exit_fee,
                    "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            else:
                # leg 消失但搵唔到 trade (例如被 cancel) — mark done 避免每 tick 重查
                rec["closed_leg_ids"] = sorted(done_ids | set(new_gone))
        remaining = round(rec.get("qty", 0) - (rec.get("realized_qty") or 0.0), 8)
        if remaining <= 1e-8:
            rec["status"] = "CLOSED"
            rec["exit_qty"] = rec.get("realized_qty")
            risk = abs(rec["entry_fill"] - float(rec["planned_stop"])) * rec["qty"]
            rec["pnl_usdt"] = round((rec.get("realized_pnl") or 0.0)
                                    - (rec.get("fee") or 0.0) * rec["entry_fill"], 2)
            rec["r_multiple"] = round(rec["pnl_usdt"] / risk, 3) if risk > 0 else 0.0
            rec["closed_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["closed_via"] = "oco_legs_all_gone"
            changed.append(rec)
        else:
            _trail_leg(rec, open_map, key, secret)
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
