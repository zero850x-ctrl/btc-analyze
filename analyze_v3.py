#!/usr/bin/env python3
"""
XAUUSD Technical Analysis Engine v3 — Full-Stack Summary
=========================================================

Multi-timeframe pattern-detection + trade-setup engine for XAUUSD.
Designed to match senior mentor's methodology: structure-first,
flag/wedge pullback entries, tight structure-based stops, 3-tier TP.

📊 TREND ANALYSIS (3 TF)
  - Daily:   MA20/MA50 + RSI(14) → trend direction + strength
  - H1:      MA20/MA50 + RSI(14) → intermediate trend
  - M15:     micro entry timing (trend, RSI, swing points, per-pattern suggestions)
  - M5:      quick microstructure (trend, RSI, swings — informational only, never gates pushes)
  - Multi-TF alignment check: aligned_with_trends()

📈 INDICATORS (add_indicators)
  - ATR(14) — base unit for stops, entry zones, trailing
  - RSI(14) — overbought/oversold
  - MA20, MA50 — trend anchors
  - Vol_SMA20 — volume baseline for breakout checks

🔍 CHART PATTERNS (detect_all_patterns)
  - Flags (bull/bear): pole + consolidation, detect_flags()
  - Triangles (ascending/descending/symmetrical): detect_triangles()
  - Wedges (rising/falling): detect_wedges()
  - Double Top/Bottom: detect_double_top_bottom()
  - Channels (parallel): detect_channels()
  - Fibonacci retracement/extension for targets
  - 0.786 deep retracement setup (from HK stock Fib 0.786 playbook research)

🕯️ CANDLESTICK CONFIRMATION (17 named patterns, detect_candlestick_patterns)
  - Engulfing, Morning/Evening Star, Hammer/Shooting Star, Harami,
    Three White Soldiers / Three Black Crows, etc.
  - Scoring: +2 HIGH, +1 MEDIUM, opposing subtracts
  - K-line confirmation: total ≥ 2 AND m30_score ≥ 0 AND day_score ≥ 0

🛡️ ENTRY FILTERS (3-layer)
  - _retest_confirm() → post-breakout retest of support/resistance
  - _volume_spike()   → volume > 1.3× recent average
  - _pattern_breakout_confirmed() → either retest OR volume spike = confirmed
  - _pullback_consolidation_ok()  → flag/wedge pullback quality gate
    Flags: retrace 0.15-0.55 + flag_range ≥ 0.5 ATR
    Wedges: pattern_height ≥ 0.5 ATR

🎯 STOP LOSS SYSTEM
  - pattern_structure_stop() → tight SL at flag/wedge boundary + 0.5 ATR
    (structure priority for flags/wedges)
  - Fallback: swing point ± 1 ATR, capped at 3 ATR
  - Pullback entries get even tighter structure stops

💰 TAKE PROFIT (3-tier)
  - TP1 (1/3): closer of 1:1 RR or 0.618 Fib ext → take profit first
  - TP2 (1/3): further of 2:1 RR or 1.0 Fib ext (beyond TP1 by design)
  - TP3 (1/3): runner with trailing stop (TRAIL_PROFIT_ATR activate,
    TRAIL_STOP_ATR trail)

⚖️ QUALITY / PRIORITY
  - R:R quality tiers: ≥2.0 → GOOD, ≥1.0 → OK, <1.0 → POOR_RR (threshold 1.0 since Jul 2026)
  - Quality from TP1 R:R only — TP2 ~2:1 by design and must not inflate tiers
  - Priority 1-6 (1=best, SEVERE checked first):
    1 broken+ALIGNED+GOOD | 2 broken+ALIGNED+OK | 3 ALIGNED waiting
    4 MILD counter-trend (waiting or broken) | 5 POOR_RR/UNCONFIRMED | 6 SEVERE
  - UNCONFIRMED: broken OK/GOOD without retest/volume — Channel/Wedge/Flag/Triangle/Double
  - Setup generation keeps top MAX_PATTERNS_PER_DIRECTION (2) patterns per side before ranking

🚪 ENTRY MODES
  - breakout: wait for price to cross trigger level
  - pullback: enter at current price inside flag/wedge consolidation
    (tighter SL, requires consolidation quality gate)
  - boundary: limit order at double top/bottom or wedge boundary
    (double: HIGH confidence; wedge: MEDIUM+; emits boundary then skips breakout+pullback)

📡 OUTPUT
  - --json → ~/.hermes/reports/xauusd_v3_<date>.json (setups, patterns, candles)
  - default → ~/.hermes/reports/xauusd_v3_<date>.md (full report with M15 section)
  - Designed for downstream execution via paper_trade.py (in-repo companion module)
  - cron_push_eligible on each setup (JSON): kline confirmed + OK/GOOD quality +
    ALIGNED counter-trend + NOT danger hour (07/18 broker) + has TP + SL +
    priority≤2 (breakout) or ≤3 (pullback/boundary/fib/fib0786) +
    seedable (filled) OR limit mode (boundary/fib0786) with entry_price for pending.
    Also blocked when mixed spot M30 vs futures daily basis exceeds max($15, 0.5×ATR).
  - K-line confirmation does NOT change ranking/stops/size; it IS a cron push hard gate.
  - JSON also includes time_quality (session), counter_trend_severity, recommended_volume,
    generated_at (UTC), date (UTC), broker_date, and spot-futures basis when mixed.

🚫 DISCIPLINE GUARDS (based on 138-trade combined sample through 2026-07-30)
  - Time gates: 07:00 + 18:00 broker hard-block (danger); 04-06/08 advisory; 17:00 golden
    (138-sample: 17:00 64.3% +$340 n=14; 07:00 25% -$213; 18:00 21.4% -$99)
  - Golden hour: 17:00 broker (64.3% win, n=14, +$340)
  - TP mandatory: kept for risk discipline (NOT for PnL — 138-sample: no-TP 40.3% vs TP-set 42.9%)
  - SL mandatory: kept for risk discipline (NOT for PnL — 138-sample: no-SL 51.4% vs SL-set 36.9%)
  - Min holding: downstream paper_trade.py enforces 15-min minimum (<15min: 26% win, -$955 combined)
  - Cooldown: 15-min lockout after trade close (prevents 16-sec / 25-sec revenge entry)
  - Anti-martingale: downstream paper_trade.py blocks volume increase after consecutive losses
  - Anti-stacking: enforced by paper_trade.py — blocks ALL overlapping positions
    (138-sample: 13 overlap pairs, -$85 net; same-dir stacking within 3min disciplined).
    This analyzer emits recommended_volume only; it does not implement stacking rules.
  - SL floor: downstream paper_trade.py rejects SL < 0.5×ATR (too tight = noise stop-out)
  - Direction bias: counter_trend_severity == ALIGNED prevents all-counter-trend days
  - Max holding benefit: >4h hold = 66.7% win, +$608 (18 trades, 138-sample); 1-4h = 70% +$466

Data sources: TradingView spot (OANDA:XAUUSD) preferred for M30/H1/M15/daily;
then Yahoo PAXG-USD (spot-anchored); GC=F last. Mixed-venue daily is labelled
and may block cron. TV daily only when M30 is TV; GC=F M30 stays on GC=F daily.

Architecture: fetch_data → add_indicators → find_swings → detect patterns
→ detect candlesticks → volume confirm → generate setups (breakout + pullback)
→ inject kline scores (step 7b) → generate report (reuses kline_* fields)
"""

import os, json, argparse, sys
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
import yfinance as yf
import ta

# TradingView data feed (preferred for spot XAUUSD)
try:
    from tvDatafeed import TvDatafeed, Interval as TVInterval
    _tv = TvDatafeed()
    _TV_AVAILABLE = True
except Exception:
    _tv = None
    _TV_AVAILABLE = False

OUTPUT_DIR = os.path.expanduser("~/.hermes/reports")
TV_SYMBOL = "OANDA:XAUUSD"
TV_EXCHANGE = "OANDA"
YF_TICKER = "GC=F"          # last-resort futures fallback (premium $15-52)
PAXG_TICKER = "PAXG-USD"    # spot-anchored token fallback (1 PAXG = 1 oz; tracks spot within ~$7)
PAXG_DATA_SOURCE = "Yahoo Finance PAXG-USD (現貨錨定)"
DATA_SOURCE = "TradingView (OANDA:XAUUSD)"  # updated at runtime (M30/H1 intraday)
DAILY_DATA_SOURCE = "Yahoo Finance GC=F (紐約期貨)"  # daily bars; prefer TV spot when available
M15_DATA_SOURCE = None  # set in fetch_data; None = M15 disabled
M5_DATA_SOURCE = None   # set in fetch_data; None = M5 disabled
# Mixed-venue basis (spot M30 close vs futures daily close). None = same venue.
SPOT_FUTURES_BASIS = None
BASIS_CRON_BLOCKED = False
BASIS_NOTE = ""
REQUIRED_COLS = ['Open', 'High', 'Low', 'Close', 'Volume']
TV_OHLC_MAP = {
    'open': 'Open', 'high': 'High', 'low': 'Low',
    'close': 'Close', 'volume': 'Volume',
}
MIN_BARS = {'m30': 50, 'h1': 30, 'm15': 30, 'm5': 100, 'day': 20}


def _env_float(name, default, tag, allow_zero=False):
    """Parse-safe, DOMAIN-safe float env read.

    同 paper_trade.py 嘅同名 helper **行為必須一致**（test_exit_plan_display.py
    有 behavioral parity 守衛，逐個壞值對比，唔係對比源碼字面）。

    唔可以淨係 catch ValueError：`nan` 會 parse 成功但令所有比較變 False（規則
    靜靜消失），`inf` 就永遠觸發 —— 兩者都係「靜默反轉」而唔係 crash。而
    analyze_v3 係 cron 每支 tick 跑嘅 signal producer，一個 typo 令 import
    crash 就等於**完全冇信號**。無效值一律 warn + 用預設。
    """
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        print(f"⚠️ [{tag}] {name}={raw!r} 唔係數字 — 用預設 {default}")
        return float(default)
    if (val != val) or val in (float("inf"), float("-inf")) \
            or val < 0 or (val == 0 and not allow_zero):
        desc = "唔係有限非負數" if allow_zero else "唔係有限正數"
        print(f"⚠️ [{tag}] {name}={raw!r} {desc} — 用預設 {default}")
        return float(default)
    return val


TRAIL_PROFIT_ATR = _env_float('TRAIL_PROFIT_ATR', 2.0, '出場')
TRAIL_STOP_ATR = _env_float('TRAIL_STOP_ATR', 1.5, '出場')

# ── 出場模式（2026-09-28）────────────────────────────────────────────────
# paper_trade.py 擁有**行為**；呢度係**讀者**，唔係第二份實作。兩邊預設必須
# 一致 —— test_exit_plan_display.py 會逐字對比 source literal 釘住，唔靠記憶。
#
# momentum-hold（預設開）: TP1 之後餘下 2/3 掛移動止損跟勢，TP2 喺 TP1 之後
# 唔會 fire（paper_trade.py 條件短路；tp1<=0 嘅退化 setup 例外）。legacy (=0)
# 先會 TP2 止賺 1/3、尾倉 1/3，而且唔會喺 TP1 後把止損移去 breakeven。
MOMENTUM_HOLD_EXIT = os.environ.get('MOMENTUM_HOLD_EXIT', '1') == '1'
# 2026-09-28 第 2 項修好之前，paper_trade.py 嘅 momentum-hold 路徑**硬編碼
# 1.5**、唔讀 TRAIL_STOP_ATR，所以呢度唯有如實寫死 1.5。修好之後兩邊都讀
# 同一個 TRAIL_STOP_ATR ⇒ 直接引用，改環境變數顯示同實際一齊變。
MOM_HOLD_TRAIL_ATR = TRAIL_STOP_ATR


def exit_fields(tp2_price, tp2_label, tp3_trail_legacy, rr1=None):
    """回傳 (tp2, tp3, exit_plan) 三個成品字串 —— 出場計劃文字嘅**單一入口**。

    2026-09-28 事故: 報告同時寫「tp2 ... 止賺 1/3」同「exit_plan ... 餘下 2/3
    無固定TP」。兩個都出自同一份報告，但只有後者同 ledger 一致（momentum-hold
    令 TP2 短路），用戶按前者以為仲有 TP2/TP3 兩級止賺。所以三個欄要由同一個
    函數產生，令佢哋**唔可能互相矛盾**。

    legacy (=0) 分支: tp2/tp3 文字**逐字保留**原本寫法；`exit_plan` 就**刻意改咗**——
    原本嗰句「餘下 2/3 無固定TP」係照抄 momentum-hold 版本，但 legacy 之下 TP2 係
    有效目標，所以嗰句本身係錯。今次一併改準（唔係「唔改行為之外嘅嘢」，而係
    「兩個模式嘅文字都要同實際一致」—— 呢個正正就係今次修嘅目的）。
    """
    if MOMENTUM_HOLD_EXIT:
        tp2 = (f"${tp2_price:.0f} ({tp2_label})"
               f" — ⚠️ 停用 (momentum-hold，TP1 後唔會 fire)")
        tp3 = (f"TP1 後餘下 2/3 跟 {MOM_HOLD_TRAIL_ATR:g}×ATR 移動止損跟勢"
               f" (無固定目標，唔會止賺)")
        if rr1 is not None:
            plan = (f"TP1 (+{rr1:.1f}R) 後 SL→BE; 餘下 2/3 無固定TP, "
                    f"{MOM_HOLD_TRAIL_ATR:g} ATR trailing 跟勢 "
                    f"(前輩式放飛: 平均贏$126/輸$27)")
        else:
            plan = (f"TP1 後 SL→BE; 餘下 2/3 無固定TP, "
                    f"{MOM_HOLD_TRAIL_ATR:g} ATR trailing 跟勢")
        return tp2, tp3, plan

    tp2 = f"${tp2_price:.0f} ({tp2_label}, 止賺 1/3)"
    tp3 = f"放飛 + {tp3_trail_legacy} (尾倉 1/3)"
    # ⚠️ legacy 嘅 exit_plan 原本係照抄 momentum-hold 版本（「餘下 2/3 無固定TP」），
    # 但 legacy 之下 TP2 係**有效**目標 ⇒ 嗰句係錯嘅。呢度改成準確描述，
    # 令兩個模式嘅文字都同實際一致（呢個就係今次修嘅目的）。
    # legacy 唔會喺 TP1 後把止損移去 breakeven（嗰步只存在於 momentum-hold）。
    # trail 距離跟 TRAIL_STOP_ATR，唔可以再寫死 1.5 —— 否則一設 env，tp3
    # （trail_stop_text）同 exit_plan 即刻互相矛盾。
    if rr1 is not None:
        plan = (f"TP1 (+{rr1:.1f}R) 後餘下 2/3: 1/3 到 TP2, "
                f"1/3 跟 {TRAIL_STOP_ATR:g} ATR 移動止損 "
                f"(前輩式放飛: 平均贏$126/輸$27)")
    else:
        plan = (f"TP1 後餘下 2/3: 1/3 到 TP2, "
                f"1/3 跟 {TRAIL_STOP_ATR:g} ATR 移動止損")
    return tp2, tp3, plan


def rr_tp2_cell(setup):
    """Markdown 表嘅「R:R TP2」格 —— 三態（生效／停用／未確認）。

    2026-09-28: 以前無條件印 `{rr_tp2}:1`。但 momentum-hold（預設開）之下 TP2
    **永遠唔會 fire** ⇒ 呢個係一個永遠唔會實現嘅回報風險比 = 假數字。同 `tp2`
    欄一樣要講清楚。

    ⚠️ 缺 key 唔可以當 False：舊 JSON（09-28 前）冇 `rr_tp2_active`，當 False 會
    反過來印成「停用」—— 由「假目標」變「假停用」，即係事故嘅鏡像版。所以三態。
    """
    rr = setup.get('rr_tp2')
    active = setup.get('rr_tp2_active')
    if active is True:
        return f"{rr}:1"
    if active is False:
        return f"{rr}:1 — ⚠️ 停用 (TP2 唔會 fire)"
    return f"{rr}:1 — ⚠️ 未確認 (記錄缺 rr_tp2_active)"


def setup_share_labels(setup):
    """Signal 表嘅 TP2／TP3 列名。份額跟 `tp2_active`，唔可以寫死 (1/3)。

    True → 兩級各 1/3。False → TP2 已停用，尾倉係餘下 2/3（再寫 (1/3) 就係
    今次事故）。缺欄位 → 未確認，兩邊都唔主張。
    """
    active = setup.get("tp2_active") if isinstance(setup, dict) else None
    if active is True:
        return "🎯 TP2 (1/3)", "🎯 TP3 (1/3)"
    if active is False:
        return "🎯 TP2 (停用)", "🎯 尾倉 (餘下 2/3)"
    return "🎯 TP2 (未確認)", "🎯 TP3 (未確認)"


def static_exit_copy(atr):
    """法則表、日誌範本、追蹤止損列。模式跟 MOMENTUM_HOLD_EXIT。

    momentum-hold 嘅 trail 由 TP1 起 arm，距離係 MOM_HOLD_TRAIL_ATR。
    legacy 先用「利潤達 TRAIL_PROFIT_ATR 先 arm、距離 TRAIL_STOP_ATR」。
    """
    if MOMENTUM_HOLD_EXIT:
        mult = f"{MOM_HOLD_TRAIL_ATR:g}"
        return {
            "rule_tp2": "停用（TP1 後唔會 fire；價位只供參考）",
            "rule_tp3": f"TP1 後餘下 2/3 跟 {mult}×ATR 移動止損（無固定目標）",
            "journal_tp2": "🎯 TP2: 停用 (momentum-hold)",
            "journal_tp3": f"🎯 尾倉 (餘下 2/3): 跟 {mult}×ATR 移動止損",
            "trail_rule": (
                f"TP1 後餘下 2/3 跟 {mult}×ATR 移動止損"
                f"（由 TP1 起 arm，唔係等 +{TRAIL_PROFIT_ATR:g}×ATR）"
            ),
        }
    return {
        "rule_tp2": "2:1 RR 或 1.0 Fib ext (取較遠，比 TP1 更遠)，止賺 1/3",
        "rule_tp3": (
            f"放飛 + {TRAIL_STOP_ATR:g}×ATR 移動止損"
            f"（尾倉 1/3；利潤達 {TRAIL_PROFIT_ATR:g}×ATR 先 arm）"
        ),
        "journal_tp2": "🎯 TP2 (1/3): ___",
        "journal_tp3": "🎯 TP3 (1/3): 放飛 (追蹤止損)",
        "trail_rule": trail_stop_text(atr),
    }


def _log(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('ascii', 'replace').decode('ascii'))


def _neutral_trend(df):
    close = float(df['Close'].iloc[-1]) if df is not None and len(df) else 0.0
    # P1 FIX: guard against close=0 causing downstream division-by-zero
    if close <= 0:
        close = 1.0  # fallback to avoid div-by-zero in R:R calculations
    return {
        'trend': 'NEUTRAL', 'strength': 0,
        'close': round(close, 2), 'ma20': round(close, 2),
        'ma50': round(close, 2), 'rsi': 50.0,
    }


def validate_dataframe(df, label, min_bars):
    if df is None or df.empty:
        raise SystemExit(f"Error: {label} data is empty. Check network or data source ({DATA_SOURCE}).")
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise SystemExit(f"Error: {label} missing columns: {missing}")
    if len(df) < min_bars:
        raise SystemExit(f"Error: {label} has only {len(df)} bars (need >= {min_bars}).")
    if df['Close'].isna().all():
        raise SystemExit(f"Error: {label} Close data is all NaN.")


def volume_usable(df):
    if 'Volume' not in df.columns or len(df) < 5:
        return False
    return float(df['Volume'].sum()) > 0


def _last_valid_float(series, label):
    """Last non-NaN float, or SystemExit if the series is all NaN."""
    s = pd.Series(series, dtype='float64').dropna()
    if s.empty:
        raise SystemExit(f"Error: {label} has no valid values (all NaN).")
    return float(s.iloc[-1])


def _normalize_tv_ohlc(df):
    """Capitalize TV ohlc columns and ensure REQUIRED_COLS exist."""
    if df is None or df.empty:
        return None
    df = df.rename(columns=TV_OHLC_MAP)
    for c in ['symbol', 'symbol.1']:
        if c in df.columns:
            df = df.drop(columns=[c])
    for col in REQUIRED_COLS:
        if col not in df.columns:
            df[col] = np.nan
    return df


def _resample_h1_from_m30(df_m30):
    """Resample M30 → H1 with consistent bar alignment (label/closed right)."""
    return df_m30.resample('1h', label='right', closed='right').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum',
    }).dropna(subset=['Close'])


def _yf_ohlc(ticker, period, interval):
    df = yf.download(ticker, period=period, interval=interval, progress=False)
    if df is None or df.empty:
        return None
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


def _evaluate_spot_futures_basis(spot_close, daily_close, atr):
    """If M30 and daily come from different venues, measure basis and maybe block cron.

    Gold spot vs GC=F is normally a few dollars. Block only on large dislocation:
    max($15, 0.5×ATR).
    """
    global SPOT_FUTURES_BASIS, BASIS_CRON_BLOCKED, BASIS_NOTE
    SPOT_FUTURES_BASIS = None
    BASIS_CRON_BLOCKED = False
    BASIS_NOTE = ""
    if DATA_SOURCE == DAILY_DATA_SOURCE:
        return
    if spot_close is None or daily_close is None or atr is None:
        return
    if not (np.isfinite(spot_close) and np.isfinite(daily_close) and np.isfinite(atr) and atr > 0):
        return
    basis = abs(float(spot_close) - float(daily_close))
    SPOT_FUTURES_BASIS = round(basis, 2)
    threshold = max(15.0, 0.5 * float(atr))
    if basis > threshold:
        BASIS_CRON_BLOCKED = True
        BASIS_NOTE = (
            f"現貨 M30 vs 期貨日線 basis ${basis:.2f} > ${threshold:.2f} "
            f"— cron 暫停（趨勢門檻可能錯）"
        )
    else:
        BASIS_NOTE = (
            f"現貨 M30 vs 期貨日線 basis ${basis:.2f} "
            f"（門檻 ${threshold:.2f}，cron 仍允許）"
        )


def trail_stop_text(atr):
    return (
        f"追蹤止損: 每 +${atr * TRAIL_PROFIT_ATR:.0f} 利潤, "
        f"止損移 ${atr * TRAIL_STOP_ATR:.0f}"
    )


def apply_trail_overrides(trail_profit, trail_stop):
    """CLI `--trail-profit` / `--trail-stop`.

    `MOM_HOLD_TRAIL_ATR = TRAIL_STOP_ATR` 係 import 時抄咗個 float。之後只改
    `TRAIL_STOP_ATR` 會令 momentum 文字停喺舊倍數、legacy `tp3` 用新倍數。
    呢度兩個一齊改。`argparse` 嘅 `type=float` 會接受 nan；無效值唔寫入，
    同 `_env_float` 一樣留低原值。
    """
    global TRAIL_PROFIT_ATR, TRAIL_STOP_ATR, MOM_HOLD_TRAIL_ATR

    def _accept(flag, val):
        if val is None:
            return None
        try:
            num = float(val)
        except (TypeError, ValueError):
            _log(f"⚠️ [出場] {flag}={val!r} 唔係數字 — 保持原值")
            return None
        if (num != num) or num in (float("inf"), float("-inf")) or num <= 0:
            _log(f"⚠️ [出場] {flag}={val!r} 唔係有限正數 — 保持原值")
            return None
        return num

    profit = _accept("--trail-profit", trail_profit)
    stop = _accept("--trail-stop", trail_stop)
    if profit is not None:
        TRAIL_PROFIT_ATR = profit
    if stop is not None:
        TRAIL_STOP_ATR = stop
        MOM_HOLD_TRAIL_ATR = TRAIL_STOP_ATR


def _breakout_status(df, direction, support=None, resistance=None, tol=0, points=None):
    """
    Unified breakout semantics across all pattern types:
    - broken_now: current close beyond trigger (matches trade setup already_broken)
    - broke_recently: any of last 5 closes beyond trigger (pattern report)
    - broken: alias for broke_recently (backward compat)
    """
    cur = float(df['Close'].iloc[-1]) if df is not None and len(df) else None
    recent_closes = df['Close'].values[-5:] if df is not None and len(df) >= 5 else None

    broken_up_now = broken_down_now = False
    broken_up_recent = broken_down_recent = False

    if resistance is not None:
        up_trigger = resistance + tol
        broken_up_now = cur is not None and cur > up_trigger
        if recent_closes is not None:
            broken_up_recent = bool(np.any(recent_closes > up_trigger))
        elif points:
            broken_up_recent = any(
                p['type'] == 'high' and p['price'] > up_trigger for p in points[-5:]
            )

    if support is not None:
        down_trigger = support - tol
        broken_down_now = cur is not None and cur < down_trigger
        if recent_closes is not None:
            broken_down_recent = bool(np.any(recent_closes < down_trigger))
        elif points:
            broken_down_recent = any(
                p['type'] == 'low' and p['price'] < down_trigger for p in points[-5:]
            )

    if direction == 'BEARISH':
        broken_now = broken_down_now
        broke_recently = broken_down_recent
    elif direction == 'BULLISH':
        broken_now = broken_up_now
        broke_recently = broken_up_recent
    else:
        broken_now = broken_up_now or broken_down_now
        broke_recently = broken_up_recent or broken_down_recent

    return {
        'broken': broke_recently,
        'broken_now': broken_now,
        'broke_recently': broke_recently,
    }


def _retest_confirm(df, direction, support=None, resistance=None, tol=0, lookback=5):
    """
    Check if price retested a breakout level: prior break beyond level,
    pullback touch, then bounce in breakout direction.
    Only scans a recent window so ancient breaks cannot confirm current setups.
    """
    if df is None or len(df) < lookback + 2:
        return False
    closes = df['Close'].values
    highs = df['High'].values
    lows = df['Low'].values
    n = len(closes)
    scan_window = lookback * 2 + 3
    scan_start = max(1, n - scan_window)

    if direction == 'BULLISH' and resistance is not None:
        level = resistance
        for i in range(scan_start, n - 1):
            prior_from = max(scan_start, i - lookback)
            prior_broke = bool(np.any(closes[prior_from:i] > level + tol))
            if not prior_broke:
                continue
            if lows[i] <= level + tol and closes[i] >= level and closes[i + 1] > level:
                return True
    elif direction == 'BEARISH' and support is not None:
        level = support
        for i in range(scan_start, n - 1):
            prior_from = max(scan_start, i - lookback)
            prior_broke = bool(np.any(closes[prior_from:i] < level - tol))
            if not prior_broke:
                continue
            if highs[i] >= level - tol and closes[i] <= level and closes[i + 1] < level:
                return True
    return False


def _pattern_retest_levels(pattern, direction):
    """Map support/resistance for retest — flags use breakout_level."""
    support = pattern.get('support')
    resistance = pattern.get('resistance')
    if 'Flag' in pattern.get('type', ''):
        level = pattern.get('breakout_level')
        if direction == 'BULLISH' and resistance is None:
            resistance = level
        elif direction == 'BEARISH' and support is None:
            support = level
    ptype = pattern.get('type', '')
    if ('Double' in ptype or '雙' in ptype) and 'neckline' in pattern:
        neckline = pattern['neckline']
        if direction == 'BEARISH' and support is None:
            support = neckline
        elif direction == 'BULLISH' and resistance is None:
            resistance = neckline
    return support, resistance


def _directional_broke_recently(pattern, direction):
    """Channel dual-side break: only count breakout on setup direction."""
    if direction == 'BULLISH' and 'broke_recently_up' in pattern:
        return pattern['broke_recently_up']
    if direction == 'BEARISH' and 'broke_recently_down' in pattern:
        return pattern['broke_recently_down']
    return pattern.get('broke_recently', pattern.get('broken', False))


def _breakout_volume_confirmed(pattern, df):
    """Volume spike, or flag pole/flag vol_confirm when SMA volume unavailable."""
    vol_spike = _volume_spike(df)
    if vol_spike is True:
        return True
    if vol_spike is False:
        return False
    return pattern.get('vol_confirm') is True


GATED_BREAKOUT_PATTERN_TYPES = ('Channel', 'Wedge', 'Flag', 'Triangle', 'Double')


