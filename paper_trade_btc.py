#!/usr/bin/env python3
"""paper_trade_btc.py — BTC paper trading (XAUUSD paper_trade.py BTC 適配版)

改動 vs 原版:
  1. LOG_PATH → paper_trade_log_btc.json (log 完全分離)
  2. _fetch_m30 → BTC 數據 (TV BITSTAMP M30 → yfinance BTC-USD 30m fallback)
  3. 24/7 — 移除 danger hour hard-block; 保留 anti-stacking/daily loss/dedup
  4. JSON 路徑 → btc_last_analysis.json (btc_engine.py 輸出)
  5. Coinbase 即時價驗證 (代替 GC=F basis check)

用法:
  python3 paper_trade_btc.py                # check LIVE + seed new (cron 主入口)
  python3 paper_trade_btc.py --check-only   # 只 check
  python3 paper_trade_btc.py --seed-only    # 只 seed
  python3 paper_trade_btc.py --reset        # 清空 BTC paper log (危險)
"""
import argparse
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

REPO = os.path.dirname(os.path.abspath(__file__))

# ── 引入原版 paper_trade (函數級重用) + analyze_v3 ─────────────────
spec = importlib.util.spec_from_file_location("analyze_v3", os.path.join(REPO, "analyze_v3.py"))
av3 = importlib.util.module_from_spec(spec); spec.loader.exec_module(av3)

spec2 = importlib.util.spec_from_file_location("paper_trade_xau", os.path.join(REPO, "paper_trade.py"))
pt = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(pt)

# ── BTC 覆寫 ────────────────────────────────────────────────
LOG_PATH = os.path.expanduser("~/.hermes/reports/paper_trade_log_btc.json")
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
COINBASE_DIFF_PCT = 0.8      # 主源 vs Coinbase >0.8% → UNVERIFIED
BTC_RISK_PCT = 0.5           # 每筆 0.5% 帳戶風險 (示意: $10k 帳戶)
MAX_DAILY_LOSS_R = 2.0       # BTC 波動大 — 日內止損比黃金 (原版 1.5R?) 更寬
MIN_HOLDING_BARS = 3

# 原版常數複製 (唔 import 改動)
SAME_DIR_MAX_CONCURRENT = 2


def _log(msg):
    print(msg)


def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            return json.load(f)
    return {"trades": [], "history": []}


