#!/usr/bin/env python3
"""btc_engine.py — BTC 適配層 (repo: xauusd-analyze-v3, branch feat/btc-adaptation)

XAUUSD 引擎 (analyze_v3.py) 提供形態/風控核心; 本檔提供:
  1. fetch_btc_data()  — TV BITSTAMP:BTCUSD (M30/H1) + yfinance BTC-USD fallback
  2. 交易所價差 guard   — Coinbase spot vs 主數據源 >0.8% → 降級 UNVERIFIED
  3. BTC 校準常數      — 冇 golden/danger hours (24/7); ATR scaling
  4. build_btc_report() — 最小報告 (引擎唔改)

用法:
  python3 btc_engine.py           # 一次分析 + 印報告 + JSON 輸出
  python3 btc_engine.py --json    # 只出 JSON (cron 用)
"""
import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

REPO = os.path.dirname(os.path.abspath(__file__))

# ── 引入 XAUUSD 引擎 (分析核心, 零改動) ─────────────────────────────
spec = importlib.util.spec_from_file_location("analyze_v3", os.path.join(REPO, "analyze_v3.py"))
av3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(av3)

# ── BTC 校準常數 ────────────────────────────────────────────────
# 24/7 市場: 冇 broker session 概念 → 移除 XAUUSD 嘅 golden/danger hours
BTC_SYMBOL = "BITSTAMP:BTCUSD"
BTC_EXCHANGE = "BITSTAMP"
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"

# 波動率 3.4×黃金 → 風控參數用 ATR 倍數不變, 但 USD sizing 要重新計
# (0.02 lot XAUUSD ≈ $2/point; BTC 冇 lot, 用 USD risk %)
BTC_RISK_PCT = 0.5          # 每筆風險 = 帳戶 0.5% (24/7 + 高波動 → 比 XAUUSD 保守)
BTC_EXCHANGE_DIFF_PCT = 0.8  # Coinbase vs 主源價差 >0.8% → UNVERIFIED (黃金 basis $40 之 BTC 版)
SL_FLOOR_ATR_MULT = 0.8     # 同 XAUUSD — SL 至少 0.8×ATR
MIN_RR = 1.2                # RR gate: TP1/risk >= 1.2 (RR<1.2 單贏細輸大 — live 15 筆實證: 贏 avg +0.28R / 輸 avg -1.08R)
ALLOWED_PATTERNS = ("Flag",)  # Pattern gate: 只做 Bull/Bear Flag (backtest +0.59/+0.66R; AT/雙頂負 EV)
BTC_MIN_BARS_M30 = 240      # M30 最少 5 日數據
BTC_MIN_BARS_H1 = 240
BTC_MIN_BARS_DAY = 120

BASIS_SOURCE_LABEL = "Coinbase BTC-USD spot"


def _log(msg):
    print(msg, file=sys.stderr)


# ── 數據源 ──────────────────────────────────────────────────────
def _tv_fetch():
    """TradingView BITSTAMP:BTCUSD — M30 主源."""
    try:
        from tvDatafeed import TvDatafeed, Interval as TVInterval
        tv = TvDatafeed()
        df_m30 = tv.get_hist(BTC_SYMBOL, BTC_EXCHANGE, interval=TVInterval.in_30_minute, n_bars=500)
        if df_m30 is None or len(df_m30) < BTC_MIN_BARS_M30:
            return None
        return av3._normalize_tv_ohlc(df_m30)
    except Exception as e:
        _log(f"[!] TV fetch fail: {e}")
        return None


