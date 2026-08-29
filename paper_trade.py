#!/usr/bin/env python3
"""
Paper Trade Backtest for analyze_v3.py signals.
Logs paper trades and checks outcomes against historical M30 data.

Discipline Guards (updated 2026-08-22, based on 138-trade combined sample):
  - cron_push_eligible gate: only seed eligible setups (includes SL+TP mandatory)
  - Anti-martingale: block volume > 0.01 after 2+ consecutive losses
  - Anti-stacking: opposite-direction LIVE always blocked; same-direction
    allowed up to SAME_DIR_MAX_CONCURRENT (2). Gate lives in discipline_check.
  - SL floor: reject SL < 0.8×ATR (too tight = noise stop-out)
  - GC=F fallback closes: traded-range + series-basis check (fail if last
    bar vs spot > $40); UNVERIFIED stays LIVE and is excluded from R
  - Max daily loss: stop after -3R drawdown
  - Cooldown: block new trade < 15 min after last close (enforced)
  - Danger hour re-check: block seed at 07/18 broker even if JSON was analyzed earlier
  - Min holding: warn on close < 15 bars (scalping = poor expectancy in 138-sample)
  - Direction bias log: warn when 3+ consecutive trades same direction
  - Seed dedup: LIVE same pattern+direction+entry_mode (any day) or CLOSED
    same key today → skip (keep existing fill). IDs from max suffix in
    trades+history.

Usage:
  python3 paper_trade.py                    # Run full cycle: seed trades + check prior
  python3 paper_trade.py --check-only       # Only check prior paper trades
  python3 paper_trade.py --seed-only        # Only seed new paper trades from latest JSON
"""

import json, os, sys, argparse, tempfile, copy, math
from datetime import datetime, timezone

# Match backtest.py's adverse execution model.
SLIPPAGE_TICKS = 0.15
import numpy as np
import yfinance as yf

# TradingView data feed (preferred for spot XAUUSD)
try:
    from tvDatafeed import TvDatafeed, Interval as TVInterval
    _tv = TvDatafeed()
    _TV_AVAILABLE = True
except Exception:
    _tv = None
    _TV_AVAILABLE = False

LOG_PATH = os.path.expanduser("~/.hermes/reports/paper_trade_log.json")