def _pattern_breakout_confirmed(pattern):
    """True when retest/volume gate passed for gated pattern types."""
    p_type = pattern.get('type', '')
    gated = GATED_BREAKOUT_PATTERN_TYPES
    if not any(ft in p_type for ft in gated):
        return True
    broke = pattern.get('broke_recently') or pattern.get('broken_now')
    if broke:
        if 'retest_confirmed' not in pattern and 'vol_breakout_confirmed' not in pattern:
            return False
        return bool(pattern.get('retest_confirmed') or pattern.get('vol_breakout_confirmed'))
    return True


def _volume_spike(df, idx=None, window=5, multiplier=1.3):
    """
    Check if volume at idx (or last `window` bars) is significantly above
    the 20-bar SMA. Returns True/False/None (None = no volume data).
    """
    if not volume_usable(df) or len(df) < window + 5:
        return None
    vol = df['Volume'].values
    vol_sma = pd.Series(vol).rolling(window=20, min_periods=1).mean().values
    check_idx = idx if idx is not None else len(vol) - 1
    if check_idx < 0 or check_idx >= len(vol):
        return None
    recent_vol = np.mean(vol[max(0, check_idx - window + 1):check_idx + 1])
    baseline = vol_sma[check_idx]
    if baseline <= 0 or np.isnan(baseline):
        return None
    return bool(recent_vol > baseline * multiplier)

# ═══════════════════════════════════════════════════════════
# DATA
# ═══════════════════════════════════════════════════════════

def _frame_ok(df, min_bars):
    return df is not None and not getattr(df, 'empty', True) and len(df) >= min_bars


def fetch_data():
    """Fetch XAUUSD data. Primary: TradingView spot; then PAXG-USD; GC=F last.

    H1 is same-venue as M30 (resample, never mix GC=F H1 onto spot M30).
    TV daily is only used when M30 itself came from TV. PAXG daily is only
    used when M30 is spot-anchored. GC=F M30 stays on GC=F daily so labels
    match bars and mixed-venue basis checks still run.
    """
    global DATA_SOURCE, DAILY_DATA_SOURCE, M15_DATA_SOURCE, M5_DATA_SOURCE

    df_m30 = df_h1 = df_day = df_m15 = df_m5 = None
    DATA_SOURCE = "TradingView (OANDA:XAUUSD)"
    DAILY_DATA_SOURCE = "Yahoo Finance GC=F (紐約期貨)"
    M15_DATA_SOURCE = None
    M5_DATA_SOURCE = None
    m30_is_spot = False
    m30_from_tv = False

    # --- TradingView (spot XAUUSD) ---
    if _TV_AVAILABLE:
        _log("[*] Fetching TradingView (OANDA:XAUUSD)...")
        try:
            df_m30 = _tv.get_hist(TV_SYMBOL, TV_EXCHANGE, interval=TVInterval.in_30_minute, n_bars=300)
            df_m30 = _normalize_tv_ohlc(df_m30)
            if df_m30 is not None:
                _log(f"   TV M30: {len(df_m30)} bars")
                m30_is_spot = True
                m30_from_tv = True
        except Exception as e:
            _log(f"   TV M30 failed: {e}")
            df_m30 = None

        if df_m30 is not None:
            try:
                df_h1 = _resample_h1_from_m30(df_m30)
                if df_h1 is None or df_h1.empty:
                    df_h1 = None
                else:
                    _log(f"   TV H1 (resampled): {len(df_h1)} bars")
            except Exception as e:
                _log(f"   TV H1 resample failed: {e}")
                df_h1 = None

    # --- Fallback: PAXG-USD (spot-anchored) then GC=F (futures) for M30 ---
    if df_m30 is None:
        try:
            _log("[*] TradingView unavailable, trying PAXG-USD (spot-anchored)...")
            df_paxg = _yf_ohlc(PAXG_TICKER, '30d', '30m')
            if _frame_ok(df_paxg, MIN_BARS['m30']):
                # Commit M30 before H1 — an H1 failure must not downgrade to GC=F.
                df_m30 = df_paxg
                DATA_SOURCE = PAXG_DATA_SOURCE
                DAILY_DATA_SOURCE = PAXG_DATA_SOURCE
                m30_is_spot = True
                _log(f"   PAXG-USD M30: {len(df_m30)} bars")
                try:
                    df_h1 = _yf_ohlc(PAXG_TICKER, '60d', '60m')
                except Exception as e:
                    _log(f"   PAXG H1 fetch failed: {e}")
                    df_h1 = None
        except Exception as e:
            _log(f"   PAXG fetch failed: {e}")

        if df_m30 is None:
            _log("[*] PAXG unavailable, falling back to Yahoo Finance GC=F...")
            DATA_SOURCE = "Yahoo Finance GC=F (紐約期貨)"
            DAILY_DATA_SOURCE = DATA_SOURCE
            m30_is_spot = False
            df_m30 = _yf_ohlc(YF_TICKER, '30d', '30m')
            df_h1 = _yf_ohlc(YF_TICKER, '60d', '60m')

    # --- H1 fallback: same venue only ---
    if df_m30 is not None and (df_h1 is None or df_h1.empty):
        _log("[*] H1 missing — retrying M30 resample (same venue)...")
        try:
            df_h1 = _resample_h1_from_m30(df_m30)
            if df_h1 is not None and not df_h1.empty:
                _log(f"   M30→H1 resample: {len(df_h1)} bars")
            else:
                df_h1 = None
        except Exception as e:
            _log(f"   H1 resample retry failed: {e}")
            df_h1 = None
        # Futures H1 only when M30 is already futures — never mix GC=F H1 with spot M30
        if (df_h1 is None or df_h1.empty) and not m30_is_spot:
            try:
                df_h1 = _yf_ohlc(YF_TICKER, '60d', '60m')
                if df_h1 is not None and not df_h1.empty:
                    _log(f"   YF H1 fallback: {len(df_h1)} bars")
                else:
                    df_h1 = None
            except Exception as e:
                _log(f"   YF H1 fallback failed: {e}")
                df_h1 = None

    # --- Daily: TV only when M30 is TV; PAXG daily only when M30 is spot ---
    if m30_from_tv and _TV_AVAILABLE:
        try:
            daily_iv = getattr(TVInterval, 'in_daily', None)
            if daily_iv is not None:
                _log("[*] Fetching daily (TradingView spot)...")
                df_tv_day = _tv.get_hist(TV_SYMBOL, TV_EXCHANGE, interval=daily_iv, n_bars=180)
                df_tv_day = _normalize_tv_ohlc(df_tv_day)
                if _frame_ok(df_tv_day, MIN_BARS['day']):
                    df_day = df_tv_day
                    DAILY_DATA_SOURCE = DATA_SOURCE
                    _log(f"   TV Daily: {len(df_day)} bars")
        except Exception as e:
            _log(f"   TV Daily failed: {e}")
            df_day = None

    if df_day is None or df_day.empty:
        if m30_is_spot:
            _log("[*] Fetching daily (yfinance PAXG-USD)...")
            try:
                df_day = _yf_ohlc(PAXG_TICKER, '6mo', '1d')
            except Exception as e:
                _log(f"   PAXG daily failed: {e}")
                df_day = None
            if _frame_ok(df_day, MIN_BARS['day']):
                DAILY_DATA_SOURCE = PAXG_DATA_SOURCE
                _log(f"   PAXG daily: {len(df_day)} bars")
            else:
                _log("   PAXG daily insufficient, falling back to GC=F...")
                df_day = _yf_ohlc(YF_TICKER, '6mo', '1d')
                DAILY_DATA_SOURCE = "Yahoo Finance GC=F (紐約期貨)"
        else:
            _log("[*] Fetching daily (yfinance GC=F, same venue as M30)...")
            df_day = _yf_ohlc(YF_TICKER, '6mo', '1d')
            DAILY_DATA_SOURCE = DATA_SOURCE

    # Validate
    validate_dataframe(df_m30, 'M30', MIN_BARS['m30'])
    validate_dataframe(df_h1, 'H1', MIN_BARS['h1'])
    validate_dataframe(df_day, 'Daily', MIN_BARS['day'])

    intraday_src = DATA_SOURCE
    daily_src = DAILY_DATA_SOURCE if DAILY_DATA_SOURCE != DATA_SOURCE else DATA_SOURCE
    src_note = intraday_src if intraday_src == daily_src else f"{intraday_src} | 日線: {daily_src}"

    # --- M15: native fetch (TV or yfinance); do not upsample M30 ---
    df_m15 = None
    if _TV_AVAILABLE and m30_from_tv:
        # TV M15 only when M30 itself came from TV — same-venue rule as daily.
        try:
            df_m15 = _tv.get_hist(TV_SYMBOL, TV_EXCHANGE, interval=TVInterval.in_15_minute, n_bars=500)
            df_m15 = _normalize_tv_ohlc(df_m15)
            if df_m15 is not None:
                M15_DATA_SOURCE = DATA_SOURCE
                _log(f"   TV M15: {len(df_m15)} bars")
        except Exception as e:
            _log(f"   TV M15 failed: {e}")
            df_m15 = None

    if df_m15 is None:
        # Venue must match M30 basis (2026-08-25): entry-timing signals are
        # price-level comparisons — mixing a spot-based setup with GC=F
        # timing bars misfires by the whole futures premium ($15-65 during
        # rollover). Any spot M30 (TV or PAXG) → PAXG M15 fallback;
        # GC=F M30 → GC=F M15.
        m15_ticker = PAXG_TICKER if m30_is_spot else YF_TICKER
        try:
            _log(f"[*] M15 fallback: Yahoo Finance {m15_ticker} 15m...")
            df_m15 = _yf_ohlc(m15_ticker, '5d', '15m')
            if df_m15 is not None and not df_m15.empty:
                M15_DATA_SOURCE = (
                    PAXG_DATA_SOURCE if m15_ticker == PAXG_TICKER
                    else "Yahoo Finance GC=F (紐約期貨)"
                )
                _log(f"   YF M15 ({m15_ticker}): {len(df_m15)} bars")
            else:
                df_m15 = None
        except Exception as e:
            _log(f"   YF M15 failed: {e}")
            df_m15 = None

    if df_m15 is not None and (df_m15.empty or len(df_m15) < MIN_BARS['m15']):
        _log(f"   M15 insufficient ({0 if df_m15 is None else len(df_m15)} bars) — M15 timing disabled")
        df_m15 = None
        M15_DATA_SOURCE = None

    # --- M5: native fetch (TV or yfinance); do not upsample M30 ---
    # Same venue rule as M15: spot M30 (TV or PAXG) → PAXG 5m fallback;
    # GC=F M30 → GC=F 5m. M5 is informational only (fast structure / trigger
    # timing) and NEVER feeds the push gate (2026-09-03 decision).
    df_m5 = None
    if _TV_AVAILABLE and m30_from_tv:
        try:
            df_m5 = _tv.get_hist(TV_SYMBOL, TV_EXCHANGE, interval=TVInterval.in_5_minute, n_bars=500)
            df_m5 = _normalize_tv_ohlc(df_m5)
            if df_m5 is not None:
                M5_DATA_SOURCE = DATA_SOURCE
                _log(f"   TV M5: {len(df_m5)} bars")
        except Exception as e:
            _log(f"   TV M5 failed: {e}")
            df_m5 = None

    if df_m5 is None:
        m5_ticker = PAXG_TICKER if m30_is_spot else YF_TICKER
        try:
            _log(f"[*] M5 fallback: Yahoo Finance {m5_ticker} 5m...")
            # 5d (not 2d): GC=F has <100 5m bars in the last 2 calendar days
            # after a weekend, which silently disabled M5 every Monday.
            df_m5 = _yf_ohlc(m5_ticker, '5d', '5m')
            if df_m5 is not None and not df_m5.empty:
                M5_DATA_SOURCE = (
                    PAXG_DATA_SOURCE if m5_ticker == PAXG_TICKER
                    else "Yahoo Finance GC=F (紐約期貨)"
                )
                _log(f"   YF M5 ({m5_ticker}): {len(df_m5)} bars")
            else:
                df_m5 = None
        except Exception as e:
            _log(f"   YF M5 failed: {e}")
            df_m5 = None

    if df_m5 is not None and (df_m5.empty or len(df_m5) < MIN_BARS['m5']):
        _log(f"   M5 insufficient ({0 if df_m5 is None else len(df_m5)} bars) — M5 quick structure disabled")
        df_m5 = None
        M5_DATA_SOURCE = None

    _log(f"   📡 Final: M30={len(df_m30)} bars | H1={len(df_h1)} | M15={len(df_m15) if df_m15 is not None else 0} | M5={len(df_m5) if df_m5 is not None else 0} | Daily={len(df_day)} | Source: {src_note}")
    return df_m30, df_h1, df_m15, df_m5, df_day

# ═══════════════════════════════════════════════════════════
# INDICATORS (lightweight — just what we need)
# ═══════════════════════════════════════════════════════════

def add_indicators(df):
    """Add RSI, ATR, volume SMA."""
    df['RSI'] = ta.momentum.RSIIndicator(df['Close'], window=14).rsi()
    df['ATR'] = ta.volatility.AverageTrueRange(df['High'], df['Low'], df['Close'], window=14).average_true_range()
    df['Vol_SMA20'] = ta.trend.sma_indicator(df['Volume'], window=20)
    df['MA20'] = ta.trend.sma_indicator(df['Close'], window=20)
    df['MA50'] = ta.trend.sma_indicator(df['Close'], window=50)
    return df

# ═══════════════════════════════════════════════════════════
# SWING POINTS (time-ordered, with sequence info)
# ═══════════════════════════════════════════════════════════

def find_swings_ordered(high, low, lookback=3, atr=None, close=None):
    """
    Find swing points in time order, alternating high/low.
    Returns list of {'type':'high'|'low', 'idx':int, 'price':float}

    ATR adaptive mode: when atr + close are provided, lookback scales with
    recent volatility relative to ATR. High volatility → larger lookback
    (fewer, more significant swings); low volatility → smaller lookback
    (catch genuine swings in tight ranges). Floor=2, cap=6.
    """
    n = len(high)
    points = []

    # Precompute adaptive lookback per bar if ATR + close given
    adaptive_lb = None
    if atr is not None and close is not None and n == len(close):
        atr_arr = np.atleast_1d(atr)
        close_arr = np.atleast_1d(close)
        if len(atr_arr) == n:
            adaptive_lb = np.full(n, lookback, dtype=int)
            for i in range(n):
                if atr_arr[i] > 0 and not np.isnan(atr_arr[i]):
                    ratio = atr_arr[i] / close_arr[i] if close_arr[i] > 0 else 0
                    # Map ATR/close ratio to lookback: typical 0.3%-1.5% for M30 gold
                    # ratio < 0.4% → lb=2, 0.4-0.7% → 3, 0.7-1.0% → 4, 1.0-1.5% → 5, >1.5% → 6
                    if ratio < 0.004:
                        adaptive_lb[i] = 2
                    elif ratio < 0.007:
                        adaptive_lb[i] = 3
                    elif ratio < 0.010:
                        adaptive_lb[i] = 4
                    elif ratio < 0.015:
                        adaptive_lb[i] = 5
                    else:
                        adaptive_lb[i] = 6

    for i in range(2, n - 2):
        lb = int(adaptive_lb[i]) if adaptive_lb is not None else lookback
        if i < lb or i >= n - lb:
            continue
        h = float(high[i])
        l = float(low[i])

        is_swing_high = all(h >= float(high[i-j]) for j in range(1, lb+1)) and \
                        all(h >= float(high[i+j]) for j in range(1, lb+1))
        is_swing_low = all(l <= float(low[i-j]) for j in range(1, lb+1)) and \
                       all(l <= float(low[i+j]) for j in range(1, lb+1))

        if is_swing_high and is_swing_low:
            # Both true (rare) — pick the stronger one
            h_dev = h - min(float(high[i-j]) for j in range(-lb, lb+1) if j != 0)
            l_dev = max(float(low[i-j]) for j in range(-lb, lb+1) if j != 0) - l
            if h_dev >= l_dev:
                points.append({'type': 'high', 'idx': i, 'price': round(h, 2)})
            else:
                points.append({'type': 'low', 'idx': i, 'price': round(l, 2)})
        elif is_swing_high:
            points.append({'type': 'high', 'idx': i, 'price': round(h, 2)})
        elif is_swing_low:
            points.append({'type': 'low', 'idx': i, 'price': round(l, 2)})
    
    # Deduplicate consecutive same-type points (keep the more extreme)
    deduped = []
    for p in points:
        if not deduped:
            deduped.append(p)
        elif p['type'] == deduped[-1]['type']:
            # Same type consecutively — keep the stronger
            prev = deduped[-1]
            if p['type'] == 'high' and p['price'] > prev['price']:
                deduped[-1] = p
            elif p['type'] == 'low' and p['price'] < prev['price']:
                deduped[-1] = p
            # else keep the previous
        else:
            deduped.append(p)
    
    return deduped

# ═══════════════════════════════════════════════════════════
# PATTERN DETECTION ENGINE
# ═══════════════════════════════════════════════════════════

def linear_regression(x, y):
    """Simple linear regression slope."""
    n = len(x)
    if n < 2:
        return 0
    x_mean = np.mean(x)
    y_mean = np.mean(y)
    num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    den = sum((xi - x_mean)**2 for xi in x)
    return num / den if den != 0 else 0

def detect_triangles(points, df=None, tolerance_pct=0.008):
    """
    Detect triangles from time-ordered swing points.
    Uses percentage-based checks instead of raw slopes.
    """
    if len(points) < 6:
        return []
    
    triangles = []
    window_sizes = [s for s in [10, 15, 20, 25] if len(points) >= s]
    found_types = set()
    
    for window_size in window_sizes:
        recent = points[-window_size:]
        highs_in_window = [p for p in recent if p['type'] == 'high']
        lows_in_window = [p for p in recent if p['type'] == 'low']
        
        if len(highs_in_window) < 2 or len(lows_in_window) < 2:
            continue
        
        use_highs = highs_in_window[-4:]
        use_lows = lows_in_window[-4:]
        
        high_prices = [h['price'] for h in use_highs]
        low_prices = [l['price'] for l in use_lows]
        
        avg_price = np.mean(high_prices + low_prices)
        tol_dollars = max(avg_price * tolerance_pct, 15)
        
        # Check for flat support (2+ lows within tight range)
        low_pairs_flat = False
        flat_low_level = 0
        for i in range(len(low_prices)-1):
            for j in range(i+1, len(low_prices)):
                if abs(low_prices[i] - low_prices[j]) < tol_dollars * 2:
                    low_pairs_flat = True
                    flat_low_level = (low_prices[i] + low_prices[j]) / 2
                    break
            if low_pairs_flat:
                break
        
        # Check for descending highs (percentage decline)
        highs_descending = False
        seq_lower = 0
        if len(high_prices) >= 2:
            pct_decline = (high_prices[0] - high_prices[-1]) / high_prices[0]
            seq_lower = sum(1 for i in range(len(high_prices)-1) if high_prices[i] > high_prices[i+1])
            highs_descending = pct_decline > 0.005 and seq_lower >= 1
        
        # Check for flat highs
        high_pairs_flat = False
        flat_high_level = 0
        for i in range(len(high_prices)-1):
            for j in range(i+1, len(high_prices)):
                if abs(high_prices[i] - high_prices[j]) < tol_dollars * 2:
                    high_pairs_flat = True
                    flat_high_level = (high_prices[i] + high_prices[j]) / 2
                    break
            if high_pairs_flat:
                break
        
        # Check for ascending lows
        lows_ascending = False
        seq_higher = 0
        if len(low_prices) >= 2:
            pct_rise = (low_prices[-1] - low_prices[0]) / low_prices[0]
            seq_higher = sum(1 for i in range(len(low_prices)-1) if low_prices[i] < low_prices[i+1])
            lows_ascending = pct_rise > 0.005 and seq_higher >= 1
        
        # Classify
        if low_pairs_flat and highs_descending and 'desc_tri' not in found_types:
            support = round(flat_low_level, 2)
            highest = round(max(high_prices), 2)
            pattern_height = round(highest - support, 2)
            brk = _breakout_status(
                df, 'BEARISH', support=support, tol=tol_dollars * 0.5, points=points
            )

            triangles.append({
                'type': '📐 Descending Triangle (下降三角形)',
                'direction': 'BEARISH',
                'support': support,
                'highest_high': highest,
                'pattern_height': pattern_height,
                'target': round(support - pattern_height, 2),
                **brk,
                'confidence': 'HIGH' if seq_lower >= 2 and pattern_height > tol_dollars*3 else 'MEDIUM',
            })
            found_types.add('desc_tri')
        
        if high_pairs_flat and lows_ascending and 'asc_tri' not in found_types:
            resistance = round(flat_high_level, 2)
            lowest = round(min(low_prices), 2)
            pattern_height = round(resistance - lowest, 2)
            brk = _breakout_status(
                df, 'BULLISH', resistance=resistance, tol=tol_dollars * 0.5, points=points
            )

            triangles.append({
                'type': '📐 Ascending Triangle (上升三角形)',
                'direction': 'BULLISH',
                'resistance': resistance,
                'lowest_low': lowest,
                'pattern_height': pattern_height,
                'target': round(resistance + pattern_height, 2),
                **brk,
                'confidence': 'HIGH' if seq_higher >= 2 and pattern_height > tol_dollars*3 else 'MEDIUM',
            })
            found_types.add('asc_tri')
        
        if highs_descending and lows_ascending and 'sym_tri' not in found_types:
            apex_price = (np.mean(high_prices[-2:]) + np.mean(low_prices[-2:])) / 2
            pattern_height = round(max(high_prices) - min(low_prices), 2)
            upper_bound = round(max(high_prices), 2)
            lower_bound = round(min(low_prices), 2)

            prior_points = [p for p in points if p['idx'] < use_highs[0]['idx']][-4:]
            prior_trend = 'BULLISH' if prior_points and prior_points[-1]['price'] > prior_points[0]['price'] else 'BEARISH'

            brk_up = _breakout_status(
                df, 'BULLISH', resistance=upper_bound, tol=tol_dollars * 0.5, points=points
            )
            brk_down = _breakout_status(
                df, 'BEARISH', support=lower_bound, tol=tol_dollars * 0.5, points=points
            )
            broken_up = brk_up['broke_recently']
            broken_down = brk_down['broke_recently']
            sym_broken = broken_up or broken_down
            sym_direction = 'BULLISH' if brk_up['broken_now'] else (
                'BEARISH' if brk_down['broken_now'] else prior_trend
            )

            triangles.append({
                'type': '📐 Symmetrical Triangle (對稱三角形)',
                'direction': sym_direction,
                'apex': round(apex_price, 2),
                'resistance': upper_bound,
                'support': lower_bound,
                'pattern_height': pattern_height,
                'target_up': round(apex_price + pattern_height, 2),
                'target_down': round(apex_price - pattern_height, 2),
                'broken': sym_broken,
                'broken_now': brk_up['broken_now'] or brk_down['broken_now'],
                'broke_recently': sym_broken,
                'confidence': 'MEDIUM',
            })
            found_types.add('sym_tri')
    
    return triangles

def detect_flags(df, points, lookback=30, atr=None):
    """
    Detect bull/bear flags. Scans for ALL pole+flag combinations.
    Relaxed slope check — only requires flag range <50% of pole.
    """
    if len(df) < lookback + 10:
        return []
    
    recent = df.iloc[-lookback:]
    high_vals = recent['High'].values
    low_vals = recent['Low'].values
    close_vals = recent['Close'].values
    vol_vals = recent['Volume'].values if 'Volume' in recent.columns else np.zeros(len(close_vals))
    
    # Find ALL pole+flag candidates
    candidates = []
    avg_price = float(np.mean(close_vals)) if len(close_vals) else 0.0
    min_pole_move = max((atr or 15) * 1.5, avg_price * 0.003, 15)

    for i in range(len(close_vals) - 15):
        for j in range(i + 5, min(i + 25, len(close_vals) - 3)):
            move = close_vals[j] - close_vals[i]
            if abs(move) < min_pole_move:
                continue
            
            # Check directional consistency
            mid = (i + j) // 2
            first_half = close_vals[i:mid]
            second_half = close_vals[mid:j]
            
            if move > 0 and np.mean(second_half) <= np.mean(first_half):
                continue
            if move < 0 and np.mean(second_half) >= np.mean(first_half):
                continue
            
            # Check consolidation after pole
            flag_start = j
            flag_end = min(j + 15, len(close_vals))
            if flag_end - flag_start < 4:
                continue
            
            flag_high = max(high_vals[flag_start:flag_end])
            flag_low = min(low_vals[flag_start:flag_end])
            flag_range = flag_high - flag_low
            
            # Flag must retrace <50% of pole
            if flag_range > abs(move) * 0.5:
                continue
            
            direction = 'BULLISH' if move > 0 else 'BEARISH'
            pole_length = abs(move)
            
            # Check breakout — broken_now matches setup semantics
            if direction == 'BEARISH':
                breakout = flag_low
                target = flag_low - pole_length
                brk = _breakout_status(
                    df, 'BEARISH', support=round(float(flag_low), 2), points=points
                )
            else:
                breakout = flag_high
                target = flag_high + pole_length
                brk = _breakout_status(
                    df, 'BULLISH', resistance=round(float(flag_high), 2), points=points
                )
            broken = brk['broken']
            broken_now = brk['broken_now']
            
            # Volume check
            pole_vol = np.mean(vol_vals[i:j])
            flag_vol = np.mean(vol_vals[flag_start:flag_end])
            vol_confirm = (
                bool(pole_vol > flag_vol * 1.05)
                if pole_vol > 0 and volume_usable(df)
                else None
            )
            
            # Confidence scoring
            retrace_pct = flag_range / pole_length
            if broken and vol_confirm:
                conf = 'HIGH'
            elif broken_now or (retrace_pct < 0.35 and vol_confirm):
                conf = 'MEDIUM'
            else:
                conf = 'LOW'
            
            candidates.append({
                'pole_start': i, 'pole_end': j,
                'direction': direction,
                'pole_length': pole_length,
                'pole_start_price': round(float(close_vals[i]), 2),
                'pole_end_price': round(float(close_vals[j]), 2),
                'flag_high': round(float(flag_high), 2),
                'flag_low': round(float(flag_low), 2),
                'flag_range': round(float(flag_range), 2),
                'retrace_pct': round(retrace_pct, 2),
                'breakout_level': round(float(breakout), 2),
                'target': round(float(target), 2),
                'broken': broken,
                'broken_now': broken_now,
                'broke_recently': broken,
                'vol_confirm': vol_confirm,
                'confidence': conf,
            })
    
    if not candidates:
        return []
    
    # Deduplicate: keep only the best (highest confidence, then newest) for each direction
    flags = []
    for direction in ['BEARISH', 'BULLISH']:
        dir_cands = [c for c in candidates if c['direction'] == direction]
        if not dir_cands:
            continue
        
        # Sort by confidence then by recency (pole_end descending)
        conf_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
        dir_cands.sort(key=lambda c: (conf_order.get(c['confidence'], 2), -c['pole_end']))
        
        best = dir_cands[0]
        flag_type = '🚩 Bull Flag (牛旗)' if direction == 'BULLISH' else '🚩 Bear Flag (熊旗)'
        
        flags.append({
            'type': flag_type,
            'direction': direction,
            'pole_start_price': best['pole_start_price'],
            'pole_end_price': best['pole_end_price'],
            'pole_length': best['pole_length'],
            'flag_high': best['flag_high'],
            'flag_low': best['flag_low'],
            'flag_range': best['flag_range'],
            'retrace_pct': best['retrace_pct'],
            'breakout_level': best['breakout_level'],
            'target': best['target'],
            'broken': best['broken'],
            'broken_now': best.get('broken_now', best['broken']),
            'broke_recently': best.get('broke_recently', best['broken']),
            'vol_confirm': best['vol_confirm'],
            'confidence': best['confidence'],
        })
    
    return flags

