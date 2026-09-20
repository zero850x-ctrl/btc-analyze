#!/usr/bin/env python3
"""Gap-aware exit fills (2026-09-12) — offline, no network.

Mirror of the XAUUSD fix: when the seed bar is skipped (look-ahead guard)
the next bar can open beyond the stop, so a naive fill at the stop price
lands outside the bar's traded range, _guard_close rejects it, and the
position stays LIVE forever.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import paper_trade as pt

SLIP = pt.SLIPPAGE_TICKS


def _bar(ts, o, h, l, c):
    return {"datetime": ts, "open": o, "high": h, "low": l, "close": c}


def _df(rows):
    return pd.DataFrame(rows)


def test_exit_fill_no_gap_is_unchanged():
    assert pt._exit_fill(78000.0, 77000.0, True, True) == 78000.0 + SLIP
    assert pt._exit_fill(77000.0, 77500.0, False, True) == 77000.0 - SLIP
    assert pt._exit_fill(76000.0, 76500.0, True, False) == 76000.0 + SLIP
    assert pt._exit_fill(80000.0, 79500.0, False, False) == 80000.0 - SLIP


def test_exit_fill_gap_uses_open():
    # SELL stop jumped over (bar opens ABOVE the stop) → fill at the open
    assert pt._exit_fill(78000.0, 78300.0, True, True) == 78300.0 + SLIP
    # BUY stop gapped under → fill at the open
    assert pt._exit_fill(77000.0, 76800.0, False, True) == 76800.0 - SLIP
    # SELL target gapped under → better fill at the open
    assert pt._exit_fill(76000.0, 75500.0, True, False) == 75500.0 + SLIP
    # BUY target gapped over → better fill at the open
    assert pt._exit_fill(80000.0, 80500.0, False, False) == 80500.0 - SLIP


def test_exit_fill_missing_open_falls_back():
    assert pt._exit_fill(78000.0, None, True, True) == 78000.0 + SLIP
    assert pt._exit_fill(78000.0, float("nan"), True, True) == 78000.0 + SLIP


def test_gap_stop_closes_instead_of_sticking():
    """SELL 78300 stop, seed mid-bar, next bar opens above the stop."""
    seed = pd.Timestamp("2026-09-12T02:01:00Z")
    bars = _df([
        _bar(pd.Timestamp("2026-09-12T02:00:00Z"), 78000.0, 78200.0, 77900.0, 78150.0),
        _bar(pd.Timestamp("2026-09-12T02:30:00Z"), 78350.0, 78500.0, 78340.0, 78480.0),
    ])
    sim = pt._simulate_staged_exit(bars, 77600.0, 78300.0, 77000.0, 76000.0,
                                   "SELL", 450.0, seed_dt=seed, data_source="tv")
    assert sim["closed"] is True, "gap past the stop must still close the trade"
    assert sim["result"] == "SL"
    assert abs(sim["close_price"] - (78350.0 + SLIP)) < 0.01


def test_guard_still_rejects_untouched_stop():
    seed = pd.Timestamp("2026-09-12T02:01:00Z")
    bars = _df([
        _bar(pd.Timestamp("2026-09-12T02:30:00Z"), 78350.0, 78500.0, 78340.0, 78480.0),
    ])
    # stop far above the bar high → never touched, must stay open
    sim = pt._simulate_staged_exit(bars, 77600.0, 79000.0, 77000.0, 76000.0,
                                   "SELL", 450.0, seed_dt=seed, data_source="tv")
    assert sim["closed"] is False


def test_normal_stop_path_unchanged():
    seed = pd.Timestamp("2026-09-12T02:01:00Z")
    bars = _df([
        _bar(pd.Timestamp("2026-09-12T02:30:00Z"), 78000.0, 78100.0, 77900.0, 78050.0),
        _bar(pd.Timestamp("2026-09-12T03:00:00Z"), 78050.0, 78400.0, 78000.0, 78380.0),
    ])
    sim = pt._simulate_staged_exit(bars, 77600.0, 78300.0, 77000.0, 76000.0,
                                   "SELL", 450.0, seed_dt=seed, data_source="tv")
    assert sim["closed"] is True
    assert abs(sim["close_price"] - (78300.0 + SLIP)) < 0.01


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