def _json_path():
    """Analyze JSON path for UTC today (matches analyze_v3 report filename)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return os.path.expanduser(f"~/.hermes/reports/xauusd_v3_{today}.json")


JSON_PATH = _json_path()  # resolved at import; main() re-resolves via _json_path()

# ═══════════════════════════════════════════════════════════
# Discipline Guards (138-trade combined sample through 2026-07-30)
# ═══════════════════════════════════════════════════════════
MIN_HOLDING_BARS = 3          # 3 × M30 = 15 min minimum hold
COOLDOWN_MINUTES = 15         # No new trade within 15 min of last close (enforced)
MAX_BARS_HELD = 100           # Timeout exit, aligned with backtest.py (≈2 days M30)
ANTI_MARTINGALE = True         # Block volume increase after consecutive losses
SL_MIN_ATR_MULT = 0.8          # SL must be >= 0.8 × ATR (2026-08-22: 0.5→0.8, fewer noise stop-outs)
GC_F_BASIS_FAIL_USD = 40.0     # GC=F last close vs spot; >$40 = rollover, fail closed
MAX_DAILY_LOSS_R = 3           # Stop trading after -3R daily drawdown
ANTI_STACKING = True           # Enable stacking / overlap guards
ANTI_STACKING_OPPOSITE_ONLY = False  # False: also cap same-direction concurrency
# Same-direction stacking allowed up to 2 concurrent; opposite always blocked.
SAME_DIR_MAX_CONCURRENT = 2
DANGER_HOURS = {7, 18}         # 07/18 broker hard-block (138-sample)
DIR_BIAS_LIMIT = 3             # Warn when 3+ consecutive trades same direction


def _broker_hour(data=None):
    """Current hour in broker-local time (matches analyze_v3 JSON metadata)."""
    offset = -3
    if data and isinstance(data.get('time_quality'), dict):
        offset = int(data['time_quality'].get('broker_utc_offset_hours', offset))
    elif os.environ.get('BROKER_UTC_OFFSET_HOURS'):
        offset = int(os.environ['BROKER_UTC_OFFSET_HOURS'])
    return (datetime.now(timezone.utc).hour + offset) % 24


def _runtime_danger_blocked(data=None):
    """Re-check danger hour at seed time (07/18 broker — 138-sample hard-block)."""
    return _broker_hour(data) in DANGER_HOURS


def _setup_is_seedable(setup):
    """Whether this setup can be seeded now (uses analyze_v3 seedable metadata)."""
    # Never revive an explicitly rejected setup from legacy fallbacks.
    if setup.get('cron_push_eligible') is False:
        return False
    if setup.get('seedable') is not None:
        return bool(setup['seedable'])
    status = setup.get('entry_status', '')
    if '🚫' in status:
        return False
    mode = setup.get('entry_mode', 'breakout')
    if mode in ('boundary', 'fib0786'):
        return True
    trigger = setup.get('entry_trigger', '')
    if '已' in trigger:
        return True
    return '等待' not in status


def _parse_entry_from_setup(setup, current_price):
    """Parse machine-readable entry price from setup JSON."""
    if setup.get('entry_price') is not None:
        try:
            return float(setup['entry_price'])
        except (TypeError, ValueError):
            pass
    trigger = setup.get('entry_trigger', '')
    if '已突破' in trigger or '已跌穿' in trigger:
        return float(current_price) if current_price else None
    if '$' in trigger:
        import re
        m = re.search(r'(\d+(?:\.\d+)?)', trigger.split('$')[-1])
        if m:
            return float(m.group(1))
    return None


def _last_close_dt(log):
    """Most recent close timestamp from history."""
    latest = None
    for h in log.get('history', []):
        if h.get('status') != 'CLOSED' or not _counts_toward_r(h):
            continue
        dt = _parse_dt(h.get('closed_time') or h.get('seeded_time'))
        if dt and (latest is None or dt > latest):
            latest = dt
    return latest


def _cooldown_active(log):
    """True if last close was within COOLDOWN_MINUTES."""
    last = _last_close_dt(log)
    if last is None:
        return False
    elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 60.0
    return elapsed < COOLDOWN_MINUTES


def _effective_stop(stop, trail_stop, trail_active, is_sell):
    """Clamp trailing stop so it never loosens beyond the original stop."""
    if trail_active and trail_stop is not None:
        eff = trail_stop
        return min(eff, stop) if is_sell else max(eff, stop)
    return stop


def _finite_px(val):
    """Parse a price; None if missing / non-finite (never default to 0)."""
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _norm_dir(direction):
    """Canonical BUY/SELL from setup or log strings (emoji prefixes allowed)."""
    s = (direction or "").upper()
    if "SELL" in s:
        return "SELL"
    if "BUY" in s:
        return "BUY"
    return ""


def _calendar_date(val):
    """Return YYYY-MM-DD or None. Rejects empty / timestamp-suffix matches."""
    if val is None:
        return None
    s = str(val).strip()[:10]
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    return s


def _report_date(data):
    """UTC report date from analyze JSON; fall back to UTC today if invalid."""
    parsed = _calendar_date((data or {}).get("date"))
    return parsed or datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _signal_key(pattern, direction, entry_mode="breakout"):
    """Stable seed identity. None if pattern is missing / '?' (not mergeable)."""
    p = (pattern or "").strip()
    if not p or p == "?":
        return None
    d = _norm_dir(direction)
    if not d:
        return None
    mode = (entry_mode or "breakout").strip() or "breakout"
    return (p, d, mode)


def _trade_signal_key(trade):
    return _signal_key(
        trade.get("pattern"),
        trade.get("direction"),
        trade.get("entry_mode", "breakout"),
    )


def _format_entry(px):
    v = _finite_px(px)
    return f"{v:.2f}" if v is not None else "?"


def _iter_log_records(log):
    for t in list(log.get("trades") or []) + list(log.get("history") or []):
        yield t


def _existing_signal(log, key, today):
    """LIVE same key (any seeded_date) or CLOSED same key seeded today."""
    if key is None:
        return None
    for t in _iter_log_records(log):
        if _trade_signal_key(t) != key:
            continue
        if t.get("status") == "LIVE":
            return t
        if t.get("status") == "CLOSED" and _calendar_date(t.get("seeded_date")) == today:
            return t
    return None


def _next_trade_id(log, today):
    """Next {date}-NN using max numeric suffix across trades and history."""
    prefix = f"{today}-"
    max_n = 0
    for t in _iter_log_records(log):
        tid = str(t.get("id") or "")
        if not tid.startswith(prefix):
            continue
        suffix = tid[len(prefix):]
        try:
            n = int(suffix)
        except ValueError:
            continue
        if n > max_n:
            max_n = n
    return f"{today}-{max_n + 1:02d}"


def _json_price_is_spot(data):
    """True when analyze JSON `price` is spot, not a GC=F / futures fallback."""
    if not data:
        return False
    src = str(data.get("intraday_source") or data.get("data_source") or "")
    low = src.lower()
    if "gc=f" in low or "future" in low or "期貨" in src:
        return False
    if data.get("basis_cron_blocked"):
        return False
    return True


GOLD_API_URL = "https://api.gold-api.com/price/XAU"
GOLD_API_TIMEOUT = 8


def _live_spot_price():
    """Fresh spot from gold-api.com. None on failure. Call once per check run."""
    try:
        import urllib.request
        req = urllib.request.Request(
            GOLD_API_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=GOLD_API_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
        px = float(payload.get("price"))
        if px > 0:
            return px
    except Exception as e:
        print(f"  ⚠️ gold-api live spot unavailable: {e}")
    return None


def _series_last_close(bars):
    """Finite last close from fetched M30 bars, or None."""
    if bars is None or getattr(bars, "empty", True):
        return None
    try:
        return _finite_px(bars["close"].iloc[-1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _spot_close_verified(close_px, spot, atr, data_source, data=None,
                         series_last_close=None, live_spot=None):
    """Validate the data series' basis before trusting a simulated close.

    Fail closed if unverifiable. Does not perform network I/O — callers pass
    `live_spot` from a single `_live_spot_price()` per run.

    - tv / paxg: bars are spot or spot-anchored — trust (range guard already ran).
    - gc_f: last bar close vs freshest spot (injected live quote, else JSON
      price if that JSON is itself spot). Fail closed when drift exceeds
      GC_F_BASIS_FAIL_USD ($40, same band as backtest.py rollover alert),
      or when series last close / spot reference is missing or non-finite.
      Individual fill vs current quote is not compared.
    """
    if data_source in ("tv", "paxg"):
        return True

    ref = _finite_px(live_spot)
    if ref is None:
        json_is_spot = data is None or _json_price_is_spot(data)
        if json_is_spot:
            ref = _finite_px(spot)
    if ref is None:
        return False

    slc = _finite_px(series_last_close)
    if slc is None:
        return False
    return abs(slc - ref) <= GC_F_BASIS_FAIL_USD


def _counts_toward_r(record):
    """UNVERIFIED closes must not affect R aggregates or discipline."""
    return record.get("verified") is not False


def _simulate_staged_exit(bars, entry, stop, tp1, tp2, direction, atr, seed_dt=None, data_source="tv"):
    """Bar-by-bar staged exit simulation (shared by check_outcomes and --backtest).

    Returns dict with keys: closed, result, pnl_r, bars_held, tp1_hit, tp2_hit,
    and on close also close_price, verified, data_source.
    If data_source != "tv", the fill must lie inside the evaluated window's
    [min low, max high] (± slippage). That is a sanity check only — futures
    basis shifts fill and range together, so check_outcomes still requires
    a spot-vs-close verification before trusting the close.
    """
    is_sell = _norm_dir(direction) == "SELL"
    risk = abs(entry - stop)
    if risk <= 0:
        return {"closed": False, "result": "LIVE", "pnl_r": 0.0, "bars_held": 0}

    tp1_hit = tp2_hit = False
    trail_active = False
    trail_stop = None
    r_tp1 = r_tp2 = 0.0
    bars_held = 0
    traded_max_high = None
    traded_min_low = None

    def _guard_close(close_px):
        if data_source == "tv":
            return True
        if traded_max_high is None or traded_min_low is None:
            return False
        px = _finite_px(close_px)
        if px is None:
            return False
        lo = traded_min_low - SLIPPAGE_TICKS
        hi = traded_max_high + SLIPPAGE_TICKS
        return lo <= px <= hi

    for _, row in bars.iterrows():
        bar_time = row.get('datetime')
        if bar_time is None:
            continue
        bar_dt = _parse_dt(bar_time)
        # Skip the bar containing seed_dt — it is incomplete and its full
        # high/low/close would introduce look-ahead bias. Start from the next bar.
        if seed_dt is not None and bar_dt is not None and bar_dt <= seed_dt:
            continue

        high = _finite_px(row.get('high'))
        low = _finite_px(row.get('low'))
        close_px = _finite_px(row.get('close'))
        bar_open = _finite_px(row.get('open'))
        if bar_open is None:
            bar_open = entry
        if high is None or low is None or close_px is None or high < low:
            continue

        bars_held += 1
        traded_max_high = high if traded_max_high is None else max(traded_max_high, high)
        traded_min_low = low if traded_min_low is None else min(traded_min_low, low)
        eff_stop = _effective_stop(stop, trail_stop, trail_active, is_sell)

        if is_sell:
            stop_in = high >= eff_stop
            tp_dists = [abs(lvl - bar_open) for hit, lvl in ((tp1_hit, tp1), (tp2_hit, tp2))
                        if not hit and lvl > 0 and low <= lvl]
        else:
            stop_in = low <= eff_stop
            tp_dists = [abs(lvl - bar_open) for hit, lvl in ((tp1_hit, tp1), (tp2_hit, tp2))
                        if not hit and lvl > 0 and high >= lvl]
        stop_first = bool(stop_in and (not tp_dists or abs(eff_stop - bar_open) <= min(tp_dists)))

        if stop_in and stop_first:
            fill = eff_stop + SLIPPAGE_TICKS if is_sell else eff_stop - SLIPPAGE_TICKS
            r_exit = (entry - fill) / risk if is_sell else (fill - entry) / risk
            portions_open = 3 - (1 if tp1_hit else 0) - (1 if tp2_hit else 0)
            total_r = r_tp1 + r_tp2 + r_exit * portions_open / 3.0
            trail_exit = trail_active and trail_stop is not None
            verified = _guard_close(fill)
            return {
                "closed": True,
                "result": "Trail" if trail_exit else "SL",
                "pnl_r": round(total_r, 2),
                "bars_held": bars_held,
                "close_price": round(fill, 2),
                "tp1_hit": tp1_hit,
                "tp2_hit": tp2_hit,
                "verified": verified,
                "data_source": data_source,
            }

        if not tp1_hit and tp1 > 0 and ((is_sell and low <= tp1) or (not is_sell and high >= tp1)):
            tp1_hit = True
            fill = tp1 + SLIPPAGE_TICKS if is_sell else tp1 - SLIPPAGE_TICKS
            r_tp1 = ((entry - fill) / risk if is_sell else (fill - entry) / risk) / 3.0

        if not tp2_hit and tp2 > 0 and not stop_in and ((is_sell and low <= tp2) or (not is_sell and high >= tp2)):
            tp2_hit = True
            fill = tp2 + SLIPPAGE_TICKS if is_sell else tp2 - SLIPPAGE_TICKS
            r_tp2 = ((entry - fill) / risk if is_sell else (fill - entry) / risk) / 3.0

        if bars_held >= MAX_BARS_HELD:
            fill = close_px + SLIPPAGE_TICKS if is_sell else close_px - SLIPPAGE_TICKS
            r_exit = (entry - fill) / risk if is_sell else (fill - entry) / risk
            portions_open = 3 - (1 if tp1_hit else 0) - (1 if tp2_hit else 0)
            total_r = r_tp1 + r_tp2 + r_exit * portions_open / 3.0
            verified = _guard_close(fill)
            return {
                "closed": True,
                "result": f"Timeout ({MAX_BARS_HELD} bars)",
                "pnl_r": round(total_r, 2),
                "bars_held": bars_held,
                "close_price": round(fill, 2),
                "tp1_hit": tp1_hit,
                "tp2_hit": tp2_hit,
                "verified": verified,
                "data_source": data_source,
            }

        # ── Update trailing stop AFTER all exit checks for this bar ──
        # The new trail can only take effect from the NEXT bar, avoiding
        # look-ahead bias (using bar close to set a stop that triggers
        # within the same bar).
        profit = (entry - close_px) if is_sell else (close_px - entry)
        if tp2_hit or profit >= 2.0 * atr:
            if is_sell:
                new_trail = close_px + 1.5 * atr
                if trail_stop is None or new_trail < trail_stop:
                    trail_stop = new_trail
                trail_stop = min(trail_stop, stop)
            else:
                new_trail = close_px - 1.5 * atr
                if trail_stop is None or new_trail > trail_stop:
                    trail_stop = new_trail
                trail_stop = max(trail_stop, stop)
            trail_active = True

    return {
        "closed": False,
        "result": "LIVE",
        "pnl_r": 0.0,
        "bars_held": bars_held,
        "tp1_hit": tp1_hit,
        "tp2_hit": tp2_hit,
        "trail_active": trail_active,
        "trail_stop": round(trail_stop, 2) if trail_stop is not None else None,
    }


def load_log():
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH) as f:
            return json.load(f)
    return {"trades": [], "history": []}


def save_log(log):
    """Atomic, uniquely-temporary write followed by replace."""
    parent = os.path.dirname(LOG_PATH) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".paper_trade_log.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(log, f, indent=2, ensure_ascii=False, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, LOG_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _parse_dt(val):
    """Parse assorted datetime representations → tz-aware UTC datetime (or None).

    2026-08-07 fix: bar/seed timestamps were compared as raw strings
    ('2026-08-07T14:30:00Z' vs '2026-08-07 14:30:00'), which is lexicographic,
    not chronological. All comparisons now go through this helper.
    Naive datetimes are assumed UTC (bars are normalized to UTC in _fetch_m30).
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        dt = val
    elif isinstance(val, str):
        s = val.strip()
        try:
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        except ValueError:
            try:
                dt = datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                return None
    else:
        try:
            import pandas as pd
            dt = pd.Timestamp(val).to_pydatetime()
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _daily_loss_r(log):
    """Sum of pnl_r for trusted CLOSED trades today. UNVERIFIED excluded.

    2026-08-25: compare against the UTC date — seeded_date is written from
    the analyze report date (UTC). Using machine-local time (HKT) made the
    -3R daily-loss circuit breaker blind to same-day losses between
    00:00–07:59 HKT, when local and UTC dates differ.
    """
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    total = 0
    for t in log.get("trades", []):
        if t.get("status") == "CLOSED" and t.get("seeded_date") == today and _counts_toward_r(t):
            r = t.get("pnl_r", 0)
            if r: total += r
    for h in log.get("history", []):
        if h.get("seeded_date") == today and _counts_toward_r(h):
            r = h.get("pnl_r", 0)
            if r: total += r
    return total