def detect_double_top_bottom(points, df=None, tolerance_pct=0.005):
    """Detect double top or double bottom patterns."""
    if len(points) < 6:
        return []

    recent = points[-15:]
    highs = [p for p in recent if p['type'] == 'high']
    lows = [p for p in recent if p['type'] == 'low']
    avg_price = np.mean([p['price'] for p in recent])
    tol = avg_price * tolerance_pct

    tops, bottoms = [], []

    for i in range(len(highs) - 1):
        for j in range(i + 1, len(highs)):
            if abs(highs[i]['price'] - highs[j]['price']) >= tol * 2:
                continue
            between_lows = [l for l in lows if highs[i]['idx'] < l['idx'] < highs[j]['idx']]
            if not between_lows:
                continue
            valley = min(l['price'] for l in between_lows)
            depth = max(highs[i]['price'], highs[j]['price']) - valley
            if depth <= tol * 3:
                continue
            neckline = round(valley, 2)
            depth = round(depth, 2)
            brk = _breakout_status(df, 'BEARISH', support=neckline, points=points)
            tops.append({
                'type': '🔻 Double Top (雙頂)',
                'direction': 'BEARISH',
                'top_price': round(max(highs[i]['price'], highs[j]['price']), 2),
                'neckline': neckline,
                'depth': depth,
                'target': round(neckline - depth, 2),
                **brk,
                'confidence': 'HIGH' if depth > avg_price * 0.02 else 'MEDIUM',
                '_recency': max(highs[i]['idx'], highs[j]['idx']),
            })

    for i in range(len(lows) - 1):
        for j in range(i + 1, len(lows)):
            if abs(lows[i]['price'] - lows[j]['price']) >= tol * 2:
                continue
            between_highs = [h for h in highs if lows[i]['idx'] < h['idx'] < lows[j]['idx']]
            if not between_highs:
                continue
            peak = max(h['price'] for h in between_highs)
            depth = peak - min(lows[i]['price'], lows[j]['price'])
            if depth <= tol * 3:
                continue
            neckline = round(peak, 2)
            depth = round(depth, 2)
            brk = _breakout_status(df, 'BULLISH', resistance=neckline, points=points)
            bottoms.append({
                'type': '🔺 Double Bottom (雙底)',
                'direction': 'BULLISH',
                'bottom_price': round(min(lows[i]['price'], lows[j]['price']), 2),
                'neckline': neckline,
                'depth': depth,
                'target': round(neckline + depth, 2),
                **brk,
                'confidence': 'HIGH' if depth > avg_price * 0.02 else 'MEDIUM',
                '_recency': max(lows[i]['idx'], lows[j]['idx']),
            })

    patterns = []
    if tops:
        tops.sort(key=lambda p: (-p['_recency'], -p['depth']))
        best = tops[0]
        del best['_recency']
        patterns.append(best)
    if bottoms:
        bottoms.sort(key=lambda p: (-p['_recency'], -p['depth']))
        best = bottoms[0]
        del best['_recency']
        patterns.append(best)
    return patterns

def detect_wedges(points, df=None, tolerance_pct=0.008):
    """
    Detect rising and falling wedges from time-ordered swing points.
    Wedge = converging trendlines with BOTH slopes in same direction.
    """
    if len(points) < 6:
        return []

    wedges = []
    found_types = set()

    window_sizes = [s for s in [10, 15, 20, 25] if len(points) >= s]

    for window_size in window_sizes:
        recent = points[-window_size:]
        highs_in_window = [p for p in recent if p['type'] == 'high']
        lows_in_window = [p for p in recent if p['type'] == 'low']

        if len(highs_in_window) < 2 or len(lows_in_window) < 2:
            continue

        use_highs = highs_in_window[-4:]
        use_lows = lows_in_window[-4:]

        high_prices = [h['price'] for h in use_highs]
        low_prices = [l['price'] for l in use_lows]
        high_indices = [h['idx'] for h in use_highs]
        low_indices = [l['idx'] for l in use_lows]

        high_slope = linear_regression(high_indices, high_prices)
        low_slope = linear_regression(low_indices, low_prices)

        avg_price = np.mean(high_prices + low_prices)
        tol_dollars = max(avg_price * tolerance_pct, 15)

        rising_wedge = high_slope > 0 and low_slope > 0 and high_slope < low_slope
        falling_wedge = high_slope < 0 and low_slope < 0 and high_slope > low_slope

        spread_start = high_prices[0] - low_prices[0]
        spread_end = high_prices[-1] - low_prices[-1]
        converging = spread_end < spread_start * 0.85 and spread_start > tol_dollars

        if not converging or not (rising_wedge or falling_wedge):
            continue

        if rising_wedge and 'rising_wedge' not in found_types:
            support = round(low_prices[-1], 2)
            resistance = round(high_prices[-1], 2)
            pattern_height = round(resistance - support, 2)
            brk = _breakout_status(df, 'BEARISH', support=support, points=points)
            wedges.append({
                'type': '△ Rising Wedge (上升楔形)',
                'direction': 'BEARISH',
                'support': support,
                'resistance': resistance,
                'pattern_height': pattern_height,
                'target': round(support - pattern_height, 2),
                **brk,
                'confidence': 'LOW',  # Rising wedge: backtest 43.3% win rate, lowest of all patterns
            })
            found_types.add('rising_wedge')

        if falling_wedge and 'falling_wedge' not in found_types:
            support = round(low_prices[-1], 2)
            resistance = round(high_prices[-1], 2)
            pattern_height = round(resistance - support, 2)
            brk = _breakout_status(df, 'BULLISH', resistance=resistance, points=points)
            wedges.append({
                'type': '▽ Falling Wedge (下降楔形)',
                'direction': 'BULLISH',
                'support': support,
                'resistance': resistance,
                'pattern_height': pattern_height,
                'target': round(resistance + pattern_height, 2),
                **brk,
                'confidence': 'MEDIUM' if pattern_height > tol_dollars * 2 else 'LOW',
            })
            found_types.add('falling_wedge')

    return wedges

def detect_channels(points, df=None, tolerance_pct=0.008):
    """
    Detect parallel channels (equidistant upper/lower trendlines).
    Horizontal / Ascending / Descending channels.
    """
    if len(points) < 6:
        return []
    
    channels = []
    # Track best channel per type across all windows (not just first match)
    best_per_type = {}  # chan_type -> (confidence_rank, window_size, channel_dict)
    conf_rank = {'HIGH': 2, 'MEDIUM': 1, 'LOW': 0}
    window_sizes = [s for s in [10, 15, 20, 25] if len(points) >= s]
    
    for window_size in window_sizes:
        recent = points[-window_size:]
        highs_in_window = [p for p in recent if p['type'] == 'high']
        lows_in_window = [p for p in recent if p['type'] == 'low']
        
        # RISK 5 fix: require ≥3 points per side for meaningful regression
        if len(highs_in_window) < 3 or len(lows_in_window) < 3:
            continue
        
        use_highs = highs_in_window[-4:]
        use_lows = lows_in_window[-4:]
        
        high_prices = [h['price'] for h in use_highs]
        low_prices = [l['price'] for l in use_lows]
        high_indices = [h['idx'] for h in use_highs]
        low_indices = [l['idx'] for l in use_lows]
        
        high_slope = linear_regression(high_indices, high_prices)
        low_slope = linear_regression(low_indices, low_prices)
        
        avg_price = np.mean(high_prices + low_prices)
        tol_dollars = max(avg_price * tolerance_pct, 15)
        
        # Slope ratio — parallel means slopes within 30% AND same sign
        # BUG 2 fix: check sign agreement, but allow gentle slopes (horizontal channel)
        # Use rise relative to channel width to determine flatness, not absolute slope threshold
        max_slope = max(abs(high_slope), abs(low_slope))
        min_slope = min(abs(high_slope), abs(low_slope))
        # Estimate channel height for relative slope check
        est_height = abs(np.mean(high_prices) - np.mean(low_prices))
        window_span_est = max(max(high_indices), max(low_indices)) - min(min(high_indices), min(low_indices))
        max_rise = max_slope * window_span_est if window_span_est > 0 else 0
        
        if max_rise < est_height * 0.15:
            # Gentle slopes: same sign required (reject converging triangles)
            # slope_ratio threshold relaxed — both slopes are already confirmed
            # gentle relative to channel height, so exact ratio matters less
            same_sign = (high_slope >= 0) == (low_slope >= 0)
            if not same_sign:
                is_parallel = False
                slope_ratio = 0.0
            elif max_slope < 0.01:
                slope_ratio = 1.0
                is_parallel = True
            else:
                r = high_slope / low_slope if abs(low_slope) > 1e-9 else 0.0
                slope_ratio = abs(r) if abs(r) <= 1 else 1.0 / abs(r)
                is_parallel = slope_ratio > 0.3  # relaxed: gentle slopes already filtered by max_rise
        elif (high_slope > 0) != (low_slope > 0):
            # Opposite signs → diverging/converging, not parallel channel
            is_parallel = False
        elif min_slope < 0.01:
            slope_ratio = 0.0  # one flat, one not → not parallel
            is_parallel = False
        else:
            r = high_slope / low_slope
            slope_ratio = r if r <= 1 else 1.0 / r
            is_parallel = slope_ratio > 0.6
        
        if not is_parallel:
            continue
        
        # Compute regression intercepts early (needed for spread measurement)
        high_intercept = np.mean(high_prices) - high_slope * np.mean(high_indices)
        low_intercept = np.mean(low_prices) - low_slope * np.mean(low_indices)
        
        # Channel spread must be stable (not converging like wedge)
        # BUG 4 fix: time-align spread using regression lines, not position-paired prices
        spreads = []
        for i in range(len(use_highs)):
            h_idx = high_indices[i]
            low_line_at_h = low_intercept + low_slope * h_idx
            spreads.append(abs(high_prices[i] - low_line_at_h))
        for i in range(len(use_lows)):
            l_idx = low_indices[i]
            high_line_at_l = high_intercept + high_slope * l_idx
            spreads.append(abs(high_line_at_l - low_prices[i]))
        
        avg_spread = np.mean(spreads)
        spread_range = max(spreads) - min(spreads)
        stable_spread = avg_spread > tol_dollars * 2 and spread_range < avg_spread * 0.4
        
        if not stable_spread:
            continue
        
        # Determine channel type — use rise relative to channel height, not absolute slope
        window_span = max(high_indices[-1], low_indices[-1]) - min(high_indices[0], low_indices[0])
        total_high_rise = abs(high_slope) * window_span if window_span > 0 else 0
        total_low_rise = abs(low_slope) * window_span if window_span > 0 else 0
        is_flat = (total_high_rise < avg_spread * 0.15 and total_low_rise < avg_spread * 0.15)

        if is_flat:
            chan_type = 'Horizontal'
            direction = None
        elif high_slope > 0.02 and low_slope > 0.02:
            chan_type = 'Ascending'
            direction = None
        elif high_slope < -0.02 and low_slope < -0.02:
            chan_type = 'Descending'
            direction = None
        else:
            continue  # not a valid channel
        
        chan_key = chan_type.lower()
        
        # Project channel boundaries to current bar (not last swing)
        last_swing_idx = max(high_indices[-1], low_indices[-1])
        proj_idx = (len(df) - 1) if df is not None and len(df) else last_swing_idx
        upper_bound = high_intercept + high_slope * proj_idx
        lower_bound = low_intercept + low_slope * proj_idx
        
        # BUG 3 fix: guard against crossing regression lines
        if upper_bound <= lower_bound:
            continue  # lines cross — not a valid channel
        
        resistance = round(upper_bound, 2)
        support = round(lower_bound, 2)
        pattern_height = round(resistance - support, 2)
        
        # BUG 3 fix: pattern_height must be positive
        if pattern_height <= 0:
            continue

        cur_price = float(df['Close'].iloc[-1]) if df is not None and len(df) else None
        if cur_price is not None:
            pos = (cur_price - support) / pattern_height
            if pos >= 0.7:
                direction = 'BULLISH'
            elif pos <= 0.3:
                direction = 'BEARISH'
            elif chan_type == 'Horizontal':
                direction = 'NEUTRAL'
            elif chan_type == 'Ascending':
                direction = 'BULLISH'
            else:
                direction = 'BEARISH'
        elif chan_type == 'Horizontal':
            direction = 'BULLISH' if avg_spread > tol_dollars * 3 else 'NEUTRAL'
        else:
            direction = 'BULLISH' if chan_type == 'Ascending' else 'BEARISH'

        # Dual-side breakout: recent closes vs current price
        recent_closes = None
        if df is not None and len(df) >= 5:
            recent_closes = df['Close'].values[-5:]

        broken_up_recent = bool(recent_closes is not None and np.any(recent_closes > resistance))
        broken_down_recent = bool(recent_closes is not None and np.any(recent_closes < support))
        broken_up_now = cur_price is not None and cur_price > resistance
        broken_down_now = cur_price is not None and cur_price < support

        broke_recently = broken_up_recent or broken_down_recent
        broken_now = broken_up_now or broken_down_now

        if broken_down_now:
            target = support - pattern_height
        elif broken_up_now:
            target = resistance + pattern_height
        elif direction == 'BEARISH':
            target = support - pattern_height
        elif direction == 'BULLISH':
            target = resistance + pattern_height
        else:
            target = None

        broken = broke_recently
        
        confidence = 'HIGH' if pattern_height > tol_dollars * 4 and slope_ratio > 0.8 else 'MEDIUM'
        
        icon_map = {
            'Horizontal': '📏 Horizontal Channel (平行通道)',
            'Ascending': '📈 Ascending Channel (上升通道)',
            'Descending': '📉 Descending Channel (下降通道)',
        }
        
        channel_dict = {
            'type': icon_map.get(chan_type, f'📏 {chan_type} Channel'),
            'direction': direction,
            'support': support,
            'resistance': resistance,
            'pattern_height': pattern_height,
            'target': round(target, 2) if target is not None else None,
            'broken': broken,
            'broken_now': broken_now,
            'broke_recently': broke_recently,
            'broke_recently_up': broken_up_recent,
            'broke_recently_down': broken_down_recent,
            'confidence': confidence,
        }
        
        # RISK 4 fix: keep best channel per type (largest window / highest confidence)
        rank = conf_rank.get(confidence, 0) * 100 + window_size
        if chan_key not in best_per_type or rank > best_per_type[chan_key][0]:
            best_per_type[chan_key] = (rank, window_size, channel_dict)
    
    # Collect best channels
    for rank, wsize, ch in best_per_type.values():
        channels.append(ch)
    return channels

def detect_all_patterns(df, points, atr=None):
    """Run all pattern detectors with multiple lookback windows."""
    all_patterns = []
    
    # Triangles
    triangles = detect_triangles(points, df=df)
    all_patterns.extend(triangles)
    
    # Wedges
    wedges = detect_wedges(points, df=df)
    all_patterns.extend(wedges)
    
    # Channels
    channels = detect_channels(points, df=df)
    all_patterns.extend(channels)
    
    # RISK 1 fix: cross-pattern mutual exclusion — drop lower-confidence overlaps
    if channels:
        overlap_tol = max(15, (atr or 15) * 0.5)

        def _overlap(p1, p2, tol=overlap_tol):
            s1, r1 = p1.get('support'), p1.get('resistance')
            s2, r2 = p2.get('support'), p2.get('resistance')
            if s1 is None and r1 is None:
                return False
            if s2 is None and r2 is None:
                return False
            if None not in (s1, r1, s2, r2):
                if abs(s1 - s2) < tol and abs(r1 - r2) < tol:
                    return True
            h1 = p1.get('pattern_height') or (abs(r1 - s1) if s1 is not None and r1 is not None else None)
            h2 = p2.get('pattern_height') or (abs(r2 - s2) if s2 is not None and r2 is not None else None)
            boundary_matches = sum(
                1 for a, b in [(s1, s2), (s1, r2), (r1, s2), (r1, r2)]
                if a is not None and b is not None and abs(a - b) < tol
            )
            if boundary_matches >= 1 and h1 and h2 and abs(h1 - h2) < tol * 2:
                return True
            return False

        conf_rank_fn = lambda p: {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}.get(p.get('confidence', 'LOW'), 1)
        to_remove = set()

        # Channel vs channel — keep higher-confidence overlap
        for i, ch1 in enumerate(channels):
            if id(ch1) in to_remove:
                continue
            for ch2 in channels[i + 1:]:
                if id(ch2) in to_remove:
                    continue
                if _overlap(ch1, ch2):
                    loser = ch2 if conf_rank_fn(ch1) >= conf_rank_fn(ch2) else ch1
                    to_remove.add(id(loser))

        for i, ch in enumerate(channels):
            if id(ch) in to_remove:
                continue
            for j, other in enumerate(all_patterns):
                if other is ch or j in to_remove or id(other) in to_remove:
                    continue
                if 'Channel' in other.get('type', ''):
                    continue
                if _overlap(ch, other):
                    if conf_rank_fn(ch) > conf_rank_fn(other):
                        to_remove.add(j)
                    else:
                        to_remove.add(id(ch))
        all_patterns = [p for i, p in enumerate(all_patterns) if i not in to_remove and id(p) not in to_remove]

    # Flags — scan multiple windows (cap lookback so len(df) guard is reachable)
    max_lb = min(max(25, len(df) - 11), 80)
    for lb in sorted(set([25, 35, 45, 55, max_lb])):
        flags = detect_flags(df, points, lookback=lb, atr=atr)
        for f in flags:
            # Check if similar flag already added
            if not any(p.get('type') == f['type'] and 
                      abs(p.get('pole_length', 0) - f['pole_length']) < 5 
                      for p in all_patterns):
                all_patterns.append(f)
    
    # Double top/bottom
    dbl = detect_double_top_bottom(points, df=df)
    all_patterns.extend(dbl)
    
    # Sort by confidence
    conf_order = {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}
    all_patterns.sort(key=lambda p: conf_order.get(p.get('confidence', 'LOW'), 2))

    # Breakout + retest / volume confirmation for major pattern types
    _false_breakout_types = GATED_BREAKOUT_PATTERN_TYPES
    for p in all_patterns:
        p_type = p.get('type', '')
        if not any(ft in p_type for ft in _false_breakout_types):
            continue

        direction = p.get('direction', '')
        if not _directional_broke_recently(p, direction):
            continue

        support, resistance = _pattern_retest_levels(p, direction)
        tol = max(1.0, (atr or 15) * 0.1)

        retest = _retest_confirm(df, direction, support=support,
                                 resistance=resistance, tol=tol)
        vol_confirmed = _breakout_volume_confirmed(p, df)
        vol_spike = _volume_spike(df)

        p['retest_confirmed'] = retest
        p['vol_spike'] = vol_spike
        p['vol_breakout_confirmed'] = vol_confirmed

        if not retest and not vol_confirmed:
            if p.get('confidence') == 'HIGH':
                p['confidence'] = 'MEDIUM'
                p['confidence_reason'] = '突破未經回測/量能確認，降級'
            elif p.get('confidence') == 'MEDIUM':
                p['confidence'] = 'LOW'
                p['confidence_reason'] = '突破未經回測/量能確認，降級'

    all_patterns.sort(key=lambda p: conf_order.get(p.get('confidence', 'LOW'), 2))

    return all_patterns

def volume_confirm(df, idx, direction='breakout', window=5):
    """
    Generic volume confirmation for breakouts.
    Compares volume at/near idx against 20-bar SMA.
    Returns bool or None if no volume data (GC=F volume often unreliable).
    """
    if not volume_usable(df) or len(df) < window + 5:
        return None
    vol_vals = df['Volume'].values
    vol_sma = pd.Series(vol_vals).rolling(window=20, min_periods=1).mean().values
    if idx < 0 or idx >= len(vol_vals):
        return None
    recent_vol = np.mean(vol_vals[max(0, idx-window+1):idx+1])
    baseline = vol_sma[idx]
    if baseline <= 0 or np.isnan(baseline):
        return None
    return bool(recent_vol > baseline * 1.05)

def add_volume_to_patterns(patterns, df, points):
    """Attach vol_confirm to triangle/double/wedge patterns using recent bars."""
    last_idx = len(df) - 1
    for p in patterns:
        if 'vol_confirm' in p:
            continue
        p['vol_confirm'] = volume_confirm(df, last_idx)
    return patterns

# ═══════════════════════════════════════════════════════════
# MULTI-TIMEFRAME TREND ANALYSIS
# ═══════════════════════════════════════════════════════════

def analyze_daily_trend(df_day):
    """Determine daily trend strength and direction."""
    if df_day is None or len(df_day) < 30:
        return _neutral_trend(df_day)
    
    close = df_day['Close'].values
    last_close = close[-1]
    if np.isnan(last_close) or last_close <= 0:
        return _neutral_trend(df_day)

    ma20 = ta.trend.sma_indicator(df_day['Close'], window=20).values
    ma50 = ta.trend.sma_indicator(df_day['Close'], window=50).values
    
    last_ma20 = ma20[-1]
    raw_ma50 = ma50[-1]
    last_ma20 = float(last_ma20) if not np.isnan(last_ma20) else float(last_close)
    last_ma50 = float(raw_ma50) if not np.isnan(raw_ma50) else last_ma20
    
    # Trend determination
    if last_close > last_ma20 > last_ma50:
        trend = 'BULLISH'
        strength = 2
    elif last_close > last_ma20:
        trend = 'BULLISH'
        strength = 1
    elif last_close < last_ma20 < last_ma50:
        trend = 'BEARISH'
        strength = 2
    elif last_close < last_ma20:
        trend = 'BEARISH'
        strength = 1
    else:
        trend = 'NEUTRAL'
        strength = 0
    
    rsi = ta.momentum.RSIIndicator(df_day['Close'], window=14).rsi().values[-1]
    rsi_val = 50.0 if np.isnan(rsi) else float(rsi)
    
    return {
        'trend': trend,
        'strength': strength,
        'close': round(float(last_close), 2),
        'ma20': round(float(last_ma20), 2),
        'ma50': round(float(last_ma50), 2),
        'rsi': round(rsi_val, 1),
    }

def analyze_h1_trend(df_h1):
    """Determine H1 trend for intermediate-timeframe confluence."""
    if len(df_h1) < 30:
        return _neutral_trend(df_h1)

    close = df_h1['Close'].values
    ma20 = ta.trend.sma_indicator(df_h1['Close'], window=20).values
    ma50 = ta.trend.sma_indicator(df_h1['Close'], window=50).values

    last_close = close[-1]
    last_ma20 = ma20[-1]
    raw_ma50 = ma50[-1]
    last_ma50 = float(raw_ma50) if not np.isnan(raw_ma50) else (
        float(last_ma20) if not np.isnan(last_ma20) else float(last_close)
    )

    if last_close > last_ma20 > last_ma50:
        trend = 'BULLISH'
        strength = 2
    elif last_close > last_ma20:
        trend = 'BULLISH'
        strength = 1
    elif last_close < last_ma20 < last_ma50:
        trend = 'BEARISH'
        strength = 2
    elif last_close < last_ma20:
        trend = 'BEARISH'
        strength = 1
    else:
        trend = 'NEUTRAL'
        strength = 0

    rsi = ta.momentum.RSIIndicator(df_h1['Close'], window=14).rsi().values[-1]

    return {
        'trend': trend,
        'strength': strength,
        'close': round(float(last_close), 2),
        'ma20': round(float(last_ma20), 2),
        'ma50': round(float(last_ma50), 2),
        'rsi': round(float(rsi), 1),
    }

# ═══════════════════════════════════════════════════════════
# M15 ENTRY TIMING
# ═══════════════════════════════════════════════════════════

def _analyze_mini_tf(df, patterns, points, current_price, atr, atr_scale=0.7):
    """
    Core mini-timeframe (M5/M15) structure + per-pattern entry timing.
    Informational ONLY — never feeds cron_push_eligible (2026-09-03 decision).
    Returns dict with neutral keys: trend, rsi, swings, suggestions.
    """
    if df is None or df.empty or len(df) < 20:
        return {
            'trend': 'N/A (數據不足)',
            'rsi': None,
            'swings': None,
            'suggestions': [],
        }

    close = df['Close'].values
    high = df['High'].values
    low = df['Low'].values
    n = len(close)

    # --- Short-term trend from last 20 bars ---
    lookback = min(20, n)
    recent = close[-lookback:]
    ma10 = np.mean(recent[-min(10, lookback):])
    if len(recent) >= 20:
        first_half = np.mean(recent[:10])
        second_half = np.mean(recent[10:])
        if second_half > first_half * 1.001:
            trend = '🟢 BULLISH'
        elif second_half < first_half * 0.999:
            trend = '🔴 BEARISH'
        else:
            trend = '🟡 NEUTRAL'
    else:
        if close[-1] > ma10:
            trend = '🟢 BULLISH'
        elif close[-1] < ma10:
            trend = '🔴 BEARISH'
        else:
            trend = '🟡 NEUTRAL'

    # --- RSI ---
    rsi = None
    try:
        if 'RSI' in df.columns:
            rsi_val = float(df['RSI'].iloc[-1])
            if not np.isnan(rsi_val):
                rsi = round(rsi_val, 1)
    except Exception:
        pass
    if rsi is None:
        try:
            rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
            rsi = round(float(rsi_series[-1]), 1) if not pd.isna(rsi_series[-1]) else None
        except Exception:
            pass

    # --- Swing points: simple swing detection on last ~40 bars ---
    swing_lookback = min(40, n - 4)
    swings = {'highs': [], 'lows': []}
    for i in range(n - swing_lookback + 2, n - 2):
        lb = 3
        if i < lb or i >= n - lb:
            continue
        h = float(high[i])
        l = float(low[i])
        is_high = all(h >= float(high[i - j]) for j in range(1, lb + 1)) and \
                  all(h >= float(high[i + j]) for j in range(1, lb + 1))
        is_low = all(l <= float(low[i - j]) for j in range(1, lb + 1)) and \
                 all(l <= float(low[i + j]) for j in range(1, lb + 1))
        if is_high:
            swings['highs'].append(round(h, 2))
        elif is_low:
            swings['lows'].append(round(l, 2))

    # Nearest swing high/low relative to current price
    nearest_high = None
    nearest_low = None
    if swings['highs']:
        above = [h for h in swings['highs'] if h > current_price]
        if above:
            nearest_high = min(above)
        else:
            nearest_high = max(swings['highs'])  # all below — use highest
    if swings['lows']:
        below = [l for l in swings['lows'] if l < current_price]
        if below:
            nearest_low = max(below)
        else:
            nearest_low = min(swings['lows'])  # all above — use lowest

    # --- Per-pattern entry suggestions ---
    entry_suggestions = []
    mini_atr = None
    try:
        if 'ATR' in df.columns:
            val = float(df['ATR'].iloc[-1])
            if not np.isnan(val):
                mini_atr = val
    except Exception:
        pass
    if mini_atr is None:
        # Estimate mini ATR as a fraction of M30 ATR (M15 ~0.7x, M5 ~0.5x)
        mini_atr = atr * atr_scale

    for p in patterns:
        direction = p.get('direction', 'NEUTRAL')
        ptype = p.get('type', 'Unknown')

        # Tighter entry zone: use mini ATR for refinement
        if 'BULL' in direction.upper() or 'LONG' in direction.upper():
            # For longs: entry zone near recent low + buffer
            entry_low = round(current_price - mini_atr * 0.5, 2)
            entry_high = round(current_price + mini_atr * 0.3, 2)
            sl = round(current_price - mini_atr * 1.5, 2) if nearest_low is None else round(min(current_price - mini_atr * 1.5, nearest_low - mini_atr * 0.3), 2)
        elif 'BEAR' in direction.upper() or 'SHORT' in direction.upper():
            entry_low = round(current_price - mini_atr * 0.3, 2)
            entry_high = round(current_price + mini_atr * 0.5, 2)
            sl = round(current_price + mini_atr * 1.5, 2) if nearest_high is None else round(max(current_price + mini_atr * 1.5, nearest_high + mini_atr * 0.3), 2)
        else:
            entry_low = round(current_price - mini_atr * 0.5, 2)
            entry_high = round(current_price + mini_atr * 0.5, 2)
            sl = round(current_price - mini_atr * 1.5, 2)

        entry_suggestions.append({
            'pattern_type': ptype,
            'direction': direction,
            'entry_zone': f'${entry_low:.0f} – ${entry_high:.0f}',
            'sl': f'${sl:.0f}',
            'mini_atr': round(mini_atr, 2),
        })

    return {
        'trend': trend,
        'rsi': rsi,
        'swings': {
            'nearest_high': nearest_high,
            'nearest_low': nearest_low,
            'highs': swings['highs'][-5:],
            'lows': swings['lows'][-5:],
        },
        'suggestions': entry_suggestions,
    }


def analyze_m15_entry_timing(df_m15, patterns, points, current_price, atr):
    """M15 wrapper — keeps m15_* key shape for existing report/JSON consumers."""
    core = _analyze_mini_tf(df_m15, patterns, points, current_price, atr, atr_scale=0.7)
    return {
        'm15_trend': core['trend'],
        'm15_rsi': core['rsi'],
        'm15_swings': core['swings'],
        'entry_suggestions': [
            {
                'pattern_type': s['pattern_type'],
                'direction': s['direction'],
                'm15_entry_zone': s['entry_zone'],
                'm15_sl': s['sl'],
                'm15_atr': s['mini_atr'],
            }
            for s in core['suggestions']
        ],
    }


