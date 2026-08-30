#!/usr/bin/env python3
"""binance_testnet_paper.py — 引擎訊號 → Binance Spot Testnet 落真單

前置:
  1. https://testnet.binance.vision 用 GitHub 授權登入
  2. Generate HMAC-SHA256 key pair → 攞 API key/secret
  3. export BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET
     (或寫入 ~/.hermes/secrets/binance_testnet.env)

用法:
  python3 binance_testnet_paper.py                # 主流程: seed 引擎訊號 → 落單 → 檢查
  python3 binance_testnet_paper.py --status       # 睇 testnet 帳戶＋開放單
  python3 binance_testnet_paper.py --cancel-all   # 取消全部開放單
  python3 binance_testnet_paper.py --reconcile    # 對帳: testnet 成交 → 更新本地 log
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

BASE = "https://testnet.binance.vision"  # endpoints 自帶 /api/v3/...
SYMBOL = "BTCUSDT"
LOG_PATH = os.path.expanduser("~/.hermes/reports/btc_testnet_orders.json")
ENV_PATH = os.path.expanduser("~/.hermes/secrets/binance_testnet.env")

TAKER_FEE = 0.001          # 0.1% (testnet 同主網同 fee schedule)
MIN_NOTIONAL = 10.0        # BTCUSDT minimum
LOT_STEP = 0.00001         # BTC lot step


def _load_keys():
    key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
    secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "")
    if not key and os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BINANCE_TESTNET_API_KEY="):
                    key = line.split("=", 1)[1]
                elif line.startswith("BINANCE_TESTNET_API_SECRET="):
                    secret = line.split("=", 1)[1]
    return key, secret


def _signed_request(method, path, params, key, secret):
    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 10000
    query = urllib.parse.urlencode(params)
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{query}&signature={sig}"
    req = urllib.request.Request(url, method=method, headers={"X-MBX-APIKEY": key})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _public_request(path, params=None):
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            return json.load(f)
    return {"orders": [], "history": []}


def save_log(log):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def account_status(key, secret):
    return _signed_request("GET", "/api/v3/account", {}, key, secret)


def current_price():
    return float(_public_request("/api/v3/ticker/price", {"symbol": SYMBOL})["price"])


def exchange_filters(key, secret):
    """攞 LOT_SIZE / MIN_NOTIONAL — testnet 可能同主網唔同.

    Testnet 實測 (2026-08-29): BTCUSDT stepSize/minQty 返 0 — 嗰陣用預設 LOT_STEP.
    """
    info = _public_request("/api/v3/exchangeInfo", {"symbol": SYMBOL})
    sym = info["symbols"][0]
    lot_step, lot_min = LOT_STEP, 0.0
    min_notional = MIN_NOTIONAL
    for f in sym["filters"]:
        if f["filterType"] == "LOT_SIZE":
            lot_step = float(f["stepSize"]) or LOT_STEP   # testnet 返 0 → fallback
            lot_min = float(f["minQty"])
        elif f["filterType"] in ("NOTIONAL", "MIN_NOTIONAL"):
            min_notional = float(f.get("minNotional") or f.get("notional") or MIN_NOTIONAL)
    return lot_step, lot_min, min_notional


def round_step(qty, step):
    return max(0.0, int(qty / step) * step)


def place_signal_order(setup, key, secret):
    """引擎 setup → testnet 真單 (market 進場 + OCO SL/TP1).

    setup: btc_engine.py 輸出格式 (btc_side/btc_entry/btc_stop/btc_tp1/pattern)
    """
    side = setup["btc_side"]
    entry = float(setup["btc_entry"])
    stop = float(setup["btc_stop"])
    tp1 = float(setup["btc_tp1"]) if setup.get("btc_tp1") else None
    pattern = setup.get("pattern", "?")

    px = current_price()
    lot_step, lot_min, min_notional = exchange_filters(key, secret)

    # Pre-flight level 驗證 — OCO 拒單係因為 TP 喺市價錯邊 (下單必敗, 先擋慳手續費)
    if tp1:
        if side == "BUY" and tp1 <= px * 1.0005:
            return None, f"TP1 {tp1} 喺市價 {px:.0f} 下面 — BUY OCO 必拒, skip"
        if side == "SELL" and tp1 >= px * 0.9995:
            return None, f"TP1 {tp1} 喺市價 {px:.0f} 上面 — SELL OCO 必拒, skip"
        if side == "BUY" and stop >= px * 0.9995:
            return None, f"SL {stop} 喺市價 {px:.0f} 上面 — BUY OCO 必拒, skip"
        if side == "SELL" and stop <= px * 1.0005:
            return None, f"SL {stop} 喺市價 {px:.0f} 下面 — SELL OCO 必拒, skip"

    # qty: USD 200 notional / entry (paper 額度), round 落 step
    notional = 200.0
    qty = round_step(notional / entry, lot_step)
    if qty < lot_min or qty * entry < min_notional:
        return None, f"qty {qty} below filter (step={lot_step}, min_notional={min_notional})"

    # 進場: market (MVP 簡化; 引擎 breakout 訊號市價追)
    order = _signed_request("POST", "/api/v3/order", {
        "symbol": SYMBOL, "side": side, "type": "MARKET", "quantity": f"{qty:.5f}",
    }, key, secret)

    fills = order.get("fills", [])
    fill_px = sum(float(f["price"]) * float(f["qty"]) for f in fills) / sum(float(f["qty"]) for f in fills) if fills else px
    fill_qty = sum(float(f["qty"]) for f in fills) or qty
    fee_paid = sum(float(f["commission"]) for f in fills)

    rec = {
        "pattern": pattern, "side": side, "qty": fill_qty,
        "order_id": order["orderId"], "status": "FILLED_ENTRY",
        "entry_fill": round(fill_px, 2), "fee": fee_paid,
        "planned_stop": stop, "planned_tp1": tp1,
        "seeded_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # OCO 出場: SELL 單 → OCO SELL stop-loss-limit + limit-maker (TP)
    if tp1:
        exit_side = "SELL" if side == "BUY" else "BUY"
        exit_qty = round_step(fill_qty, lot_step)
        try:
            if exit_side == "SELL":
                oco = _signed_request("POST", "/api/v3/order/oco", {
                    "symbol": SYMBOL, "side": "SELL",
                    "quantity": f"{exit_qty:.5f}",
                    "price": f"{tp1 * 0.999:.2f}",         # limit maker 略低過 TP
                    "stopPrice": f"{stop:.2f}",
                    "stopLimitPrice": f"{stop * 0.9985:.2f}",
                    "stopLimitTimeInForce": "GTC",
                }, key, secret)
            else:
                oco = _signed_request("POST", "/api/v3/order/oco", {
                    "symbol": SYMBOL, "side": "BUY",
                    "quantity": f"{exit_qty:.5f}",
                    "price": f"{tp1 * 1.001:.2f}",
                    "stopPrice": f"{stop:.2f}",
                    "stopLimitPrice": f"{stop * 1.0015:.2f}",
                    "stopLimitTimeInForce": "GTC",
                }, key, secret)
            rec["oco_id"] = oco["orderListId"]
            rec["status"] = "OCO_PLACED"
            rec["oco_leg_ids"] = [o["orderId"] for o in oco.get("orders", [])]
        except urllib.error.HTTPError as e:
            rec["status"] = "OCO_FAILED"
            rec["oco_error"] = e.read().decode()[:200]
            # 安全網: OCO 掛唔上 → 立刻 market 平返 (冇裸倉過夜)
            try:
                _signed_request("POST", "/api/v3/order", {
                    "symbol": SYMBOL, "side": exit_side, "type": "MARKET",
                    "quantity": f"{exit_qty:.5f}",
                }, key, secret)
                rec["status"] = "FLATTENED_OCO_FAILED"
                rec["flatten_note"] = "OCO rejected → emergency market close"
            except urllib.error.HTTPError as e2:
                rec["flatten_error"] = e2.read().decode()[:200]
            rec["flatten_ts"] = time.time()

    log = load_log()
    log["orders"].append(rec)
    save_log(log)
    return rec, None


def reconcile(key, secret):
    """對帳: 開放 OCO 有冇成交 → 更新本地 log + 統計."""
    log = load_log()
    open_orders = _signed_request("GET", "/api/v3/openOrders", {"symbol": SYMBOL}, key, secret)
    open_ids = {o["orderId"] for o in open_orders}
    changed = 0
    for rec in log["orders"]:
        if rec.get("status") != "OCO_PLACED":
            continue
        if rec.get("oco_id") is None:
            continue
        # OCO 兩腿 — 攞 openOrders 睇剩邊條
        pass
    # 簡化對帳: 用 myTrades 對成交
    trades = _signed_request("GET", "/api/v3/myTrades", {"symbol": SYMBOL}, key, secret)
    log["trades_raw"] = trades[-50:]
    save_log(log)
    return open_orders, trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--cancel-all", action="store_true")
    ap.add_argument("--reconcile", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只顯示會落咩單, 唔真落")
    args = ap.parse_args()

    key, secret = _load_keys()
    if not key or not secret:
        print("❌ 未設定 BINANCE_TESTNET_API_KEY/SECRET")
        print(f"   1. 去 https://testnet.binance.vision (GitHub 授權登入)")
        print(f"   2. Generate key pair")
        print(f"   3. mkdir -p ~/.hermes/secrets && cat > ~/.hermes/secrets/binance_testnet.env")
        print(f"      BINANCE_TESTNET_API_KEY=...")
        print(f"      BINANCE_TESTNET_API_SECRET=...")
        sys.exit(1)

    if args.status:
        acct = account_status(key, secret)
        balances = {b["asset"]: b["free"] for b in acct["balances"] if float(b["free"]) > 0}
        print("💰 Testnet balances:", balances)
        opens = _signed_request("GET", "/api/v3/openOrders", {"symbol": SYMBOL}, key, secret)
        print(f"📋 Open orders ({len(opens)}):")
        for o in opens:
            print(f"   {o['symbol']} {o['side']} {o['type']} qty={o['origQty']} px={o.get('price','-')} stop={o.get('stopPrice','-')}")
        return

    if args.cancel_all:
        _signed_request("DELETE", "/api/v3/openOrders", {"symbol": SYMBOL}, key, secret)
        print("🧹 All open orders cancelled")
        return

    if args.reconcile:
        opens, trades = reconcile(key, secret)
        print(f"📋 open: {len(opens)} | trades: {len(trades)}")
        for t in trades[-5:]:
            print(f"   {t['time']} {t['qty']}@{t['price']} commission={t['commission']} {t.get('isBuyer')}")
        return

    # 主流程: 引擎訊號 → 落單
    json_path = os.path.join(REPO, "btc_last_analysis.json")
    if not os.path.exists(json_path):
        print("⚠️ 冇 btc_last_analysis.json — 先跑 python3 btc_engine.py")
        sys.exit(1)
    with open(json_path) as f:
        data = json.load(f)
    setups = [s for s in data.get("setups", []) if s.get("verified")]
    if not setups:
        print("⏳ 冇 verified setups")
        return
    log = load_log()
    # 同 pattern dedup (即時更新: 落一單入一單, 5min cron 唔會重複)
    def _live_patterns():
        return {o["pattern"] for o in load_log()["orders"] if o.get("status") in ("FILLED_ENTRY", "OCO_PLACED", "OCO_FAILED")}

    todo = [s for s in setups if s.get("pattern") not in _live_patterns()]
    if not todo:
        print("⏳ 全部 setups 已落單")
        return

    # Flatten 冷靜期: 同 pattern 15 分鐘內 OCO 失敗過 → 冇意義即刻再入 (條件冇變)
    now = time.time()
    cooled = []
    for s in todo:
        recent_fail = False
        for o in log["orders"]:
            if o.get("status") not in ("FLATTENED_OCO_FAILED", "OCO_FAILED"):
                continue
            if o.get("pattern") != s.get("pattern"):
                continue
            ts = o.get("flatten_ts")
            if isinstance(ts, (int, float)) and now - ts < 900:
                recent_fail = True
                break
        if recent_fail:
            print(f"🧊 {s.get('pattern','?')} skip — OCO 失敗冷靜期 (15 分鐘)")
        else:
            cooled.append(s)
    todo = cooled
    if not todo:
        return
    # 風控: 同方向最多 2 單 live + 相反方向鎖
    for s in todo:
        live = [o for o in load_log()["orders"] if o.get("status") in ("FILLED_ENTRY", "OCO_PLACED")]
        same_side = [o for o in live if o.get("side") == s["btc_side"]]
        opp_side = [o for o in live if o.get("side") != s["btc_side"]]
        if opp_side:
            print(f"🚫 {s.get('pattern','?')} skip — 有一邊向 {opp_side[0]['side']} live 倉, 唔開反向")
            continue
        if len(same_side) >= 2:
            print(f"🚫 {s.get('pattern','?')} skip — 同向 live 已 2 單 (cap)")
            continue
        if args.dry_run:
            print(f"[DRY] {s['btc_side']} {s.get('pattern','?')} entry={s['btc_entry']} SL={s['btc_stop']} TP1={s.get('btc_tp1')}")
            continue
        rec, err = place_signal_order(s, key, secret)
        if err:
            print(f"❌ {s.get('pattern','?')}: {err}")
        else:
            print(f"✅ {rec['side']} {rec['pattern']} fill={rec['entry_fill']} qty={rec['qty']} status={rec['status']} oco={rec.get('oco_id','-')}")


if __name__ == "__main__":
    main()