def _consecutive_losses(log):
    """Count consecutive losses from most recent trusted closed trades."""
    count = 0
    for t in reversed(log.get("history", [])):
        if not _counts_toward_r(t):
            continue
        r = t.get("pnl_r", 0)
        if r < 0:
            count += 1
        else:
            break
    return count


def _live_position_directions(log):
    """Return canonical BUY/SELL of currently-LIVE positions."""
    dirs = [_norm_dir(t.get("direction", "")) for t in log.get("trades", [])
            if t.get("status") == "LIVE"]
    return [d for d in dirs if d]


def _live_same_direction_count(log, direction):
    """Count currently-LIVE positions in the same canonical direction."""
    d = _norm_dir(direction)
    return sum(1 for t in log.get("trades", [])
               if t.get("status") == "LIVE" and _norm_dir(t.get("direction")) == d)


def _consecutive_same_direction(log, direction):
    """Count consecutive same-direction trades (including LIVE), most recent first."""
    want = _norm_dir(direction)
    all_trades = sorted(
        log.get("trades", []) + log.get("history", []),
        key=lambda t: t.get("seeded_time", "") or t.get("seeded_date", ""),
        reverse=True,
    )
    count = 0
    for t in all_trades:
        if not _counts_toward_r(t):
            continue
        d = _norm_dir(t.get("direction"))
        if d == want:
            count += 1
        else:
            break
    return count