def _yf_ohlc_btc(interval, period):
    df = yf.download("BTC-USD", period=period, interval=interval, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.dropna(subset=["Close"])
    return df


def fetch_btc_data():
    """回傳 dict: m30/h1/day DataFrames + 來源標籤 + spot price + spot source."""
    out = {"m30": None, "h1": None, "day": None, "source": None, "spot": None, "spot_source": None}
    df_m30 = _tv_fetch()
    if df_m30 is not None:
        out["m30"] = df_m30
        out["source"] = "TradingView (BITSTAMP:BTCUSD)"
        out["spot"] = float(df_m30["Close"].iloc[-1])
        out["spot_source"] = "TV M30 close"
        df_h1 = av3._resample_h1_from_m30(df_m30)
        if df_h1 is not None and len(df_h1) >= BTC_MIN_BARS_H1:
            out["h1"] = df_h1
    # yfinance BTC-USD — 1h 直接抓 (TV 冇 H1 時) + 日線 (trend 用)
    try:
        df_h1y = _yf_ohlc_btc("1h", "60d")
        if out["h1"] is None and len(df_h1y) >= BTC_MIN_BARS_H1:
            out["h1"] = df_h1y
            out["source"] = out["source"] or "yfinance BTC-USD 1h"
        df_day = _yf_ohlc_btc("1d", "2y")
        if len(df_day) >= BTC_MIN_BARS_DAY:
            out["day"] = df_day
            if out["source"] is None:
                out["source"] = "yfinance BTC-USD 1d"
    except Exception as e:
        _log(f"[!] yf fail: {e}")
    # spot: Coinbase live quote (24/7, 冇 broker close 概念)
    try:
        r = requests.get(COINBASE_TICKER_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        cb = float(r.json()["price"])
        out["coinbase_spot"] = cb
        if out["spot"] is None:
            out["spot"] = cb
            out["spot_source"] = "Coinbase"
    except Exception as e:
        _log(f"[!] Coinbase fail: {e}")
        out["coinbase_spot"] = None
    return out


def evaluate_exchange_diff(primary_price, coinbase_price):
    """BTC 版嘅 basis guard: 主源 vs Coinbase >0.8% → UNVERIFIED."""
    if primary_price is None or coinbase_price is None:
        return {"status": "NO_SPOT", "diff_pct": None,
                "note": "無法取得 Coinbase 即時價 — 價格 UNVERIFIED"}
    diff_pct = abs(primary_price - coinbase_price) / coinbase_price * 100
    if diff_pct > BTC_EXCHANGE_DIFF_PCT:
        return {"status": "EXCHANGE_DIFF_FAIL", "diff_pct": round(diff_pct, 3),
                "note": f"主源 vs Coinbase 差 {diff_pct:.2f}% > {BTC_EXCHANGE_DIFF_PCT}% — 數據源唔一致, 全部訊號 UNVERIFIED"}
    return {"status": "OK", "diff_pct": round(diff_pct, 3),
            "note": f"主源 vs Coinbase 差 {diff_pct:.2f}% — OK"}


# ── Setup 過濾 (BTC 校準) ───────────────────────────────────────
def _parse_setup_level(val):
    """Setup 欄位係 '$77,404 - $77,626' / '$78,625' 字串 — 取第一個數字."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    import re as _re
    m = _re.search(r"-?[\d,]+\.?\d*", str(val).replace("$", "").replace(",", ""))
    return float(m.group(0).replace(",", "")) if m else None


def btc_filter_setups(setups, atr, px, diff_check):
    """套用 BTC 校準: SL floor、RR 篩選、pattern gate、risk sizing、UNVERIFIED 標記.

    引擎 setup 欄位: direction/pattern/entry_zone/stop_loss/tp1/tp2/tp3/risk_amount (字串格式).
    Gate (backtest 68 樣本 + testnet 9 筆實證 2026-08-30):
      - RR >= 1.0: TP(pattern 高度) 細過 SL floor 嘅單贏都贏唔起 (AT/fib live RR 0.1-0.4)
      - Pattern: 只做 Bull/Bear Flag (+0.59/+0.66R); AT 33.3% -0.25R、雙頂負 EV 全 live 實證
    """
    out = []
    for s in setups:
        side = "SELL" if "SELL" in str(s.get("direction", "")) else "BUY"
        pattern = str(s.get("pattern", "?"))
        # Pattern gate: 負 EV pattern 直接 skip (保留 setup 字串方便 debug)
        if ALLOWED_PATTERNS is not None:
            if not any(k in pattern for k in ALLOWED_PATTERNS):
                s["_gate_skip"] = "pattern_not_allowed"
                continue
        entry = _parse_setup_level(s.get("entry_zone"))
        if entry is None:
            entry = _parse_setup_level(s.get("entry_trigger"))
        stop = _parse_setup_level(s.get("stop_loss"))
        tp1 = _parse_setup_level(s.get("tp1"))
        tp2 = _parse_setup_level(s.get("tp2"))
        if entry is None or stop is None or not np.isfinite(stop):
            continue
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        # SL floor: >= 0.8×ATR (BTC 1h ATR ~0.48%, 波動大, floor 更重要)
        min_stop_dist = SL_FLOOR_ATR_MULT * atr
        if risk < min_stop_dist:
            if side == "SELL":
                stop = entry + min_stop_dist
            else:
                stop = entry - min_stop_dist
            risk = abs(entry - stop)
            s["sl_floor_applied"] = True
        # risk sizing: USD risk % — BTC 冇 lot, 直接計倉位 ($10k 帳戶示例)
        s["btc_side"] = side
        s["btc_entry"] = round(entry, 2)
        s["btc_stop"] = round(stop, 2)
        s["btc_tp1"] = round(tp1, 2) if tp1 else None
        s["btc_tp2"] = round(tp2, 2) if tp2 else None
        s["btc_risk_pct"] = BTC_RISK_PCT
        s["btc_position_size_usd"] = round(10000 * BTC_RISK_PCT / 100 / risk * entry, 2)
        # RR gate: TP 細過 risk → 贏都贏唔起, skip (live 實證: RR<1 單贏 +0.09R 但輸 −1.08R)
        if tp1:
            rr = abs(tp1 - entry) / risk
            s["rr_tp1"] = round(rr, 2)
            if rr < MIN_RR:
                s["_gate_skip"] = f"rr_{s['rr_tp1']}_lt_{MIN_RR}"
                continue
        # UNVERIFIED 標記
        if diff_check["status"] != "OK":
            s["verified"] = False
            s["unverified_reason"] = diff_check["status"]
        else:
            s["verified"] = True
        out.append(s)
    return out


def pick_best_setup(setups):
    """最簡單排位: ALIGNED > counter-trend 輕 > rr 高."""
    if not setups:
        return None
    def key(s):
        sev = s.get("counter_trend_severity", "ALIGNED")
        sev_rank = {"ALIGNED": 0, "MILD": 1, "SEVERE": 2}.get(sev, 1)
        return (sev_rank, -(s.get("rr_tp1") or 0))
    return sorted(setups, key=key)[0]


# ── 報告 ────────────────────────────────────────────────────────
def build_btc_report(data, patterns, setups, daily_trend, h1_trend, diff_check, best):
    px = data["spot"]
    atr = None
    if data.get("m30") is not None:
        df = av3.add_indicators(data["m30"])
        atr = float(df["ATR"].iloc[-1])
    elif data.get("h1") is not None:
        df = av3.add_indicators(data["h1"])
        atr = float(df["ATR"].iloc[-1])
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("# ₿ BTC 圖表形態深度分析 (XAUUSD 引擎 BTC 適配版 MVP)")
    lines.append(f"- 數據源: {data['source']}  |  spot: {BASIS_SOURCE_LABEL}")
    lines.append(f"- 時間: {now.strftime('%Y-%m-%d %H:%M UTC')} (24/7 — 冇 session gates)")
    lines.append(f"- 價格: ${px:,.0f}  |  ATR(14, M30): ${atr:,.0f} ({atr/px*100:.2f}%)" if atr else f"- 價格: ${px:,.0f}")
    lines.append(f"- 交易所價差: {diff_check['note']}")
    lines.append(f"- 日線趨勢: {daily_trend['trend'] if isinstance(daily_trend, dict) else daily_trend}  |  H1 趨勢: {h1_trend['trend'] if isinstance(h1_trend, dict) else h1_trend}")
    lines.append("")
    lines.append(f"## 形態 ({len(patterns)})")
    for p in patterns[:8]:
        lines.append(f"- {p.get('type','?')} dir={p.get('direction','?')} conf={p.get('confidence','?')} broken={p.get('broken', '?')}")
    lines.append("")
    lines.append(f"## Trade Setups ({len(setups)}) — SL floor {SL_FLOOR_ATR_MULT}×ATR, risk {BTC_RISK_PCT}%/筆")
    if best:
        lines.append(f"### ⭐ Best: {best['btc_side']} {best.get('pattern','?')}  entry~${best.get('btc_entry',0):,.0f}  SL ${best.get('btc_stop',0):,.0f}  TP1 {best.get('btc_tp1') or 0:,.0f} (RR {best.get('rr_tp1',0)})")
    for s in setups[:5]:
        v = "" if s.get("verified") else "  ⚠️UNVERIFIED"
        lines.append(f"- {s['btc_side']} {s.get('pattern','?')}: entry~${s.get('btc_entry',0):,.0f} SL ${s.get('btc_stop',0):,.0f} TP1 ${s.get('btc_tp1') or 0:,.0f} TP2 ${s.get('btc_tp2') or 0:,.0f} RR {s.get('rr_tp1','?')}{v}")
    if not setups:
        lines.append("- (冇合格 setup — 全部畀 SL floor/數據驗證過濾)")
    lines.append("")
    lines.append("---")
    lines.append("*MVP: 黃金 138-sample 勝率統計唔適用於 BTC; paper-trade 累積 BTC 自己嘅樣本先。*")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="輸出 JSON (cron 用)")
    args = ap.parse_args()

    data = fetch_btc_data()
    if data["m30"] is None and data["h1"] is None:
        _log("[X] 完全攞唔到 BTC 數據")
        sys.exit(2)
    diff_check = evaluate_exchange_diff(data["spot"], data.get("coinbase_spot"))

    # 分析: 用 m30 (fallback h1)
    base = data["m30"] if data["m30"] is not None else data["h1"]
    df = av3.add_indicators(base.copy())
    atr = float(df["ATR"].iloc[-1])
    px = float(df["Close"].iloc[-1])
    pts = av3.find_swings_ordered(df["High"].values, df["Low"].values, lookback=3,
                                  atr=df["ATR"].values, close=df["Close"].values)
    patterns = av3.detect_all_patterns(df, pts, atr=atr)
    daily_trend = av3.analyze_daily_trend(data["day"]) if data["day"] is not None else {"trend": "NEUTRAL"}
    h1_trend = av3.analyze_h1_trend(data["h1"]) if data["h1"] is not None else {"trend": "NEUTRAL"}
    setups = av3.generate_trade_setups(df, patterns, pts, daily_trend, px, atr, h1_trend=h1_trend)
    setups = btc_filter_setups(setups, atr, px, diff_check)
    best = pick_best_setup(setups)
    report = build_btc_report(data, patterns, setups, daily_trend, h1_trend, diff_check, best)

    if args.json:
        payload = {
            "generated_at": now_iso(),
            "symbol": "BTC-USD",
            "price": px,
            "atr": atr,
            "source": data["source"],
            "coinbase_spot": data.get("coinbase_spot"),
            "exchange_diff": diff_check,
            "daily_trend": daily_trend["trend"] if isinstance(daily_trend, dict) else str(daily_trend),
            "h1_trend": h1_trend["trend"] if isinstance(h1_trend, dict) else str(h1_trend),
            "patterns": len(patterns),
            "setups": setups,
            "best": best,
        }
        print(json.dumps(payload, ensure_ascii=False, default=str))
    else:
        print(report)
        # 保存 JSON 供 paper_trade 用
        out = os.path.join(REPO, "btc_last_analysis.json")
        with open(out, "w") as f:
            json.dump({"generated_at": now_iso(), "price": px, "atr": atr,
                       "exchange_diff": diff_check, "setups": setups, "best": best,
                       "daily_trend": str(daily_trend), "h1_trend": str(h1_trend)},
                      f, ensure_ascii=False, default=str)
        _log(f"[*] JSON saved {out}")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


if __name__ == "__main__":
    main()
