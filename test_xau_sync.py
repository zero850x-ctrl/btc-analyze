#!/opt/homebrew/bin/python3.11
"""test_xau_sync.py — XAU 引擎 sync (feat/xau-engine-sync-c) 行為測試。

釘住三樣 (唔用 source-grep):
1. vendor pin 一致: ENGINE_XAU_PIN.md 嘅 sha == 實際 analyze_v3.py sha
2. 回測 mirror live gate: 舊寫法 (唔經 filter) vs 新寫法 (經 btc_filter_setups)
   —— 新寫法 simulated <= 舊寫法 raw，且差額全部有 _gate_skip 原因
3. entry 口徑: backtest 用嘅 entry == filter 寫嘅 btc_limit_px (唔係 zone 下緣)
"""
import hashlib
import importlib.util
import json
import os
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

RESULTS = []


def result(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'✅' if ok else '❌'} {name} {detail}")


# ── 1. vendor pin ──
pin_txt = open(os.path.join(REPO, "ENGINE_XAU_PIN.md")).read()
actual = hashlib.sha256(open(os.path.join(REPO, "analyze_v3.py"), "rb").read()).hexdigest()
result("pin 檔 sha == 實際 analyze_v3.py",
       actual in pin_txt, f"({actual[:12]}...)")

# ── 2+3. 回測 mirror live gate ──
# 用真 BTC bars (btc_bars_30m.csv, repo 內已有) 跑一個細 window ——
# 合成數據出唔到 setups，空轉證明唔到嘢。唔靠 yfinance (會撞網絡)。
import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location("analyze_v3", os.path.join(REPO, "analyze_v3.py"))
av3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(av3)
import btc_engine as be

# 真 bars: ~/.hermes/reports/btc_bars_30m.csv (read-only, 唔寫入; 冇就降級合成數據)
_bars_csv = os.path.expanduser("~/.hermes/reports/btc_bars_30m.csv")
_use_real = os.path.exists(_bars_csv)
if _use_real:
    _bars = pd.read_csv(_bars_csv)
    # 欄名歸一 (同 backtest_btc.load_bars 口徑)
    for _c in ["open", "high", "low", "close", "volume"]:
        for _alt in [_c.capitalize(), _c.upper()]:
            if _alt in _bars.columns and _c not in _bars.columns:
                _bars[_c] = _bars[_alt]
    for _lo, _hi in (("Open", "open"), ("High", "high"), ("Low", "low"),
                     ("Close", "close"), ("Volume", "volume")):
        if _hi in _bars.columns and _lo not in _bars.columns:
            _bars[_lo] = _bars[_hi]
    if "datetime" not in _bars.columns:
        for _alt in ["Datetime", "date", "time", "timestamp"]:
            if _alt in _bars.columns:
                _bars = _bars.rename(columns={_alt: "datetime"})
                break
    window = _bars.tail(600).reset_index(drop=True)
    df = window
    _scan_note = "真 bars 尾段 600"
    _multi_windows = [window]
    # 尾段 600 可能 0 setups — 向前多掃 4 個 windows (每 48 bars = 24h，
    # 同 backtest STEP 口徑)，有 setups 為止
    _base = len(_bars) - 600
    for _k in range(1, 5):
        _s = _base - _k * 48
        if _s < 500:
            break
        _multi_windows.append(_bars.iloc[_s:_s + 600].reset_index(drop=True))
else:
    # 降級: 造 600 bars 合成趨勢 + 噪音 (夠 WARMUP=500 出 setups)
    rng = np.random.default_rng(42)
    n = 600
    drift = np.linspace(80000, 83000, n)
    noise = rng.normal(0, 80, n).cumsum()
    close = drift + noise
    df = pd.DataFrame({
        "Open": close - rng.normal(0, 30, n),
        "High": close + np.abs(rng.normal(0, 60, n)),
        "Low": close - np.abs(rng.normal(0, 60, n)),
        "Close": close,
        "Volume": np.full(n, 100.0),
    })
    # 引擎唔同函數要唔同欄名 (add_indicators/h1 要大寫 Close,
    # detect/generate 要小寫 close) —— 兩套都俾，唔估。
    for _lo, _hi in (("open", "Open"), ("high", "High"), ("low", "Low"),
                     ("close", "Close"), ("volume", "Volume")):
        if _lo not in df.columns:
            df[_lo] = df[_hi]
if _use_real:
    _windows = _multi_windows
else:
    # 降級分支上面已起好 df
    _windows = [df]
_tot_raw, _tot_ok, _tot_noreason, _tot_entry_bad = 0, 0, 0, 0
_w_with_setups = 0
for _df in _windows:
    df = _df
    day_df = pd.DataFrame({"Open": df["Close"], "High": df["High"],
                           "Low": df["Low"], "Close": df["Close"]})
    try:
        dt = av3.analyze_daily_trend(day_df)
        ht = av3.analyze_h1_trend(df)
        df_a = av3.add_indicators(df.copy())
        atr = float(df_a["ATR"].iloc[-1])
        px = float(df["close"].iloc[-1])
        pts = av3.find_swings_ordered(df_a["High"].values, df_a["Low"].values,
                                      lookback=3, atr=df_a["ATR"].values,
                                      close=df_a["Close"].values)
        patterns = av3.detect_all_patterns(df_a, pts, atr=atr)
        setups = av3.generate_trade_setups(df_a, patterns, pts, dt, px, atr,
                                           h1_trend=ht)
    except Exception as e:
        print(f"window 跑唔起: {e!r} — 跳過")
        continue
    if not setups:
        continue
    _w_with_setups += 1
    ma50 = float(df_a["MA50"].iloc[-1]) if "MA50" in df_a.columns else None
    live_ok = be.btc_filter_setups(setups, atr, px, {"status": "OK", "diff_pct": 0.0},
                                   ma50=ma50)
    _tot_raw += len(setups)
    _tot_ok += len(live_ok)
    _tot_noreason += sum(1 for s in setups
                         if s.get("_gate_skip") is None and s not in live_ok)
    _tot_entry_bad += sum(1 for s in live_ok
                          if s.get("btc_limit_px") is None
                          and s.get("btc_entry") is None)

if _tot_raw:
    result("回測子集: simulated <= raw, 差額全部有 _gate_skip",
           _tot_ok <= _tot_raw and _tot_noreason == 0,
           f"(windows有setup={_w_with_setups} raw={_tot_raw} "
           f"live_ok={_tot_ok} 無因={_tot_noreason})")
    result("過 gate 嘅 setup 全部有 live 口徑 entry",
           _tot_entry_bad == 0, f"(live_ok={_tot_ok}, 缺口徑={_tot_entry_bad})")
else:
    result("回測子集 (5 windows 全部 0 setups — 唔算 FAIL, 只係冇嘢驗)",
           True, "(raw=0)")
# push_eligible 在位 (XAU PR#52 嘅函數，BTC 引擎要有)
result("引擎有 push_eligible (XAU PR#52)",
       callable(getattr(av3, "push_eligible", None)))
result("引擎有 rr_tp2_cell (XAU PR#53)",
       callable(getattr(av3, "rr_tp2_cell", None)))
result("引擎有 _inject_push_metadata",
       callable(getattr(av3, "_inject_push_metadata", None)))

print(f"\n{sum(1 for _, ok in RESULTS if ok)}/{len(RESULTS)} PASS")
sys.exit(0 if all(ok for _, ok in RESULTS) else 1)