def discipline_check(log, direction, volume, sl_price, entry_price, atr):
    """Multi-stage discipline guard. Returns (pass: bool, reason: str)."""
    # ── 1. Daily loss limit ──
    daily_r = _daily_loss_r(log)
    if daily_r <= -MAX_DAILY_LOSS_R:
        return False, f"🚫 Daily loss limit: {daily_r:.1f}R — no new trades"

    # ── 2. Cooldown after last close ──
    if _cooldown_active(log):
        last = _last_close_dt(log)
        mins = (datetime.now(timezone.utc) - last).total_seconds() / 60.0 if last else 0
        return False, f"🚫 Cooldown: {mins:.0f}min since last close (< {COOLDOWN_MINUTES}min)"

    # ── 3. Anti-stacking: opposite always blocked; same-dir capped ──
    if ANTI_STACKING:
        live_dirs = _live_position_directions(log)
        if live_dirs:
            my_dir = _norm_dir(direction)
            has_opposite = any(d != my_dir for d in live_dirs)
            same_count = sum(1 for d in live_dirs if d == my_dir)
            if has_opposite:
                return False, f"🚫 Opposite LIVE {live_dirs} — no stacking"
            if not ANTI_STACKING_OPPOSITE_ONLY and same_count >= SAME_DIR_MAX_CONCURRENT:
                return False, (
                    f"🚫 {same_count} same-direction LIVE "
                    f"(max {SAME_DIR_MAX_CONCURRENT}) — no stacking"
                )

    # ── 4. Anti-martingale ──
    if ANTI_MARTINGALE:
        consec_loss = _consecutive_losses(log)
        if consec_loss >= 2 and volume > 0.01:
            return False, f"🚫 Anti-martingale: {consec_loss} consecutive losses → volume capped at 0.01"

    # ── 5. SL floor ──
    if sl_price and entry_price and atr > 0:
        sl_dist = abs(entry_price - sl_price)
        if sl_dist < SL_MIN_ATR_MULT * atr:
            return False, f"🚫 SL too tight: {sl_dist:.2f} pts < {SL_MIN_ATR_MULT:.1f}×ATR ({atr:.2f})"

    # ── 6. Direction bias warning ──
    dir_count = _consecutive_same_direction(log, direction)
    if dir_count >= DIR_BIAS_LIMIT:
        return True, f"⚠️ Direction bias: {dir_count} consecutive {direction} trades — consider reversal risk"

    return True, "✅"


