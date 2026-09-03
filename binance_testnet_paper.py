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

# fix/btc-exit-symmetry: 落單前 hard RR gate — 歷史實證 RR<1.2 嘅單全部贏細輸大
# (engine json 嘅 rr_tp1 有缺口, 呢度做最後防線, 唔信 json)
MIN_RR_EXEC = 1.2


def _compute_rr(setup):
    """setup → TP1/risk RR (用 planned entry/stop/tp1, 落單前驗證用)."""
    try:
        entry = float(setup["btc_entry"])
        stop = float(setup["btc_stop"])
        tp1 = float(setup["btc_tp1"]) if setup.get("btc_tp1") else None
    except (KeyError, TypeError, ValueError):
        return None
    if tp1 is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return abs(tp1 - entry) / risk


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


def place_signal_order(setup, key, secret, atr=None):
    """引擎 setup → testnet 真單 (market 進場 + 3 段出場).

    fix/btc-exit-symmetry exit 結構:
      - 1/3 qty → OCO A: stop-limit SL + limit TP1 (掛 TP 本價, 唔食 0.1% maker 差價)
      - 1/3 qty → OCO B: stop-limit SL + limit TP2 (冇 TP2 就併入尾倉)
      - 尾倉 1/3 → 獨立 stop-limit SL, 之後由 reconcile 按 ATR trailing
    任一段 exit 建立失敗 → 取消已建 exit + market flatten (冇裸倉).

    setup: btc_engine.py 輸出格式 (btc_side/btc_entry/btc_stop/btc_tp1/btc_tp2/pattern)
    """
    side = setup["btc_side"]
    entry = float(setup["btc_entry"])
    stop = float(setup["btc_stop"])
    tp1 = float(setup["btc_tp1"]) if setup.get("btc_tp1") else None
    tp2 = float(setup["btc_tp2"]) if setup.get("btc_tp2") else None
    pattern = setup.get("pattern", "?")

    # RR hard gate — 落單前最後防線, 唔信 json
    rr = _compute_rr(setup)
    if rr is not None and rr < MIN_RR_EXEC:
        return None, f"RR {rr:.2f} < {MIN_RR_EXEC} hard gate — skip"

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
        "planned_stop": stop, "planned_tp1": tp1, "planned_tp2": tp2,
        "atr": round(float(atr), 2) if atr else None,
        "seeded_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # ── 3 段出場 (1/3 each; TP2 冇就尾倉 2/3) ─────────────────────
    exit_side = "SELL" if side == "BUY" else "BUY"
    q1 = round_step(fill_qty / 3, lot_step)
    q2 = round_step(fill_qty / 3, lot_step) if tp2 else 0.0
    q3 = round_step(fill_qty - q1 - q2 + 1e-9, lot_step)   # +epsilon: float 精度唔好蝕尾數
    if q1 + q2 + q3 > fill_qty + 1e-12:      # 尾數保護 (float 誤差唔計, round 後唔可以 over-sell)
        q3 = round_step(fill_qty - q1 - q2, lot_step) - lot_step
        if q3 < 0:
            q3 = 0.0

    exit_orders = []   # (tag, ids) for cleanup
    leg_ids = []

    def _oco_qty(sl_qty, tp_price):
        if sl_qty <= 0:
            return None
        if exit_side == "SELL":
            return _signed_request("POST", "/api/v3/order/oco", {
                "symbol": SYMBOL, "side": "SELL",
                "quantity": f"{sl_qty:.5f}",
                "price": f"{tp_price:.2f}",              # TP 本價 (fix: 唔再 ×0.999 蝕 0.1%)
                "stopPrice": f"{stop:.2f}",
                "stopLimitPrice": f"{stop * 0.9985:.2f}",
                "stopLimitTimeInForce": "GTC",
            }, key, secret)
        return _signed_request("POST", "/api/v3/order/oco", {
            "symbol": SYMBOL, "side": "BUY",
            "quantity": f"{sl_qty:.5f}",
            "price": f"{tp_price:.2f}",
            "stopPrice": f"{stop:.2f}",
            "stopLimitPrice": f"{stop * 1.0015:.2f}",
            "stopLimitTimeInForce": "GTC",
        }, key, secret)

    def _sl_only_qty(sl_qty):
        if sl_qty <= 0:
            return None
        if exit_side == "SELL":
            return _signed_request("POST", "/api/v3/order", {
                "symbol": SYMBOL, "side": "SELL", "type": "STOP_LOSS_LIMIT",
                "quantity": f"{sl_qty:.5f}",
                "stopPrice": f"{stop:.2f}",
                "price": f"{stop * 0.9985:.2f}",
                "timeInForce": "GTC",
            }, key, secret)
        return _signed_request("POST", "/api/v3/order", {
            "symbol": SYMBOL, "side": "BUY", "type": "STOP_LOSS_LIMIT",
            "quantity": f"{sl_qty:.5f}",
            "stopPrice": f"{stop:.2f}",
            "price": f"{stop * 1.0015:.2f}",
            "timeInForce": "GTC",
        }, key, secret)

    try:
        oco_a = _oco_qty(q1, tp1) if tp1 else None
        if oco_a is not None:
            rec["oco_a_id"] = oco_a["orderListId"]
            ids_a = [o["orderId"] for o in oco_a.get("orders", [])]
            leg_ids.extend(ids_a)
            exit_orders.append(("OCO_A", ids_a))
        oco_b = _oco_qty(q2, tp2) if tp2 else None
        if oco_b is not None:
            rec["oco_b_id"] = oco_b["orderListId"]
            ids_b = [o["orderId"] for o in oco_b.get("orders", [])]
            leg_ids.extend(ids_b)
            exit_orders.append(("OCO_B", ids_b))
        l3 = _sl_only_qty(q3)
        if l3 is not None:
            rec["l3_id"] = l3["orderId"]
            leg_ids.append(l3["orderId"])
            exit_orders.append(("L3", [l3["orderId"]]))
    except urllib.error.HTTPError as e:
        # exit 建立失敗 → 取消已建 exit order + market flatten (冇裸倉)
        for tag, ids in exit_orders:
            for oid in ids:
                try:
                    _signed_request("DELETE", "/api/v3/order",
                                    {"symbol": SYMBOL, "orderId": oid}, key, secret)
                except Exception:
                    pass
        try:
            _signed_request("POST", "/api/v3/order", {
                "symbol": SYMBOL, "side": exit_side, "type": "MARKET",
                "quantity": f"{fill_qty:.5f}",
            }, key, secret)
            rec["status"] = "FLATTENED_OCO_FAILED"
            rec["flatten_note"] = "exit order 建立失敗 → emergency market close"
        except urllib.error.HTTPError as e2:
            rec["flatten_error"] = e2.read().decode()[:200]
        rec["oco_error"] = e.read().decode()[:200]
        rec["flatten_ts"] = time.time()

    if leg_ids and rec.get("status") != "FLATTENED_OCO_FAILED":
        rec["exit_leg_ids"] = leg_ids
        rec["status"] = "OCO_PLACED"
    elif not leg_ids and rec.get("status") == "FILLED_ENTRY":
        rec["status"] = "OCO_FAILED"
        rec["oco_error"] = "no exit legs built"

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
    # C: 平倉/flatten 後同 pattern 60 分鐘冷靜期 — 防 churn (平完即刻重入, 每次俾費用)
    now = datetime.now(timezone.utc)
    cooled_out = []
    for s in todo:
        blocked = False
        for o in log["orders"]:
            if o.get("pattern") != s.get("pattern"):
                continue
            ts_raw = o.get("closed_time") or o.get("seeded_time") or ""
            if o.get("status") in ("SKIP_PREFLIGHT",) and o.get("closed_time") is None:
                continue  # skip 記憶由 preflight block 處理, 呢度只管平倉/失敗
            if o.get("status") not in ("CLOSED", "FLATTENED_OCO_FAILED", "OCO_FAILED", "SKIP_PREFLIGHT"):
                continue
            try:
                ts = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            age_min = (now - ts).total_seconds() / 60
            if age_min < 60:
                blocked = True
                break
        if blocked:
            print(f"🧊 {s.get('pattern','?')} skip — 平倉/失敗後冷靜期 (60 分鐘)")
        else:
            cooled_out.append(s)
    todo = cooled_out
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
    # 風控 (fix/btc-exit-symmetry): 同 pattern 限 1 單 + 同方向限 1 單 + 相反方向鎖
    for s in todo:
        live = [o for o in load_log()["orders"] if o.get("status") in ("FILLED_ENTRY", "OCO_PLACED")]
        same_pattern = [o for o in live if o.get("pattern") == s.get("pattern")]
        same_side = [o for o in live if o.get("side") == s["btc_side"]]
        opp_side = [o for o in live if o.get("side") != s["btc_side"]]
        if same_pattern:
            print(f"🚫 {s.get('pattern','?')} skip — 同 pattern 已 live, 限 1 單")
            continue
        if opp_side:
            print(f"🚫 {s.get('pattern','?')} skip — 有一邊向 {opp_side[0]['side']} live 倉, 唔開反向")
            continue
        if same_side:
            print(f"🚫 {s.get('pattern','?')} skip — 同向 live 已 1 單 (cap)")
            continue
        # RR hard gate — 落單前最後防線 (歷史單 RR<1.2 全部贏細輸大)
        rr = _compute_rr(s)
        if rr is not None and rr < MIN_RR_EXEC:
            log2 = load_log()
            log2["orders"].append({
                "pattern": s.get("pattern", "?"), "side": s.get("btc_side"),
                "status": "SKIP_PREFLIGHT", "skip_reason": f"RR {rr:.2f} < {MIN_RR_EXEC} hard gate",
                "seeded_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            save_log(log2)
            print(f"❌ {s.get('pattern','?')}: RR {rr:.2f} < {MIN_RR_EXEC} hard gate — skip")
            continue
        if args.dry_run:
            print(f"[DRY] {s['btc_side']} {s.get('pattern','?')} entry={s['btc_entry']} SL={s['btc_stop']} TP1={s.get('btc_tp1')} TP2={s.get('btc_tp2')} RR={rr}")
            continue
        rec, err = place_signal_order(s, key, secret, atr=data.get("atr"))
        if err:
            # 記入 log (SKIP_PREFLIGHT) — 下次 tick 見到同 pattern 已 skip 就靜默, 唔會重複 ❌
            log2 = load_log()
            log2["orders"].append({
                "pattern": s.get("pattern", "?"), "side": s.get("btc_side"),
                "status": "SKIP_PREFLIGHT", "skip_reason": err,
                "seeded_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            save_log(log2)
            print(f"❌ {s.get('pattern','?')}: {err}")
        else:
            print(f"✅ {rec['side']} {rec['pattern']} fill={rec['entry_fill']} qty={rec['qty']} status={rec['status']} ocoA={rec.get('oco_a_id','-')} ocoB={rec.get('oco_b_id','-')} l3={rec.get('l3_id','-')}")


if __name__ == "__main__":
    main()