def analyze_m5_entry_timing(df_m5, patterns, points, current_price, atr):
    """M5 wrapper — mirrors M15 keys with m5_* prefix (informational only)."""
    core = _analyze_mini_tf(df_m5, patterns, points, current_price, atr, atr_scale=0.5)
    return {
        'm5_trend': core['trend'],
        'm5_rsi': core['rsi'],
        'm5_swings': core['swings'],
        'entry_suggestions': [
            {
                'pattern_type': s['pattern_type'],
                'direction': s['direction'],
                'm5_entry_zone': s['entry_zone'],
                'm5_sl': s['sl'],
                'm5_atr': s['mini_atr'],
            }
            for s in core['suggestions']
        ],
    }


# ═══════════════════════════════════════════════════════════
# CANDLESTICK PATTERN DETECTION
# ═══════════════════════════════════════════════════════════

_REBOUND_EMPTY = {
    'signal': False, 'reason': 'M15 數據不足', 'bar_time': None, 'entry': None,
    'sma10': None, 'prev_high3': None,
}


def detect_rebound_signal(df_m15):
    """
    S3 反彈確認信號 (2026-09-03):
    15m bar: 陽燭 + close > SMA10 + close > 前3bar最高 → 之後 10 分鐘 snapshot。
    ⚠️ 歷史回測協議曾現 look-ahead bias (entry 早於信號確認) — 修正後勝率
    ~47% ≈ baseline ~50%, 即歷史上無 edge。真實勝率由 live paper sim
    (paper_martingale.json) 收集驗證。配合 3 級馬丁 (0.01→0.04) 模擬。
    純資訊 + paper_trade 模擬 — 唔影響 cron_push_eligible。

    2026-09-03 fix: 必須 evaluate 最後一條「已收市」15m bar — tvDatafeed 最後
    一行係形成中 (未收市) bar, cron 10min tick 對 15m bar 永遠撞唔正。舊 code
    用 forming bar 計信號 → 全日 12 個真確認信號全部 miss (實測證實)。
    """
    if df_m15 is None or df_m15.empty or len(df_m15) < 20:
        return dict(_REBOUND_EMPTY)
    close = df_m15['Close'].values
    high = df_m15['High'].values
    open_ = df_m15['Open'].values

    # --- 只睇已收市 bar (naive index = UTC, same convention as M30/H1) ---
    try:
        last_start = pd.Timestamp(df_m15.index[-1])
        if last_start.tzinfo is not None:
            last_start = last_start.tz_convert('UTC').tz_localize(None)
        interval = pd.Timedelta(minutes=15)
        now_ts = pd.Timestamp.now(tz='UTC').tz_localize(None)
        if last_start + interval > now_ts:
            bar_idx = len(df_m15) - 2      # 最後一行未收市 → 用上一條 (已收市)
        else:
            bar_idx = len(df_m15) - 1
    except Exception:
        bar_idx = len(df_m15) - 2          # 判斷失敗 → 保守用上一條
    if bar_idx < 5:
        return dict(_REBOUND_EMPTY)

    last = bar_idx
    sma10 = float(np.mean(close[last - 9:last + 1]))     # 含評估 bar, 同研究 rolling(10) 一致
    prev_high3 = float(np.max(high[last - 3:last]))      # 前 3 支 bar 最高 (唔含評估 bar)
    bullish = close[last] > open_[last]
    signal = bool(bullish and close[last] > sma10 and close[last] > prev_high3)
    bar_time = None
    try:
        bar_time = str(df_m15.index[last])
    except Exception:
        pass
    return {
        'signal': signal,
        'reason': 'S3 陽燭+站上MA10+破3bar高' if signal else '未確認',
        'bar_time': bar_time,
        'entry': round(float(close[last]), 2),
        'sma10': round(sma10, 2),
        'prev_high3': round(prev_high3, 2),
    }



def _body(o, c):
    """Absolute body size."""
    return abs(c - o)

def _range(h, l):
    """Full candle range."""
    return h - l

def _upper_shadow(h, o, c):
    return h - max(o, c)

def _lower_shadow(l, o, c):
    return min(o, c) - l

def _is_bullish(o, c):
    return c > o

def _is_bearish(o, c):
    return o > c


def _short_term_trend(df, i, lookback=5):
    """Trend over prior closes (excludes current bar i)."""
    if i < 1:
        return 'FLAT'
    start = max(0, i - lookback)
    c_end = float(df['Close'].iloc[i - 1])
    c_start = float(df['Close'].iloc[start])
    if c_start == 0:
        return 'FLAT'
    pct = (c_end - c_start) / c_start
    if pct > 0.002:
        return 'UP'
    if pct < -0.002:
        return 'DOWN'
    return 'FLAT'


def _candle_strength_rank(strength):
    return {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}.get(strength, 0)


def _pick_best_candle(candidates):
    """Keep one pattern per bar — highest strength wins."""
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda p: (
            _candle_strength_rank(p['strength']),
            p['direction'] != 'NEUTRAL',
        ),
    )


def detect_candlestick_patterns(df, lookback=10):
    """
    Detect key candlestick patterns on the given DataFrame (works on any TF).
    Returns one pattern per bar (deduped by strength).
    """
    if len(df) < lookback + 2:
        return []

    results = []
    n = len(df)
    start = max(0, n - lookback)

    for i in range(start, n):
        o = float(df['Open'].iloc[i])
        h = float(df['High'].iloc[i])
        l = float(df['Low'].iloc[i])
        c = float(df['Close'].iloc[i])

        body = _body(o, c)
        rng = _range(h, l)
        if rng == 0:
            continue
        body_ratio = body / rng
        upper = _upper_shadow(h, o, c)
        lower = _lower_shadow(l, o, c)

        is_bull = _is_bullish(o, c)
        is_bear = _is_bearish(o, c)
        trend = _short_term_trend(df, i)

        bar_hits = []

        def _add(name, direction, price, strength, desc):
            bar_hits.append({
                'name': name,
                'direction': direction,
                'bar_index': i,
                'price': round(price, 2),
                'strength': strength,
                'description': desc,
                'time': str(df.index[i]) if hasattr(df.index[i], 'strftime') else str(df.index[i]),
            })

        # ── SINGLE-CANDLE PATTERNS (any bar) ──
        if body_ratio < 0.1:
            _add('Doji', 'NEUTRAL', (h + l) / 2, 'MEDIUM',
                 f'十字星：猶豫信號，body_ratio={body_ratio:.1%}')
        elif body > 0 and lower >= body * 2 and upper < body * 0.5:
            if trend == 'DOWN':
                _add('Hammer', 'BULLISH', l, 'HIGH',
                     f'錘子線（下跌後）：看漲反轉，下影線={lower:.1f}')
            elif trend == 'UP':
                _add('Hanging Man', 'BEARISH', l, 'MEDIUM',
                     f'上吊線（上漲後）：看跌信號，下影線={lower:.1f}')
            elif is_bull:
                _add('Hammer', 'BULLISH', l, 'HIGH',
                     f'錘子線：看漲反轉，下影線={lower:.1f}')
            else:
                _add('Hanging Man', 'BEARISH', l, 'MEDIUM',
                     f'上吊線：需確認，下影線={lower:.1f}')
        elif body > 0 and upper >= body * 2 and lower < body * 0.5:
            if trend == 'UP':
                _add('Shooting Star', 'BEARISH', h, 'HIGH',
                     f'射擊之星（上漲後）：看跌反轉，上影線={upper:.1f}')
            elif trend == 'DOWN':
                _add('Inverted Hammer', 'BULLISH', h, 'MEDIUM',
                     f'倒錘（下跌後）：看漲需確認，上影線={upper:.1f}')
            elif i >= 1 and _is_bullish(float(df['Open'].iloc[i - 1]), float(df['Close'].iloc[i - 1])):
                _add('Shooting Star', 'BEARISH', h, 'HIGH',
                     f'射擊之星：看跌反轉，上影線={upper:.1f}')
            else:
                _add('Inverted Hammer', 'BULLISH', h, 'MEDIUM',
                     f'倒錘：需確認，上影線={upper:.1f}')
        elif body_ratio > 0.8:
            if is_bull:
                _add('Bullish Marubozu', 'BULLISH', c, 'HIGH',
                     f'陽線大燭：強烈買方力量，body_ratio={body_ratio:.1%}')
            else:
                _add('Bearish Marubozu', 'BEARISH', c, 'HIGH',
                     f'陰線大燭：強烈賣方力量，body_ratio={body_ratio:.1%}')

        # ── TWO-CANDLE PATTERNS (need i >= 1) ──
        if i >= 1:
            o1 = float(df['Open'].iloc[i - 1])
            h1 = float(df['High'].iloc[i - 1])
            l1 = float(df['Low'].iloc[i - 1])
            c1 = float(df['Close'].iloc[i - 1])
            body1 = _body(o1, c1)
            is_bull1 = _is_bullish(o1, c1)
            is_bear1 = _is_bearish(o1, c1)

            if is_bear1 and is_bull and o <= c1 and c >= o1 and body > body1:
                _add('Bullish Engulfing', 'BULLISH', l, 'HIGH', '看漲吞沒：陽線完全包住前陰線')
            if is_bull1 and is_bear and o >= c1 and c <= o1 and body > body1:
                _add('Bearish Engulfing', 'BEARISH', h, 'HIGH', '看跌吞沒：陰線完全包住前陽線')
            if is_bear1 and is_bull and body1 > 0:
                mid_prev = (o1 + c1) / 2
                if o < l1 and c > mid_prev and c < o1:
                    _add('Piercing Line', 'BULLISH', l, 'HIGH', '刺穿線：陽線刺入前陰線 >50%')
            if is_bull1 and is_bear and body1 > 0:
                mid_prev = (o1 + c1) / 2
                if o > h1 and c < mid_prev and c > o1:
                    _add('Dark Cloud Cover', 'BEARISH', h, 'HIGH', '烏雲蓋頂：陰線插入前陽線 >50%')
            if is_bear1 and is_bull and body1 > body * 1.5 and o > c1 and c < o1:
                _add('Bullish Harami', 'BULLISH', l, 'MEDIUM', '多頭母子：小陽線喺大陰線內部')
            if is_bull1 and is_bear and body1 > body * 1.5 and o < c1 and c > o1:
                _add('Bearish Harami', 'BEARISH', h, 'MEDIUM', '空頭母子：小陰線喺大陽線內部')

        # ── THREE-CANDLE PATTERNS (need i >= 2) ──
        if i >= 2:
            o1 = float(df['Open'].iloc[i - 1])
            c1 = float(df['Close'].iloc[i - 1])
            body1 = _body(o1, c1)
            o2 = float(df['Open'].iloc[i - 2])
            c2 = float(df['Close'].iloc[i - 2])
            body2 = _body(o2, c2)
            is_bull2 = _is_bullish(o2, c2)
            is_bear2 = _is_bearish(o2, c2)

            if is_bear2 and body2 > 0:
                mid_first = (o2 + c2) / 2
                if body1 < body2 * 0.4 and is_bull and c > mid_first:
                    _add('Morning Star', 'BULLISH', l, 'HIGH', '晨星：三根K線看漲反轉')
            if is_bull2 and body2 > 0:
                mid_first = (o2 + c2) / 2
                if body1 < body2 * 0.4 and is_bear and c < mid_first:
                    _add('Evening Star', 'BEARISH', h, 'HIGH', '暮星：三根K線看跌反轉')

        # Three soldiers/crows — only on completion bar (avoid duplicate firings)
        if i == n - 1 and i >= 2:
            o1 = float(df['Open'].iloc[i - 1])
            c1 = float(df['Close'].iloc[i - 1])
            o2 = float(df['Open'].iloc[i - 2])
            c2 = float(df['Close'].iloc[i - 2])
            if _is_bullish(o2, c2) and _is_bullish(o1, c1) and is_bull:
                if c > c1 > c2 and o >= o1:
                    _add('Three White Soldiers', 'BULLISH', c, 'HIGH', '三白兵：連續三根陽線')
            if _is_bearish(o2, c2) and _is_bearish(o1, c1) and is_bear:
                if c < c1 < c2 and o <= o1:
                    _add('Three Black Crows', 'BEARISH', c, 'HIGH', '三黑鴉：連續三根陰線')

        best = _pick_best_candle(bar_hits)
        if best:
            results.append(best)

    return results


def candlestick_confirmation(candle_patterns, direction, lookback_bars=5, last_bar_idx=None):
    """
    Check if recent candlestick patterns confirm a trade direction.
    Returns: (confirmed, confirming_patterns, score, opposing_patterns)

    Score: +2 HIGH match, +1 MEDIUM, opposing subtracts same.
    Confirmed when score >= 2 within lookback anchored to last_bar_idx.
    """
    if not candle_patterns:
        return False, [], 0, []

    if last_bar_idx is None:
        last_bar_idx = max(p['bar_index'] for p in candle_patterns)

    recent = [p for p in candle_patterns if p['bar_index'] >= last_bar_idx - lookback_bars + 1]

    score = 0
    confirming = []
    opposing = []

    for p in recent:
        if p['direction'] == 'NEUTRAL':
            continue
        strength_val = {'HIGH': 2, 'MEDIUM': 1, 'LOW': 0}.get(p['strength'], 0)
        if p['direction'] == direction:
            score += strength_val
            confirming.append(p)
        else:
            score -= strength_val
            opposing.append(p)

    confirmed = score >= 2
    return confirmed, confirming, score, opposing


def _inject_kline_scores(setups, candle_m30, candle_day, last_m30_idx, last_day_idx):
    """Attach K-line confirmation fields to each setup for JSON and report reuse."""
    for s in setups:
        direction = 'BEARISH' if 'SELL' in s.get('direction', '') else 'BULLISH'
        _, m30_pat, m30_score, m30_opp = candlestick_confirmation(
            candle_m30, direction, lookback_bars=5, last_bar_idx=last_m30_idx
        )
        _, day_pat, day_score, day_opp = candlestick_confirmation(
            candle_day, direction, lookback_bars=5, last_bar_idx=last_day_idx
        )
        total = m30_score + day_score
        s['kline_m30_score'] = m30_score
        s['kline_daily_score'] = day_score
        s['kline_total_score'] = total
        s['kline_confirmed'] = total >= 2 and m30_score >= 0 and day_score >= 0
        s['kline_m30_patterns'] = [cp['name'] for cp in m30_pat]
        s['kline_daily_patterns'] = [cp['name'] for cp in day_pat]
        s['kline_opposing'] = [cp['name'] for cp in (m30_opp + day_opp)]


def cron_push_eligible(setup):
    """Whether a setup qualifies for automated cron/Hermes push.

    Replaces the legacy prompt rule (priority≤2 ∧ kline ∧ quality≠POOR_RR) which
    admitted MILD counter-trend breakouts and excluded pullback/boundary entries.

    All conditions required:
    - kline_confirmed
    - quality in (OK, GOOD)
    - counter_trend_severity == ALIGNED
    - NOT in danger hour (07:00/18:00 broker hard-block as of 2026-07-30; advisory allowed)
    - Has TP targets (tp1 > 0) — no naked trades
      [Note: 138-sample shows no-TP 40.3% win vs TP-set 42.9% — TP-mandatory
       is a RISK MANAGEMENT guard, not a statistical edge signal.]
    - Has SL (stop_loss > 0) — no naked positions
      [Note: 138-sample shows no-SL 51.4% win vs SL-set 36.9% — SL-mandatory
       is a RISK MANAGEMENT guard, not a statistical edge signal.]
    - breakout: priority ≤ 2; pullback/boundary/fib/fib0786: priority ≤ 3
    - NOT blocked by mixed spot/futures basis (see _evaluate_spot_futures_basis)
    """
    if BASIS_CRON_BLOCKED:
        return False
    if not setup.get('kline_confirmed'):
        return False
    if setup.get('quality') not in ('OK', 'GOOD'):
        return False
    if setup.get('counter_trend_severity') != 'ALIGNED':
        return False
    # Danger hour block (07:00/18:00 — 138-sample); advisory hours allowed through
    if setup.get('time_quality') == 'danger':
        return False
    # Post-spike chase gate (2026-09-04, mentor 9/1-9/4): 急跌/急升後短窗口
    # 內追同一方向 = V 型反彈陷阱 (9/4 追沽 6 筆全滅 −$2,200+)
    if setup.get('post_spike_blocked'):
        return False
    # Must have TP targets — no naked entries
    tp1_str = str(setup.get('tp1', ''))
    if not tp1_str or tp1_str == '0' or '$0' in tp1_str:
        return False
    # Must have SL — risk guard (138-sample: no-SL 51.4% win, but tail risk)
    sl_str = str(setup.get('stop_loss', ''))
    if not sl_str or sl_str == '0' or '$0' in sl_str:
        return False
    priority = setup.get('priority', 99)
    entry_mode = setup.get('entry_mode', 'breakout')
    if entry_mode in ('pullback', 'boundary', 'fib', 'fib0786'):
        return priority <= 3
    return priority <= 2


_CONTRACT_GAP_SEEN = set()


def _report_contract_gap(setup):
    """「eligible 但缺 push_suppressed」= producer 契約破損，要出聲（唔可以靜默）。

    刻意保留 `push_eligible()` 嘅 fail-open 回傳值（要 mirror 歷史，見其
    docstring），但唔可以連警告都冇：新 producer 若唔記得跑
    `_inject_push_metadata`，一個 0-15% 勝率嘅限價訊號會被當成可推送
    → 直接去真錢用戶。守衛唔可以係 no-op（同類：重試語義／靜默失敗家族）。

    警告去 **stderr** —— 唔污染 stdout（cron 報告由 stdout／JSON 驅動），
    並按 (entry_mode, pattern) 去重，免得掃 79 日歷史報告時洗版。
    """
    key = (setup.get('entry_mode'), setup.get('pattern'))
    if key in _CONTRACT_GAP_SEEN:
        return
    _CONTRACT_GAP_SEEN.add(key)
    print(f"⚠️ producer contract: eligible setup 缺 push_suppressed "
          f"(entry_mode={setup.get('entry_mode')!r}, pattern={setup.get('pattern')!r}) "
          f"— fail-open 放行; 若見於**新**報告 = _inject_push_metadata 冇跑",
          file=sys.stderr)


def push_eligible(setup):
    """最終推送規則 — 「呢個 setup 會被推去 WhatsApp／Hermes 嗎？」嘅唯一真相。

    2026-09-24 抽出：原本 `main()` 嘅 `push_candidates` 係 inline 兩條件，
    `backtest.py` 只抄咗第一條（`cron_push_eligible`）而漏咗 `push_suppressed`
    → 回測會 trade 四個限價模式（boundary/pullback/fib/fib0786），而**現行策略**
    唔推呢批。實測同一 6 個月：97 單 vs 82 單（15 單 = 15% 係推唔到嘅），
    勝率 45.4% vs 53.7%。n=15 本身喺噪音範圍 —— 呢個修嘅價值係 **fidelity**
    （回測要 mirror live 實際會推嘅嘢），唔係「回測變好咗」，唔好當 alpha 證據。

    抽出單一真相函數嘅目的係令「兩份實作各自漂移」唔會再無聲發生（本次事故
    正是如此：一個條件被抄漏，冇任何東西會發現）。

    回測 mirror 嘅係**現行**推送政策（`_inject_push_metadata` 每個 bar 都寫
    bool），唔係 09-08 之前嗰段日曆。`run_backtest` 唔會行到缺 key 分支。

    ⚠️ 缺 `push_suppressed` key 時 fail-open（回 True）只服務**舊 JSON**：
    09-08 之前嘅報告有 `cron_push_eligible=True` 但冇呢個欄（當時限價係有推）。
    key **存在但唔係 bool**（`null` / `1` / `"True"`）係契約破損，**fail-closed**
    （只有明確 `False` 先當「冇壓制」），同時 `_report_contract_gap()` 出聲。
    缺 key 都出聲，但回傳值保持 fail-open，先唔會改寫嗰批舊報告。

    ⚠️ 同 `cron_push_eligible` 嘅分工（兩者係唔同問題，唔可以互換）:
      • `cron_push_eligible` = 紀律閘／「可執行嗎」→ paper_trade seeding 用佢。
        限價模式經 walk-forward 證實無 edge，唔推送但要照 seed（累積真 fill
        樣本做裁決）。
      • `push_eligible` = 推送閘 → 報告／cron／回測用佢。
    任何「決定會被推嗎」嘅消費者（含 backtest harness）一律 call 呢個，
    唔好自己讀 `cron_push_eligible`（2026-09-24 事故正是如此）。
    """
    cron_ok = setup.get('cron_push_eligible') is True
    has_key = 'push_suppressed' in setup
    val = setup.get('push_suppressed')
    if cron_ok and not isinstance(val, bool):
        _report_contract_gap(setup)
    if not has_key:
        return cron_ok
    return cron_ok and val is False


def _parse_entry_price_from_setup(setup, current_price=None):
    """Machine-readable entry price for paper_trade / backtest seeding."""
    if setup.get('entry_price') is not None:
        try:
            return float(setup['entry_price'])
        except (TypeError, ValueError):
            pass
    trigger = setup.get('entry_trigger', '')
    if '已突破' in trigger or '已跌穿' in trigger:
        return float(current_price) if current_price is not None else None
    if '$' in trigger:
        import re
        m = re.search(r'(\d+(?:\.\d+)?)', trigger.split('$')[-1])
        if m:
            return float(m.group(1))
    return None


def _limit_level_touched(side_is_sell, entry_price, current_price):
    """Whether current price has reached a limit/proximity entry level."""
    if entry_price is None or current_price is None:
        return False
    if side_is_sell:
        return float(current_price) >= float(entry_price)
    return float(current_price) <= float(entry_price)


def _setup_seedable(setup, current_price=None):
    """Whether paper_trade / backtest can open this setup now (filled / triggered).

    Limit modes (boundary/fib0786/pullback/fib) are seedable only after the
    entry level is touched — never solely because the mode exists.
    """
    status = setup.get('entry_status', '')
    if '🚫' in status or '待突破' in status:
        return False
    if setup.get('seedable') is not None:
        return bool(setup['seedable'])
    if setup.get('triggered') is True:
        return True
    mode = setup.get('entry_mode', 'breakout')
    trigger = setup.get('entry_trigger', '')
    if mode == 'breakout' and '已' in trigger:
        return True
    ep = _parse_entry_price_from_setup(setup, current_price)
    if ep is not None and current_price is not None and mode in ('boundary', 'fib0786', 'fib', 'pullback'):
        is_sell = 'SELL' in setup.get('direction', '')
        return _limit_level_touched(is_sell, ep, current_price)
    return False


def _spike_window(closes, atr):
    """Shared precondition check for the post-spike window.

    Returns `(window, None)` when the window is usable, otherwise
    `(None, reason)`. The REASON is the whole point: an unusable window and a
    quiet market both used to surface as `None`, and the gate treated both as
    "no spike" — i.e. it failed OPEN on the exact guard the 09-04 incident
    (six consecutive chasing losses) produced. Callers that must not guess use
    `_post_spike_indeterminate()` and block instead.

    Two precision rules (2026-10-11 review):
      * ATR must be a finite positive number. `nan <= 0` is False, so the old
        `atr <= 0` test let a NaN ATR through, and `abs(move) <= mult * nan` is
        then also False — a flat series became a fabricated "up" spike that
        blocked BUY and let SELL through. NaN / inf ATR is unusable.
      * Every COMPLETED close in the window must be finite, but the final value
        is the still-forming bar: live already expects it to be NaN sometimes
        (it falls back to the last valid close) and the math never reads it
        (`window[-2]` is the reference, `window[0]` the base). Requiring it
        finite turned one NaN forming bar into a blocked tick for every push
        and every seed. A NaN among the completed bars still means a gappy /
        corrupt series, so that stays indeterminate (fail closed).
    """
    if closes is None:
        return None, "no M30 close series"
    try:
        atr_f = float(atr)
    except (TypeError, ValueError):
        atr_f = float("nan")
    if atr is None or not np.isfinite(atr_f) or atr_f <= 0:
        return None, f"ATR unusable ({atr})"
    need = SPIKE_WINDOW_BARS + 2  # 4 收市 bar + last(forming) + 前 1 參考
    if len(closes) < need:
        return None, f"only {len(closes)} M30 bars (< {need})"
    # Index by position (never compact), so a bad value cannot shift the window.
    tail = []
    for c in list(closes[-need:]):
        try:
            tail.append(float(c))
        except (TypeError, ValueError):
            tail.append(float("nan"))
    if not all(np.isfinite(c) for c in tail[:-1]):   # tail[-1] = forming bar
        return None, "non-finite close inside the window"
    return tail, None


def _post_spike_indeterminate(closes, atr):
    """Why the post-spike window could NOT be evaluated (None when it could).

    Kept separate from `_post_spike_state` so the gate can fail CLOSED without
    changing that function's long-standing contract (`None` == "no spike"),
    which the existing unit tests assert.
    """
    return _spike_window(closes, atr)[1]


def _post_spike_state(closes, atr):
    """Post-sharp-move mean-reversion window (see SPIKE_* constants).

    2026-09-04 (mentor 9/1-9/4, 132 trades): 9/4 金價急插 4470→4375 後 2 小時
    內追沽 6 筆全滅 (−$2,200+)。急跌/急升後短窗口內追同一方向 = V 型反彈
    陷阱。本函數檢查「最近 SPIKE_WINDOW_BARS 條已收市 M30 bar」嘅累計變動
    (close-to-close, 唔含 forming bar), > SPIKE_ATR_MULT×ATR 即返回 spike
    方向。setup 方向同 spike 方向一致 → post_spike_blocked=True (唔推送)。

    closes: M30 close 序列 (numpy array 或 list, 最後一個可能係 forming bar)
    atr:    M30 ATR 值 (float)
    """
    window, reason = _spike_window(closes, atr)
    if reason is not None or window is None:
        return None
    # closes[-1] = forming bar (未收市, tvDatafeed 最後一行), closes[-2] = 最後已收市
    ref = window[-2]
    base = window[-2 - SPIKE_WINDOW_BARS]
    move = ref - base
    if abs(move) <= SPIKE_ATR_MULT * float(atr):
        return None
    return {"direction": "down" if move < 0 else "up", "move": round(move, 2)}


_UNSET = object()   # 「caller 冇接線呢個參數」哨兵，唔可以同「明確傳 None」混淆