def seed_trades(data, setups=None):
    """Create paper trades from analyze_v3 JSON setups (only cron_push_eligible ones)."""
    if setups is None:
        setups = [s for s in data.get("setups", []) if s.get("cron_push_eligible") is True]

    if not setups:
        print("⏳ No cron_push_eligible setups to seed")
        return False

    if _runtime_danger_blocked(data):
        hour = _broker_hour(data)
        print(f"🚫 Danger hour {hour:02d}:00 broker — skipping seed (138-sample hard-block)")
        return False

    log = load_log()
    todays_date = _report_date(data)
    current_price = data.get("price", 0)
    atr = data.get("atr_30m", 15)

    # ── Pre-check: daily loss limit ──
    daily_r = _daily_loss_r(log)
    if daily_r <= -MAX_DAILY_LOSS_R:
        print(f"🚫 Daily loss limit reached: {daily_r:.1f}R — no new trades today")
        return False

    new_count = 0
    skipped = 0
    for s in setups:
        direction = s["direction"]
        is_sell = _norm_dir(direction) == "SELL"

        # ── Skip setups that aren't seedable (waiting limit/breakout) ──
        if not _setup_is_seedable(s):
            continue

        # ── Skip if not cron_push_eligible (discipline gate) ──
        if not s.get("cron_push_eligible", False):
            skipped += 1
            print(f"  ⏭️  Skip {s.get('pattern', '?')} — cron_push_eligible=false")
            continue

        # ── Parse entry price ──
        try:
            entry = _parse_entry_from_setup(s, current_price)
            if entry is None:
                continue

            stop = float(s["stop_loss"].replace("$", "").replace(",", ""))
            tp1 = float(s["tp1"].split("$")[1].split(" ")[0]) if "tp1" in s else 0
            tp2 = float(s["tp2"].split("$")[1].split(" ")[0]) if "tp2" in s else 0

            risk = abs(entry - stop)
            if risk <= 0:
                continue
            # Reject a stale setup if the latest report price has already
            # crossed its protective stop.
            report_price = float(current_price) if current_price else None
            if report_price is not None and ((is_sell and report_price >= stop) or
                                              (not is_sell and report_price <= stop)):
                skipped += 1
                print(f"  ⏭️  Skip {s.get('pattern', '?')} — current price crossed stop")
                continue
            rr_tp1 = abs(entry - tp1) / risk
        except (IndexError, ValueError, AttributeError):
            continue

        pattern = s.get("pattern", "?")
        my_dir = "SELL" if is_sell else "BUY"
        entry_mode = s.get("entry_mode", "breakout")
        sig_key = _signal_key(pattern, my_dir, entry_mode)

        # ── Dedup before discipline so logs name the real reason ──
        # LIVE same key any day, or CLOSED same key today → skip, keep fill.
        prev = _existing_signal(log, sig_key, todays_date)
        if prev:
            skipped += 1
            prev_status = prev.get("status", "?")
            why = "already LIVE" if prev_status == "LIVE" else "already CLOSED today"
            print(f"  ⏭️  Skip {pattern} {entry_mode} — {why} "
                  f"({prev.get('id')} @ {_format_entry(prev.get('entry'))}); keep existing fill")
            continue

        # ── Discipline guards ──
        vol = s.get("recommended_volume", 0.01)
        ok, reason = discipline_check(log, my_dir, vol, stop, entry, atr)
        if not ok:
            skipped += 1
            print(f"  {reason} ({pattern})")
            continue

        # ── Direction bias log ──
        dir_count = _consecutive_same_direction(log, my_dir)
        if dir_count >= DIR_BIAS_LIMIT:
            print(f"  ⚠️ Direction bias: {dir_count} consecutive {my_dir} — proceed with caution")

        if ANTI_STACKING:
            same_count = _live_same_direction_count(log, my_dir)
            if same_count >= 1:
                print(f"  ℹ️ Stacking: {same_count} same-direction LIVE + this = {same_count+1} (max {SAME_DIR_MAX_CONCURRENT})")

        trade = {
            "id": _next_trade_id(log, todays_date),
            "seeded_date": todays_date,
            "seeded_time": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            "status": "LIVE",
            "direction": my_dir,
            "pattern": pattern,
            "confidence": s.get("confidence", "?"),
            "quality": s.get("quality", "?"),
            "entry": round(entry, 2),
            "stop_loss": round(stop, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "risk_amount": round(risk, 2),
            "rr_tp1": round(rr_tp1, 2),
            "lot_size": vol,
            "cron_push_eligible": True,
            "quality_grade": s.get("quality", "?"),
            "priority": s.get("priority", 99),
            "counter_trend_severity": s.get("counter_trend_severity", "?"),
            "time_quality": s.get("time_quality", "?"),
            "entry_mode": entry_mode,
            "atr": atr,  # 2026-08-07: needed for trailing-stop simulation in check_outcomes
            "signal_price": float(current_price) if current_price else None,
        }
        log["trades"].append(trade)
        new_count += 1
        print(f"  ✅ Seeded: {trade['id']} {'SELL' if is_sell else 'BUY'} {s.get('pattern', '?')} "
              f"@ {entry:.2f} SL={stop:.2f} TP1={tp1:.2f} vol={vol}")

    if new_count > 0:
        save_log(log)
        print(f"\n📝 Seeded {new_count} paper trade(s), skipped {skipped}")
    else:
        print(f"\n⏳ No new trades seeded (skipped {skipped})")

    return new_count > 0


def _fetch_m30(start, end):
    """Fetch M30 OHLC data for the given date range via yfinance or TradingView.

    Returns (bars, data_source) where data_source is:
      'tv'   — TradingView OANDA spot (primary, trusted)
      'paxg' — yfinance PAXG-USD (spot-anchored token; 1 PAXG = 1 oz London
               Good Delivery — tracks spot within a few $, far tighter than
               GC=F futures premium; added 2026-08-24 after GC=F pushed a
               fake Bull Flag with $52 premium)
      'gc_f' — yfinance GC=F futures (last resort; premium $15-52, needs
               spot-vs-close verification in check_outcomes)
    """
    bars = None
    data_source = "gc_f"

    # ── Primary: TradingView OANDA spot ──
    if _TV_AVAILABLE:
        try:
            bars = _tv.get_hist(
                symbol="XAUUSD", exchange="OANDA",
                interval=TVInterval.min_30,
                n_bars=500,
            )
            if bars is not None and not bars.empty:
                bars = bars.reset_index()
                if 'datetime' in bars.columns:
                    # 2026-08-07 fix: normalize to UTC before stripping tz so
                    # bar times compare chronologically with seeded_time (UTC).
                    if bars['datetime'].dt.tz is not None:
                        bars['datetime'] = bars['datetime'].dt.tz_convert('UTC').dt.tz_localize(None)
                data_source = "tv"
                print(f"  📊 TradingView M30: {len(bars)} bars")
        except Exception as e:
            print(f"  ⚠️ TradingView fetch failed: {e}")

    # ── Fallback 2: yfinance PAXG-USD (spot-anchored) ──
    if bars is None or bars.empty:
        try:
            ticker = yf.Ticker("PAXG-USD")
            bars = ticker.history(period="5d", interval="30m")
            if not bars.empty:
                bars = bars.reset_index()
                if 'Datetime' in bars.columns:
                    bars.rename(columns={'Datetime': 'datetime'}, inplace=True)
                if 'datetime' in bars.columns and bars['datetime'].dt.tz is not None:
                    bars['datetime'] = bars['datetime'].dt.tz_convert('UTC').dt.tz_localize(None)
                data_source = "paxg"
                print(f"  📊 yfinance PAXG-USD M30: {len(bars)} bars")
                # 2026-08-25: PAXG trades 24/7 as a crypto token — during the
                # gold market weekend close it drifts on thin crypto flow.
                # Flag when the newest bar falls outside Mon-Fri so consumers
                # know the basis may be slightly off spot until Globex reopens
                # (Sun 18:00 ET / Mon 07:00 HKT).
                try:
                    last_dt = bars['datetime'].max()
                    if hasattr(last_dt, 'weekday') and last_dt.weekday() >= 5:  # 5=Sat, 6=Sun
                        print("  ⚠️ Weekend PAXG bars — gold market closed; "
                              "token drifting on thin crypto flow, basis may deviate from spot")
                except Exception:
                    pass
        except Exception as e:
            print(f"  ⚠️ yfinance PAXG fetch failed: {e}")

    # ── Fallback 3 (last resort): yfinance GC=F futures ──
    if bars is None or bars.empty:
        try:
            ticker = yf.Ticker("GC=F")
            bars = ticker.history(period="5d", interval="30m")
            if not bars.empty:
                bars = bars.reset_index()
                if 'Datetime' in bars.columns:
                    bars.rename(columns={'Datetime': 'datetime'}, inplace=True)
                # 2026-08-07 fix: GC=F arrives in America/New_York tz — convert
                # to UTC-naive so comparisons with seeded_time (UTC) are valid.
                if 'datetime' in bars.columns and bars['datetime'].dt.tz is not None:
                    bars['datetime'] = bars['datetime'].dt.tz_convert('UTC').dt.tz_localize(None)
                data_source = "gc_f"
                print(f"  📊 yfinance GC=F M30: {len(bars)} bars")
        except Exception as e:
            print(f"  ⚠️ yfinance fetch failed: {e}")

    if bars is None or bars.empty:
        return None, data_source

    for col in ['open', 'high', 'low', 'close', 'volume']:
        for alt in [col.capitalize(), col.upper()]:
            if alt in bars.columns and col not in bars.columns:
                bars[col] = bars[alt]

    return bars, data_source


def check_outcomes(data):
    """Check all LIVE paper trades against latest bars — hit SL or TP?"""
    log = load_log()
    live_trades = [t for t in log.get("trades", []) if t.get("status") == "LIVE"]
    if not live_trades:
        print("⏳ No paper trades to check")
        return

    bars, data_source = _fetch_m30(None, None)
    if bars is None or bars.empty:
        print("⚠️ Could not fetch M30 data — skipping check")
        return

    series_last = _series_last_close(bars)
    live_spot = None
    if data_source not in ("tv", "paxg"):
        live_spot = _live_spot_price()

    still_live = []
    closed = 0

    for trade in log.get("trades", []):
        if trade.get("status") != "LIVE":
            # Preserve non-LIVE records (e.g. cancelled, partially closed)
            # rather than silently discarding them.
            still_live.append(trade)
            continue

        entry = trade.get("entry", 0)
        stop = trade.get("stop_loss", 0)
        tp1 = trade.get("tp1", 0)
        tp2 = trade.get("tp2", 0)
        direction = trade.get("direction", "BUY")
        seed_dt = _parse_dt(trade.get("seeded_time", ""))
        atr = trade.get("atr") or data.get("atr_30m", 15)

        sim = _simulate_staged_exit(bars, entry, stop, tp1, tp2, direction, atr,
                                    seed_dt=seed_dt, data_source=data_source)

        if sim.get("closed"):
            verified = bool(sim.get("verified", True))
            close_px = sim.get("close_price")
            spot = data.get("price")
            if verified:
                verified = _spot_close_verified(
                    close_px, spot, atr, data_source, data,
                    series_last_close=series_last,
                    live_spot=live_spot,
                )
            if not verified:
                trade["data_source"] = sim.get("data_source", data_source)
                trade["last_unverified"] = {
                    "result": sim["result"],
                    "close_price": close_px,
                    "pnl_r": sim["pnl_r"],
                    "reason": "gc_f series basis check failed",
                }
                still_live.append(trade)
                print(
                    f"  ❌ UNVERIFIED {sim['result']}: {trade['id']} {trade['direction']} "
                    f"@ {close_px} vs spot ${spot} on {data_source} — kept LIVE, not counted"
                )
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
                "data_source": sim.get("data_source", data_source),
                "closed_time": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            })
            closed += 1
            emoji = "🟠" if sim["result"] == "Trail" else "🔴" if sim["result"] == "SL" else "⏱️"
            print(f"  {emoji} {sim['result']}: {trade['id']} {trade['direction']} @ {close_px} ({sim['pnl_r']:+.2f}R)")
            if sim["bars_held"] < MIN_HOLDING_BARS:
                print(f"  ⚠️ SCALP WARNING: {trade['id']} closed in {sim['bars_held']} bars (~{sim['bars_held']*30}min)")
            log["history"].append(trade)
            continue

        trade["tp1_hit"] = sim.get("tp1_hit", False)
        trade["tp2_hit"] = sim.get("tp2_hit", False)
        trade["trail_active"] = sim.get("trail_active", False)
        trade["trail_stop"] = sim.get("trail_stop")
        last_bar = bars.iloc[-1]
        current = last_bar.get('close', 0)
        is_sell = _norm_dir(direction) == "SELL"
        floating = (entry - current) if is_sell else (current - entry)
        trade["floating_pnl"] = round(floating, 2)
        trade["bars_held"] = sim["bars_held"]
        still_live.append(trade)
        print(f"  📊 LIVE: {trade['id']} {trade['direction']} entry={entry:.2f} float={floating:+.2f} ({sim['bars_held']} bars)")

    # Preserve all records that were not closed during this run, including
    # historical CANCELLED/CLOSED/partially-closed records. Newly closed LIVE
    # trades are represented in history and are intentionally removed here.
    log["trades"] = still_live
    save_log(log)

    # Keep the log schema stable: callers use status=LIVE to identify active
    # positions, while preserved non-LIVE records remain auditable.

    print(f"\n📊 Check complete: {closed} closed, {len(still_live)} still LIVE")


