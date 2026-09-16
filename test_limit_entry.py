#!/usr/bin/env python3
"""test_limit_entry.py — feat/limit-entry 限價入場測試.

背景 (09-16): market 追入時價已穿過 entry zone 0.10-0.17%, TP1 又近 → 實際 RR
0.44-0.90 (planned 1.23-1.74), 32 次 setup 冇一個過得到 RR gate。改為掛 LIMIT
@ engine 明示價位, 成交價 = limit_px → RR 準確。

測: engine parse_entry_limit / limit 掛單 / 三種 cancel 條件 / 成交建 exit /
    HISTORY 唔被 LIMIT_* 污染 / market legacy 模式仍運作

用法: python3 test_limit_entry.py   (repo root)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import binance_testnet_paper as btp

RESULTS = []


def result(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'✅' if ok else '❌'} {name} {detail}")


# ── 1. engine: parse_entry_limit ─────────────────────────────
import btc_engine as be

result("E1 trigger '@ $74913' → 74913",
       be.parse_entry_limit("限價買入 @ $74913（形態邊界入場）", "$74796 - $75031") == 74913.0)
result("E2 SELL trigger → 77700",
       be.parse_entry_limit("限價沽出 @ $77700（形態邊界入場）", "$77582 - $77818") == 77700.0)
result("E3 冇 trigger → zone 中點",
       be.parse_entry_limit(None, "$74796 - $75031") == 74913.5,
       f"({be.parse_entry_limit(None, '$74796 - $75031')})")
result("E4 全冇 → None", be.parse_entry_limit(None, None) is None)

# ── 2. limit 掛單 ─────────────────────────────────────────────
SETUP = {
    "btc_side": "BUY", "btc_entry": 74913.0, "btc_limit_px": 74913.0,
    "btc_stop": 74717.0, "btc_tp1": 75356.0, "btc_tp2": 75799.0,
    "pattern": "🚩 Bull Flag (牛旗)",
}

placed = []
LOG = {"orders": [], "history": []}


def fake_post(method, path, params, key, secret):
    placed.append((method, path, dict(params)))
    if path == "/api/v3/order" and params.get("type") == "LIMIT":
        return {"orderId": 500001, "status": "NEW"}
    if path == "/api/v3/order/oco":
        return {"orderListId": 900, "orders": [{"orderId": 1}, {"orderId": 2}]}
    return {"orderId": 42}


btp._signed_request = fake_post
btp.exchange_filters = lambda k, s: (0.00001, 0.0, 5.0)
btp.load_log = lambda: LOG
btp.save_log = lambda lg: LOG.update(lg)
btp.current_price = lambda: 76000.0     # 市價高過 limit (BUY 掛低) → 應該掛

rec, err = btp.place_signal_order(dict(SETUP), "k", "s", atr=200.0, mode="limit")
result("L1 limit 模式掛單成功", err is None and rec is not None, f"(err={err})")
result("L2 用 LIMIT 唔係 MARKET",
       any(c[2].get("type") == "LIMIT" for c in placed),
       f"({[c[2].get('type') for c in placed]})")
result("L3 status=LIMIT_PENDING", rec.get("status") == "LIMIT_PENDING", f"({rec.get('status')})")
result("L4 掛單價 = engine btc_limit_px", rec.get("limit_px") == 74913.0, f"({rec.get('limit_px')})")
result("L5 唔會即刻建 exit legs (等成交)", not rec.get("exit_leg_ids"), f"({rec.get('exit_leg_ids')})")
result("L6 記低 limit_ts", isinstance(rec.get("limit_ts"), float))
result("L7 rr_limit 用 limit 價計",
       rec.get("rr_limit") == round(abs(75356.0 - 74913.0) / abs(74913.0 - 74717.0), 2),
       f"({rec.get('rr_limit')})")

# L8: BUY 掛單價高過市價 → 應該 skip (變 taker 追高)
placed.clear()
rec8, err8 = btp.place_signal_order(dict(SETUP), "k", "s", atr=200.0, mode="limit")
btp.current_price = lambda: 74500.0
placed.clear()
rec8, err8 = btp.place_signal_order(dict(SETUP), "k", "s", atr=200.0, mode="limit")
result("L8 BUY 掛單價 >= 市價 → skip", rec8 is None and "唔低過市價" in err8, f"({err8})")
result("L8b 冇落任何單", len(placed) == 0)

# L9: SELL 掛單價低過市價 → skip (要等價格彈返上 zone)
btp.current_price = lambda: 78000.0
se = {"btc_side": "SELL", "btc_entry": 77700.0, "btc_limit_px": 77700.0,
      "btc_stop": 77896.0, "btc_tp1": 77000.0, "btc_tp2": 76600.0, "pattern": "🚩 Bear Flag"}
placed.clear()
rec9, err9 = btp.place_signal_order(dict(se), "k", "s", atr=200.0, mode="limit")
result("L9 SELL 掛單價 <= 市價 → skip", rec9 is None and "唔高過市價" in err9, f"({err9})")

# L9b: SELL 掛單價高過市價 (等反彈) → 應該掛得到
btp.current_price = lambda: 76000.0
placed.clear()
rec9b, err9b = btp.place_signal_order(dict(se), "k", "s", atr=200.0, mode="limit")
result("L9b SELL 掛高過市價 → 掛得到", rec9b is not None and rec9b.get("status") == "LIMIT_PENDING",
       f"(err={err9b}, status={rec9b.get('status') if rec9b else None})")

# L10: 限價 RR < 1.2 → skip
btp.current_price = lambda: 76000.0
bad = dict(SETUP)
bad["btc_tp1"] = 75000.0     # reward 87 / risk 196 = 0.44
placed.clear()
rec10, err10 = btp.place_signal_order(bad, "k", "s", atr=200.0, mode="limit")
result("L10 RR < 1.2 → skip (唔理邊層擋)", rec10 is None and "RR" in err10 and "< 1.2" in err10,
       f"({err10})")

# L11: market 模式仍運作 (legacy)
placed.clear()
btp.current_price = lambda: 74913.0
mkt = dict(SETUP)
mkt["btc_stop"], mkt["btc_tp1"] = 74717.0, 75356.0
rec11, err11 = btp.place_signal_order(mkt, "k", "s", atr=200.0, mode="market")
result("L11 market 模式仍可落單 (legacy)",
       any(c[2].get("type") == "MARKET" for c in placed),
       f"(types={[c[2].get('type') for c in placed]})")

# ── 3. reconcile: LIMIT_PENDING 處理 ──────────────────────────
import btc_auto_trade_cycle as cyc

HIST_PATH = cyc.HISTORY


def run_reconcile(orders, opens, order_lookup, price=76000.0, history_file=None):
    """orders=local log records, opens=openOrders, order_lookup=orderId→order dict"""
    global LOG
    LOG = {"orders": orders, "history": []}
    btp.load_log = lambda: LOG
    btp.save_log = lambda lg: LOG.update(lg)
    btp.current_price = lambda: price
    btp.exchange_filters = lambda k, s: (0.00001, 0.0, 5.0)

    def fake(method, path, params, key, secret):
        if path == "/api/v3/openOrders":
            return opens
        if path == "/api/v3/order" and method == "GET":
            return order_lookup.get(params.get("orderId"), {})
        if path == "/api/v3/order" and method == "DELETE":
            placed.append((method, path, dict(params)))
            return {"orderId": params.get("orderId")}
        if path == "/api/v3/myTrades":
            return [{"orderId": 500001, "commission": "0.00001", "commissionAsset": "BTC",
                     "time": int(time.time() * 1000), "isBuyer": True, "qty": "0.0026",
                     "price": "74913.00"}]
        if path == "/api/v3/order/oco":
            return {"orderListId": 901, "orders": [{"orderId": 11}, {"orderId": 12}]}
        if path == "/api/v3/order":
            return {"orderId": 55}
        return {}

    btp._signed_request = fake
    cyc.HISTORY = history_file or "/tmp/_test_hist_unused.json"
    return cyc.reconcile_cycle("k", "s")


PENDING = {"pattern": "🚩 Bull Flag (牛旗)", "side": "BUY", "qty": 0.0026,
           "order_id": 500001, "status": "LIMIT_PENDING", "limit_px": 74913.0,
           "limit_ts": time.time(), "planned_stop": 74717.0,
           "planned_tp1": 75356.0, "planned_tp2": 75799.0, "atr": 200.0,
           "planned_entry": 74913.0, "rr_limit": 2.26}

# R1: 仲掛住 (喺 openOrders) 且未過期 → 唔動
placed.clear()
recs1 = [dict(PENDING)]
ch, wp = run_reconcile(recs1, [{"orderId": 500001}], {})
result("R1 掛住未過期 → 唔動", ch == [] and recs1[0]["status"] == "LIMIT_PENDING",
       f"(status={recs1[0]['status']}, changed={len(ch)})")

# R2: TTL 過期 → cancel + LIMIT_EXPIRED
placed.clear()
old = dict(PENDING)
old["limit_ts"] = time.time() - (btp.LIMIT_TTL_HOURS + 1) * 3600
recs2 = [old]
ch, wp = run_reconcile(recs2, [{"orderId": 500001}], {})
result("R2 TTL 過期 → LIMIT_EXPIRED", recs2[0]["status"] == "LIMIT_EXPIRED",
       f"(status={recs2[0]['status']})")
result("R2b 有發 cancel (DELETE)", any(c[0] == "DELETE" for c in placed),
       f"({[c[0] for c in placed]})")
result("R2c 記低原因", "TTL" in str(recs2[0].get("closed_note")), f"({recs2[0].get('closed_note')})")

# R3: 掛單價偏離市價 > 3% → cancel
placed.clear()
recs3 = [dict(PENDING)]
ch, wp = run_reconcile(recs3, [{"orderId": 500001}], {}, price=74913.0 * 1.05)  # +5%
result("R3 價格偏離 >3% → LIMIT_EXPIRED", recs3[0]["status"] == "LIMIT_EXPIRED",
       f"(status={recs3[0]['status']})")
result("R3b 原因係偏離", "偏離" in str(recs3[0].get("closed_note")), f"({recs3[0].get('closed_note')})")

# R4: 交易所已 cancel → LIMIT_CANCELLED
placed.clear()
recs4 = [dict(PENDING)]
ch, wp = run_reconcile(recs4, [], {500001: {"orderId": 500001, "status": "CANCELED",
                                            "executedQty": "0.00000000"}})
result("R4 交易所 CANCELED → LIMIT_CANCELLED", recs4[0]["status"] == "LIMIT_CANCELLED",
       f"(status={recs4[0]['status']})")

# R5: 成交 → 建 3 段 exit (OCO_PLACED)
placed.clear()
recs5 = [dict(PENDING)]
ch, wp = run_reconcile(recs5, [], {500001: {"orderId": 500001, "status": "FILLED",
                                            "executedQty": "0.00260000",
                                            "cummulativeQuoteQty": "194.7738"}})
r5 = recs5[0]
result("R5 成交 → OCO_PLACED", r5.get("status") == "OCO_PLACED", f"(status={r5.get('status')})")
result("R5b 成交價 = cumQuote/execQty = 74913",
       r5.get("entry_fill") == 74913.0, f"(entry_fill={r5.get('entry_fill')})")
result("R5c 標記 was_limit_fill", r5.get("was_limit_fill") is True)
result("R5d 有建 exit legs", bool(r5.get("exit_leg_ids")), f"({r5.get('exit_leg_ids')})")
result("R5e rr_fill = 443/196 = 2.26", r5.get("rr_fill") == 2.26, f"({r5.get('rr_fill')})")

# R6: LIMIT_EXPIRED 唔可以入 closed HISTORY
import tempfile
tmp_hist = tempfile.mktemp(suffix=".json")
with open(tmp_hist, "w") as f:
    json.dump({"trades": []}, f)
placed.clear()
recs6 = [dict(PENDING)]
recs6[0]["limit_ts"] = time.time() - (btp.LIMIT_TTL_HOURS + 1) * 3600
ch, wp = run_reconcile(recs6, [{"orderId": 500001}], {}, history_file=tmp_hist)
hist_after = json.load(open(tmp_hist))
result("R6 LIMIT_EXPIRED 唔入 HISTORY (唔污染統計)", len(hist_after["trades"]) == 0,
       f"(trades={len(hist_after['trades'])}, status={recs6[0]['status']})")
os.unlink(tmp_hist)

# R7: CLOSED 照入 HISTORY (regression)
with open(tmp_hist, "w") as f:
    json.dump({"trades": []}, f)
placed.clear()
closed_rec = {"pattern": "x", "side": "BUY", "qty": 0.0026, "status": "OCO_PLACED",
              "entry_fill": 75000.0, "planned_stop": 74800.0, "realized_qty": 0.0026,
              "realized_pnl": 5.0, "fee": 0.00001, "exit_leg_ids": [11, 12]}
ch, wp = run_reconcile([closed_rec], [], {}, history_file=tmp_hist)
hist_after = json.load(open(tmp_hist))
result("R7 CLOSED 照入 HISTORY (regression)", len(hist_after["trades"]) == 1,
       f"(trades={len(hist_after['trades'])}, status={closed_rec.get('status')})")
os.unlink(tmp_hist)

cyc.HISTORY = HIST_PATH

print(f"\n{sum(1 for _, ok in RESULTS if ok)}/{len(RESULTS)} PASS")
sys.exit(0 if all(ok for _, ok in RESULTS) else 1)
