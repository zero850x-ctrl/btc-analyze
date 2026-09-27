#!/usr/bin/env python3
"""test_gate_log.py — btc_gate_log.py 單元測試."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import btc_gate_log as gl  # noqa: E402

HKT = timezone(timedelta(hours=8))
PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {extra}")


def tmp(name="gate.json"):
    fd, p = tempfile.mkstemp(suffix=name)
    os.close(fd)
    os.unlink(p)
    return p


print("=== T1: 首次記錄 ===")
p = tmp()
ok_, s = gl.record(None, p, generated_at="2026-09-24T08:11:23+08:00",
                   gate_skips={"n_raw": 3, "n_kept": 0,
                               "reasons": {"rr_1.0_lt_1.2": 2, "family_fib": 1}})
ok("首次記錄成功", ok_ is True, ok_)
ok("total = 3", s["total"] == 3, s)
ok("reasons 正確", s["reasons"] == {"rr_1.0_lt_1.2": 2, "family_fib": 1}, s["reasons"])

print("\n=== T2: 同一 M30 bar 再掃 → 唔重複計 (核心) ===")
# cron 每 15 分鐘跑, 同一個 M30 bar 會被掃 2 次
ok2, s2 = gl.record(None, p, generated_at="2026-09-24T08:26:00+08:00",
                    gate_skips={"n_raw": 3, "n_kept": 0,
                                "reasons": {"rr_1.0_lt_1.2": 2, "family_fib": 1}})
ok("重複 bar 唔記錄", ok2 is False, ok2)
ok("總數仍然 = 3 (冇 double count)", s2["total"] == 3, s2["total"])

print("\n=== T3: 下一個 M30 bar → 正常累加 ===")
ok3, s3 = gl.record(None, p, generated_at="2026-09-24T08:31:00+08:00",
                    gate_skips={"n_raw": 2, "n_kept": 0,
                                "reasons": {"rr_1.0_lt_1.2": 2}})
ok("新 bar 記錄", ok3 is True, ok3)
ok("累加 total = 5", s3["total"] == 5, s3["total"])
ok("rr 累加 = 4", s3["reasons"]["rr_1.0_lt_1.2"] == 4, s3["reasons"])

print("\n=== T4: M30 floor 邊界 (08:29 vs 08:30 係唔同 bar) ===")
ok4a, _ = gl.record(None, p, generated_at="2026-09-24T09:29:00+08:00",
                    gate_skips={"n_raw": 1, "n_kept": 0, "reasons": {"a": 1}})
ok4b, _ = gl.record(None, p, generated_at="2026-09-24T09:30:00+08:00",
                    gate_skips={"n_raw": 1, "n_kept": 0, "reasons": {"b": 1}})
ok("09:29 → 記錄 (09:00 bar)", ok4a is True, ok4a)
ok("09:30 → 記錄 (09:30 bar, 唔同)", ok4b is True, ok4b)
ok4c, _ = gl.record(None, p, generated_at="2026-09-24T09:44:00+08:00",
                    gate_skips={"n_raw": 1, "n_kept": 0, "reasons": {"b": 1}})
ok("09:44 → 重複 (09:30 bar)", ok4c is False, ok4c)

print("\n=== T5: 冇 setup 可擋 → 唔記錄 ===")
ok5, _ = gl.record(None, p, generated_at="2026-09-24T10:00:00+08:00",
                   gate_skips={"n_raw": 0, "n_kept": 0, "reasons": {}})
ok("n_raw=0 唔記錄", ok5 is False, ok5)

print("\n=== T6: today_summary ===")
s6 = gl.today_summary(p, day="2026-09-24")
ok("total = 7", s6["total"] == 7, s6["total"])
ok("bars = 4", s6["bars"] == 4, s6["bars"])
ok("rr 合計 4", s6["reasons"].get("rr_1.0_lt_1.2") == 4, s6["reasons"])

print("\n=== T7: fmt_summary ===")
txt = gl.fmt_summary(s6)
ok("有 emoji", "🚧" in txt, txt)
ok("有總數", "7" in txt, txt)
ok("有原因", "rr_1.0_lt_1.2" in txt, txt)
ok("冇記錄時回空字串", gl.fmt_summary({"total": 0, "reasons": {}}) == "", "--")

print("\n=== T8: parse_generated_at 各種格式 ===")
d1 = gl.parse_generated_at("2026-09-24T08:11:23+08:00")
ok("ISO 帶時區", d1.strftime("%Y-%m-%d %H:%M") == "2026-09-24 08:11", d1)
d2 = gl.parse_generated_at("2026-09-24T08:11:23")
ok("ISO 冇時區 → 當 HKT", d2.strftime("%H:%M") == "08:11", d2)
d3 = gl.parse_generated_at(1758670283000)
ok("epoch 毫秒", d3.year == 2025 or d3.year == 2026, d3)

print("\n=== T9: 跨日分開計 ===")
p9 = tmp("cross.json")
gl.record(None, p9, generated_at="2026-09-24T23:50:00+08:00",
          gate_skips={"n_raw": 1, "n_kept": 0, "reasons": {"x": 1}})
gl.record(None, p9, generated_at="2026-09-25T00:10:00+08:00",
          gate_skips={"n_raw": 2, "n_kept": 0, "reasons": {"y": 2}})
ok("24 日 total=1", gl.today_summary(p9, "2026-09-24")["total"] == 1, "--")
ok("25 日 total=2", gl.today_summary(p9, "2026-09-25")["total"] == 2, "--")

print("\n=== T10: 從真 analysis JSON 讀 ===")
if os.path.exists("btc_last_analysis.json"):
    p10 = tmp("real.json")
    rec, s10 = gl.record("btc_last_analysis.json", p10)
    print(f"     (真檔: recorded={rec} {s10})")
    ok("唔會 crash", True, "")
else:
    ok("(冇 analysis 檔, skip)", True, "")

print(f"\n{'=' * 60}\n結果: {PASS} PASS / {FAIL} FAIL\n{'=' * 60}")
sys.exit(1 if FAIL else 0)