def _inject_push_metadata(setups, daily_trend, h1_trend, current_price=None,
                          time_quality_override=None, points=None, atr=_UNSET,
                          closes=_UNSET, bar_time=None):
    """Attach counter-trend severity, recommended volume, time quality, and cron gate.

    time_quality_override: when set (e.g. walk-forward backtest), use this level
    instead of wall-clock _time_quality_score() so historical bars aren't gated
    by the hour the script happens to run.
    bar_time: the BAR being evaluated, for a replay. Without it every replayed
    bar is scored with the run hour (session_bonus + golden-hour volume became
    constants); a live run leaves it None and keeps the wall clock.

    2026-10-10 外審 follow-up: `atr` / `closes` 用 `_UNSET` 哨兵，令兩種情況
    分得開 ——
      • 冇接線（default `_UNSET`）→ 唔 block，只記 `post_spike_indeterminate`。
        漏一個參數唔應該變成靜默推送乾旱。
      • 明確傳入但唔可用（None／NaN／ATR<=0／bar 不足）→ BLOCK。
        呢個係資料問題，同「冇接線」係兩件事。
    之前 `atr=None` 同 `closes=None` 一律當「冇接線」（唔 block），而
    `atr=None` 又其實會被 `_spike_window` 當唔可用 —— 兩個方向都唔一致。
    """
    if time_quality_override is not None:
        tq_level = time_quality_override
    else:
        tq_level, _ = _time_quality_score()
    broker_hour = _broker_hour(bar_time)
    session_bonus = (
        tq_level != 'danger'
        and broker_hour in SESSION_BONUS_HOURS
    )
    unwired = []
    if atr is _UNSET:
        unwired.append("atr")
    if closes is _UNSET:
        unwired.append("closes")
    if unwired:
        spike = None
        spike_unknown = None
        spike_unwired = "caller 冇接線: " + ", ".join(unwired)
    else:
        spike = _post_spike_state(closes, atr)
        # 2026-10-10 review: an UNUSABLE window is not a quiet market. The gate
        # used to read both as "no spike" and push anyway — fail-OPEN on the
        # guard that exists because of the 09-04 chasing losses. Unknown blocks.
        spike_unknown = (_post_spike_indeterminate(closes, atr)
                         if spike is None else None)
        spike_unwired = None
    for s in setups:
        side = 'BEARISH' if 'SELL' in s.get('direction', '') else 'BULLISH'
        is_sell = side == 'BEARISH'
        severity = counter_trend_severity(side, daily_trend, h1_trend)
        vol, _ = _volume_risk_tier(severity, tq_level=tq_level)
        s['counter_trend_severity'] = severity
        s['recommended_volume'] = vol
        s['time_quality'] = tq_level
        # 2026-09-01 regime metadata: D1+H1 both oppose side. This is exactly
        # counter_trend_severity == 'SEVERE' — which cron_push_eligible already
        # blocks via the ALIGNED gate — so regime_blocked is informational only,
        # never an additional push gate (no dead-code duplicate guard).
        d_trend = (daily_trend or {}).get('trend', 'NEUTRAL')
        h1_t = (h1_trend or {}).get('trend', 'NEUTRAL')
        if side == 'BEARISH':
            both_opp = d_trend == 'BULLISH' and h1_t == 'BULLISH'
        else:
            both_opp = d_trend == 'BEARISH' and h1_t == 'BEARISH'
        s['regime_blocked'] = bool(both_opp)
        # 2026-09-01: mentor-sample session bonus — priority bump for aligned
        # setups during the mentor's strongest hours. Bonus applies only when
        # the setup would already be pushed (post cron_push_eligible below);
        # danger/advisory gating is never lifted by the bonus.
        s['session_bonus'] = bool(
            session_bonus and severity == 'ALIGNED'
        )
        # 2026-09-02 zone-rejection (mentor H1 charts: he sold $4420-4449
        # resistance 38 times — repeatedly-tested zones hold). Setups whose
        # entry sits on a swing-high/low cluster (>=2 touches within 1.0 ATR
        # either side, from find_swings_ordered) get a priority boost.
        # Breakout/已突破 setups: _parse_entry_price_from_setup falls back to
        # current_price, so their zone is scored at the live price (entry ==
        # 2026-09-08 review: breakout setups fall back to current_price, and
        # after a break the price sits at the *opposite* swing type (SELL
        # breakout → near swing lows), so counting highs there is meaningless.
        # Score only limit-style modes whose entry is a real level.
        zone_modes = ('boundary', 'pullback', 'fib', 'fib0786')
        if points is not None and atr and s.get('entry_mode', 'breakout') in zone_modes:
            ep = s.get('entry_price') or _parse_entry_price_from_setup(s, current_price)
            if ep is not None:
                touches, zlabel, _ztouch_idx = _zone_rejection_score(
                    side, float(ep), points, atr
                )
                s['zone_touches'] = touches
                s['zone_label'] = zlabel
            else:
                s['zone_touches'] = 0
                s['zone_label'] = ''
        else:
            s['zone_touches'] = 0
            s['zone_label'] = ''

        # 2026-09-04 post-spike chase gate: 急跌/急升後短窗口內追同一方向 →
        # 唔推送 (V 型反彈陷阱, mentor 9/1-9/4: 9/4 追沽 6 筆全滅 −$2,200+)
        if spike is not None:
            same_dir = (spike['direction'] == 'down' and is_sell) or \
                       (spike['direction'] == 'up' and not is_sell)
            s['post_spike_blocked'] = bool(same_dir)
            s['post_spike_note'] = (
                f"⚠️ 最近 {SPIKE_WINDOW_BARS} bar 急{'跌' if spike['direction']=='down' else '升'} "
                f"{abs(spike['move']):.0f} 點 — 追{'沽' if is_sell else '買'}風險窗口內, 唔推送"
                if same_dir else ""
            )
            s['post_spike_indeterminate'] = None
        elif spike_unknown is not None:
            # 2026-10-10 review: 舊碼喺呢個情況當「冇 spike」照推 = fail-OPEN。
            # 呢個閘係 09-04 追勢六連敗 (−$2,200+) 之後加嘅；睇唔到就當有風險。
            s['post_spike_blocked'] = True
            s['post_spike_note'] = (
                f"⚠️ 急升/急跌窗口無法判斷（{spike_unknown}）— 保守起見唔推送"
            )
            s['post_spike_indeterminate'] = spike_unknown
        else:
            s['post_spike_blocked'] = False
            s['post_spike_note'] = ""
            # Audible either way: a wiring gap ("no series provided") is recorded
            # next to a real "window unusable" reason, so a silent no-gate state
            # is greppable in the JSON instead of invisible.
            s['post_spike_indeterminate'] = spike_unwired

        if s.get('entry_price') is None:
            ep = _parse_entry_price_from_setup(s, current_price)
            if ep is not None:
                s['entry_price'] = round(ep, 2)

        mode = s.get('entry_mode', 'breakout')
        status = s.get('entry_status', '')
        # triggered = fill/touch only — never default from seedable
        if '🚫' in status or '待突破' in status:
            triggered = False
        elif mode == 'breakout':
            triggered = '已' in s.get('entry_trigger', '')
        elif s.get('entry_price') is not None and current_price is not None:
            triggered = _limit_level_touched(is_sell, s['entry_price'], current_price)
        else:
            triggered = bool(s.get('triggered', False))
        s['triggered'] = triggered
        s['seedable'] = triggered and '🚫' not in status and '待突破' not in status

        # Quality gate first; executable setups always eligible.
        # Limit modes with a machine entry_price may also be pushed so Hermes
        # can place a pending order — paper_trade still requires seedable.
        base = cron_push_eligible(s)
        # 2026-09-08 walk-forward review (claude-fable-5-1 + Cursor 3-model):
        # limit-style entries have NO demonstrated edge — 2y walk-forward with
        # limit-fill verification shows boundary 0-15% win (both segments
        # negative R), pullback/fib have ~0 fills.
        #
        # DESIGN (Cursor review round 2): cron_push_eligible keeps its meaning
        # "executable by paper_trade" — it stays the discipline gate, so limit
        # modes ARE still seeded and recorded (paper_trade accumulates real
        # fill samples for the 3-month verdict). The PUSH layer reads the
        # separate push_suppressed flag instead. Env LIMIT_MODE_PUSH=1
        # restores pushing without a git revert.
        suppress_push = mode in ('boundary', 'pullback', 'fib', 'fib0786')
        if os.environ.get('LIMIT_MODE_PUSH') == '1':
            suppress_push = False
        if suppress_push:
            s['push_suppressed'] = True
            s['limit_mode_blocked'] = True
            s['limit_mode_note'] = (
                '🔒 限價模式經 walk-forward 證實無 edge（boundary 真 fill 後 0-15% '
                '勝率, pullback/fib 近乎零成交）— paper 照記錄, 唔推送; '
                '三個月後再裁決 (LIMIT_MODE_PUSH=1 可恢復)'
            )
        else:
            s['push_suppressed'] = False
        # cron_push_eligible = discipline gate for paper_trade (executable).
        # seedable or limit-mode-with-levels passes; everything else False.
        # NOTE (Cursor round-2): keep this exactly on ('boundary','fib0786') as
        # in main — widening to all 4 limit modes would make untriggered
        # pullback/fib eligible once LIMIT_MODE_PUSH=1, which main never did.
        # push_suppressed still covers all 4 modes, so the suppression layer
        # is unaffected; this guard only limits what an explicit re-enable
        # (LIMIT_MODE_PUSH=1) can surface.
        if s['seedable']:
            s['cron_push_eligible'] = base
        elif mode in ('boundary', 'fib0786') and s.get('entry_price') is not None:
            s['cron_push_eligible'] = base
        else:
            s['cron_push_eligible'] = False

        # rank_priority: display/sort only — must not widen cron_push_eligible
        rp = s.get('priority', 99)
        if s.get('session_bonus') and rp > 1:
            rp -= 1
        if s.get('zone_touches', 0) >= 2 and rp > 1:
            rp -= 1
        s['rank_priority'] = rp

    # 2026-10-10 外審 follow-up: fail-closed 唔可以有靜默乾旱。真 spike 擋單係
    # 預期行為（有 log 亦入 gate_summary），但「數據用唔到而擋晒所有單」係事故
    # —— 只寫落 JSON 而冇人讀，就係上次靜默乾旱嘅翻版。所以喺 operator 真係睇
    # 得到嘅 stdout 出 warning（cron output 亦會capture）。
    _indet_blocked = [s for s in setups
                      if s.get('post_spike_blocked') and s.get('post_spike_indeterminate')]
    if _indet_blocked and spike is None:
        _log(f"⚠️  post-spike gate 擋咗 {len(_indet_blocked)} 個 setup，但唔係因為偵測到"
             f"真 spike — 而係 M30 窗／ATR 用唔到。原因：{_indet_blocked[0]['post_spike_indeterminate']}")

    setups.sort(key=lambda s: (s.get('rank_priority', s.get('priority', 99)), -s.get('rr_tp1', 0)))
    return spike


# ═══════════════════════════════════════════════════════════
# FIBONACCI (proper extension calculation)
# ═══════════════════════════════════════════════════════════

def _normalize_swing_pair(start, end):
    if start['idx'] > end['idx']:
        start, end = end, start
    return start, end


def _swing_matches_direction(start, end, direction):
    start, end = _normalize_swing_pair(start, end)
    if direction == 'BULLISH':
        return start['type'] == 'low' and end['type'] == 'high'
    if direction == 'BEARISH':
        return start['type'] == 'high' and end['type'] == 'low'
    return start['type'] != end['type']


def find_relevant_swing(points, current_idx, direction='auto', atr=None):
    """
    Find the most recent complete swing for Fibonacci.
    direction=BULLISH -> low to high; BEARISH -> high to low.
    Returns chronological (start, end) swing points.
    """
    if len(points) < 4:
        return None, None

    min_swing_range = max(atr * 3, 50) if atr else 50

    def scan(window, require_min_size):
        best = None
        best_key = (-1, 0)
        recent = points[-window:] if len(points) >= window else points
        for i in range(len(recent) - 1, 0, -1):
            end = recent[i]
            if end['idx'] > current_idx:
                continue
            for j in range(i - 1, -1, -1):
                start = recent[j]
                if start['type'] == end['type']:
                    continue
                if not _swing_matches_direction(start, end, direction):
                    continue
                start_n, end_n = _normalize_swing_pair(start, end)
                rng = abs(end_n['price'] - start_n['price'])
                if require_min_size and rng < min_swing_range:
                    continue
                key = (end_n['idx'], rng)
                if key > best_key:
                    best = (start_n, end_n)
                    best_key = key
                break
        return best

    best = scan(30, require_min_size=True)
    if best:
        return best
    best = scan(40, require_min_size=False)
    return best if best else (None, None)


def fibonacci_retracement(start_point, end_point):
    """Calculate Fibonacci retracement levels."""
    start_price = start_point['price']
    end_price = end_point['price']
    diff = end_price - start_price
    
    return {
        'swing_start': round(start_price, 2),
        'swing_end': round(end_price, 2),
        'range': round(abs(diff), 2),
        '0.0': round(start_price, 2),
        '0.236': round(end_price - diff * 0.236, 2),
        '0.382': round(end_price - diff * 0.382, 2),
        '0.5': round(end_price - diff * 0.5, 2),
        '0.618': round(end_price - diff * 0.618, 2),
        '0.786': round(end_price - diff * 0.786, 2),
        '1.0': round(end_price, 2),
    }

def fibonacci_extension(start_point, end_point, retrace_point):
    """ABC Fibonacci extension from retracement point C."""
    s = start_point['price']
    e = end_point['price']
    r = float(retrace_point) if isinstance(retrace_point, (int, float)) else float(retrace_point['price'])

    diff = e - s
    ext_618 = r + diff * 0.618
    ext_100 = r + diff * 1.0
    ext_1618 = r + diff * 1.618

    return {
        'swing_start': round(s, 2),
        'swing_end': round(e, 2),
        'retracement': round(r, 2),
        'ext_0.618': round(ext_618, 2),
        'ext_1.0': round(ext_100, 2),
        'ext_1.618': round(ext_1618, 2),
    }

# ═══════════════════════════════════════════════════════════
# TRADE SETUP GENERATOR (Fixed)
# ═══════════════════════════════════════════════════════════

def _pattern_confidence_order(pattern):
    return {'HIGH': 0, 'MEDIUM': 1, 'LOW': 2}.get(pattern.get('confidence', 'LOW'), 2)


def _pattern_setup_order(pattern):
    """Prefer confirmed breakouts, then higher confidence."""
    unconfirmed = 0 if _pattern_breakout_confirmed(pattern) else 1
    return (unconfirmed, _pattern_confidence_order(pattern))


def _triangle_breakout_tol(current_price):
    """Tolerance band used in detect_triangles _breakout_status (tol_dollars * 0.5)."""
    return max(current_price * 0.008, 15) * 0.5


def _setup_breakout_triggered(side, current_price, level, pattern):
    """Whether price has broken trigger level — matches _breakout_status for triangles."""
    tol = 0.0
    ptype = pattern.get('type', '')
    if 'Triangle' in ptype or '三角' in ptype:
        tol = _triangle_breakout_tol(current_price)
    if side == 'BEARISH':
        return current_price < level - tol
    return current_price > level + tol


def aligned_with_trends(side, daily_trend, h1_trend):
    h1 = (h1_trend or {}).get('trend', 'NEUTRAL')
    if side == 'BEARISH':
        return daily_trend['trend'] == 'BEARISH' and h1 in ('BEARISH', 'NEUTRAL')
    return daily_trend['trend'] == 'BULLISH' and h1 in ('BULLISH', 'NEUTRAL')


def counter_trend_severity(side, daily_trend, h1_trend):
    """Classify counter-trend severity: 'ALIGNED', 'MILD', 'SEVERE'.

    ALIGNED — matches aligned_with_trends(), or daily NEUTRAL with supportive H1.
    SEVERE — daily AND H1 both opposite.
    MILD — exactly one TF opposite (daily neutral alone is not MILD).
    """
    if aligned_with_trends(side, daily_trend, h1_trend):
        return 'ALIGNED'
    h1 = (h1_trend or {}).get('trend', 'NEUTRAL')
    daily = daily_trend['trend']
    if side == 'BEARISH':
        if daily == 'NEUTRAL' and h1 in ('BEARISH', 'NEUTRAL'):
            return 'ALIGNED'
        daily_opp = daily == 'BULLISH'
        h1_opp = h1 == 'BULLISH'
    else:
        if daily == 'NEUTRAL' and h1 in ('BULLISH', 'NEUTRAL'):
            return 'ALIGNED'
        daily_opp = daily == 'BEARISH'
        h1_opp = h1 == 'BEARISH'
    if daily_opp and h1_opp:
        return 'SEVERE'
    return 'MILD'


def _counter_trend_note(side, daily_trend, h1_trend, prefix=''):
    """Generate graded counter-trend warning based on severity (uses _volume_risk_tier)."""
    severity = counter_trend_severity(side, daily_trend, h1_trend)
    if severity == 'ALIGNED':
        return ''
    vol, _ = _volume_risk_tier(severity)
    label = f'{prefix} ' if prefix else ''
    if severity == 'SEVERE':
        return f'🚫 逆勢{label}— 日線+H1 均相反，強烈不建議！最多 1/4 倉 ({vol})'
    return f'⚠️ 逆勢{label}— 半倉 ({vol})'


# ═══════════════════════════════════════════════════════════
# Trading Performance Insights (Jul 2026 mentor samples)
# Recalibrated 2026-07-30: 138 trades (68 Jul 14-17 + 70 Jul 29-30 in scripts/).
# 40.6% win (56W/82L), net -$10.86. Recompute via: python scripts/compute_mentor_stats.py
# Same-direction open within 3min: 13 pairs, 25 unique trades, net -$85 (138-sample).
# ═══════════════════════════════════════════════════════════

# Broker timezone offset from UTC (hours). Override via BROKER_UTC_OFFSET_HOURS env.
BROKER_UTC_OFFSET_HOURS = int(os.environ.get('BROKER_UTC_OFFSET_HOURS', '-3'))
HKT_LOCAL = timezone(timedelta(hours=8))  # trade-day timezone (cron window)

# 2026-09-04 post-spike chase gate (mentor 9/1-9/4, 132 trades):
#   - 9/4: 金價 13:00-13:37 急插 4470→4375 (≈9.5 ATR) 後 2 小時內追沽 6 筆全滅
#     (−$2,200+), 全部被 V 型反彈夾爆 (13:32 SELL@4395→−$365, 13:37→−$461,
#     14:18→−$236, 14:24→−$466 ...)
#   - 9/1 崩盤日嘅贏家 sell 全部係 spike 前入 (07:47 SELL@4429 +$869), 唔係
#     spike 後追 — 所以 block 追 direction 唔會誤殺 trend-follow 入場
#   - Gate 只 block「追 spike 方向」嘅 setup 推送; 反方向 (搏反彈) 唔 block,
#     因為 counter-trend severity gate 已處理佢
SPIKE_WINDOW_BARS = 4          # 最近 4 條已收市 M30 bar 嘅累計變動窗口
# 2026-09-08 review: 2.0×ATR over 4 bars fires on ordinary trend continuation
# (0.5 ATR/bar). The motivating 9/4 move was ≈9.5 ATR. 3.0 keeps the gate for
# genuine spikes while letting normal trend-follow pushes through. Hit rate is
# exported in JSON (`post_spike`) so the threshold can be tuned on data.
SPIKE_ATR_MULT = float(os.environ.get('SPIKE_ATR_MULT', '3.0'))
MAX_PATTERNS_PER_DIRECTION = 2  # top-N patterns per side before setup generation

# Broker-local hours below (UTC + offset).
GOLDEN_HOURS = {17}          # 17:00 (64.3% win, +$339 — 138-sample 2026-07-30)
ADVISORY_HOURS_0408 = {4, 5, 6, 8}
# 17:00 promoted to GOLDEN (64.3% win, +$339 net, 14 trades — 138-sample)
ADVISORY_HOUR_1700 = set()  # vacated; 17:00 now golden
# 2026-09-01 mentor 129-trade sample (8/24-9/1): strong hours in BROKER time.
# NOTE (2026-09-02 review): mentor chart times are MT5 SERVER time = GMT+3 =
# BROKER_UTC_OFFSET_HOURS; so mentor "07:00/18:00 strong" maps directly onto
# broker hours {7,18} — which CONFLICT with the 138-sample DANGER_HOURS {7,18}
# (25%/21.4% win). The 9/1 crash-day trades dominate the mentor hour PnL, so
# those hours are NOT reliable as bonus hours. Only broker 03:00/06:00 are
# both mentor-positive and not contradicted — and 06:00 is in ADVISORY_HOURS.
# Final: bonus ONLY at broker 03:00 (+$1,354, 14 trades) when ALIGNED; the
# 07:00/18:00 mentor "edge" is rejected as outlier-driven, not added.
SESSION_BONUS_HOURS = {3}  # broker 03:00 only (mentor +$1,354, n=14; no conflict)
DANGER_HOURS = {7, 18}       # 07:00 (25% win, -$212 — 0.12 lot massacre); 18:00 (21.4%, -$98)
MAX_DAILY_TRADES = 8         # Overtrading threshold (123 trades/week = ~17/day avg)
RAPID_FIRE_WINDOW_MIN = 5    # 2+ opens within 5min — see scripts/compute_mentor_stats.py
RAPID_FIRE_MAX_TRADES = 2    # Max trades allowed within RAPID_FIRE_WINDOW_MIN


_BAD_BAR_TIME_SEEN = set()   # dedupe：同一個壞 timestamp 唔洗版


def _note_bad_bar_time(when, exc):
    """Warn once per distinct unparseable bar stamp (see `_broker_hour`)."""
    key = repr(when)
    if key in _BAD_BAR_TIME_SEEN:
        return
    _BAD_BAR_TIME_SEEN.add(key)
    print(f"⚠️ _broker_hour: 解析唔到 bar 時間 {key} ({type(exc).__name__}: {exc}) "
          f"— 呢個 bar 退回 wall clock，session_bonus／volume tier 可能失真",
          file=sys.stderr)


def _broker_hour(when=None):
    """Hour in broker-local time (UTC + BROKER_UTC_OFFSET_HOURS).

    `when` (2026-10-10 review): a walk-forward replay must score the BAR it is
    replaying, not the moment the script happens to run. Reading the wall clock
    made `session_bonus` a constant across the whole replay (whatever hour the
    operator started it), so the session term carried no information at all.
    Naive stamps are read as UTC — the engine's convention for broker
    timestamps — and aware ones are converted, so a caller cannot silently get
    a third interpretation.

    2026-10-10 外審 follow-up: an unparseable `when` used to fall back to the
    wall clock with NO trace — i.e. exactly the bug this function exists to
    remove, reinstated silently for that bar (`pd.NaT.hour` raises). The
    fallback is kept (a replay shouldn't die on one bad row) but it now prints
    a deduped warning, so "which bars fell back" is answerable from the log
    instead of invisible.
    """
    if when is None:
        utc = datetime.now(timezone.utc)
    else:
        try:
            ts = pd.Timestamp(when)
            # `pd.NaT` is the trap: Timestamp(NaT) does NOT raise, tz_localize
            # and tz_convert pass it through, and `NaT.hour` returns **nan** —
            # so a missing bar stamp used to yield a NaN broker hour with no
            # exception at all (session_bonus silently False). Catch it here.
            if pd.isna(ts):
                raise ValueError("NaT / NaN bar timestamp")
            if ts.tzinfo is None:
                ts = ts.tz_localize('UTC')
            utc = ts.tz_convert('UTC').to_pydatetime()
        except Exception as e:
            _note_bad_bar_time(when, e)
            utc = datetime.now(timezone.utc)
    return (utc.hour + BROKER_UTC_OFFSET_HOURS) % 24


def _time_quality_score():
    """Rate current broker time for trading quality.

    Updated 2026-07-31 (138-trade combined: 68 + 5 Jul-29 + 65 Jul-29/30):
    - Golden hour 17:00: 64.3% win, +$339 (14 trades) ← NEW GOLDEN
    - 09:00 collapsed: 66.7% → 37.5% (5 scalp BUY 0.02 all lost)
    - DANGER 07:00: 25% win, -$212 (0.12 lot cluster wiped)
    - DANGER 18:00: 21.4% win, -$98
    - Advisory 04-06/08: still advisory

    Returns: ('golden'|'danger'|'advisory'|'normal', advisory_text)
    """
    return _time_quality_for_hour(_broker_hour())


def _time_quality_for_hour(broker_hour):
    """Time-quality rating for an explicit broker-local hour (backtest-safe)."""
    if broker_hour in GOLDEN_HOURS:
        return ('golden', '🌅 黃金時段 (17:00 broker, 64.3% 勝, +$339 — 138-sample) — 適合入場')
    if broker_hour in DANGER_HOURS:
        return ('danger', f'🚫 {broker_hour:02d}:00 broker — 138-sample 高虧損時段, 避免入場')
    if broker_hour in ADVISORY_HOURS_0408:
        return ('advisory', '⚠️ 04-06/08 broker (138-sample advisory) — 謹慎入場')
    return ('normal', '')


def _scalp_risk_warning():
    """Warn against ultra-short holding periods.

    Updated 2026-07-31 (138-trade combined; see scripts/compute_mentor_stats.py):
    - <5min hold: 26.3% win, -$423 (38 trades)
    - 5-15min hold: 25.8% win, -$532 (31 trades) ← WORST (lowest win rate + largest loss)
    - 15-60min hold: 38.7% win, -$130 (31 trades)
    - 1-4h hold: 70.0% win, +$466 (20 trades) ← GOOD
    - >4h hold: 66.7% win, +$608 (18 trades) ← BEST
    """
    return (
        "⚠️ **反剝頭皮提醒 (基於 138 筆合併數據 2026-07-31; scripts/compute_mentor_stats.py):**\n"
        "- <5min 持倉: 勝率 26.3%, 虧損 -$423\n"
        "- 5-15min 持倉: 勝率 25.8%, 虧損 -$532 ← **最差**\n"
        "- 15-60min 持倉: 勝率 38.7%, 虧損 -$130\n"
        "- 1-4h 持倉: 勝率 70.0%, **盈利 +$466**\n"
        "- >4h 持倉: 勝率 66.7%, **盈利 +$608** (最佳)\n"
        "- 建議: 前輩贏錢靠放飛, 持倉 ≥1h 才讓形態充分發展"
    )


def _volume_risk_tier(severity='ALIGNED', vol=0.02, tq_level=None):
    """Recommend position size with volume-aware risk tiers.

    tq_level (2026-10-10 review): pass the SAME session level the caller already
    resolved. Left None it re-reads the wall clock, so a replay scored the
    golden-hour 0.03 tier from whatever hour the script ran at — the exact
    divergence `time_quality_override` exists to remove.

    Updated 2026-07-31 (138-trade combined; see scripts/compute_mentor_stats.py):
    - 0.01: 50.0% win, +$59 (22 trades)
    - 0.02: 52.9% win, +$172 (34 trades) — best tier by net PnL
    - 0.03-0.04: 50.0% win, +$131 (20 trades)
    - 0.05: 20.0% win, -$290 (10 trades) — avoid
    - 0.06: 31.2% win, -$92 (16 trades)
    - 0.07-0.15: 28.0% win, +$156 (25 trades) — includes +$473 outlier at 0.08
    - 0.16+: 27.3% win, -$147 (11 trades) — avoid (0.31 lot -$124 Jul-29)

    Combined with counter-trend severity:
    ALIGNED: 0.02 base, 0.03 if golden hour (n=14 at 17:00)
    MILD: 0.01 (half)
    SEVERE: 0.005 (quarter) + 🚫

    recommended_volume is advisory metadata for downstream executor — not enforced here.
    Do not raise golden-hour cap above 0.03 without golden-hour evidence at that size.
    0.16+ and 0.05 lots confirmed negative in 138-sample.
    """
    if severity == 'SEVERE':
        return 0.005, '🚫 強烈不建議！最多 0.005 倉 (風險管理: 逆勢大倉極端波動)'
    elif severity == 'MILD':
        return 0.01, '⚠️ 逆勢半倉 0.01 (歷史: 逆勢虧損率 65%)'
    else:
        tq = tq_level if tq_level is not None else _time_quality_score()[0]
        if tq == 'golden':
            return 0.03, '🌅 順勢 + 黃金時段, 0.03 倉 (138-sample 17:00 n=14, 64.3% 勝; 0.16+ 避免)'
        return 0.02, '順勢 0.02 倉'


def _time_quality_label(level):
    """Human-readable session label for report tables."""
    if level == 'golden':
        return '🌅 黃金時段'
    if level == 'danger':
        return '🚫 危險時段'
    if level == 'advisory':
        return '⚠️ 謹慎時段'
    return '➖ 一般時段'


def setup_priority(side, already_broken, daily_trend, h1_trend, quality):
    severity = counter_trend_severity(side, daily_trend, h1_trend)
    aligned = severity == 'ALIGNED'
    if severity == 'SEVERE':
        return 6  # Both daily + H1 against → lowest priority, strong discouragement
    if quality == 'UNCONFIRMED' or quality == 'POOR_RR':
        return 5
    if already_broken and aligned and quality == 'GOOD':
        return 1
    if already_broken and aligned and quality == 'OK':
        return 2
    if aligned:
        return 3
    if already_broken and quality in ('GOOD', 'OK'):
        return 4  # MILD counter-trend broken — below aligned waiting (3)
    return 4


def pattern_stop_swing(pattern, side, nearest_high, nearest_low):
    # BUG 6 fix: channels use regression-projected support/resistance which aren't real
    # swing extremes — fall through to nearest_high/nearest_low for channels
    is_channel = 'Channel' in pattern.get('type', '')
    
    if side == 'SELL':
        if not is_channel:
            for key in ('highest_high', 'flag_high', 'top_price', 'resistance'):
                if key in pattern:
                    return pattern[key]
        return nearest_high['price']
    else:
        if not is_channel:
            for key in ('lowest_low', 'flag_low', 'bottom_price', 'support'):
                if key in pattern:
                    return pattern[key]
        return nearest_low['price']


def pattern_structure_stop(pattern, side, atr):
    """Return (structure_stop, has_structure) for flags and wedges.
    
    Bear flag SELL:   flag_high + atr * 0.5
    Bull flag BUY:    flag_low  - atr * 0.5
    Rising wedge SELL:  resistance + atr * 0.5
    Falling wedge BUY:  support    - atr * 0.5
    Other patterns: has_structure=False
    """
    ptype = pattern.get('type', '')
    is_flag = 'Flag' in ptype
    is_wedge = 'Wedge' in ptype or '\u6954\u5f62' in ptype

    if not is_flag and not is_wedge:
        return None, False

    if side == 'SELL':
        if is_flag and 'flag_high' in pattern:
            return pattern['flag_high'] + atr * 0.5, True
        if is_wedge and 'resistance' in pattern:
            return pattern['resistance'] + atr * 0.5, True
    else:
        if is_flag and 'flag_low' in pattern:
            return pattern['flag_low'] - atr * 0.5, True
        if is_wedge and 'support' in pattern:
            return pattern['support'] - atr * 0.5, True

    return None, False


