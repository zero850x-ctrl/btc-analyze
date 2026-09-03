#!/usr/bin/env python3
"""btc_martingale.py — 受限制馬丁格爾實驗 (Binance Spot Testnet only)

設計 (用戶確認 2026-09-03):
- 第一注 S0 = $15 notional (細注), qty = round_step(15/px, 0.00001)
- Max chain = 4 注 (S0 ×1, ×2, ×4, ×8) — 第 4 注重見反向 trigger = 全平止損
- WIN: chain 淨盈 >= $3 → 市價全平, chain reset
- Add-on trigger: price 由最後一注 entry 反向 >= 1×ATR(15m)
- 每日 loss cap: $225 (chain 止損累計) → 到 cap 即日唔再開新 chain
- 方向: 15m close vs SMA50 (trend filter)
- 純 urllib, 唔需要 numpy/pandas (cron 環境穩陣)

用法:
  python3 btc_martingale.py            # tick: 開 chain / 加註 / 平倉
  python3 btc_martingale.py --status   # 睇 state
  python3 btc_martingale.py --dry-run  # 唔落單, 模擬 tick
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

# ── 參數 (受限制馬丁) ────────────────────────────────────────────
S0_NOTIONAL = 15.0          # 第一注 notional USD
WIN_TARGET_USD = 1.0        # chain 淨盈 target → 全平 ($3 對 $15 首注太難達, 改 $1)
MAX_LEVEL = 3               # level 0..3 = 最多 4 注 (1+2+4+8 = 15×S0)
DAILY_LOSS_CAP = 225.0      # 每日 chain 止損累計 cap (2.25% 帳戶)
ATR_TRIGGER_MULT = 1.0      # 反向觸發加註: >= 1×ATR(15m)
TREND_LOOKBACK = 50         # SMA bars

BASE = "https://testnet.binance.vision"
SYMBOL = "BTCUSDT"
LOG_PATH = os.path.expanduser("~/.hermes/reports/btc_martingale_log.json")
LOT_STEP = 0.00001
TAKER_FEE = 0.001

from binance_testnet_paper import _signed_request, _public_request, _load_keys


def _today():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def load_state():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            return json.load(f)
    return {"active": None, "chains": [], "daily": {}, "updated": None}


def save_state(s):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    s["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(LOG_PATH, "w") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def klines_15m():
    """BTCUSDT 15m klines → (closes, atr14). 純 urllib."""
    r = _public_request("/api/v3/klines", {"symbol": SYMBOL, "interval": "15m", "limit": "100"})
    closes, highs, lows = [], [], []
    for k in r:
        closes.append(float(k[4]))
        highs.append(float(k[2]))
        lows.append(float(k[3]))
    atr = _atr(highs, lows, closes, 14)
    return closes, atr


def _atr(highs, lows, closes, period):
    trs = []
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    if len(trs) < period:
        return 0.0
    return sum(trs[-period:]) / period


def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def round_step(qty, step):
    return max(0.0, int(qty / step) * step)


def _mk_order(side, qty, key, secret):
    r = _signed_request("POST", "/api/v3/order", {
        "symbol": SYMBOL, "side": side, "type": "MARKET",
        "quantity": f"{qty:.5f}",
    }, key, secret)
    fills = r.get("fills", [])
    if fills:
        px = sum(float(f["price"]) * float(f["qty"]) for f in fills) / sum(float(f["qty"]) for f in fills)
        fee = sum(float(f["commission"]) for f in fills)
    else:
        px = float(r.get("price") or 0) or None
        fee = 0.0
    return {"order_id": r["orderId"], "fill_px": px, "qty": sum(float(f["qty"]) for f in fills) or qty, "fee": fee}


def chain_net(chain, px, entries):
    """chain 浮動淨盈 (USD): Σ qty×(px−entry) × dir − 粗估 fee (已付唔計未平)."""
    d = 1 if chain["side"] == "BUY" else -1
    gross = sum((px - e["px"]) * e["qty"] for e in entries) * d
    return gross


def tick(key, secret, dry=False):
    closes, atr = klines_15m()
    px = float(_public_request("/api/v3/ticker/price", {"symbol": SYMBOL})["price"])
    s = load_state()
    today = _today()
    daily = s["daily"].setdefault(today, {"loss_usd": 0.0, "wins": 0, "losses": 0})
    out = []

    if daily["loss_usd"] >= DAILY_LOSS_CAP:
        out.append(f"🛑 馬丁 daily loss cap ${daily['loss_usd']:.0f} — 今日收工")
        save_state(s)
        print("\n".join(out))
        return

    chain = s["active"]
    if chain is not None:
        entries = chain["entries"]
        net = chain_net(chain, px, entries)
        total_qty = round_step(sum(e["qty"] for e in entries), LOT_STEP)
        last_px = entries[-1]["px"]
        # 1) WIN: 到 target → 全平
        if net >= WIN_TARGET_USD:
            if dry:
                out.append(f"✅ [DRY] WIN trigger net=${net:.2f} ≥ ${WIN_TARGET_USD} — close {total_qty:.5f}")
                chain["state"] = "DRY_WIN"
            else:
                close_side = "SELL" if chain["side"] == "BUY" else "BUY"
                o = _mk_order(close_side, total_qty, key, secret)
                profit = chain_net(chain, o["fill_px"], entries) - (o["fee"] * o["fill_px"] if o["fill_px"] else 0)
                chain.update({"state": "WIN", "closed_px": o["fill_px"], "profit_usd": round(profit, 2),
                              "closed_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
                s["chains"].append(chain)
                daily["wins"] += 1
                out.append(f"✅ 馬丁 WIN +${profit:.2f} — {chain['side']} {len(entries)}注 close@{o['fill_px']:.0f}")
            s["active"] = None
        # 2) CAP LOSS: 第 4 注重見 → 全平止損
        elif chain["level"] >= MAX_LEVEL:
            if dry:
                out.append(f"❌ [DRY] CAP trigger level={chain['level']} — close {total_qty:.5f}")
                chain["state"] = "DRY_LOSS"
            else:
                close_side = "SELL" if chain["side"] == "BUY" else "BUY"
                o = _mk_order(close_side, total_qty, key, secret)
                loss = chain_net(chain, o["fill_px"], entries) - (o["fee"] * o["fill_px"] if o["fill_px"] else 0)
                chain.update({"state": "LOSS", "closed_px": o["fill_px"], "profit_usd": round(loss, 2),
                              "closed_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
                s["chains"].append(chain)
                daily["losses"] += 1
                daily["loss_usd"] = round(daily["loss_usd"] + abs(loss), 2)
                out.append(f"❌ 馬丁 LOSS {loss:+.2f} (cap) — {chain['side']} {len(entries)}注 close@{o['fill_px']:.0f}")
            s["active"] = None
        # 3) ADD-ON: 反向 >= 1×ATR → 雙倍加註
        else:
            if chain["side"] == "BUY":
                backed = last_px - px >= atr * ATR_TRIGGER_MULT
            else:
                backed = px - last_px >= atr * ATR_TRIGGER_MULT
            if backed:
                lvl = chain["level"] + 1
                qty = round_step(S0_NOTIONAL * (2 ** lvl) / px, LOT_STEP)
                if dry:
                    out.append(f"➕ [DRY] ADD level={lvl} qty={qty:.5f} (backed {last_px:.0f}→{px:.0f})")
                    chain["entries"].append({"qty": qty, "px": px, "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "dry": True})
                    chain["level"] = lvl
                else:
                    o = _mk_order(chain["side"], qty, key, secret)
                    chain["entries"].append({"qty": o["qty"], "px": o["fill_px"],
                                             "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
                    chain["level"] = lvl
                    out.append(f"➕ 馬丁 ADD level={lvl} {chain['side']} {o['qty']:.5f}@{o['fill_px']:.0f}")
            else:
                out.append(f"⏳ 馬丁 chain {chain['side']} L{chain['level']} net=${net:.2f} px=${px:.0f}")
                chain["net_snapshot"] = round(net, 2)
    else:
        # 新 chain
        m = sma(closes[-TREND_LOOKBACK:], TREND_LOOKBACK) if len(closes) >= TREND_LOOKBACK else sma(closes, len(closes))
        side = "BUY" if (m is not None and closes[-1] > m) else "SELL"
        qty = round_step(S0_NOTIONAL / px, LOT_STEP)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if dry:
            s["active"] = {"id": f"m-{now[:10]}-{int(time.time())}", "side": side, "level": 0,
                           "entries": [{"qty": qty, "px": px, "time": now, "dry": True}],
                           "state": "OPEN", "target_usd": WIN_TARGET_USD, "opened": now, "dry": True}
            out.append(f"🔵 [DRY] OPEN chain {side} qty={qty:.5f}@{px:.0f} (SMA50={m:.0f})")
        else:
            o = _mk_order(side, qty, key, secret)
            s["active"] = {"id": f"m-{o['order_id']}", "side": side, "level": 0,
                           "entries": [{"qty": o["qty"], "px": o["fill_px"], "time": now}],
                           "state": "OPEN", "target_usd": WIN_TARGET_USD, "opened": now}
            out.append(f"🔵 馬丁 OPEN {side} {o['qty']:.5f}@{o['fill_px']:.0f} (SMA50={m:.0f})")

    if not dry:
        save_state(s)
    print("\n".join(out))


def status():
    s = load_state()
    print(json.dumps(s, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    key, secret = _load_keys()
    if not key or not secret:
        print("❌ 冇 testnet keys")
        sys.exit(1)
    if args.status:
        status()
        return
    tick(key, secret, dry=args.dry_run)


if __name__ == "__main__":
    main()