def save_log(log):
    parent = os.path.dirname(LOG_PATH)
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".paper_trade_log_btc.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, LOG_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _coinbase_spot():
    try:
        r = requests.get(COINBASE_TICKER_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return float(r.json()["price"])
    except Exception as e:
        _log(f"  ⚠️ Coinbase fetch failed: {e}")
        return None


def _fetch_m30_btc(start=None, end=None):
    """BTC M30 bars — TV BITSTAMP 主源 → yfinance BTC-USD fallback.

    Returns (bars_df with open/high/low/close/volume + datetime UTC-naive, data_source)
    data_source: 'tv' | 'yf'
    """
    bars = None
    data_source = "yf"
    try:
        from tvDatafeed import TvDatafeed, Interval as TVInterval
        tv = TvDatafeed()
        b = tv.get_hist("BTCUSD", "BITSTAMP", interval=TVInterval.in_30_minute, n_bars=500)
        if b is not None and len(b) > 50:
            b = b.reset_index()
            bars = b
            data_source = "tv"
            _log(f"  📊 TradingView BITSTAMP:BTCUSD M30: {len(bars)} bars")
    except Exception as e:
        _log(f"  ⚠️ TV fetch failed: {e}")
    if bars is None:
        try:
            t = yf.Ticker("BTC-USD")
            b = t.history(period="7d", interval="30m")
            if not b.empty:
                b = b.reset_index()
                if "Datetime" in b.columns:
                    b = b.rename(columns={"Datetime": "datetime"})
                if "datetime" in b.columns and b["datetime"].dt.tz is not None:
                    b["datetime"] = b["datetime"].dt.tz_convert("UTC").dt.tz_localize(None)
                bars = b
                data_source = "yf"
                _log(f"  📊 yfinance BTC-USD M30: {len(bars)} bars")
        except Exception as e:
            _log(f"  ⚠️ yfinance BTC fetch failed: {e}")
    if bars is None or bars.empty:
        return None, data_source
    for col in ["open", "high", "low", "close", "volume"]:
        for alt in [col.capitalize(), col.upper()]:
            if alt in bars.columns and col not in bars.columns:
                bars[col] = bars[alt]
    return bars, data_source


def _series_last_close(bars):
    try:
        return float(bars["close"].iloc[-1])
    except Exception:
        return None


def _spot_verified(close_px, series_last, data_source):
    """BTC 版 close 驗證: 主源最後 close vs Coinbase 即時價.

    24/7 市場 — close 應該同 Coinbase 價好近 (>0.8% = 數據源唔一致).
    """
    cb = _coinbase_spot()
    if cb is None or close_px is None:
        return False, cb  # fail-closed: 驗證唔到 = UNVERIFIED
    diff_pct = abs(close_px - cb) / cb * 100
    return (diff_pct <= COINBASE_DIFF_PCT), cb


# ── 簡化版 discipline (照抄原版語義, 冇 danger hours) ──────────────
def _daily_loss_r(log):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0.0
    for t in log.get("history", []):
        if str(t.get("closed_time", ""))[:10] == today and t.get("pnl_r") is not None:
            total += float(t["pnl_r"])
    return total


def _live_same_direction_count(log, direction):
    return sum(1 for t in log.get("trades", [])
               if t.get("status") == "LIVE" and t.get("direction") == direction)


def _consecutive_same_direction(log, direction):
    n = 0
    for t in reversed(log.get("history", [])[-10:]):
        if t.get("direction") == direction:
            n += 1
        else:
            break
    return n


def _signal_key(pattern, direction, entry_mode="breakout"):
    return f"{pattern}|{direction}|{entry_mode}"


def _existing_signal(log, key, today):
    for t in log.get("trades", []):
        if t.get("status") == "LIVE" and _signal_key(t.get("pattern"), t.get("direction"), t.get("entry_mode", "breakout")) == key:
            return t
    for t in log.get("history", []):
        if _signal_key(t.get("pattern"), t.get("direction"), t.get("entry_mode", "breakout")) == key and str(t.get("closed_time", ""))[:10] == today:
            return t
    return None


def _next_trade_id(log, today):
    ym = today[:7].replace("-", "")
    prefix = f"btc-{ym}"
    n = 1
    for t in log.get("trades", []) + log.get("history", []):
        tid = str(t.get("id", ""))
        if tid.startswith(prefix):
            try:
                n = max(n, int(tid.split("-")[-1]) + 1)
            except ValueError:
                pass
    return f"{prefix}-{n:03d}"


def seed_trades(data, setups=None):
    """BTC 版 seed — 邏輯同原版, 但 24/7 冇 danger hour gate."""
    if setups is None:
        setups = data.get("setups", [])
    if not setups:
        _log("⏳ No setups to seed")
        return False

    log = load_log()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    current_price = float(data.get("price", 0) or 0)
    atr = float(data.get("atr", 1) or 1)

    daily_r = _daily_loss_r(log)
    if daily_r <= -MAX_DAILY_LOSS_R:
        _log(f"🚫 Daily loss limit: {daily_r:.1f}R — no new trades")
        return False

    new_count = 0
    skipped = 0
    for s in setups:
        side = s.get("btc_side", "SELL" if "SELL" in str(s.get("direction", "")) else "BUY")
        if not s.get("verified", False):
            skipped += 1
            _log(f"  ⏭️ Skip {s.get('pattern', '?')} — UNVERIFIED ({s.get('unverified_reason', '?')})")
            continue
        entry = s.get("btc_entry")
        stop = s.get("btc_stop")
        tp1 = s.get("btc_tp1")
        tp2 = s.get("btc_tp2")
        if entry is None or stop is None:
            continue
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        # 現價已穿 SL → skip
        if (side == "SELL" and current_price >= stop) or (side == "BUY" and current_price <= stop):
            skipped += 1
            _log(f"  ⏭️ Skip {s.get('pattern', '?')} — price crossed stop")
            continue
        entry_mode = s.get("entry_mode", "breakout")
        sig_key = _signal_key(s.get("pattern", "?"), side, entry_mode)
        prev = _existing_signal(log, sig_key, today)
        if prev:
            skipped += 1
            _log(f"  ⏭️ Skip {s.get('pattern', '?')} {entry_mode} — already {prev.get('status')}")
            continue
        # anti-stacking
        same = _live_same_direction_count(log, side)
        if same >= SAME_DIR_MAX_CONCURRENT:
            skipped += 1
            _log(f"  🚫 Skip {s.get('pattern', '?')} — same-direction cap {SAME_DIR_MAX_CONCURRENT}")
            continue
        opp = _live_same_direction_count(log, "BUY" if side == "SELL" else "SELL")
        if opp > 0:
            skipped += 1
            _log(f"  🚫 Skip {s.get('pattern', '?')} — opposite-direction LIVE exists")
            continue
        n_dir = _consecutive_same_direction(log, side)
        if n_dir >= 3:
            _log(f"  ⚠️ Direction bias: {n_dir} consecutive {side}")

        # position size (USD): BTC_RISK_PCT% of $10k / risk% × notional
        risk_pct_of_px = risk / entry
        size_usd = round(10000 * BTC_RISK_PCT / 100 / risk_pct_of_px, 2) if risk_pct_of_px > 0 else 0

        trade = {
            "id": _next_trade_id(log, today),
            "seeded_date": today,
            "seeded_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "LIVE",
            "direction": side,
            "pattern": s.get("pattern", "?"),
            "confidence": s.get("confidence", "?"),
            "quality": s.get("quality", "?"),
            "entry": round(entry, 2),
            "stop_loss": round(stop, 2),
            "tp1": round(tp1, 2) if tp1 else 0,
            "tp2": round(tp2, 2) if tp2 else 0,
            "risk_amount": round(risk, 2),
            "rr_tp1": round(abs(tp1 - entry) / risk, 2) if tp1 else None,
            "size_usd": size_usd,
            "risk_pct": BTC_RISK_PCT,
            "entry_mode": entry_mode,
            "atr": atr,
            "signal_price": current_price,
        }
        log["trades"].append(trade)
        new_count += 1
        _log(f"  ✅ Seeded: {trade['id']} {side} {s.get('pattern', '?')} @ {entry:.0f} SL={stop:.0f} TP1={tp1 or 0:.0f} size=${size_usd:,.0f}")

    if new_count:
        save_log(log)
        _log(f"\n📝 Seeded {new_count}, skipped {skipped}")
    else:
        _log(f"\n⏳ No new trades (skipped {skipped})")
    return new_count > 0


def check_outcomes(data):
    """BTC 版 check — _simulate_staged_exit 重用原版 (bars 格式相同)."""
    log = load_log()
    live = [t for t in log.get("trades", []) if t.get("status") == "LIVE"]
    if not live:
        _log("⏳ No BTC paper trades to check")
        return
    bars, data_source = _fetch_m30_btc()
    if bars is None or bars.empty:
        _log("⚠️ Could not fetch BTC M30 — skipping check")
        return
    series_last = _series_last_close(bars)
    still = []
    closed = 0
    for trade in log.get("trades", []):
        if trade.get("status") != "LIVE":
            still.append(trade)
            continue
        entry = trade.get("entry", 0)
        stop = trade.get("stop_loss", 0)
        tp1 = trade.get("tp1", 0)
        tp2 = trade.get("tp2", 0)
        direction = trade.get("direction", "BUY")
        seed_dt = pt._parse_dt(trade.get("seeded_time", ""))
        atr = trade.get("atr") or data.get("atr", 1)
        sim = pt._simulate_staged_exit(bars, entry, stop, tp1, tp2, direction, atr,
                                       seed_dt=seed_dt, data_source=data_source)
        if sim.get("closed"):
            close_px = sim.get("close_price")
            verified, cb = _spot_verified(close_px, series_last, data_source)
            if not verified:
                trade["last_unverified"] = {"result": sim["result"], "close_price": close_px,
                                            "pnl_r": sim["pnl_r"], "reason": "Coinbase diff check failed"}
                still.append(trade)
                _log(f"  ❌ UNVERIFIED {sim['result']}: {trade['id']} {direction} @ {close_px} — kept LIVE")
                continue
            trade.update({
                "status": "CLOSED",
                "result": sim["result"],
                "close_price": close_px,
                "pnl_r": sim["pnl_r"],
                "bars_held": sim["bars_held"],
                "tp1_hit": sim.get("tp1_hit", False),
                "tp2_hit": sim.get("tp2_hit", False),
                "verified": True,
                "data_source": data_source,
                "coinbase_at_close": cb,
                "closed_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            closed += 1
            emoji = "🟠" if sim["result"] == "Trail" else "🔴" if sim["result"] == "SL" else "⏱️"
            _log(f"  {emoji} {sim['result']}: {trade['id']} {direction} @ {close_px:.0f} ({sim['pnl_r']:+.2f}R)")
            log["history"].append(trade)
            continue
        trade["tp1_hit"] = sim.get("tp1_hit", False)
        trade["tp2_hit"] = sim.get("tp2_hit", False)
        trade["trail_active"] = sim.get("trail_active", False)
        trade["trail_stop"] = sim.get("trail_stop")
        last_close = float(bars["close"].iloc[-1])
        floating = (last_close - entry) if direction == "BUY" else (entry - last_close)
        trade["floating_pnl"] = round(floating, 2)
        trade["bars_held"] = sim["bars_held"]
        still.append(trade)
        _log(f"  📊 LIVE: {trade['id']} {direction} entry={entry:.0f} float={floating:+.0f} ({sim['bars_held']} bars)")
    log["trades"] = still
    save_log(log)
    _log(f"\n📊 Check complete: {closed} closed, {len(still)} still LIVE")


def report_status():
    log = load_log()
    trades = log.get("trades", [])
    hist = log.get("history", [])
    closed = [t for t in hist if t.get("status") == "CLOSED"]
    wins = [t for t in closed if (t.get("pnl_r") or 0) > 0]
    total_r = sum(t.get("pnl_r") or 0 for t in closed)
    _log(f"\n{'='*50}")
    _log(f"₿ BTC Paper Trades — LIVE {len(trades)} | CLOSED {len(closed)} | 勝率 {len(wins)}/{len(closed)} ({len(wins)/len(closed)*100:.0f}%)" if closed
         else f"\n₿ BTC Paper Trades — LIVE {len(trades)} | CLOSED 0")
    if closed:
        _log(f"Total R: {total_r:+.2f}R")
    for t in trades:
        _log(f"  📊 {t['id']} {t['direction']} {t.get('pattern','?')[:26]} float={t.get('floating_pnl', 0):+.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--reset", action="store_true", help="清空 BTC paper log")
    args = ap.parse_args()
    if args.reset:
        save_log({"trades": [], "history": []})
        _log("🧹 BTC paper log cleared")
        return
    json_path = os.path.join(REPO, "btc_last_analysis.json")
    if not os.path.exists(json_path):
        _log(f"⚠️ No analysis JSON: {json_path} — run btc_engine.py first")
        sys.exit(1)
    with open(json_path) as f:
        data = json.load(f)
    if not args.seed_only:
        check_outcomes(data)
    if not args.check_only:
        # 只 seed verified 且有 SL/TP 嘅 setups
        seedable = [s for s in data.get("setups", []) if s.get("verified")]
        seed_trades(data, seedable)
    report_status()


if __name__ == "__main__":
    main()