def report_status(data):
    """Print current paper trade status summary."""
    log = load_log()
    trades = log.get("trades", [])
    history = log.get("history", [])

    print("=" * 60)
    print("📋 PAPER TRADE STATUS")
    print("=" * 60)

    if trades:
        print(f"\n 🔴 LIVE ({len(trades)}):")
        for t in trades:
            direction = t.get("direction", "?")
            entry = t.get("entry", 0)
            sl = t.get("stop_loss", 0)
            tp1 = t.get("tp1", 0)
            floats = t.get("floating_pnl", 0)
            pattern = t.get("pattern", "?")
            bars = t.get("bars_held", 0)
            print(f"  {t.get('id', '?')} {direction} {pattern} entry={entry:.2f} SL={sl:.2f} TP1={tp1:.2f} float={floats:+.2f} ({bars} bars)")
    else:
        print("\n 🔴 LIVE: none")

    if history:
        trusted = [h for h in history if _counts_toward_r(h)]
        total_r = sum(h.get("pnl_r", 0) for h in trusted)
        wins = sum(1 for h in trusted if h.get("pnl_r", 0) > 0)
        losses = sum(1 for h in trusted if h.get("pnl_r", 0) <= 0)
        win_rate = wins / len(trusted) * 100 if trusted else 0
        unverified_n = len(history) - len(trusted)
        extra = f" | {unverified_n} UNVERIFIED excluded" if unverified_n else ""
        print(f"\n ✅ CLOSED ({len(trusted)}): {wins}W / {losses}L | win rate: {win_rate:.1f}% | total: {total_r:.1f}R{extra}")
        for h in history[-5:]:  # Last 5
            result = h.get("result", "?")
            direction = h.get("direction", "?")
            close = h.get("close_price", 0)
            r = h.get("pnl_r", 0)
            bars = h.get("bars_held", 0)
            pattern = h.get("pattern", "?")
            scalp = " ⚠️SCALP" if bars < MIN_HOLDING_BARS else ""
            unv = " ❌UNVERIFIED" if not _counts_toward_r(h) else ""
            print(f"  {h.get('id', '?')} {result} {direction} {pattern} @ {close:.2f} ({r:+.1f}R, {bars} bars){scalp}{unv}")
    else:
        print("\n ✅ CLOSED: none")

    # ── Daily PnL ──
    daily_r = _daily_loss_r(log)
    print(f"\n 📅 Today PnL: {daily_r:.1f}R (limit: -{MAX_DAILY_LOSS_R}R)")

    # ── Discipline summary ──
    consec_loss = _consecutive_losses(log)
    if consec_loss >= 2:
        print(f" ⚠️ {consec_loss} consecutive losses — anti-martingale active (vol capped at 0.01)")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Paper Trade Backtest for analyze_v3.py")
    parser.add_argument("--check-only", action="store_true", help="Only check prior paper trades")
    parser.add_argument("--seed-only", action="store_true", help="Only seed new paper trades")
    parser.add_argument("--backtest", action="store_true", help="Backtest mode: seed at pattern trigger bars")
    args = parser.parse_args()

    # Load latest JSON
    json_path = _json_path()
    if not os.path.exists(json_path):
        print(f"⚠️ No JSON found: {json_path}")
        print("Run analyze_v3.py --json first.")
        sys.exit(1)

    with open(json_path) as f:
        data = json.load(f)

    if args.check_only:
        check_outcomes(data)
        report_status(data)
    elif args.seed_only:
        # Re-select eligible at runtime
        eligible = [s for s in data.get("setups", []) if s.get("cron_push_eligible")]
        seed_trades(data, eligible)
        report_status(data)
    elif args.backtest:
        run_backtest(data)
    else:
        # Full cycle: check first, then seed
        check_outcomes(data)
        eligible = [s for s in data.get("setups", []) if s.get("cron_push_eligible")]
        seed_trades(data, eligible)
        report_status(data)


