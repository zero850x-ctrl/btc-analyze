#!/usr/bin/env python3
"""test_rebalance.py — btc_rebalance.py 單元測試 (mock, 唔掂真 API)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import btc_rebalance as rb  # noqa: E402

PASS = FAIL = 0
def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {extra}")


def mock_env(btc=1.00061, usdt=9951.02, px=78002.0, patch_post=None):
    """把 read_account / current_price / exchange_filters / _signed_request 換成假嘅."""
    rb.current_price = lambda: px
    rb.read_account = lambda k, s: (btc, usdt)
    rb.exchange_filters = lambda k, s: (0.00001, 0.00001, 10.0)
    calls = []

    def fake_signed(method, path, params, key, secret):
        calls.append((method, path, params))
        if patched := patch_post:
            return patched(method, path, params)
        return {"orderId": 12345, "fills": [
            {"qty": "0.323690", "price": "78002.00"}]}

    rb._signed_request = fake_signed
    return calls


def tmp_log():
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(p)
    rb.LOG_PATH = p
    return p


print("=== T1: compute_state 數學 ===")
st = rb.compute_state(0.6, 20000.0, 50000.0)      # 0.6*50000=30000 + 20000 = 50000 → 60/40
ok("總值 = BTC*px + USDT", abs(st["total_usd"] - 50000.0) < 1e-6, st["total_usd"])
ok("BTC% 正確 (60%)", abs(st["btc_pct"] - 0.6) < 1e-9, st["btc_pct"])
ok("drift = 0 (已在目標)", abs(st["drift_pct"]) < 1e-9, st["drift_pct"])
ok("target_btc = 0.6", abs(st["target_btc"] - 0.6) < 1e-9, st["target_btc"])
st2 = rb.compute_state(1.0, 10000.0, 50000.0)     # 1*50000=50000 vs 10000 → 83.3%
ok("BTC 83.3% 情況", abs(st2["btc_pct"] - 0.8333333) < 1e-6, st2["btc_pct"])
ok("drift = +23.3", abs(st2["drift_pct"] - 23.3333333) < 1e-6, st2["drift_pct"])

print("\n=== T2: 偏離細 → 唔做 (非季度月) ===")
rb.BAND = 0.05
rb.QUARTER_MONTHS = ()          # 確保非季度月
mock_env(btc=0.7, usdt=36000.0, px=78002.0)
tmp_log()
act, det = rb.rebalance("k", "s", dry=True)
ok("action = none", act == "none", act)
ok("訊息提偏離", "偏離" in det.get("msg", "") or "唔需要" in det.get("msg", ""), det.get("msg"))

print("\n=== T3: 偏離大 (BTC 88.7%) → 賣出 ===")
calls = mock_env(btc=1.00061, usdt=9951.02, px=78002.0)
act, det = rb.rebalance("k", "s", dry=True)
ok("action = sell", act == "sell", act)
ok("賣出量 ~0.3237", abs(det["quantity"] - 0.32369) < 0.001, det.get("quantity"))
ok("dry-run 冇 POST", not any(c[0] == "POST" for c in calls), calls)

print("\n=== T4: 偏離負 (BTC 40%) → 買入 ===")
mock_env(btc=0.45, usdt=53000.0, px=78002.0)
act, det = rb.rebalance("k", "s", dry=True)
ok("action = buy", act == "buy", act)
ok("用 quoteOrderQty (USDT 金額)", "quoteOrderQty" in det, det)
ok("買入額 = 差額 (~$17,760)", abs(det["quoteOrderQty"] - 17760.0) < 60, det.get("quoteOrderQty"))

print("\n=== T5: 真落單 (sell) — POST + fill 解析 ===")
calls = mock_env(btc=1.00061, usdt=9951.02, px=78002.0)
p = tmp_log()
act, det = rb.rebalance("k", "s", dry=False)
ok("action = sell", act == "sell", act)
ok("有 POST 落單", any(c[0] == "POST" for c in calls), calls)
post = [c for c in calls if c[0] == "POST"][0]
ok("side = SELL", post[2].get("side") == "SELL", post[2])
ok("type = MARKET", post[2].get("type") == "MARKET", post[2])
ok("quantity 有 step 對齊", float(post[2]["quantity"]) == 0.32369, post[2].get("quantity"))
ok("avg_price 由 fills 計", abs(det["avg_price"] - 78002.0) < 0.01, det.get("avg_price"))
ok("quote_gained 正確", abs(det["quote_gained"] - 0.323690 * 78002.0) < 1.0, det.get("quote_gained"))
ok("log 檔已寫", os.path.exists(p), p)
if os.path.exists(p):
    import json
    lg = json.load(open(p))
    ok("log 有 1 個 event", len(lg["events"]) == 1, len(lg.get("events", [])))
    ok("log 有 snapshot", len(lg["snapshots"]) == 1, len(lg.get("snapshots", [])))

print("\n=== T6: dry-run 唔寫 log ===")
mock_env(btc=1.00061, usdt=9951.02, px=78002.0)
p2 = tmp_log()
rb.rebalance("k", "s", dry=True)
ok("log 唔存在", not os.path.exists(p2), p2)

print("\n=== T7: 差額 < MIN_TRADE_USD → 唔做 ===")
rb.MIN_TRADE_USD = 50.0
rb.BAND = 0.0001               # 令偏離觸發
mock_env(btc=0.6769, usdt=35200.0, px=78002.0)   # 已接近 60/40
act, det = rb.rebalance("k", "s", dry=True)
ok("action = none", act == "none", act)
ok("訊息提 MIN", "MIN" in det.get("msg", "") or "唔需要" in det.get("msg", ""), det.get("msg"))
rb.BAND = 0.05

print("\n=== T8: force 強制觸發 ===")
mock_env(btc=0.6769, usdt=35200.0, px=78002.0)
act, det = rb.rebalance("k", "s", dry=True, force=True, reason="測試")
ok("force 但差額太細 → none", act == "none", act)

print("\n=== T9: 季度月觸發 ===")
rb.QUARTER_MONTHS = (1, 4, 7, 10)
rb.is_quarter_month = lambda dt=None: True
mock_env(btc=1.00061, usdt=9951.02, px=78002.0)
act, det = rb.rebalance("k", "s", dry=True)
ok("季度月 → sell", act == "sell", act)
ok("trigger 提季度", "季度" in (det.get("trigger") or ""), det.get("trigger"))
rb.is_quarter_month = lambda dt=None: (dt or rb.now_hkt()).month in (1, 4, 7, 10)

print("\n=== T10: 買入 POST 用 quoteOrderQty ===")
calls = mock_env(btc=0.45, usdt=53000.0, px=78002.0)
act, det = rb.rebalance("k", "s", dry=False)
post = [c for c in calls if c[0] == "POST"][0]
ok("side = BUY", post[2].get("side") == "BUY", post[2])
ok("有 quoteOrderQty", "quoteOrderQty" in post[2], post[2])
ok("冇 quantity (BUY 用 quote)", "quantity" not in post[2], post[2])

print(f"\n{'=' * 60}\n結果: {PASS} PASS / {FAIL} FAIL\n{'=' * 60}")
sys.exit(1 if FAIL else 0)