def pattern_add_level(pattern, side, points, trigger_level):
    highs = [p for p in points if p['type'] == 'high']
    lows = [p for p in points if p['type'] == 'low']
    if side == 'SELL':
        candidates = sorted([l['price'] for l in lows if l['price'] < trigger_level], reverse=True)
        if candidates:
            return candidates[0]
        return pattern.get('support', trigger_level)
    candidates = sorted([h['price'] for h in highs if h['price'] > trigger_level])
    if candidates:
        return candidates[0]
    return pattern.get('resistance', trigger_level)


def _build_fib_fallback_setup(side, fib, entry_level, stop_level, risk, tp1, tp2, tp3_trail,
                              daily_trend, h1_trend, atr):
    """Build 0.618 Fib fallback setup through shared quality/priority pipeline."""
    rr_tp1 = abs(entry_level - tp1) / risk if side == 'BEARISH' else abs(tp1 - entry_level) / risk
    rr_tp2 = abs(entry_level - tp2) / risk if side == 'BEARISH' else abs(tp2 - entry_level) / risk
    quality = _quality_from_rr(rr_tp1, rr_tp2)
    severity = counter_trend_severity(side, daily_trend, h1_trend)
    aligned = severity == 'ALIGNED'

    if side == 'BEARISH':
        swing_label = f"前頂 ${fib['swing_start']:.0f}"
        _ef = exit_fields(tp2, '2:1 RR', tp3_trail)
        return {
            'direction': '🔴 SELL',
            'priority': setup_priority(side, False, daily_trend, h1_trend, quality),
            'pattern': f"0.618 Fib 回調 (${fib['swing_start']:.0f}→${fib['swing_end']:.0f})",
            'confidence': 'MEDIUM',
            'quality': quality,
            'entry_mode': 'fib',
            'entry_status': _entry_status_bearish(False, aligned, quality, 'fib', severity),
            'entry_zone': f"${entry_level:.0f} - ${entry_level + atr:.0f}",
            'entry_trigger': f"跌破 0.618 Fib (${entry_level:.0f})",
            'add_position': '-',
            'stop_loss': f"${stop_level:.0f}",
            'stop_rationale': f"{swing_label} + 1 ATR",
            'tp1': f"${tp1:.0f} (0.618 RR, 止賺 1/3)",
            'tp2': _ef[0], 'tp2_active': not MOMENTUM_HOLD_EXIT,
            'tp3': _ef[1],
            'exit_plan': _ef[2],
            'risk_amount': round(risk, 1),
            'rr_tp1': round(rr_tp1, 1),
            'rr_tp2': round(rr_tp2, 1), 'rr_tp2_active': not MOMENTUM_HOLD_EXIT,
            'daily_alignment': daily_alignment_str(side, daily_trend, h1_trend),
            'note': '' if aligned else _counter_trend_note(side, daily_trend, h1_trend, prefix='Fib'),
        }

    swing_label = f"前底 ${fib['swing_start']:.0f}"
    _ef = exit_fields(tp2, '2:1 RR', tp3_trail)
    return {
        'direction': '🟢 BUY',
        'priority': setup_priority(side, False, daily_trend, h1_trend, quality),
        'pattern': f"0.618 Fib 回調 (${fib['swing_start']:.0f}→${fib['swing_end']:.0f})",
        'confidence': 'MEDIUM',
        'quality': quality,
        'entry_mode': 'fib',
        'entry_status': _entry_status_bullish(False, aligned, quality, 'fib', severity),
        'entry_zone': f"${entry_level - atr:.0f} - ${entry_level:.0f}",
        'entry_trigger': f"突破 0.618 Fib (${entry_level:.0f})",
        'add_position': '-',
        'stop_loss': f"${stop_level:.0f}",
        'stop_rationale': f"{swing_label} - 1 ATR",
        'tp1': f"${tp1:.0f} (0.618 RR, 止賺 1/3)",
        'tp2': _ef[0], 'tp2_active': not MOMENTUM_HOLD_EXIT,
        'tp3': _ef[1],
        'exit_plan': _ef[2],
        'risk_amount': round(risk, 1),
        'rr_tp1': round(rr_tp1, 1),
        'rr_tp2': round(rr_tp2, 1), 'rr_tp2_active': not MOMENTUM_HOLD_EXIT,
        'daily_alignment': daily_alignment_str(side, daily_trend, h1_trend),
        'note': '' if aligned else _counter_trend_note(side, daily_trend, h1_trend, prefix='Fib'),
    }


def _build_fib_0786_setup(side, fib, entry_level, stop_level, risk, tp1, tp2, tp3_trail,
                          daily_trend, h1_trend, atr, current_price=None):
    """Build 0.786 deep retracement setup — derived from HK stock Fib 0.786 playbook.

    Strategy: When price retraces deeply to 0.786 Fib level (deep wash-out),
    enter with:
      SL  = swing extreme (回調浪最低/高點) + buffer
      TP1 = 0.618 Fib level (跌浪中的 0.618 位置)
      TP2 = swing high/low (升/跌浪的最高/低點)
    """
    rr_tp1 = abs(tp1 - entry_level) / risk if risk > 0 else 0
    rr_tp2 = abs(tp2 - entry_level) / risk if risk > 0 else 0
    quality = _quality_from_rr(rr_tp1, rr_tp2)
    severity = counter_trend_severity(side, daily_trend, h1_trend)
    aligned = severity == 'ALIGNED'

    # ── 2026-08-08 fix: fib0786 fill requires actual touch at/through level ──
    # Symmetric proximity (abs <= ATR) is for status messaging only.
    # seedable/triggered require price to have reached the limit:
    #   SELL @ 0.786 → current >= entry; BUY @ 0.786 → current <= entry.
    near = (
        current_price is not None
        and abs(current_price - entry_level) <= atr
    )
    touched = (
        current_price is not None
        and (
            current_price >= entry_level if side == 'BEARISH'
            else current_price <= entry_level
        )
    )
    seedable = severity != 'SEVERE' and touched
    triggered = seedable

    if side == 'BEARISH':
        swing_label = f"前頂 ${fib['swing_start']:.0f}"
        if seedable:
            entry_status = '📍 0.786 已觸及 (可入場)'
        elif near and severity != 'SEVERE':
            entry_status = '⏳ 接近 0.786 Fib (待觸及)'
        else:
            entry_status = _entry_status_bearish(False, aligned, quality, 'fib0786', severity)
        _ef = exit_fields(tp2, '跌浪最低點', tp3_trail)
        return {
            'direction': '🔴 SELL',
            'priority': setup_priority(side, False, daily_trend, h1_trend, quality),
            'pattern': f"0.786 深度回調 (${fib['swing_start']:.0f}→${fib['swing_end']:.0f})",
            'confidence': 'MEDIUM',
            'quality': quality,
            'entry_mode': 'fib0786',
            'entry_status': entry_status,
            'entry_zone': f"${entry_level:.0f} - ${entry_level + atr:.0f}",
            'entry_trigger': f"觸及 0.786 Fib (${entry_level:.0f}) 並收市確認回落",
            'entry_price': round(entry_level, 2),
            'seedable': seedable,
            'triggered': triggered,
            'add_position': '-',
            'stop_loss': f"${stop_level:.0f}",
            'stop_rationale': f"前頂 ${fib['swing_start']:.0f} (回調浪最高點) + 1 ATR",
            'tp1': f"${tp1:.0f} (0.618 Fib 位, 止賺 1/3)",
            'tp2': _ef[0], 'tp2_active': not MOMENTUM_HOLD_EXIT,
            'tp3': _ef[1],
            'exit_plan': _ef[2],
            'risk_amount': round(risk, 1),
            'rr_tp1': round(rr_tp1, 1),
            'rr_tp2': round(rr_tp2, 1), 'rr_tp2_active': not MOMENTUM_HOLD_EXIT,
            'daily_alignment': daily_alignment_str(side, daily_trend, h1_trend),
            'note': '0.786 深度回調 — 實驗性（港股 playbook；未經 XAUUSD 138-sample 驗證）' if aligned else _counter_trend_note(side, daily_trend, h1_trend, prefix='0.786 Fib'),
        }

    swing_label = f"前底 ${fib['swing_start']:.0f}"
    if seedable:
        entry_status = '📍 0.786 已觸及 (可入場)'
    elif near and severity != 'SEVERE':
        entry_status = '⏳ 接近 0.786 Fib (待觸及)'
    else:
        entry_status = _entry_status_bullish(False, aligned, quality, 'fib0786', severity)
    _ef = exit_fields(tp2, '升浪最高點', tp3_trail)
    return {
        'direction': '🟢 BUY',
        'priority': setup_priority(side, False, daily_trend, h1_trend, quality),
        'pattern': f"0.786 深度回調 (${fib['swing_start']:.0f}→${fib['swing_end']:.0f})",
        'confidence': 'MEDIUM',
        'quality': quality,
        'entry_mode': 'fib0786',
        'entry_status': entry_status,
        'entry_zone': f"${entry_level - atr:.0f} - ${entry_level:.0f}",
        'entry_trigger': f"觸及 0.786 Fib (${entry_level:.0f}) 並收市確認企穩",
        'entry_price': round(entry_level, 2),
        'seedable': seedable,
        'triggered': triggered,
        'add_position': '-',
        'stop_loss': f"${stop_level:.0f}",
        'stop_rationale': f"前底 ${fib['swing_start']:.0f} (回調浪最低點) - 1 ATR",
        'tp1': f"${tp1:.0f} (0.618 Fib 位, 止賺 1/3)",
        'tp2': _ef[0], 'tp2_active': not MOMENTUM_HOLD_EXIT,
        'tp3': _ef[1],
        'exit_plan': _ef[2],
        'risk_amount': round(risk, 1),
        'rr_tp1': round(rr_tp1, 1),
        'rr_tp2': round(rr_tp2, 1), 'rr_tp2_active': not MOMENTUM_HOLD_EXIT,
        'daily_alignment': daily_alignment_str(side, daily_trend, h1_trend),
        'note': '0.786 深度回調 — 實驗性（港股 playbook；未經 XAUUSD 138-sample 驗證）' if aligned else _counter_trend_note(side, daily_trend, h1_trend, prefix='0.786 Fib'),
    }



def _entry_status_bearish(already_broken, aligned, quality, entry_mode='breakout', severity='ALIGNED'):
    is_aligned = severity == 'ALIGNED'
    if entry_mode == 'fib':
        if severity == 'SEVERE':
            return '🚫 Fib 回調 (日線+H1逆勢!)'
        return '⏳ 等待跌破 0.618 Fib'
    if entry_mode == 'fib0786':
        if severity == 'SEVERE':
            return '🚫 0.786 深度回調 (日線+H1逆勢!)'
        return '⏳ 等待反彈至 0.786 Fib'
    if entry_mode == 'pullback':
        if severity == 'SEVERE':
            return '🚫 反彈入場 (日線+H1逆勢!)'
        return '🎯 反彈入場 (待突破)'
    if entry_mode == 'boundary':
        if severity == 'SEVERE':
            return '🚫 邊界沽出 (日線+H1逆勢!)'
        return '📍 邊界沽出 (限價入場)'
    if severity == 'SEVERE':
        return '🚫 已觸發 (日線+H1逆勢!)' if already_broken else '🚫 等待跌穿 (日線+H1逆勢!)'
    if already_broken and quality == 'UNCONFIRMED':
        return '⚠️ 已觸發 (未確認突破)'
    if already_broken and is_aligned and quality == 'GOOD':
        return '🌟 已觸發 (順勢, R:R佳)'
    if already_broken and is_aligned and quality == 'OK':
        return '✅ 已觸發 (順勢)'
    if already_broken and quality == 'POOR_RR':
        return '⚠️ 已觸發 (R:R低)'
    if already_broken:
        return '⚠️ 已觸發 (逆勢!)'
    if not is_aligned:
        return '⏳ 等待跌穿 (逆勢⚠️)'
    return '⏳ 等待跌穿'


def _entry_status_bullish(already_broken, aligned, quality, entry_mode='breakout', severity='ALIGNED'):
    is_aligned = severity == 'ALIGNED'
    if entry_mode == 'fib':
        if severity == 'SEVERE':
            return '🚫 Fib 回調 (日線+H1逆勢!)'
        return '⏳ 等待突破 0.618 Fib'
    if entry_mode == 'fib0786':
        if severity == 'SEVERE':
            return '🚫 0.786 深度回調 (日線+H1逆勢!)'
        return '⏳ 等待回調至 0.786 Fib'
    if entry_mode == 'pullback':
        if severity == 'SEVERE':
            return '🚫 回撤入場 (日線+H1逆勢!)'
        return '🎯 回撤入場 (待突破)'
    if entry_mode == 'boundary':
        if severity == 'SEVERE':
            return '🚫 邊界買入 (日線+H1逆勢!)'
        return '📍 邊界買入 (限價入場)'
    if severity == 'SEVERE':
        return '🚫 已突破 (日線+H1逆勢!)' if already_broken else '🚫 等待突破 (日線+H1逆勢!)'
    if not already_broken:
        return '⏳ 等待突破 (逆勢⚠️)' if not is_aligned else '⏳ 等待突破'
    if quality == 'UNCONFIRMED':
        return '⚠️ 已突破 (未確認)'
    if quality == 'GOOD' and is_aligned:
        return '🌟 已突破 (順勢, R:R佳)'
    if quality == 'OK' and is_aligned:
        return '✅ 已突破 (順勢)'
    if quality == 'POOR_RR':
        return '⚠️ 已突破 (R:R低)'
    return '⚠️ 已突破 (逆勢!)'


def daily_alignment_str(expected_trend, daily_trend, h1_trend=None):
    """Format daily/H1 alignment — H1 NEUTRAL matches aligned_with_trends()."""
    chk = '\u2705'
    wrn = '\u26a0\ufe0f'
    d_ok = daily_trend['trend'] == expected_trend
    h1 = (h1_trend or {}).get('trend', 'NEUTRAL')
    if expected_trend == 'BEARISH':
        h_ok = h1 in ('BEARISH', 'NEUTRAL')
    else:
        h_ok = h1 in ('BULLISH', 'NEUTRAL')
    return f"\u65e5\u7dda{chk if d_ok else wrn} H1{chk if h_ok else wrn}"


def _quality_from_rr(rr_tp1, rr_tp2=None):
    """R:R quality tiers — TP1 R:R only (TP2 is ~2:1 by design and must not inflate quality).

    ≥2.0 → GOOD, ≥1.0 → OK, <1.0 → POOR_RR (threshold 1.0 since Jul 2026)."""
    rr = rr_tp1
    if rr < 1.0:
        return 'POOR_RR'
    if rr >= 2.0:
        return 'GOOD'
    return 'OK'


def _is_boundary_reversal(pattern):
    """Double top/bottom: HIGH confidence. Wedges: MEDIUM+ (detector caps wedges at MEDIUM)."""
    ptype = pattern.get('type', '')
    confidence = pattern.get('confidence', '').upper()
    is_double = 'Double Top' in ptype or 'Double Bottom' in ptype or '雙頂' in ptype or '雙底' in ptype
    is_wedge = 'Wedge' in ptype or '楔形' in ptype
    if is_double:
        return confidence == 'HIGH'
    if is_wedge:
        return confidence in ('HIGH', 'MEDIUM')
    return False


def _max_boundary_risk(atr, current_price):
    """Cap boundary-entry risk in ATR terms (gold M30 scale)."""
    return max(atr * 6, current_price * 0.02)


def _boundary_entry_sl(pattern, direction, atr, current_price):
    """SL for boundary entry: just outside the pattern boundary + 0.5 ATR.
    Not relative to current price — this is a limit order; SL is based on where
    you'll be filled at the boundary, not where the market is now."""
    if direction == 'SELL':
        top = pattern.get('top_price') or pattern.get('resistance')
        if top:
            return top + atr * 0.5
        return current_price + atr * 2
    else:
        bottom = pattern.get('bottom_price') or pattern.get('support')
        if bottom:
            return bottom - atr * 0.5
        return current_price - atr * 2


def _boundary_entry_level(pattern, direction):
    """Entry level for boundary trade: the pattern boundary (top/support)."""
    if direction == 'SELL':
        return pattern.get('top_price') or pattern.get('resistance') or pattern.get('flag_high')
    return pattern.get('bottom_price') or pattern.get('support') or pattern.get('flag_low')


def _pullback_consolidation_ok(pattern, atr, is_flag, is_wedge):
    """Flag: retrace_pct + flag_range. Wedge: pattern_height (no flag fields)."""
    if is_flag:
        retrace_pct = pattern.get('retrace_pct', 0)
        flag_range = pattern.get('flag_range', 0)
        return 0.15 <= retrace_pct <= 0.55 and flag_range >= atr * 0.5
    if is_wedge:
        height = pattern.get('pattern_height', 0)
        return height >= atr * 0.5
    return False


def _tp_method_label(pattern, tp_value, fib_tp, rr_tp, tp2_fib_tp=None, tp2_rr_tp=None):
    """Label TP method — channels use measured move, not Fib extension."""
    if 'Channel' in pattern.get('type', ''):
        ch_tgt = pattern.get('target')
        if ch_tgt is not None and abs(tp_value - ch_tgt) < 1.0:
            return '通道量度目標'
    if abs(tp_value - rr_tp) < 0.01:
        return '1:1 RR'
    if abs(tp_value - fib_tp) < 0.01:
        return '0.618 Fib ext'
    if tp2_rr_tp is not None and abs(tp_value - tp2_rr_tp) < 1.0:
        return '2:1 RR'
    if tp2_fib_tp is not None and abs(tp_value - tp2_fib_tp) < 1.0:
        return '1.0 Fib ext'
    if 'Channel' in pattern.get('type', ''):
        return '通道量度目標'
    return '0.618 Fib ext'


def _compute_tp1(pattern, actual_entry, risk, side):
    fib_ext = None
    if 'Channel' in pattern.get('type', '') and pattern.get('target') is not None:
        return pattern['target'], None
    if side == 'SELL':
        if 'support' in pattern and 'highest_high' in pattern:
            fib_ext = fibonacci_extension(
                {'price': pattern['highest_high']},
                {'price': pattern['support']},
                actual_entry,
            )
        elif 'pole_start_price' in pattern and 'pole_end_price' in pattern:
            fib_ext = fibonacci_extension(
                {'price': pattern['pole_start_price']},
                {'price': pattern['pole_end_price']},
                actual_entry,
            )
        elif 'top_price' in pattern and 'neckline' in pattern:
            fib_ext = fibonacci_extension(
                {'price': pattern['top_price']},
                {'price': pattern['neckline']},
                actual_entry,
            )
        elif 'support' in pattern and 'resistance' in pattern:
            fib_ext = fibonacci_extension(
                {'price': pattern['resistance']},
                {'price': pattern['support']},
                actual_entry,
            )
        elif 'target' in pattern and pattern['target'] is not None:
            return pattern['target'], None
        elif pattern.get('direction') == 'BEARISH' and 'target_down' in pattern:
            return pattern['target_down'], None
        else:
            return actual_entry - risk * 0.618, None

        tp1 = fib_ext['ext_0.618']
        if tp1 >= actual_entry:
            tp1 = fib_ext['ext_1.0']
            if tp1 >= actual_entry:
                tp1 = fib_ext['ext_1.618']
        return tp1, fib_ext

    if 'resistance' in pattern and 'lowest_low' in pattern:
        fib_ext = fibonacci_extension(
            {'price': pattern['lowest_low']},
            {'price': pattern['resistance']},
            actual_entry,
        )
    elif 'pole_start_price' in pattern and 'pole_end_price' in pattern:
        fib_ext = fibonacci_extension(
            {'price': pattern['pole_start_price']},
            {'price': pattern['pole_end_price']},
            actual_entry,
        )
    elif 'bottom_price' in pattern and 'neckline' in pattern:
        fib_ext = fibonacci_extension(
            {'price': pattern['bottom_price']},
            {'price': pattern['neckline']},
            actual_entry,
        )
    elif 'support' in pattern and 'resistance' in pattern:
        fib_ext = fibonacci_extension(
            {'price': pattern['lowest_low'] if 'lowest_low' in pattern else pattern['support']},
            {'price': pattern['resistance']},
            actual_entry,
        )
    elif 'target' in pattern and pattern['target'] is not None:
        return pattern['target'], None
    elif pattern.get('direction') == 'BULLISH' and 'target_up' in pattern:
        return pattern['target_up'], None
    else:
        return actual_entry + risk * 0.618, None

    tp1 = fib_ext['ext_0.618']
    if tp1 <= actual_entry:
        tp1 = fib_ext['ext_1.0']
        if tp1 <= actual_entry:
            tp1 = fib_ext['ext_1.618']
    return tp1, fib_ext



def _compute_tp2(pattern, actual_entry, risk, side, fib_ext=None):
    """Compute TP2 target: further of 1.0 Fib extension or 2:1 RR.

    TP2 is intentionally further than TP1:
    - TP1 = closer of (0.618 Fib ext, 1:1 RR)
    - TP2 = further of (1.0 Fib ext, 2:1 RR)
    """
    if 'Channel' in pattern.get('type', '') and pattern.get('target') is not None:
        # For channels with measured move target, TP2 = 2x the measured move distance
        ch_tgt = pattern['target']
        if side == 'SELL':
            tp2_rr = actual_entry - risk * 2  # 2:1 RR
            tp2_fib = actual_entry - (actual_entry - ch_tgt) * 2  # 2x channel target distance
            tp2 = min(tp2_fib, tp2_rr)  # further = lower for SELL
            return tp2, tp2_fib
        else:
            tp2_rr = actual_entry + risk * 2  # 2:1 RR
            tp2_fib = actual_entry + (ch_tgt - actual_entry) * 2  # 2x channel target distance
            tp2 = max(tp2_fib, tp2_rr)  # further = higher for BUY
            return tp2, tp2_fib

    if fib_ext is not None:
        tp2_fib = fib_ext.get('ext_1.0', fib_ext.get('ext_0.618'))
    else:
        # Recompute fib_ext if not provided
        tp1_result = _compute_tp1(pattern, actual_entry, risk, side)
        tp2_fib = tp1_result[1].get('ext_1.0', tp1_result[1].get('ext_0.618')) if tp1_result[1] else None

    if tp2_fib is None:
        if fib_ext:
            tp2_fib = fib_ext.get('ext_0.618')
        else:
            tp2_fib = actual_entry - risk if side == 'SELL' else actual_entry + risk

    if side == 'SELL':
        tp2_rr = actual_entry - risk * 2  # 2:1 RR
        # TP2 = further target = min(fib, rr) for SELL (lower = further)
        tp2 = min(tp2_fib, tp2_rr)
        # Ensure TP2 is beyond entry
        if tp2 >= actual_entry:
            tp2 = tp2_rr
        return tp2, tp2_fib
    else:
        tp2_rr = actual_entry + risk * 2  # 2:1 RR
        # TP2 = further target = max(fib, rr) for BUY (higher = further)
        tp2 = max(tp2_fib, tp2_rr)
        # Ensure TP2 is beyond entry
        if tp2 <= actual_entry:
            tp2 = tp2_rr
        return tp2, tp2_fib


def _enforce_tp2_beyond_tp1(tp1, tp2, entry, risk, side):
    """Ensure TP2 is further from entry than TP1 (SELL: tp2 < tp1; BUY: tp2 > tp1)."""
    min_sep = risk * 0.1
    if side == 'SELL':
        if tp2 >= tp1:
            tp2 = entry - risk * 2
        if tp2 >= tp1:
            tp2 = tp1 - min_sep
        return tp2
    if tp2 <= tp1:
        tp2 = entry + risk * 2
    if tp2 <= tp1:
        tp2 = tp1 + min_sep
    return tp2


def _trade_order(side):
    if side == 'BEARISH':
        return 'SELL'
    if side == 'BULLISH':
        return 'BUY'
    raise ValueError(f'unrecognised side: {side!r}')


def _pattern_trigger_level(pattern, side, nearest_high, nearest_low):
    if side == 'BEARISH':
        for key in ('support', 'breakout_level', 'neckline'):
            if key in pattern:
                return pattern[key]
        return nearest_low['price']
    if side == 'BULLISH':
        for key in ('resistance', 'breakout_level', 'neckline'):
            if key in pattern:
                return pattern[key]
        return nearest_high['price']
    raise ValueError(f'unrecognised side: {side!r}')


def _staged_targets(pattern, entry, risk, order):
    """TP1/TP2 + R:R + quality. Argument order matches `_compute_tp1`."""
    if risk <= 0:
        raise ValueError(f'risk must be positive, got {risk}')
    if order not in ('SELL', 'BUY'):
        raise ValueError(f'unrecognised order: {order!r}')
    fib_tp, fib_ext = _compute_tp1(pattern, entry, risk, order)
    if order == 'SELL':
        rr_tp = entry - risk
        tp1 = max(fib_tp, rr_tp)
        tp2_rr = entry - risk * 2
    else:
        rr_tp = entry + risk
        tp1 = min(fib_tp, rr_tp)
        tp2_rr = entry + risk * 2
    tp2, tp2_fib = _compute_tp2(pattern, entry, risk, order, fib_ext)
    tp2 = _enforce_tp2_beyond_tp1(tp1, tp2, entry, risk, order)
    rr1 = abs(entry - tp1) / risk
    rr2 = abs(entry - tp2) / risk
    return {
        'fib_tp': fib_tp, 'fib_ext': fib_ext, 'rr_tp': rr_tp,
        'tp1': tp1, 'tp2': tp2, 'tp2_fib': tp2_fib, 'tp2_rr': tp2_rr,
        'rr1': rr1, 'rr2': rr2,
        'quality': _quality_from_rr(rr1, rr2),
    }


def _make_setup(side, pattern, daily_trend, h1_trend, tp3_trail, *,
                already_broken, quality, entry_mode, entry_zone, entry_trigger,
                add_position, stop_loss, stop_rationale, targets, risk,
                entry_price=None, seedable=None, triggered=None, note=None,
                priority_broken=None):
    """Shared setup dict for boundary / breakout / pullback emitters."""
    order = _trade_order(side)
    severity = counter_trend_severity(side, daily_trend, h1_trend)
    aligned = severity == 'ALIGNED'
    status_fn = _entry_status_bearish if side == 'BEARISH' else _entry_status_bullish
    broken_for_priority = already_broken if priority_broken is None else priority_broken
    if note is None:
        note = '' if aligned else _counter_trend_note(side, daily_trend, h1_trend)
    _ef = exit_fields(
        targets['tp2'],
        _tp_method_label(pattern, targets['tp2'], targets['fib_tp'], targets['rr_tp'],
                         targets['tp2_fib'], targets['tp2_rr']),
        tp3_trail, rr1=targets['rr1'])
    rec = {
        'direction': '🔴 SELL' if order == 'SELL' else '🟢 BUY',
        'priority': setup_priority(side, broken_for_priority, daily_trend, h1_trend, quality),
        'pattern': pattern['type'],
        'confidence': pattern.get('confidence', 'MEDIUM'),
        'quality': quality,
        'entry_mode': entry_mode,
        'entry_status': status_fn(already_broken, aligned, quality, entry_mode, severity),
        'entry_zone': entry_zone,
        'entry_trigger': entry_trigger,
        'add_position': add_position,
        'stop_loss': f"${stop_loss:.0f}",
        'stop_rationale': stop_rationale,
        'tp1': f"${targets['tp1']:.0f} ({_tp_method_label(pattern, targets['tp1'], targets['fib_tp'], targets['rr_tp'])}, 止賺 1/3)",
        'tp2': _ef[0], 'tp2_active': not MOMENTUM_HOLD_EXIT,
        'tp3': _ef[1],
        'exit_plan': _ef[2],
        'risk_amount': round(risk, 1),
        'rr_tp1': round(targets['rr1'], 1),
        'rr_tp2': round(targets['rr2'], 1), 'rr_tp2_active': not MOMENTUM_HOLD_EXIT,
        'daily_alignment': daily_alignment_str(side, daily_trend, h1_trend),
        'note': note,
    }
    if entry_price is not None:
        rec['entry_price'] = round(entry_price, 2)
    if seedable is not None:
        rec['seedable'] = seedable
    if triggered is not None:
        rec['triggered'] = triggered
    return rec


