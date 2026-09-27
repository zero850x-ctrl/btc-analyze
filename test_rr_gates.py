#!/usr/bin/env python3
"""test_rr_gates.py — 落單前「現價 RR」gate (fix 1) + 成交後 RR 覆核 (fix 2) 測試.

背景 (2026-09-13):
  09-12 嗰單 planned RR 1.35 過關, 但 MARKET 成交差 57-77 點 (0.07-0.1%)
  → 實際 RR 只有 0.51。25 單統計: 實際 RR<1.2 佔 21 單 (贏 +0.37R / 輸 -0.90R
  = 贏細輸大), 即係 RR gate 用 planned entry 計係失效嘅。

  fix 1: 落單前用 current_price 重算 RR, < MIN_RR_EXEC 唔落單
  fix 2: 成交後用 entry_fill 覆核, < MIN_RR_EXEC 即刻市價平倉 (未建 exit legs,
         成本 = spread, 唔使 cancel 任何 order)

用法: python3 test_rr_gates.py   (repo root)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binance_testnet_paper as btp

# ── 沙盒 (fix ①, 2026-09-27) ─────────────────────────────────────────
# 之前呢個 test 會直接寫**生產 log**: T2 嘅 fake fill 會 persist 成真記錄
# (orderId 999001), 生產 reconcile 當佢係孤兒 → 每 tick 重建失敗 → 觸發市價
# 平倉 (實證 09-20~09-27: 270 筆幻影賣出 = 0.6966 BTC)。測試一律唔准掂生產
# log — redirect LOG_PATH 去即棄 temp dir; 完場有 T6/T6b 斷言驗證冇洩漏。
_TMP = tempfile.mkdtemp(prefix="btc-rr-gates-")
btp.LOG_PATH = os.path.join(_TMP, "orders.json")

_PROD_LOG = os.path.expanduser("~/.hermes/reports/btc_testnet_orders.json")


def _prod_order_ids():
    """生產 log 嘅 order_id 出現次數 (Counter — 重複 append 同一 id 都捉到)。"""
    if not os.path.exists(_PROD_LOG):
        return {}
    try:
        with open(_PROD_LOG) as f:
            ids = [o.get("order_id") for o in json.load(f).get("orders", [])
                   if o.get("order_id")]
        cnt = {}
        for i in ids:
            cnt[i] = cnt.get(i, 0) + 1
        return cnt
    except Exception:
        return {}


_prod_ids_before = _prod_order_ids()

RESULTS = []


def result(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'✅' if ok else '❌'} {name} {detail}")


# 09-12 真實單: planned entry 77215, SL 77095.5, TP1 77369, 實際成交 77276.93
SETUP = {"btc_side": "BUY", "btc_entry": 77215.0, "btc_stop": 77095.5,
         "btc_tp1": 77369.0, "btc_tp2": 77511.0, "pattern": "🚩 Bull Flag (牛旗)"}

# ── fix 1: 落單前現價 RR gate ────────────────────────────────
rr_planned = btp._compute_rr(SETUP)
result("T1a planned RR 過 1.2", rr_planned is not None and rr_planned >= 1.2,
       f"(planned RR={rr_planned:.2f})")

rr_px = btp._compute_rr(SETUP, entry_override=77310.0)
result("T1b 現價 RR 反映追高", rr_px < 1.2, f"(RR(px)={rr_px:.2f})")

placed = []


def fake_post_flat(method, path, params, key, secret):
    placed.append((method, path, params))
    if path == "/api/v3/order" and params.get("type") == "MARKET":
        return {"orderId": 999001,
                "fills": [{"price": "77310.00", "qty": "0.00258", "commission": "0"}]}
    return {}


btp.current_price = lambda: 77310.0
btp.exchange_filters = lambda k, s: (0.00001, 0.0, 5.0)
btp._signed_request = fake_post_flat

rec, err = btp.place_signal_order(dict(SETUP), "k", "s", atr=145.6, mode="market")
result("T1c 現價 RR<1.2 → 落單前擋住", rec is None and err and "現價" in err, f"({err})")
result("T1d 冇落任何單", len(placed) == 0, f"(calls={len(placed)})")

# T2: 現價 RR 過關 → 正常落單
placed.clear()
btp.current_price = lambda: 77215.0
rec2, err2 = btp.place_signal_order(dict(SETUP), "k", "s", atr=145.6, mode="market")
result("T2 現價 RR 過關 → 正常落單", err2 is None, f"(err={err2})")
order_calls = [c for c in placed if c[1] == "/api/v3/order" and c[2].get("type") == "MARKET"]
result("T2b 有落 market 單", len(order_calls) >= 1, f"(market calls={len(order_calls)})")

# ── fix 2: 成交後 RR 覆核 ─────────────────────────────────────
# T3: 落單前用 px 77215 過關, 但實際成交 77276.93 (滑價) → 覆核應該 flat
placed.clear()


def fake_post_slippage(method, path, params, key, secret):
    placed.append((method, path, params))
    if path == "/api/v3/order" and params.get("type") == "MARKET":
        if params["side"] == "BUY":
            return {"orderId": 999002,
                    "fills": [{"price": "77276.93", "qty": "0.00258", "commission": "0"}]}
        return {"orderId": 999003,
                "fills": [{"price": "77277.00", "qty": "0.00258", "commission": "0"}]}
    return {}


btp._signed_request = fake_post_slippage
saved = {}
btp.load_log = lambda: {"orders": [], "history": []}
btp.save_log = lambda lg: saved.update({"lg": lg})

rec3, err3 = btp.place_signal_order(dict(SETUP), "k", "s", atr=145.6, mode="market")
result("T3 成交後 RR 0.51 < 1.2 → FLATTENED_LOW_FILL_RR",
       rec3 is not None and rec3.get("status") == "FLATTENED_LOW_FILL_RR",
       f"(status={rec3.get('status') if rec3 else None}, rr_fill={rec3.get('rr_fill') if rec3 else None})")
sells = [c for c in placed if c[1] == "/api/v3/order" and c[2].get("type") == "MARKET"
         and c[2]["side"] == "SELL"]
result("T3b 有即刻市價平倉 (SELL)", len(sells) == 1, f"(sell calls={len(sells)})")
result("T3c 冇建任何 exit legs", not rec3.get("exit_leg_ids"), f"({rec3.get('exit_leg_ids')})")
result("T3d 冇 OCO 建單", not any("oco" in c[1] for c in placed), f"({[c[1] for c in placed]})")
result("T3e 記入 log", len(saved.get("lg", {}).get("orders", [])) == 1,
       f"(orders={len(saved.get('lg', {}).get('orders', []))})")
result("T3f 記低 rr_planned / rr_px / rr_fill",
       rec3.get("rr_planned") is not None and rec3.get("rr_px") is not None
       and rec3.get("rr_fill") == 0.51,
       f"(planned={rec3.get('rr_planned')}, px={rec3.get('rr_px')}, fill={rec3.get('rr_fill')})")
result("T3g flatten_order_id 有記錄 (P2 賣出對帳匹配用)",
       rec3.get("flatten_order_id") == 999003, f"({rec3.get('flatten_order_id')})")

# T4: 成交價好過計劃 → RR 過關, 唔應該 flat
placed.clear()


def fake_post_good(method, path, params, key, secret):
    placed.append((method, path, params))
    if path == "/api/v3/order" and params.get("type") == "MARKET":
        if params["side"] == "BUY":
            return {"orderId": 999004,
                    "fills": [{"price": "77100.00", "qty": "0.00258", "commission": "0"}]}
        return {"orderId": 999005,
                "fills": [{"price": "77369.00", "qty": "0.00086", "commission": "0"}]}
    if "oco" in path:
        return {"orderListId": 777, "orders": [{"orderId": 1}, {"orderId": 2}]}
    return {"orderId": 42}


btp._signed_request = fake_post_good
rec4, err4 = btp.place_signal_order(dict(SETUP), "k", "s", atr=145.6, mode="market")
result("T4 成交價好 → 唔 flat (RR 過關)",
       rec4 is not None and rec4.get("status") != "FLATTENED_LOW_FILL_RR",
       f"(status={rec4.get('status') if rec4 else None}, rr_fill={rec4.get('rr_fill') if rec4 else None})")

# T5: 冇 TP1 → 一律唔落單 (fix 1 保護)
placed.clear()
no_tp1 = dict(SETUP)
no_tp1.pop("btc_tp1")
btp.current_price = lambda: 77215.0
rec5, err5 = btp.place_signal_order(no_tp1, "k", "s", atr=145.6, mode="market")
result("T5 冇 TP1 → 唔落單", rec5 is None and err5, f"({err5})")

# ── T6: 沙盒斷言 (fix ①, 2026-09-27) ─────────────────────────────────
# 冇 redirect 嘅話 T2 就會寫生產 log; 呢兩個斷言係防回歸守衛。
result("T6 沙盒生效: LOG_PATH 已 redirect 去 temp",
       btp.LOG_PATH.startswith(_TMP), f"({btp.LOG_PATH})")
_after = _prod_order_ids()
_new_ids = [i for i, cnt in _after.items() if cnt > _prod_ids_before.get(i, 0)]
# GLM 09-27 #11: 唔止 999001-5 —— 任何新增 order_id < 1,000,000 都當洩漏。
# (生產真實 order id 而家 6.9M+ 且單調遞增; 新記錄出現 ≤1M id 冇可能係真單。)
_leaked = sorted(i for i in _new_ids if isinstance(i, int) and i < 1_000_000)
result("T6b 生產 log 冇新增測試幻影記錄 (任何 <1M 新 id)", not _leaked, f"(leaked={_leaked})")

print(f"\n{sum(1 for _, ok in RESULTS if ok)}/{len(RESULTS)} PASS")
sys.exit(0 if all(ok for _, ok in RESULTS) else 1)