def run_backtest(data):
    """Backtest mode: simulate cron-eligible seedable setups from analyze JSON."""
    bars, data_source = _fetch_m30(None, None)
    if bars is None or bars.empty:
        print("⚠️ Could not fetch M30 data for backtest")
        return

    series_last = _series_last_close(bars)
    live_spot = None
    if data_source not in ("tv", "paxg"):
        live_spot = _live_spot_price()

    setups = data.get("setups", [])
    if not setups:
        print("⏳ No setups in JSON")
        return

    seed_dt = _parse_dt(data.get("generated_at"))
    if seed_dt is None:
        seed_dt = _parse_dt(f"{_report_date(data)}T00:00:00Z")

    current_price = data.get("price", 0)
    atr = data.get("atr_30m", 15)
    results = []

    # ── Discipline guards (aligned with seed_trades) ──
    # Use an isolated simulated log and update it after each candidate so
    # anti-stacking, cooldown, loss and direction guards apply chronologically.
    log = copy.deepcopy(load_log())
    log["trades"] = [t for t in log.get("trades", []) if t.get("status") == "LIVE"]
    log["history"] = list(log.get("history", []))
    if _runtime_danger_blocked(data):
        hour = _broker_hour(data)
        print(f"🚫 Danger hour {hour:02d}:00 broker — backtest blocked (138-sample hard-block)")
        return
    daily_r = _daily_loss_r(log)
    if daily_r <= -MAX_DAILY_LOSS_R:
        print(f"🚫 Daily loss limit reached: {daily_r:.1f}R — backtest blocked")
        return

    for s in setups:
        pattern = s.get("pattern", "?")
        direction = s.get("direction", "BUY")
        side = _norm_dir(direction) or "BUY"
        eligible = s.get("cron_push_eligible", False)

        if not eligible:
            print(f"  ⏭️  Skip {pattern} — cron_push_eligible=false")
            continue

        if not _setup_is_seedable(s):
            print(f"  ⏳ {pattern} — not seedable yet")
            continue

        try:
            entry = _parse_entry_from_setup(s, current_price)
            if entry is None:
                continue
            # Match backtest adverse entry fill: BUY pays ask, SELL sells bid.
            entry = entry - SLIPPAGE_TICKS if side == "SELL" else entry + SLIPPAGE_TICKS
            stop = float(s["stop_loss"].replace("$", "").replace(",", ""))
            tp1 = float(s["tp1"].split("$")[1].split(" ")[0]) if "tp1" in s else 0
            tp2 = float(s["tp2"].split("$")[1].split(" ")[0]) if "tp2" in s else 0
        except (IndexError, ValueError, AttributeError):
            continue

        if abs(entry - stop) <= 0:
            continue

        # ── Per-trade discipline check ──
        vol = s.get("recommended_volume", 0.01)
        ok, reason = discipline_check(log, side, vol, stop, entry, atr)
        if not ok:
            print(f"  {reason} ({pattern})")
            continue

        sim = _simulate_staged_exit(
            bars, entry, stop, tp1, tp2, side, atr, seed_dt=seed_dt,
            data_source=data_source,
        )
        status = sim["result"]
        pnl_r = sim["pnl_r"]
        bars_held = sim["bars_held"]
        verified = bool(sim.get("verified", True))
        if sim.get("closed") and verified:
            verified = _spot_close_verified(
                sim.get("close_price"), current_price, atr, data_source, data,
                series_last_close=series_last,
                live_spot=live_spot,
            )
        scalp = " ⚠️SCALP" if bars_held < MIN_HOLDING_BARS else ""
        unv = "" if verified else " ❌UNVERIFIED"
        print(f"  {pattern}: {direction} entry={entry:.2f} SL={stop:.2f} TP1={tp1:.2f} → {status} ({pnl_r:+.1f}R, {bars_held} bars){scalp}{unv}")

        result_record = {
            "pattern": pattern,
            "direction": side,
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "result": status,
            "pnl_r": pnl_r,
            "bars_held": bars_held,
            "scalp_warning": bars_held < MIN_HOLDING_BARS,
            "verified": verified,
        }
        counted = (not sim.get("closed")) or verified
        if counted:
            results.append(result_record)
        # Feed trusted closes into history; unverified / LIVE stay as LIVE
        # placeholders so subsequent candidates cannot stack through a fake close.
        really_closed = bool(sim.get("closed") and verified)
        simulated = {
            "direction": side,
            "status": "CLOSED" if really_closed else "LIVE",
            "seeded_time": str(seed_dt) if seed_dt else "",
            "seeded_date": _report_date(data),
            "pnl_r": pnl_r if really_closed else 0.0,
            "verified": verified,
            "close_time": str(seed_dt) if really_closed else "",
        }
        if simulated["status"] == "LIVE":
            log.setdefault("trades", []).append(simulated)
        else:
            log.setdefault("history", []).append(simulated)

    if results:
        closed = [r for r in results if r.get("verified", True) and r["result"] != "LIVE"]
        live_n = sum(1 for r in results if r["result"] == "LIVE")
        total_r = sum(r["pnl_r"] for r in closed)
        wins = sum(1 for r in closed if r["pnl_r"] > 0)
        losses = sum(1 for r in closed if r["pnl_r"] <= 0)
        scalps = sum(1 for r in closed if r["scalp_warning"])
        print(f"\n📊 Backtest: {len(closed)} closed | {wins}W/{losses}L | total: {total_r:.1f}R | {scalps} scalp warnings | {live_n} still LIVE")


if __name__ == "__main__":
    main()