def _boundary_max_distance(atr):
    """Max distance (in ATR) between current price and a boundary limit entry.

    2026-09-04 fix: 之前冇距離上限 — Double Bottom 喺低位形成後價格升走,
    setup 永遠指返舊 bottom ($4418 vs 現價 $4485, 偏離 6.4 ATR) 照 emit +
    push。限價單射程外嘅 boundary = stale, 唔 actionable。
    """
    return atr * 2.5


def _emit_boundary(side, pattern, current_price, atr, daily_trend, h1_trend,
                   tp3_trail, add_vol, aligned):
    """Limit entry at pattern boundary. None if not fillable / risk too wide."""
    order = _trade_order(side)
    bd_entry = _boundary_entry_level(pattern, order)
    if not bd_entry:
        return None
    if order == 'SELL' and not (bd_entry > current_price):
        return None
    if order == 'BUY' and not (bd_entry < current_price):
        return None
    # 2026-09-04: distance cap — boundary 太遠 = stale, 唔 emit
    max_dist = _boundary_max_distance(atr)
    dist = (bd_entry - current_price) if order == 'SELL' else (current_price - bd_entry)
    if dist > max_dist:
        return None
    bd_stop = _boundary_entry_sl(pattern, order, atr, current_price)
    bd_risk = (bd_stop - bd_entry) if order == 'SELL' else (bd_entry - bd_stop)
    max_risk = _max_boundary_risk(atr, current_price)
    if not (bd_risk > 0 and bd_risk <= max_risk):
        return None
    targets = _staged_targets(pattern, bd_entry, bd_risk, order)
    ptype = pattern.get('type', '')
    if 'Double' in ptype:
        bd_sl_rationale = (
            f"雙頂頂部 ${bd_entry:.0f} + 0.5 ATR" if order == 'SELL'
            else f"雙底底部 ${bd_entry:.0f} - 0.5 ATR"
        )
    elif 'Wedge' in ptype:
        bd_sl_rationale = (
            f"楔形阻力 ${bd_entry:.0f} + 0.5 ATR" if order == 'SELL'
            else f"楔形支持 ${bd_entry:.0f} - 0.5 ATR"
        )
    else:
        bd_sl_rationale = (
            f"形態邊界 ${bd_entry:.0f} + 0.5 ATR" if order == 'SELL'
            else f"形態邊界 ${bd_entry:.0f} - 0.5 ATR"
        )
    add_default = (bd_entry - bd_risk) if order == 'SELL' else (bd_entry + bd_risk)
    add_level = pattern.get('neckline', add_default)
    # 2026-09-01 pyramiding (mentor 129-trade: winners scale UP with trend, e.g.
    # 0.05→0.2→0.35 on 9/1 crash day; losers stay at base size). Add only when
    # the first tranche is >= +1R in profit, capped at 2 extra tranches total.
    pyr_add_vol = round(add_vol * 0.5, 3)
    add_position = (
        f"跌穿 ${add_level:.0f} 加注 {add_vol}；金字塔: 浮盈≥1R後同向加注 {pyr_add_vol}×2 (上限3注)"
        if order == 'SELL'
        else f"突破 ${add_level:.0f} 加注 {add_vol}；金字塔: 浮盈≥1R後同向加注 {pyr_add_vol}×2 (上限3注)"
    )
    if aligned:
        note = (
            '📍 形態邊界入場 — 不等待跌穿，較佳R:R' if order == 'SELL'
            else '📍 形態邊界入場 — 不等待突破，較佳R:R'
        )
    else:
        note = _counter_trend_note(
            side, daily_trend, h1_trend,
            prefix='邊界沽' if order == 'SELL' else '邊界買',
        )
    return _make_setup(
        side, pattern, daily_trend, h1_trend, tp3_trail,
        already_broken=False,
        quality=targets['quality'],
        entry_mode='boundary',
        entry_zone=f"${bd_entry - atr * 0.3:.0f} - ${bd_entry + atr * 0.3:.0f}",
        entry_trigger=(
            f"限價沽出 @ ${bd_entry:.0f}（形態邊界入場）" if order == 'SELL'
            else f"限價買入 @ ${bd_entry:.0f}（形態邊界入場）"
        ),
        add_position=add_position,
        stop_loss=bd_stop,
        stop_rationale=bd_sl_rationale,
        targets=targets,
        risk=bd_risk,
        entry_price=bd_entry,
        seedable=False,
        triggered=False,
        note=note,
        priority_broken=False,
    )


def _breakout_stop(order, pattern, current_price, atr, nearest_high, nearest_low, actual_entry):
    """Structure-first stop; cap 2 ATR (struct) / 3 ATR (swing) from current.

    ``capped`` is True when the swing stop was replaced by the 3 ATR bound.
    """
    if order not in ('SELL', 'BUY'):
        raise ValueError(f'unrecognised order: {order!r}')
    struct_stop, has_struct = pattern_structure_stop(pattern, order, atr)
    stop_swing = pattern_stop_swing(pattern, order, nearest_high, nearest_low)
    capped = False
    if order == 'SELL':
        if has_struct:
            stop_level = min(struct_stop, current_price + atr * 2)
        else:
            swing_stop = stop_swing + atr
            stop_level = min(swing_stop, current_price + atr * 3)
            capped = stop_level is not swing_stop
        risk = stop_level - actual_entry
    else:
        if has_struct:
            stop_level = max(struct_stop, current_price - atr * 2)
        else:
            swing_stop = stop_swing - atr
            stop_level = max(swing_stop, current_price - atr * 3)
            capped = stop_level is not swing_stop
        risk = actual_entry - stop_level
    return stop_level, stop_swing, has_struct, risk, capped


def _breakout_stop_rationale(order, pattern, atr, stop_swing, has_struct, capped):
    if order not in ('SELL', 'BUY'):
        raise ValueError(f'unrecognised order: {order!r}')
    ptype = pattern.get('type', '')
    is_wedge = 'Wedge' in ptype or '楔形' in ptype
    if order == 'SELL':
        if has_struct:
            flag_high = pattern.get('flag_high', 0)
            if 'resistance' in pattern and is_wedge:
                flag_high = pattern['resistance']
                return f"旗面結構止損 (resistance ${flag_high:.0f} + 0.5 ATR)"
            return f"旗面結構止損 (flag_high ${flag_high:.0f} + 0.5 ATR)"
        if not capped:
            return f"前頂 ${stop_swing:.0f} + 1 ATR (${atr:.0f})"
        return f"3 ATR 封頂 (前頂 ${stop_swing:.0f} 太遠)"
    if has_struct:
        flag_low = pattern.get('flag_low', 0)
        if 'support' in pattern and is_wedge:
            flag_low = pattern['support']
            return f"旗面結構止損 (support ${flag_low:.0f} - 0.5 ATR)"
        return f"旗面結構止損 (flag_low ${flag_low:.0f} - 0.5 ATR)"
    if not capped:
        return f"前底 ${stop_swing:.0f} - 1 ATR (${atr:.0f})"
    return f"3 ATR 封底 (前底 ${stop_swing:.0f} 太遠)"


def _emit_breakout(side, pattern, current_price, atr, daily_trend, h1_trend,
                   tp3_trail, trigger_level, already_broken, add_vol,
                   nearest_high, nearest_low, points):
    """Breakout / breakdown setup. None if risk is non-positive.

    Asymmetry preserved: bearish entry zone tightens after a break; bullish
    zone is always trigger .. trigger+0.5 ATR. Broken-trigger copy also differs.
    """
    order = _trade_order(side)
    actual_entry = current_price if already_broken else trigger_level
    if order == 'SELL':
        if already_broken:
            entry_low = current_price - atr * 0.3
            entry_high = min(current_price + atr * 0.3, trigger_level)
        else:
            entry_low = trigger_level - atr * 0.5
            entry_high = trigger_level
        entry_trigger = (
            f"跌穿 ${trigger_level:.0f} 入場" if not already_broken
            else f"已跌穿 ${trigger_level:.0f}，現價 ${current_price:.0f} 入場"
        )
        add_verb = '跌穿'
    else:
        entry_low = trigger_level
        entry_high = trigger_level + atr * 0.5
        entry_trigger = (
            f"突破 ${trigger_level:.0f}" if not already_broken
            else f"已突破 ${trigger_level:.0f}"
        )
        add_verb = '突破'

    stop_level, stop_swing, has_struct, risk, capped = _breakout_stop(
        order, pattern, current_price, atr, nearest_high, nearest_low, actual_entry,
    )
    if risk <= 0:
        return None
    targets = _staged_targets(pattern, actual_entry, risk, order)
    quality = targets['quality']
    if quality in ('OK', 'GOOD') and already_broken and not _pattern_breakout_confirmed(pattern):
        quality = 'UNCONFIRMED'
    add_level = pattern_add_level(pattern, order, points, trigger_level)
    return _make_setup(
        side, pattern, daily_trend, h1_trend, tp3_trail,
        already_broken=already_broken,
        quality=quality,
        entry_mode='breakout',
        entry_zone=f"${entry_low:.0f} - ${entry_high:.0f}",
        entry_trigger=entry_trigger,
        add_position=f"{add_verb} ${add_level:.0f} 加注 {add_vol}",
        stop_loss=stop_level,
        stop_rationale=_breakout_stop_rationale(
            order, pattern, atr, stop_swing, has_struct, capped,
        ),
        targets=targets,
        risk=risk,
    ), add_level


def _emit_pullback(side, pattern, current_price, atr, daily_trend, h1_trend,
                   tp3_trail, already_broken, add_vol, add_level, aligned):
    """Flag/wedge pullback while still inside the consolidation. None if ineligible."""
    if already_broken:
        return None
    ptype = pattern.get('type', '')
    is_flag = 'Flag' in ptype
    is_wedge = 'Wedge' in ptype or '楔形' in ptype
    if not (is_flag or is_wedge):
        return None
    if not _pullback_consolidation_ok(pattern, atr, is_flag, is_wedge):
        return None

    order = _trade_order(side)
    flag_high = pattern.get('flag_high')
    if flag_high is None and 'resistance' in pattern:
        flag_high = pattern['resistance']
    flag_low = pattern.get('flag_low')
    if flag_low is None and 'support' in pattern:
        flag_low = pattern['support']

    if order == 'SELL':
        if flag_high is None:
            return None
        near = abs(current_price - flag_high) <= atr * 0.3
        in_zone = flag_low is not None and flag_low <= current_price <= flag_high + atr * 0.3
        if not (near or in_zone):
            return None
        pb_entry = current_price
        pb_stop, _ = pattern_structure_stop(pattern, 'SELL', atr)
        if pb_stop is None:
            pb_stop = flag_high + atr * 0.5
        pb_stop = min(pb_stop, current_price + atr * 2)
        pb_risk = pb_stop - pb_entry
        pb_stop_rationale = f"旗面結構止損 (flag_high ${flag_high:.1f} + 0.5 ATR)"
        if 'resistance' in pattern and is_wedge:
            pb_stop_rationale = (
                f"旗面結構止損 (resistance ${pattern['resistance']:.1f} + 0.5 ATR)"
            )
        add_position = f"跌穿 ${add_level:.0f} 加注 {add_vol}"
    else:
        if flag_low is None:
            return None
        near = abs(current_price - flag_low) <= atr * 0.3
        in_zone = flag_high is not None and flag_low - atr * 0.3 <= current_price <= flag_high
        if not (near or in_zone):
            return None
        pb_entry = current_price
        pb_stop, _ = pattern_structure_stop(pattern, 'BUY', atr)
        if pb_stop is None:
            pb_stop = flag_low - atr * 0.5
        pb_stop = max(pb_stop, current_price - atr * 2)
        pb_risk = pb_entry - pb_stop
        pb_stop_rationale = f"旗面結構止損 (flag_low ${flag_low:.1f} - 0.5 ATR)"
        if 'support' in pattern and is_wedge:
            pb_stop_rationale = (
                f"旗面結構止損 (support ${pattern['support']:.1f} - 0.5 ATR)"
            )
        add_position = f"突破 ${add_level:.0f} 加注 {add_vol}"

    if pb_risk <= 0:
        return None
    targets = _staged_targets(pattern, pb_entry, pb_risk, order)
    if aligned:
        note = '🎯 反彈入場 — 旗面內縮倉，待突破追加'
    else:
        note = _counter_trend_note(side, daily_trend, h1_trend, prefix='反彈')
    return _make_setup(
        side, pattern, daily_trend, h1_trend, tp3_trail,
        already_broken=False,
        quality=targets['quality'],
        entry_mode='pullback',
        entry_zone=f"${pb_entry - atr * 0.3:.0f} - ${pb_entry + atr * 0.3:.0f}",
        entry_trigger=f"旗面反彈入場 @ ${pb_entry:.0f}",
        add_position=add_position,
        stop_loss=pb_stop,
        stop_rationale=pb_stop_rationale,
        targets=targets,
        risk=pb_risk,
        note=note,
        priority_broken=False,
    )


def _emit_pattern_setups(side, pattern, current_price, atr, daily_trend, h1_trend,
                         tp3_trail, nearest_high, nearest_low, points, seen_trigger_keys):
    """One pattern → 0–2 setups (boundary XOR breakout[+pullback])."""
    trigger_level = _pattern_trigger_level(pattern, side, nearest_high, nearest_low)
    if any(abs(trigger_level - tk) < atr * 0.5 for tk in seen_trigger_keys):
        return []
    seen_trigger_keys.add(trigger_level)

    already_broken = _setup_breakout_triggered(side, current_price, trigger_level, pattern)
    severity = counter_trend_severity(side, daily_trend, h1_trend)
    aligned = severity == 'ALIGNED'
    add_vol = _volume_risk_tier(severity)[0]
    out = []

    if not already_broken and _is_boundary_reversal(pattern):
        bd = _emit_boundary(
            side, pattern, current_price, atr, daily_trend, h1_trend,
            tp3_trail, add_vol, aligned,
        )
        if bd:
            return [bd]

    if side == 'BEARISH':
        if current_price > trigger_level + atr * 2:
            return out
    elif side == 'BULLISH':
        if current_price < trigger_level - atr * 2:
            return out
    else:
        raise ValueError(f'unrecognised side: {side!r}')

    emitted = _emit_breakout(
        side, pattern, current_price, atr, daily_trend, h1_trend,
        tp3_trail, trigger_level, already_broken, add_vol,
        nearest_high, nearest_low, points,
    )
    if emitted is None:
        return out
    bo, add_level = emitted
    out.append(bo)
    pb = _emit_pullback(
        side, pattern, current_price, atr, daily_trend, h1_trend,
        tp3_trail, already_broken, add_vol, add_level, aligned,
    )
    if pb:
        out.append(pb)
    return out



def _zone_rejection_score(side, entry_level, points, atr, band_mult=1.0):
    """Count swing extremes clustering at the entry level (mentor 129-trade:
    he sold the SAME $4420-4449 resistance band 38 times — repeatedly tested
    zones are the ones that hold. A setup at a zone with >=2 prior touches is
    a 'rejection zone' and deserves a priority boost.

    side: 'BEARISH' (SELL at resistance) or 'BULLISH' (BUY at support)
    entry_level: the setup entry price
    points: swing list from find_swings_ordered
    band_mult: zone half-width in ATR multiples (default 1.0 ATR either side)
    Returns (touches, zone_label, last_touch_idx).
    """
    band = atr * band_mult
    wtype = 'high' if side == 'BEARISH' else 'low'
    touches = 0
    last_touch_idx = -1
    for p in points:
        if p['type'] != wtype:
            continue
        if abs(p['price'] - entry_level) <= band:
            touches += 1
            last_touch_idx = max(last_touch_idx, p['idx'])
    if touches >= 3:
        zone_label = f"強力測試帶 (已測試 {touches} 次)"
    elif touches == 2:
        zone_label = f"測試帶 (已測試 {touches} 次)"
    elif touches == 1:
        zone_label = f"單次觸及 (測試 {touches} 次)"
    else:
        zone_label = ""
    return touches, zone_label, last_touch_idx

def generate_trade_setups(df_m30, patterns, points, daily_trend, current_price, atr, h1_trend=None):
    """Generate trade setups following user's methodology."""
    setups = []
    highs = [p for p in points if p['type'] == 'high']
    lows = [p for p in points if p['type'] == 'low']

    highs_above_current = sorted([h for h in highs if h['price'] > current_price], key=lambda h: h['price'])
    lows_below_current = sorted(
        [l for l in lows if l['price'] < current_price], key=lambda l: l['price'], reverse=True
    )

    nearest_high = highs_above_current[0] if highs_above_current else {'price': current_price + atr * 3}
    nearest_low = lows_below_current[0] if lows_below_current else {'price': current_price - atr * 3}

    bearish_p = sorted(
        [p for p in patterns if p['direction'] == 'BEARISH'],
        key=_pattern_setup_order,
    )[:MAX_PATTERNS_PER_DIRECTION]
    bullish_p = sorted(
        [p for p in patterns if p['direction'] == 'BULLISH'],
        key=_pattern_setup_order,
    )[:MAX_PATTERNS_PER_DIRECTION]

    seen_trigger_keys = set()
    tp3_trail = trail_stop_text(atr)
    ctx = dict(
        current_price=current_price, atr=atr, daily_trend=daily_trend,
        h1_trend=h1_trend, tp3_trail=tp3_trail, nearest_high=nearest_high,
        nearest_low=nearest_low, points=points, seen_trigger_keys=seen_trigger_keys,
    )
    for pattern in bearish_p:
        setups.extend(_emit_pattern_setups('BEARISH', pattern, **ctx))
    for pattern in bullish_p:
        setups.extend(_emit_pattern_setups('BULLISH', pattern, **ctx))

    trend_dir = daily_trend['trend']
    if trend_dir in ('BEARISH', 'BULLISH'):
        fib_start, fib_end = find_relevant_swing(
            points, len(df_m30) - 1, trend_dir, atr=atr
        )
        fib = fibonacci_retracement(fib_start, fib_end) if fib_start and fib_end else None

        # Keep the original 0.618 fallback exclusive: 0.786 is an independent candidate.
        if not setups and fib is not None:
            near_618 = abs(current_price - fib['0.618']) < atr * 1.5
            if near_618:
                if trend_dir == 'BEARISH':
                    entry_level = fib['0.618']
                    stop_level = fib_start['price'] + atr
                    risk = stop_level - entry_level
                    if risk > 0:
                        tp1 = entry_level - risk * 0.618
                        tp2 = entry_level - risk * 2
                        setups.append(_build_fib_fallback_setup(
                            'BEARISH', fib, entry_level, stop_level, risk, tp1, tp2, tp3_trail,
                            daily_trend, h1_trend, atr,
                        ))
                else:
                    entry_level = fib['0.618']
                    stop_level = fib_start['price'] - atr
                    risk = entry_level - stop_level
                    if risk > 0:
                        tp1 = entry_level + risk * 0.618
                        tp2 = entry_level + risk * 2
                        setups.append(_build_fib_fallback_setup(
                            'BULLISH', fib, entry_level, stop_level, risk, tp1, tp2, tp3_trail,
                            daily_trend, h1_trend, atr,
                        ))

        # 0.786 is evaluated even when another pattern setup exists.
        if fib is not None and abs(current_price - fib['0.786']) < atr * 1.5:
            if trend_dir == 'BEARISH':
                entry_0786 = fib['0.786']
                stop_0786 = fib_start['price'] + atr
                risk_0786 = stop_0786 - entry_0786
                tp1_0786, tp2_0786 = fib['0.618'], fib['swing_end']
                if risk_0786 > 0 and tp2_0786 < tp1_0786 < entry_0786 < stop_0786:
                    setups.append(_build_fib_0786_setup(
                        'BEARISH', fib, entry_0786, stop_0786, risk_0786,
                        tp1_0786, tp2_0786, tp3_trail, daily_trend, h1_trend, atr,
                        current_price=current_price,
                    ))
            else:
                entry_0786 = fib['0.786']
                stop_0786 = fib_start['price'] - atr
                risk_0786 = entry_0786 - stop_0786
                tp1_0786, tp2_0786 = fib['0.618'], fib['swing_end']
                if risk_0786 > 0 and stop_0786 < entry_0786 < tp1_0786 < tp2_0786:
                    setups.append(_build_fib_0786_setup(
                        'BULLISH', fib, entry_0786, stop_0786, risk_0786,
                        tp1_0786, tp2_0786, tp3_trail, daily_trend, h1_trend, atr,
                        current_price=current_price,
                    ))

    setups.sort(key=lambda s: (s['priority'], -s.get('rr_tp1', 0)))
    return setups


# ═══════════════════════════════════════════════════════════
# REPORT GENERATION (Improved)
# ═══════════════════════════════════════════════════════════

def ascii_chart(points, current_price, width=40, height=12):
    """Generate simple ASCII chart of recent price action."""
    if not points:
        return "```\n(no data)\n```"
    
    recent = points[-20:]
    prices = [p['price'] for p in recent] + [current_price]
    p_min, p_max = min(prices), max(prices)
    price_range = p_max - p_min
    if price_range == 0:
        price_range = 1
    
    # Create canvas
    canvas = [[' ' for _ in range(width)] for _ in range(height)]
    
    # Plot swing points
    for i, p in enumerate(recent):
        x = int(i * (width - 1) / max(len(recent) - 1, 1))
        y = height - 1 - int((p['price'] - p_min) / price_range * (height - 1))
        y = max(0, min(height - 1, y))
        canvas[y][x] = '▼' if p['type'] == 'low' else '▲'
    
    # Plot current price line
    cur_y = height - 1 - int((current_price - p_min) / price_range * (height - 1))
    cur_y = max(0, min(height - 1, cur_y))
    
    result = "```\n"
    for y in range(height):
        price_at_y = p_max - (y * price_range / (height - 1))
        if y == 0:
            label = f"${p_max:.0f} ┤"
        elif y == height - 1:
            label = f"${p_min:.0f} ┤"
        elif y == cur_y:
            label = f"${current_price:.0f} ┤"
        else:
            label = "     ┤"
        line = label + ''.join(canvas[y])
        if y == cur_y:
            line += f" ← 現價 ${current_price:.0f}"
        result += line + "\n"
    result += "     └" + "─" * (width - 1) + "\n```"
    
    return result

def _data_source_label():
    if DAILY_DATA_SOURCE != DATA_SOURCE:
        label = f"{DATA_SOURCE}（M30/H1）| 日線: {DAILY_DATA_SOURCE}"
    else:
        label = DATA_SOURCE
    if SPOT_FUTURES_BASIS is not None:
        label += f" | basis ${SPOT_FUTURES_BASIS:.2f}"
        if BASIS_CRON_BLOCKED:
            label += " 🚫 cron 暫停"
    return label


def _quality_report_label(quality):
    labels = {
        'GOOD': '🌟 GOOD',
        'OK': '✅ OK',
        'POOR_RR': '⚠️ POOR_RR (R:R < 1.0)',
        'UNCONFIRMED': '⚠️ 未確認突破',
    }
    return labels.get(quality, f'⚠️ {quality}')


def _entry_mode_report_label(mode):
    labels = {
        'breakout': '突破入場',
        'pullback': '回撤/反彈入場',
        'boundary': '邊界限價入場',
    }
    return labels.get(mode, mode or '突破入場')


