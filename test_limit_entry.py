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

# ── 沙盒 (fix ①, 2026-09-27): 測試唔准寫生產 log ──────────────────────
# 任何漏咗 patch load_log/save_log 嘅路徑會寫入 temp, 唔會洩漏去生產檔案。
# (背景: test_rr_gates T2 曾直寫生產 log → 生產 reconcile 追殺幻影記錄。)
import tempfile as _tempfile

btp.LOG_PATH = os.path.join(_tempfile.mkdtemp(prefix="btc-limit-entry-"), "orders.json")

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

# 沙盒延伸 (fix ①, 2026-09-27): reconcile_cycle 會經 log() 寫生產 heartbeat +
# 寫 reconcile 狀態檔。用 fake API call 生產函数 = 一定要 redirect 呢兩個路徑,
# 否則假讀數會污染生產檔案。(實證 09:05:37 UTC: 呢個 test 令生產 heartbeat 出現
# 假「餘額 0.00000」警報 — fake /account 冇 BTC balance → acct=0。)
_cyc_tmp = _tempfile.mkdtemp(prefix="btc-limit-entry-cyc-")
cyc.HEARTBEAT = os.path.join(_cyc_tmp, "heartbeat.txt")
cyc.RECONCILE_STATE = os.path.join(_cyc_tmp, "reconcile_state.json")
# GLM 09-27 #8: HISTORY 都要 redirect —— 測試路徑寫 closed_recs 會污染生產統計。
HIST_PATH = cyc.HISTORY
cyc.HISTORY = os.path.join(_cyc_tmp, "history.json")


def run_reconcile(orders, opens, order_lookup, price=76000.0, history_file=None,
                  account_btc=0.0, my_trades=None):
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
        if path == "/api/v3/account":
            return {"balances": [{"asset": "BTC", "free": f"{account_btc:.8f}",
                                  "locked": "0.00000000"}]}
        if path == "/api/v3/order" and method == "GET":
            return order_lookup.get(params.get("orderId"), {})
        if path == "/api/v3/order" and method == "DELETE":
            placed.append((method, path, dict(params)))
            return {"orderId": params.get("orderId")}
        if path == "/api/v3/myTrades":
            if my_trades is not None:
                return my_trades
            return [{"orderId": 500001, "commission": "0.00001", "commissionAsset": "BTC",
                     "time": int(time.time() * 1000), "isBuyer": True, "qty": "0.0026",
                     "price": "74913.00"}]
        if path == "/api/v3/order/oco":
            return {"orderListId": 901, "orders": [{"orderId": 11}, {"orderId": 12}]}
        if path == "/api/v3/order":
            return {"orderId": 55}
        return {}

    btp._signed_request = fake
    cyc.HISTORY = history_file or os.path.join(_cyc_tmp, "history.json")
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

# R8 (2026-09-27 GLM review #2): 幽靈記錄 (entry 成交唔喺 myTrades) 唔可以補建
# legs —— 就算價位合理都唔可以掛真 OCO/SL (SL 觸發 = 延遲版市價平倉)。
placed.clear()
ghost = {"pattern": "👻 GHOST", "side": "BUY", "qty": 0.0026, "order_id": 777777,
         "status": "FILLED_ENTRY", "entry_fill": 75000.0,
         "planned_stop": 74800.0, "planned_tp1": 76000.0, "atr": 150.0}
ch, wp = run_reconcile([ghost], [], {}, account_btc=0.0026)
_oco_calls = [c[1] for c in placed if c[1].endswith("/order/oco")]
result("R8 幽靈記錄 → 拒建 legs (唔落任何 OCO)",
       ghost.get("rebuild_fail_count") == 1 and ghost.get("needs_legs") is True
       and not _oco_calls,
       f"(count={ghost.get('rebuild_fail_count')}, oco={_oco_calls})")
result("R8b 錯誤訊息指名 entry 唔喺 myTrades",
       "唔喺 myTrades" in str(ghost.get("rebuild_error")),
       f"({ghost.get('rebuild_error')})")

# R9 (2026-09-27 GLM round-2 P2): 異常賣出偵測 —— 賣出成交唔對應本地記錄 → ⚠️
import io as _io
import contextlib as _cl
_st = json.load(open(cyc.RECONCILE_STATE)) if os.path.exists(cyc.RECONCILE_STATE) else {}
_st["sell_scan_since"] = time.time() - 3600     # 令 fake 賣出 (1-2 分鐘前) 落入窗內
_st.pop("unmatched_sell_alert_ts", None)
json.dump(_st, open(cyc.RECONCILE_STATE, "w"))
_now_ms = int(time.time() * 1000)
_sells = [
    {"orderId": 880001, "commission": "0.00001", "commissionAsset": "USDT",
     "time": _now_ms - 120_000, "isBuyer": False, "qty": "0.0009", "price": "84000"},
    {"orderId": 880002, "commission": "0.00001", "commissionAsset": "USDT",
     "time": _now_ms - 60_000, "isBuyer": False, "qty": "0.0009", "price": "84000"},
]
_buf = _io.StringIO()
with _cl.redirect_stdout(_buf):
    ch, wp = run_reconcile([dict(PENDING)], [{"orderId": 500001}], {}, my_trades=_sells)
