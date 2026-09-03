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


def _pnl_of(rec, exit_px, qty, fee, fee_asset):
    """單一段 exit 嘅 pnl (扣 fee).

    Binance spot: seller 付 USDT (quote)、buyer 付 BTC (base) —
    用 myTrades 嘅 commissionAsset 判斷 (GLM review #A1, 真數據驗證):
      USDT → 直接扣; BTC → ×price 轉 USD.
    """
    direction = -1 if rec["side"] == "SELL" else 1
    fee_usdt = fee if str(fee_asset).upper() == "USDT" else fee * exit_px
    return (exit_px - rec["entry_fill"]) * qty * direction - fee_usdt


def _trail_leg(rec, open_map, key, secret):
    """尾倉 SL (l3) 按 ATR step 推 — 每行 +1 ATR 利潤 → SL 追 (steps-1)×ATR.

    steps=1 時 SL 推到 breakeven (鎖打和); steps=2 鎖 +1 ATR, 如此類推.
    先 POST 新 SL order 成功先 DELETE 舊 (避免 naked 窗口); DELETE 失敗 →
    即刻 DELETE 剛 POST 嘅新 SL (rollback), 防止雙 SL live 開反向裸倉
    (GLM review #B3).
    """
    from binance_testnet_paper import _signed_request, current_price

    l3_id = rec.get("l3_id")
    if not l3_id or l3_id not in open_map:
        return
    # trail_stuck cleanup (DSv4 review #C): 上 tick rollback 失敗留低雙 SL →
    # 掃 open_map 清走同向獨立 stop-limit (orderListId=-1 = 唔係 OCO leg)
    if rec.get("trail_stuck"):
        exit_side = "SELL" if rec["side"] == "BUY" else "BUY"
        for o in list(open_map.values()):
            if (o.get("type") == "STOP_LOSS_LIMIT" and o.get("symbol") == "BTCUSDT"
                    and o.get("side") == exit_side
                    and int(o.get("orderListId") or -1) == -1
                    and o["orderId"] != l3_id):
                try:
                    _signed_request("DELETE", "/api/v3/order",
                                    {"symbol": "BTCUSDT", "orderId": o["orderId"]}, key, secret)
                    rec.setdefault("trail_errors", []).append(
                        f"stuck cleanup del {o['orderId']}")
                except Exception as e:
                    rec.setdefault("trail_errors", []).append(
                        f"stuck cleanup FAIL {o['orderId']}: {str(e)[:60]}")
        rec["trail_stuck"] = False
    atr = rec.get("atr")
    if not atr or atr <= 0:
        return
    entry = rec["entry_fill"]
    side = rec["side"]
    # 高水位: 用本 tick 見過嘅最優價計 steps (spike 回落都唔會錯過追蹤)
    px = current_price()
    best = max(rec.get("max_favorable_px") or 0, px) if side == "BUY" \
        else min(rec.get("max_favorable_px") or px, px)
    rec["max_favorable_px"] = best
    old_stop = float(open_map[l3_id]["stopPrice"])
    if side == "BUY":
        steps = int((best - entry) / atr)
        new_stop = entry + max(0, steps - 1) * atr
    else:
        steps = int((entry - best) / atr)
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
    except Exception as e:
        rec.setdefault("trail_errors", []).append(f"POST fail: {str(e)[:100]}")
        return
    # POST 成功先 DELETE 舊 SL; DELETE 失敗 → rollback 新 SL (雙 SL = 反向裸倉風險)
    try:
        _signed_request("DELETE", "/api/v3/order",
                        {"symbol": "BTCUSDT", "orderId": l3_id}, key, secret)
    except Exception as e:
        try:
            _signed_request("DELETE", "/api/v3/order",
                            {"symbol": "BTCUSDT", "orderId": r["orderId"]}, key, secret)
            rec.setdefault("trail_errors", []).append(
                f"old-DELETE fail → new SL rolled back: {str(e)[:80]}")
        except Exception as e2:
            rec["trail_stuck"] = True   # 雙 SL 都剷唔走 → 標記, 下 tick 優先處理
            rec.setdefault("trail_errors", []).append(
                f"ROLLBACK FAIL 雙SL live old={l3_id} new={r['orderId']}: {str(e2)[:80]}")
        return
    rec["l3_id"] = r["orderId"]
    rec.setdefault("exit_leg_ids", []).append(r["orderId"])   # 新 SL leg 都要 reconcile
    rec["trail_stop"] = new_stop
    rec.setdefault("trails", []).append({
        "to": new_stop,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


def _breakeven_remaining(rec, open_map, key, secret):
    """TP1 成交後 → OCO_B 同尾倉 SL 推 breakeven.

    Binance OCO 係 order list: DELETE 單一 leg = 取消成個 list (連 TP2 一齊冇)
    → OCO_B 必須成個 orderList 取消再重建 (TP2 原價 + breakeven stop);
    l3 係獨立 SL → 直接 POST/DELETE. 全部先 POST 後 DELETE (失敗 rollback).

    只喺 OCO_A 嘅 **TP leg 成交** 先 trigger (SL leg 成交 = 價格已反轉,
    推 breakeven 會令剩倉 SL 高過市價即時觸發 — 唔可以做).
    """
    from binance_testnet_paper import _signed_request

    if not rec.get("oco_a_tp_hit"):
        return
    if rec.get("be_done"):
        return
    entry = rec["entry_fill"]
    side = rec["side"]
    be = round(entry, 2)
    exit_side = "SELL" if side == "BUY" else "BUY"

    def _oco_rebuild():
        """OCO_B → 成個 orderList 取消, 重建 (TP2 + breakeven stop)."""
        oco_b_id = rec.get("oco_b_id")
        if oco_b_id is None:
            return
        legs = [o for o in open_map.values() if o.get("orderListId") == oco_b_id and o.get("symbol") == "BTCUSDT"]
        if not legs:
            return  # 已冇 leg (可能已成交) — 唔郁
        o = legs[0]
        qty = float(o["origQty"])
        tp2 = rec.get("planned_tp2")
        if not tp2 or abs(float(o.get("stopPrice", 0)) - be) < 1e-6:
            return  # 已係 breakeven 或冇 TP2
        try:
            if exit_side == "SELL":
                nr = _signed_request("POST", "/api/v3/order/oco", {
                    "symbol": "BTCUSDT", "side": "SELL",
                    "quantity": f"{qty:.5f}", "price": f"{tp2:.2f}",
                    "stopPrice": f"{be:.2f}",
                    "stopLimitPrice": f"{be * 0.9985:.2f}",
                    "stopLimitTimeInForce": "GTC"}, key, secret)
            else:
                nr = _signed_request("POST", "/api/v3/order/oco", {
                    "symbol": "BTCUSDT", "side": "BUY",
                    "quantity": f"{qty:.5f}", "price": f"{tp2:.2f}",
                    "stopPrice": f"{be:.2f}",
                    "stopLimitPrice": f"{be * 1.0015:.2f}",
                    "stopLimitTimeInForce": "GTC"}, key, secret)
        except Exception as e:
            rec.setdefault("trail_errors", []).append(f"BE OCO POST fail: {str(e)[:80]}")
            return
        try:
            _signed_request("DELETE", "/api/v3/orderList",
                            {"symbol": "BTCUSDT", "orderListId": oco_b_id}, key, secret)
        except Exception as e:
            try:
                _signed_request("DELETE", "/api/v3/orderList",
                                {"symbol": "BTCUSDT", "orderListId": nr["orderListId"]}, key, secret)
                rec.setdefault("trail_errors", []).append(f"BE OCO rollback: {str(e)[:80]}")
            except Exception as e2:
                rec["trail_stuck"] = True
                rec.setdefault("trail_errors", []).append(f"BE OCO ROLLBACK FAIL: {str(e2)[:80]}")
            return
        # 更新 referenced leg ids: 移除舊 OCO_B legs, 加新
        old_legs = {o["orderId"] for o in legs}
        new_legs = [o["orderId"] for o in nr.get("orders", [])]
        rec["exit_leg_ids"] = [i for i in rec.get("exit_leg_ids", []) if i not in old_legs] + new_legs
        rec["oco_b_id"] = nr["orderListId"]
        rec.setdefault("breakeven_moves", []).append({"list": oco_b_id, "new": nr["orderListId"], "be": be})

    def _l3_rebuild():
        """尾倉獨立 SL → POST 新 (breakeven) + DELETE 舊, 失敗 rollback."""
        l3_id = rec.get("l3_id")
        if not l3_id or l3_id not in open_map:
            return
        o = open_map[l3_id]
        if abs(float(o.get("stopPrice", 0)) - be) < 1e-6:
            return
        qty = float(o["origQty"])
        try:
            if side == "BUY":
                r = _signed_request("POST", "/api/v3/order", {
                    "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_LOSS_LIMIT",
                    "quantity": f"{qty:.5f}", "stopPrice": f"{be:.2f}",
                    "price": f"{be * 0.9985:.2f}", "timeInForce": "GTC"}, key, secret)
            else:
                r = _signed_request("POST", "/api/v3/order", {
                    "symbol": "BTCUSDT", "side": "BUY", "type": "STOP_LOSS_LIMIT",
                    "quantity": f"{qty:.5f}", "stopPrice": f"{be:.2f}",
                    "price": f"{be * 1.0015:.2f}", "timeInForce": "GTC"}, key, secret)
        except Exception as e:
            rec.setdefault("trail_errors", []).append(f"BE l3 POST fail: {str(e)[:80]}")
            return
        try:
            _signed_request("DELETE", "/api/v3/order",
                            {"symbol": "BTCUSDT", "orderId": l3_id}, key, secret)
        except Exception as e:
            try:
                _signed_request("DELETE", "/api/v3/order",
                                {"symbol": "BTCUSDT", "orderId": r["orderId"]}, key, secret)
                rec.setdefault("trail_errors", []).append(f"BE l3 rollback: {str(e)[:80]}")
            except Exception as e2:
                rec["trail_stuck"] = True
                rec.setdefault("trail_errors", []).append(f"BE l3 ROLLBACK FAIL: {str(e2)[:80]}")
            return
        rec["l3_id"] = r["orderId"]
        rec["exit_leg_ids"] = [i for i in rec.get("exit_leg_ids", []) if i != l3_id] + [r["orderId"]]
        rec.setdefault("breakeven_moves", []).append({"l3": l3_id, "new": r["orderId"], "be": be})

    _oco_rebuild()
    _l3_rebuild()
    if rec.get("breakeven_moves"):
        rec["be_done"] = True


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
        # 舊格式 (oco_leg_ids) → migration 到 exit_leg_ids (GLM review #C1:
        # 唔 migrate 嘅舊單會永久佔住 cap 1 名額, 癱瘓新開倉)
        if rec.get("status") == "OCO_PLACED" and not rec.get("exit_leg_ids") and rec.get("oco_leg_ids"):
            rec["exit_leg_ids"] = list(rec["oco_leg_ids"])
            # 舊格式 = 單一 OCO 全倉 = OCO_A (DSv4 review #E: 舊單 breakeven 保護)
            rec.setdefault("oco_a_leg_ids", list(rec["oco_leg_ids"]))
        if rec.get("status") != "OCO_PLACED" or not rec.get("exit_leg_ids"):
            continue
        legs_gone = [i for i in rec["exit_leg_ids"] if i not in open_ids]
        done_ids = set(rec.get("closed_leg_ids") or [])
        new_gone = [i for i in legs_gone if i not in done_ids]
        if new_gone:
            # OCO_A legs 消失 → 判斷係 TP 定 SL 成交 (TP 成交先推 breakeven)
            oco_a_ids = rec.get("oco_a_leg_ids") or []
            if oco_a_ids and all(i in set(new_gone) | done_ids for i in oco_a_ids):
                rec["oco_a_gone"] = True
            trades = _signed_request("GET", "/api/v3/myTrades", {"symbol": "BTCUSDT"}, key, secret)
            t = sorted(trades, key=lambda x: x["time"])
            exit_trades = [x for x in t if x["orderId"] in new_gone]
            if exit_trades:
                exit_qty = sum(float(x["qty"]) for x in exit_trades)
                exit_px = sum(float(x["price"]) * float(x["qty"]) for x in exit_trades) / exit_qty
                # fee 逐筆按 commissionAsset 換算 (MIXED 都啱 — DSv4 review #B)
                fee_usdt = sum(
                    (float(x["commission"])
                     if str(x.get("commissionAsset", "BTC")).upper() == "USDT"
                     else float(x["commission"]) * float(x["price"]))
                    for x in exit_trades)
                # OCO_A 成交判定: exit price 向 TP 方向行 = TP hit, 向 SL = stop hit
                tp1 = rec.get("planned_tp1")
                if rec.get("oco_a_gone") and tp1:
                    if (rec["side"] == "BUY" and exit_px >= float(tp1) * 0.9985) or \
                       (rec["side"] == "SELL" and exit_px <= float(tp1) * 1.0015):
                        rec["oco_a_tp_hit"] = True
                rec["closed_leg_ids"] = sorted(done_ids | set(new_gone))
                rec["realized_qty"] = round((rec.get("realized_qty") or 0.0) + exit_qty, 8)
                rec["realized_pnl"] = round((rec.get("realized_pnl") or 0.0)
                                            + _pnl_of(rec, exit_px, exit_qty, fee_usdt, "USDT"), 2)
                rec["exit_fee"] = round((rec.get("exit_fee") or 0.0) + fee_usdt, 8)
                rec.setdefault("realized_parts", []).append({
                    "qty": exit_qty, "price": round(exit_px, 2), "fee": fee_usdt,
                    "fee_asset": "USDT(EQV)",
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
            _breakeven_remaining(rec, open_map, key, secret)
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