def generate_report(df_m30, df_h1, df_day, patterns, points, setups, daily_trend, h1_trend=None,
                    candle_m30=None, candle_day=None, m15_result=None, m5_result=None,
                    rebound=None, report_date=None):
    """Generate comprehensive Markdown report."""
    candle_m30 = candle_m30 or []
    candle_day = candle_day or []

    current = float(df_m30['Close'].iloc[-1])
    today = report_date or datetime.now(HKT_LOCAL).strftime('%Y-%m-%d')
    
    # Indicators
    atr_m30 = _last_valid_float(df_m30['ATR'], 'M30 ATR')
    rsi_m30 = _last_valid_float(df_m30['RSI'], 'M30 RSI')
    rsi_daily = daily_trend['rsi']
    h1 = h1_trend or _neutral_trend(df_h1 if df_h1 is not None and len(df_h1) else df_m30)
    rsi_h1 = h1.get('rsi', '-')

    # Volume check (GC=F volume is often unreliable on Yahoo)
    last_vol = float(df_m30['Volume'].iloc[-1])
    avg_vol = float(df_m30['Vol_SMA20'].iloc[-1]) if not pd.isna(df_m30['Vol_SMA20'].iloc[-1]) else last_vol
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1
    vol_suffix = '' if volume_usable(df_m30) else ' (量能不可靠)'
    
    # Separate swings
    highs = [p for p in points[-15:] if p['type'] == 'high']
    lows = [p for p in points[-15:] if p['type'] == 'low']
    
    # Pattern section
    if patterns:
        pattern_lines = []
        for i, p in enumerate(patterns, 1):
            conf = p.get('confidence', '?')
            broken_now = p.get('broken_now', p.get('broken', False))
            broke_recently = p.get('broke_recently', p.get('broken', False))
            if broken_now:
                status = ' ✅ 已突破 (現價)'
            elif broke_recently:
                status = ' ⚠️ 近期曾突破'
            else:
                status = ''
            vol = p.get('vol_confirm', None)
            vol_str = f" | 量確認: {'✅' if vol else '⚠️ 不足'}" if vol is not None and volume_usable(df_m30) else ''
            pattern_lines.append(f"**{i}. {p['type']}** ({conf} confidence{status}){vol_str}")
            for k, v in p.items():
                if k in ('type', 'direction', 'confidence', 'broken', 'broken_now', 'broke_recently',
                         'broke_recently_up', 'broke_recently_down', 'vol_breakout_confirmed',
                         'vol_confirm', 'swing_highs', 'swing_lows',
                         'support_trend', 'resistance_trend', 'upper_trend', 'lower_trend', 'flag_slope_correct'):
                    continue
                if v is None:
                    continue  # RISK 3 fix: skip None values (e.g., NEUTRAL channel target)
                if isinstance(v, (int, float)):
                    if 'retrace_pct' in k:
                        pattern_lines.append(f"  - {k}: **{v:.0%}**")
                    elif 'price' in k or 'level' in k or 'high' in k or 'low' in k or 'target' in k or 'length' in k or 'range' in k or 'height' in k or 'support' in k or 'resistance' in k or 'apex' in k or 'depth' in k or 'neckline' in k:
                        pattern_lines.append(f"  - {k}: **${v:.0f}**")
                    else:
                        pattern_lines.append(f"  - {k}: **{v:.2f}**")
        pattern_text = '\n'.join(pattern_lines)
    else:
        pattern_text = "⚠️ 未檢測到明顯圖表形態"
    
    # Fibonacci
    fib_swing_low, fib_swing_high = find_relevant_swing(points, len(df_m30)-1, daily_trend['trend'], atr=atr_m30)
    fib_text = ""
    if fib_swing_low and fib_swing_high:
        fib = fibonacci_retracement(fib_swing_low, fib_swing_high)
        fib_text = f"""
| 波段 | ${fib['swing_start']:.0f} → ${fib['swing_end']:.0f} (${fib['range']:.0f}) |
|------|------|
"""
        for key in ['0.0', '0.236', '0.382', '0.5', '0.618', '0.786', '1.0']:
            lvl = fib[key]
            marker = " ← 現價" if abs(lvl - current) < atr_m30 * 1.5 else ""
            fib_text += f"| {key} | **${lvl:.0f}**{marker} |\n"
    
    # Setup section
    setup_text = ""
    if setups:
        for i, s in enumerate(setups, 1):
            note = s.get('note', '')
            # 2026-09-02 zone-rejection: surface the swing-cluster score so the
            # human report matches what paper_trade/cron see in the JSON.
            zone_row = ""
            if s.get('zone_label'):
                zone_row = f"\n| 🧲 位測試 | {s['zone_label']} |"
            tp2_lab, tp3_lab = setup_share_labels(s)
            setup_text += f"""
### Signal {i}: {s['direction']} ({s['pattern']})

| 參數 | 詳情 |
|------|------|
| 信號確定性 | {s['confidence']} |
| 優先級 | P{s.get('priority', '?')} (排序 P{s.get('rank_priority', s.get('priority', '?'))}) |
| 入場模式 | {_entry_mode_report_label(s.get('entry_mode', 'breakout'))} |
| 質素 | {_quality_report_label(s.get('quality', '?'))} |
| 日線配合 | {s['daily_alignment']} |
| 觸發狀態 | **{s['entry_status']}** |
| 入場區間 | {s['entry_zone']} |
| 入場觸發 | {s['entry_trigger']} |
| 加注位 | {s['add_position']} |
| 🛑 止損 | {s['stop_loss']} |
| 止損理由 | {s['stop_rationale']} |
| 🎯 TP1 (1/3) | {s['tp1']} |
| {tp2_lab} | {s['tp2']} |
| {tp3_lab} | {s['tp3']} |
| 風險金額 | ${s['risk_amount']:.0f} |
| R:R TP1 | {s['rr_tp1']}:1 |
| R:R TP2 | {rr_tp2_cell(s)} |
{zone_row}
{note}
"""
    else:
        setup_text = "\n⚠️ 無有效交易信號 — 等待形態形成\n"
    
    # --- M5 quick structure section (informational — never gates pushes) ---
    m5_text = ""
    if m5_result and m5_result.get('m5_trend', 'N/A') != 'N/A (數據不足)':
        m5_rsi_str = f"{m5_result['m5_rsi']:.1f}" if m5_result.get('m5_rsi') is not None else '-'
        m5_swings = m5_result.get('m5_swings') or {}
        m5_nh = f"${m5_swings.get('nearest_high'):.0f}" if m5_swings.get('nearest_high') else '-'
        m5_nl = f"${m5_swings.get('nearest_low'):.0f}" if m5_swings.get('nearest_low') else '-'

        m5_text = f"""
## 🕒 五-A、M5 快速結構

| 指標 | M5 |
|------|-----|
| 短線趨勢 | {m5_result['m5_trend']} |
| RSI(14) | {m5_rsi_str} |
| 最近阻力 (swing high) | {m5_nh} |
| 最近支持 (swing low) | {m5_nl} |

"""
        m5_suggestions = m5_result.get('entry_suggestions', [])
        if m5_suggestions:
            m5_text += "### M5 微調建議\n\n"
            m5_text += "| 信號 | 方向 | M5 入場區間 | M5 止損 | M5 ATR |\n"
            m5_text += "|------|------|-------------|---------|--------|\n"
            for i, sug in enumerate(m5_suggestions, 1):
                m5_text += f"| Signal {i} ({sug['pattern_type']}) | {sug['direction']} | {sug['m5_entry_zone']} | {sug['m5_sl']} | ${sug['m5_atr']:.2f} |\n"
            m5_text += "\n> ℹ️ M5 只反映即時微結構 — 極敏感，僅作觸發時機參考，**不影響推送 gate**。\n"
        else:
            m5_text += "\n⚠️ 無 M5 微調建議\n"
    else:
        m5_text = "\n## 🕒 五-A、M5 快速結構\n\n⚠️ M5 數據不足或不可用 — 無法提供快速結構\n"

    # --- Rebound confirmation section (S3 martingale precondition, informational) ---
    rebound_text = ""
    if rebound and rebound.get('signal'):
        rebound_text = f"""
## 🎯 五-C、反彈確認 (S3 馬丁前置信號)

| 項目 | 數值 |
|------|------|
| 信號 | ✅ {rebound['reason']} |
| 參考入場 | ${rebound['entry']} |
| SMA10 (M15) | ${rebound['sma10']} |
| 前3bar高 | ${rebound['prev_high3']} |

> ⚠️ 歷史回測曾現 look-ahead bias（entry 早於信號確認）— 修正後勝率 ~47% ≈ baseline，
> 歷史上無 edge。呢個信號而家處於 **live paper sim 驗證階段**（paper_martingale.json
> 收集真實勝率，幾星期後覆核）。配合 3 級馬丁 0.01→0.04 模擬。**唔影響推送 gate。**
"""
    else:
        rebound_text = "\n## 🎯 五-C、反彈確認 (S3 馬丁前置信號)\n\n⏳ 未確認 — 等陽燭 + 站上 SMA10 + 突破前 3 bar 高\n"

    # --- M15 entry timing section ---
    m15_text = ""
    if m15_result and m15_result.get('m15_trend', 'N/A') != 'N/A (數據不足)':
        m15_rsi_str = f"{m15_result['m15_rsi']:.1f}" if m15_result.get('m15_rsi') is not None else '-'
        swings = m15_result.get('m15_swings') or {}
        nearest_high = swings.get('nearest_high')
        nearest_low = swings.get('nearest_low')
        nh = f'${nearest_high:.0f}' if nearest_high else '-'
        nl = f'${nearest_low:.0f}' if nearest_low else '-'

        m15_text = f"""
## 🕒 五-B、M15 進場時機

| 指標 | M15 |
|------|-----|
| 短線趨勢 | {m15_result['m15_trend']} |
| RSI(14) | {m15_rsi_str} |
| 最近阻力 (swing high) | {nh} |
| 最近支持 (swing low) | {nl} |

"""
        suggestions = m15_result.get('entry_suggestions', [])
        if suggestions:
            m15_text += "### M15 微調建議\n\n"
            m15_text += "| 信號 | 方向 | M15 入場區間 | M15 止損 | M15 ATR |\n"
            m15_text += "|------|------|-------------|---------|--------|\n"
            for i, sug in enumerate(suggestions, 1):
                m15_text += f"| Signal {i} ({sug['pattern_type']}) | {sug['direction']} | {sug['m15_entry_zone']} | {sug['m15_sl']} | ${sug['m15_atr']:.1f} |\n"
            m15_text += "\n> ℹ️ M15 入場區間較 M30 更窄，適合以更精確的價格進場。M15 止損以 M15 ATR + 最近擺動點為基準。\n"
        else:
            m15_text += "\n⚠️ 無 M15 微調建議\n"
    else:
        m15_text = "\n## 🕒 五-B、M15 進場時機\n\n⚠️ M15 數據不足或不可用 — 無法提供短線進場時機\n"
    
    # Support/Resistance - include raw price extremes as fallback
    raw_recent_low = float(df_m30['Low'].min())
    
    sr_highs = sorted(set(round(h['price']) for h in highs if h['price'] > current))[:5]
    sr_lows = sorted(set(round(l['price']) for l in lows if l['price'] < current), reverse=True)[:5]
    
    # Fallback: if no swing lows below current, use raw lows + round numbers
    if not sr_lows:
        raw_lows_below = [int(raw_recent_low)]
        for lvl in range(int(current/50)*50, int(raw_recent_low)-50, -50):
            if lvl < current:
                raw_lows_below.append(lvl)
        sr_lows = sorted(set(raw_lows_below), reverse=True)[:5]
    
    sr_high_text = "\n".join([f"| R{i+1} | **${h}** | Swing High |" for i, h in enumerate(sr_highs)]) if sr_highs else "| - | 無 | - |"
    sr_low_text = "\n".join([f"| S{i+1} | **${l}** | Swing Low |" for i, l in enumerate(sr_lows)]) if sr_lows else "| - | 無 | - |"
    
    # Risk summary
    avg_risk = np.mean([s['risk_amount'] for s in setups]) if setups else atr_m30 * 2
    
    _exit_copy = static_exit_copy(atr_m30)
    trail_rule = _exit_copy["trail_rule"]
    rule_tp2 = _exit_copy["rule_tp2"]
    rule_tp3 = _exit_copy["rule_tp3"]
    journal_tp2 = _exit_copy["journal_tp2"]
    journal_tp3 = _exit_copy["journal_tp3"]

    # Candlestick text builders
    def _candle_list_text(candles):
        if not candles:
            return "未檢測到 K 線形態"
        lines = []
        for cp in candles:
            dir_icon = {'BULLISH': '🟢', 'BEARISH': '🔴', 'NEUTRAL': '🟡'}.get(cp['direction'], '⚪')
            strength_tag = {'HIGH': '強', 'MEDIUM': '中', 'LOW': '弱'}.get(cp['strength'], '')
            lines.append(f"- {dir_icon} **{cp['name']}** [{strength_tag}] ${cp['price']} — {cp['description']} ({cp['time']})")
        return '\n'.join(lines)

    candle_m30_text = _candle_list_text(candle_m30)
    candle_day_text = _candle_list_text(candle_day)

    # Candlestick confirmation per setup direction (anchored to last bar per TF)
    # Prefer pre-computed scores from main() injection; recompute only if missing
    last_m30 = len(df_m30) - 1
    last_day = len(df_day) - 1
    confirm_lines = []
    if setups:
        for i, s in enumerate(setups, 1):
            # Use pre-computed K-line scores if available (from main() step 7b)
            m30_score = s.get('kline_m30_score')
            day_score = s.get('kline_daily_score')
            total_score = s.get('kline_total_score')
            if m30_score is None or day_score is None:
                # Fallback recompute
                direction = 'BEARISH' if 'SELL' in s.get('direction', '') else 'BULLISH'
                m30_conf, m30_pat, m30_score, m30_opp = candlestick_confirmation(
                    candle_m30, direction, lookback_bars=5, last_bar_idx=last_m30
                )
                day_conf, day_pat, day_score, day_opp = candlestick_confirmation(
                    candle_day, direction, lookback_bars=5, last_bar_idx=last_day
                )
                total_score = m30_score + day_score
                all_pat = m30_pat + day_pat
                all_opp = m30_opp + day_opp
            else:
                if total_score is None:
                    total_score = m30_score + day_score
                all_pat_names = s.get('kline_m30_patterns', []) + s.get('kline_daily_patterns', [])
                all_opp_names = s.get('kline_opposing', [])
                all_pat = [{'name': n} for n in all_pat_names]
                all_opp = [{'name': n} for n in all_opp_names]
            all_confirmed = total_score >= 2 and m30_score >= 0 and day_score >= 0
            if all_confirmed:
                names = ', '.join(cp['name'] for cp in all_pat)
                src_tags = [f"M30:{m30_score}", f"日線:{day_score}"]
                line = (
                    f"- ✅ Signal {i} ({s['direction']}): **K 線確認** "
                    f"(total={total_score}, {' + '.join(src_tags)}) ← {names}"
                )
                if all_opp:
                    opp_names = ', '.join(cp['name'] for cp in all_opp)
                    line += f" | ⚠️ 反向: {opp_names}"
                confirm_lines.append(line)
            else:
                opp_note = ''
                if all_opp:
                    opp_names = ', '.join(cp['name'] for cp in all_opp)
                    opp_note = f" | 反向: {opp_names}"
                confirm_lines.append(
                    f"- ⚠️ Signal {i} ({s['direction']}): K 線未確認 "
                    f"(M30={m30_score}, 日線={day_score}, total={total_score}){opp_note}"
                )
    else:
        confirm_lines.append("- 無交易信號需要確認")
    candle_confirm_text = '\n'.join(confirm_lines)

    # Priority setup — top of report
    priority_text = ""
    if setups:
        best = setups[0]  # sorted by priority asc, then R:R desc
        conf_icon = {'HIGH': '🟢', 'MEDIUM': '🟡', 'LOW': '🔴'}.get(best.get('confidence', 'LOW'), '🔴')
        priority_text = f"""\
## ⭐ 當前最優先 Setup

| 項目 | 詳情 |
|------|------|
| 信號 | {best['direction']} — {best['pattern']} |
| 確定性 | {conf_icon} {best['confidence']} |
| 觸發狀態 | {best['entry_status']} |
| 入場區間 | {best['entry_zone']} |
| 🛑 止損 | {best['stop_loss']} |
| 🎯 TP1 | {best['tp1']} (R:R {best['rr_tp1']}:1) |
| 日線配合 | {best['daily_alignment']} |

"""
    else:
        priority_text = "## ⭐ 當前最優先 Setup\n\n⚠️ 無有效交易信號 — 等待形態形成\n\n"

    if setups:
        best_side = 'BEARISH' if 'SELL' in setups[0].get('direction', '') else 'BULLISH'
        vol_advice = _volume_risk_tier(counter_trend_severity(best_side, daily_trend, h1_trend))[1]
    else:
        vol_advice = _volume_risk_tier('ALIGNED')[1]

    # Time quality advisory
    tq_level, tq_advice = _time_quality_score()
    time_quality_line = f"\n| ⏰ 時段品質 | {_time_quality_label(tq_level)} | - | - |"
    if tq_advice:
        time_quality_line += f"\n| | {tq_advice} | - | - |"

    basis_header = f"**Basis:** {BASIS_NOTE}  \n" if BASIS_NOTE else ""

    report = f"""# 🔥 XAUUSD 圖表形態深度分析 v3

**日期:** {today} (HKT)  
**框架:** M30 (主要) / H1 / 日線  
**當前價格:** **${current:.2f}**  
**數據源:** {_data_source_label()}  
{basis_header}**日線趨勢:** {'🔴 **BEARISH**' if daily_trend['trend'] == 'BEARISH' else '🟢 **BULLISH**' if daily_trend['trend'] == 'BULLISH' else '🟡 **NEUTRAL**'} (強度: {daily_trend['strength']}/2)

---

{priority_text}
## 📊 一、市場狀態

| 指標 | M30 | H1 | 日線 |
|------|-----|-----|------|
| RSI(14) | {rsi_m30:.1f} | {rsi_h1 if isinstance(rsi_h1, str) else f"{rsi_h1:.1f}"} | {rsi_daily:.1f} |
| ATR(14) | ${atr_m30:.1f} | - | - |
| MA20 | - | ${h1['ma20']:.0f} | ${daily_trend['ma20']:.0f} |
| MA50 | - | ${h1['ma50']:.0f} | ${daily_trend['ma50']:.0f} |
| 趨勢 | - | {h1['trend']} ({h1['strength']}/2) | {daily_trend['trend']} ({daily_trend['strength']}/2) |
| 成交量 | {vol_ratio:.1f}x avg{vol_suffix} | - | - |{time_quality_line}

> {_scalp_risk_warning()}

## 📐 二、圖表形態

{pattern_text}

## 📈 三、價格結構 (ASCII)

{ascii_chart(points, current)}

## 🎯 四、Fibonacci 分析
{fib_text}

## 🕯️ 四-B、K 線形態 (Candlestick Patterns)

> ℹ️ **K 線確認不改變信號排序、止損或倉位**，但是 cron/Hermes 推送硬門檻：未確認則 `cron_push_eligible=false`。

### M30 (最近 12 根)
{candle_m30_text}

### 日線 (最近 8 根)
{candle_day_text}

### 交易信號 K 線確認
{candle_confirm_text}

## 🔫 五、交易信號 (你的法則)

| 法則 | 執行 |
|------|------|
| 📍 入場 | 突破 / 邊界限價 / 旗楔形回撤 |
| 📍 加注 | 突破前底/前頂（或跌穿 neckline） |
| 🛑 止損 | 前頂之上 / 前底之下 + 1 ATR (必設!) |
| 🎯 TP1 (1/3) | 1:1 RR 或 0.618 Fib ext (取較近) |
| 🔄 0.786 深度回調 | 實驗性（港股 playbook，**未經 XAUUSD 138-sample 驗證**）; SL=回調浪極端, TP1=0.618, TP2=浪頂/底 |
| 🎯 TP2 | {rule_tp2} |
| 🎯 TP3 | {rule_tp3} |
| ⏰ 最佳時段 | 17:00 (broker time) — 138-sample 64.3% 勝, +$340 (n=14) |
| 🚫 危險時段 | 07:00 + 18:00 broker — 138-sample hard-block (25% / 21.4% 勝) |
| ⚠️ 謹慎時段 | 04-06/08 broker — 138-sample 23.8% 勝, -$558 (advisory) |
| ⚠️ 同向疊倉 | 3min 內同向開倉: 13 pairs, 25 unique net -$85 (138-sample) — 避免 |
| ⛔ 倉位上限 | 0.16+ 27.3% -$147; 0.05 20% -$290 (138-sample; 0.31 lot -$124) |
| 📉 日上限 | 最多 {MAX_DAILY_TRADES} 筆/日 |
| 🔥 快速連發 | {RAPID_FIRE_WINDOW_MIN}min 內 ≤ {RAPID_FIRE_MAX_TRADES} 筆 (<5min 持倉勝率 26.3%) |

{setup_text}
{m5_text}
{rebound_text}
{m15_text}
## 📊 六、關鍵價位

### 阻力位
| 級別 | 價格 | 來源 |
|------|------|------|
{sr_high_text}

### 支持位
| 級別 | 價格 | 來源 |
|------|------|------|
{sr_low_text}

## 📋 七、風險管理

| 參數 | 建議 |
|------|------|
| M30 ATR | ${atr_m30:.1f} |
| 平均風險/筆 | ~${avg_risk:.0f} |
| 建議倉位 | {vol_advice} |
| ⛔ 大倉禁忌 | ≥0.16 歷史 27.3% -$147 (含 0.31 lot -$124); 0.05 = 20% -$290 (138-sample) |
| 最大日交易數 | {MAX_DAILY_TRADES} 筆 (歷史: 17筆/日 = 過度交易); {RAPID_FIRE_WINDOW_MIN}min 內 ≤ {RAPID_FIRE_MAX_TRADES} 筆 |
| 最大日虧損 | 賬戶 2% |
| 🎯 TP 要求 | **風控要求** (138-sample: 124/138 無 TP, 40.3% 勝 — 尾部風險仍高) |
| 追蹤止損 | {trail_rule} |

### 📊 歷史交易績效回顧 (Jul 2026, 138 筆合併 mentor sample)

| 指標 | 數值 |
|------|------|
| 總交易數 | 138 筆 (68 Jul 14-17 + 70 Jul 29-30; scripts/mentor_trades.py) |
| 勝率 | 40.6% (56W/82L) |
| 總盈虧 | -$10.86 |
| 有 SL | 103 筆, 36.9% 勝 |
| 無 SL | 35 筆, 51.4% 勝 |
| 有 TP | 14 筆, 42.9% 勝 |
| 無 TP | 124 筆, 40.3% 勝 |
| 最佳時段 | 17:00 (64.3%, +$340, n=14) |
| 危險時段 | 07:00 (25%, -$213) / 18:00 (21.4%, -$99) |
| 謹慎時段 | 04-06/08 (23.8% 勝, -$558, advisory) |
| 最佳持倉 | >4h (66.7% 勝, +$608) / 1-4h (70% 勝, +$466) |
| 最差持倉 | 5-15min (25.8% 勝, -$532) |

> 數據截至 2026-07-31: 138 筆合併, 40.6% 勝, -$10.86。審計: `python scripts/compute_mentor_stats.py`

## 📝 八、交易日誌

```
═══════════════════════════════
📅 日期: {today}
📐 形態: {patterns[0]['type'] if patterns else '等待中'}
📍 入場價: ___
🛑 止損價: ___
🎯 TP1 (1/3): ___
{journal_tp2}
{journal_tp3}
💰 風險: $___
📊 R:R: ___:1
📝 結果:
═══════════════════════════════
```

---

> ⚠️ **免責聲明:** AI 自動分析 v3，僅供學習參考。交易有風險，入市需謹慎。  
> 📡 數據源: {_data_source_label()} | 形態辨識: 三角形/旗形/雙頂底/楔形/通道

*生成: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC*
"""
    return report

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='XAUUSD Pattern Analysis v3')
    parser.add_argument('--output', '-o', help='Output report path')
    parser.add_argument('--json', action='store_true', help='Also output JSON')
    parser.add_argument('--trail-profit', type=float, default=None,
                        help='Trailing stop profit threshold in ATR multiples (default: 2.0, env: TRAIL_PROFIT_ATR)')
    parser.add_argument('--trail-stop', type=float, default=None,
                        help='Trailing stop distance in ATR multiples (default: 1.5, env: TRAIL_STOP_ATR)')
    args = parser.parse_args()

    # CLI overrides stay aliased: MOM_HOLD_TRAIL_ATR tracks TRAIL_STOP_ATR.
    apply_trail_overrides(args.trail_profit, args.trail_stop)
    
    # 1. Fetch data
    df_m30, df_h1, df_m15, df_m5, df_day = fetch_data()
    
    # 2. Add indicators
    df_m30 = add_indicators(df_m30)
    df_h1 = add_indicators(df_h1)
    df_day = add_indicators(df_day)
    if df_m15 is not None and not df_m15.empty:
        df_m15 = add_indicators(df_m15)
    if df_m5 is not None and not df_m5.empty:
        df_m5 = add_indicators(df_m5)
    
    # 3. Find swing points
    points = find_swings_ordered(df_m30['High'].values, df_m30['Low'].values,
                                 atr=df_m30['ATR'].values, close=df_m30['Close'].values)
    _log(f"[*] Swing points: {len(points)} (highs: {sum(1 for p in points if p['type']=='high')}, lows: {sum(1 for p in points if p['type']=='low')})")
    
    # 4. Current price & ATR (needed for pattern detection)
    current = _last_valid_float(df_m30['Close'], 'M30 Close')
    atr = _last_valid_float(df_m30['ATR'], 'M30 ATR')
    if pd.isna(df_m30['ATR'].iloc[-1]):
        _log(f"[!] Last M30 ATR is NaN — using last valid ATR ${atr:.2f}")

    daily_close = _last_valid_float(df_day['Close'], 'Daily Close')
    _evaluate_spot_futures_basis(current, daily_close, atr)
    if BASIS_NOTE:
        _log(f"[*] {BASIS_NOTE}")

    # 5. Detect patterns
    patterns = detect_all_patterns(df_m30, points, atr=atr)
    patterns = add_volume_to_patterns(patterns, df_m30, points)
    _log(f"[*] Patterns found: {len(patterns)}")
    for p in patterns:
        _log(f"   {p['type']} ({p['direction']}, {p.get('confidence','?')})")

    # 5b. Candlestick pattern detection (M30 + Daily)
    candle_m30 = detect_candlestick_patterns(df_m30, lookback=12)
    candle_day = detect_candlestick_patterns(df_day, lookback=8)
    all_candle = candle_m30 + candle_day
    _log(f"[*] Candlestick patterns: {len(candle_m30)} M30 + {len(candle_day)} Daily = {len(all_candle)} total")
    for cp in all_candle:
        _log(f"   {cp['name']} ({cp['direction']}, {cp['strength']}) @ {cp['price']}")

    # 5c. M15/M5 entry-timing analysis (informational — never feeds push gate)
    m15_result = analyze_m15_entry_timing(df_m15, patterns, points, current, atr)
    _log(f"[*] M15 trend: {m15_result['m15_trend']} | RSI: {m15_result['m15_rsi']} | Suggestions: {len(m15_result['entry_suggestions'])}")
    m5_result = analyze_m5_entry_timing(df_m5, patterns, points, current, atr)
    _log(f"[*] M5 trend: {m5_result['m5_trend']} | RSI: {m5_result['m5_rsi']} | Suggestions: {len(m5_result['entry_suggestions'])}")
    rebound = detect_rebound_signal(df_m15)
    _log(f"[*] 反彈確認 (S3): {rebound['signal']} @ {rebound['entry']} ({rebound['reason']})")

    # 6. Multi-timeframe analysis (Daily + H1)
    daily_trend = analyze_daily_trend(df_day)
    h1_trend = analyze_h1_trend(df_h1)
    _log(f"[*] Daily trend: {daily_trend['trend']} (strength: {daily_trend['strength']})")
    _log(f"[*] H1 trend: {h1_trend['trend']} (strength: {h1_trend['strength']})")
    
    # 7. Generate setups
    setups = generate_trade_setups(df_m30, patterns, points, daily_trend, current, atr, h1_trend)
    _log(f"[*] Trade setups: {len(setups)}")
    for s in setups:
        _log(f"   {s['direction']}: {s['pattern']} [{s['entry_status']}]")

    # 7b. Inject K-line confirmation scores into setups (used by paper_trade + cron filtering)
    _inject_kline_scores(setups, candle_m30, candle_day, len(df_m30) - 1, len(df_day) - 1)
    spike_state = _inject_push_metadata(setups, daily_trend, h1_trend, current_price=current,
                                        points=points, atr=atr, closes=df_m30['Close'].values)
    if spike_state:
        _log(f"[*] Post-spike gate ACTIVE: {spike_state['direction']} {spike_state['move']:+.1f} "
             f"(> {SPIKE_ATR_MULT}×ATR) — blocking {'SELL' if spike_state['direction']=='down' else 'BUY'} pushes")
    
    utc_now = datetime.now(timezone.utc)
    # 2026-09-11: trade day = HKT, matching the cron --output filename
    # (`xauusd_v3_$(date +%Y-%m-%d).json`) and the 07:00-23:59 HKT schedule.
    # While this was UTC, the JSON 'date' disagreed with its own filename
    # between 00:00-07:59 HKT, so paper_trade's same-day dedupe treated a new
    # 07:00 setup as a duplicate of the previous evening's entry.
    today = datetime.now(HKT_LOCAL).strftime('%Y-%m-%d')
    broker_dt = utc_now + timedelta(hours=BROKER_UTC_OFFSET_HOURS)
    broker_date = broker_dt.strftime('%Y-%m-%d')

    # 8. Generate report
    report = generate_report(df_m30, df_h1, df_day, patterns, points, setups, daily_trend, h1_trend,
                             candle_m30=candle_m30, candle_day=candle_day,
                             m15_result=m15_result, m5_result=m5_result,
                             rebound=rebound, report_date=today)
    
    # 9. Save — date is UTC to match generated_at (avoids local-midnight filename drift)
    if args.output:
        output_path = args.output
    else:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        output_path = os.path.join(OUTPUT_DIR, f"xauusd_v3_{today}.md")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    _log(f"\n[OK] Report: {output_path}")
    
    if args.json:
        tq_level, tq_advice = _time_quality_score()
        json_out = {
            'date': today,
            'broker_date': broker_date,
            'generated_at': utc_now.strftime('%Y-%m-%dT%H:%M:%SZ'),
            # Report layer reads this. It must not re-read MOMENTUM_HOLD_EXIT.
            'exit_model': 'momentum-hold' if MOMENTUM_HOLD_EXIT else 'fixed-tp2',
            'price': current,
            'data_source': _data_source_label(),
            'intraday_source': DATA_SOURCE,
            'daily_source': DAILY_DATA_SOURCE,
            'm15_source': M15_DATA_SOURCE,
            'm15_analysis': m15_result or None,
            'm5_source': M5_DATA_SOURCE,
            'm5_analysis': m5_result or None,
            'rebound_martingale': {
                'signal': rebound['signal'],
                'reason': rebound['reason'],
                'bar_time': rebound['bar_time'],
                'entry': rebound['entry'],
                'sma10': rebound['sma10'],
                'prev_high3': rebound['prev_high3'],
                'note': 'S3 反彈確認 + 3級馬丁 (0.01→0.04) — live paper sim 驗證中（歷史 backtest 有 look-ahead bias，修正後勝率 ~47% ≈ baseline，無歷史 edge）',
            },
            'post_spike': {
                'active': spike_state is not None,
                'direction': spike_state['direction'] if spike_state else None,
                'move': spike_state['move'] if spike_state else None,
                'window_bars': SPIKE_WINDOW_BARS,
                'atr_mult': SPIKE_ATR_MULT,
                'blocked_setups': sum(1 for s in setups if s.get('post_spike_blocked')),
                'indeterminate': next(
                    (s.get('post_spike_indeterminate') for s in setups
                     if s.get('post_spike_indeterminate')), None),
            },
            'spot_futures_basis': SPOT_FUTURES_BASIS,
            'basis_cron_blocked': BASIS_CRON_BLOCKED,
            'basis_note': BASIS_NOTE or None,
            'atr_30m': round(atr, 2),
            'daily_trend': daily_trend,
            'h1_trend': h1_trend,
            'time_quality': {
                'level': tq_level,
                'advice': tq_advice,
                'broker_utc_offset_hours': BROKER_UTC_OFFSET_HOURS,
            },
            'patterns': patterns,
            'candlestick_m30': candle_m30,
            'candlestick_daily': candle_day,
            'setups': setups,
            # 2026-09-08 (Cursor round-2 follow-up): code-level push list so the
            # cron prompt doesn't have to re-derive push eligibility by reading
            # flags via natural language. Prompt reads ONLY this list.
            # 2026-09-24: 改為 call `push_eligible()` 單一真相 —— 呢兩條條件
            # 曾經喺 backtest.py 被抄漏一半（見 push_eligible docstring）。
            'push_candidates': [s for s in setups if push_eligible(s)],
        }
        json_path = output_path.replace('.md', '.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_out, f, indent=2, default=str)
        _log(f"   JSON: {json_path}")

if __name__ == '__main__':
    import sys
    import time
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            main()
            break
        except (BrokenPipeError, IOError, RuntimeError) as e:
            print(f"[ERROR] Attempt {attempt}/{max_retries}: {e}", file=sys.stderr)
            if attempt < max_retries:
                wait = attempt * 10
                print(f"[INFO] Retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"[FATAL] All {max_retries} attempts failed: {e}", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"[FATAL] Unrecoverable error: {e}", file=sys.stderr)
            sys.exit(1)