_out9 = _buf.getvalue()
result("R9 異常賣出 → ⚠️ 出聲 (2 筆唔對應)",
       "唔對應任何本地記錄" in _out9 and "880001" in _out9,
       f"({[l.strip() for l in _out9.splitlines() if '⚠️' in l][:1]})")

# R9c: find_unmatched_sells 純函數 (含 exit_leg_ids / oco_leg_ids / flatten_order_id 匹配)
_u = cyc.find_unmatched_sells(
    [{"orderId": 1, "isBuyer": False, "time": 0},
     {"orderId": 2, "isBuyer": True, "time": 0},
     {"orderId": 3, "isBuyer": False, "time": 0},
     {"orderId": 4, "isBuyer": False, "time": 0},
     {"orderId": 6, "isBuyer": False, "time": 0}],
    {"orders": [{"order_id": 1, "exit_leg_ids": [5]}, {"flatten_order_id": 3},
                {"oco_leg_ids": [6]}]},
    since_ms=None)
result("R9c find_unmatched_sells: 只揪未對應賣出 (4; 1/3/6 已對應)", _u == [4], f"({_u})")


# R9d-g (GLM round-3): sell_reconcile_alerts 狀態機 (dedupe / 首次 since=now / 閾值 / 盲點)
def _state_patch(**kw):
    s = json.load(open(cyc.RECONCILE_STATE)) if os.path.exists(cyc.RECONCILE_STATE) else {}
    for k in kw.pop("_pop", []):
        s.pop(k, None)
    s.update(kw)
    json.dump(s, open(cyc.RECONCILE_STATE, "w"))


def _mk_sell(oid, age=60):
    return {"orderId": oid, "isBuyer": False,
            "time": int(time.time() * 1000) - age * 1000}


_state_patch(_pop=["unmatched_sell_alert_ts"], sell_scan_since=time.time() - 3600)
_m1 = cyc.sell_reconcile_alerts([_mk_sell(880010), _mk_sell(880011)], {"orders": []})
_m2 = cyc.sell_reconcile_alerts([_mk_sell(880010), _mk_sell(880011)], {"orders": []})
result("R9d dedupe: 首次響、12h 內再 call 靜默", bool(_m1) and not _m2,
       f"(m1={len(_m1)}, m2={len(_m2)})")

_state_patch(_pop=["sell_scan_since", "unmatched_sell_alert_ts"])
_m3 = cyc.sell_reconcile_alerts([_mk_sell(880012, age=7200), _mk_sell(880013, age=7100)],
                                {"orders": []})
_s2 = json.load(open(cyc.RECONCILE_STATE))
result("R9e 首次執行 since=now: 歷史賣出唔響 + 錨點已寫", not _m3 and "sell_scan_since" in _s2,
       f"(m3={len(_m3)}, 錨點={'有' if 'sell_scan_since' in _s2 else '冇'})")

_state_patch(_pop=["unmatched_sell_alert_ts"], sell_scan_since=time.time() - 3600)
_m4 = cyc.sell_reconcile_alerts([_mk_sell(880014)], {"orders": []})
_m5 = cyc.sell_reconcile_alerts([_mk_sell(880014), _mk_sell(880015)], {"orders": []})
result("R9f 閾值: 1 筆靜默、2 筆響", not _m4 and bool(_m5), f"(m4={len(_m4)}, m5={len(_m5)})")

_state_patch(_pop=["trades_fail_alert_ts"], sell_scan_since=time.time() - 3600)
_m6 = cyc.sell_reconcile_alerts(None, {"orders": []})
_m7 = cyc.sell_reconcile_alerts(None, {"orders": []})
result("R9g myTrades 失敗: 出一次聲 (6h dedupe)", bool(_m6) and not _m7,
       f"(m6={len(_m6)}, m7={len(_m7)})")

# R10 (2026-09-27 1+2 office R1-B): freeze 專屬警報 —— summary 有 frozen_rebuild
# → 除咗 ⚠️/⏰ 之外多一條 🚨 (唔使靠數字變化先見到)
_state_patch(orphan_summary={}, alert_ts=0)
_m10 = cyc.reconcile_alerts({"needs_legs": 1, "frozen_rebuild": 1}, None)
result("R10 freeze 專屬警報 🚨", any("🚨" in m and "凍結" in m for m in _m10), f"({_m10})")
_m11 = cyc.reconcile_alerts({"needs_legs": 1}, None)
result("R10b 冇 frozen 就唔會出 🚨", not any("🚨" in m for m in _m11), f"({_m11})")

cyc.HISTORY = HIST_PATH

print(f"\n{sum(1 for _, ok in RESULTS if ok)}/{len(RESULTS)} PASS")
sys.exit(0 if all(ok for _, ok in RESULTS) else 1)
