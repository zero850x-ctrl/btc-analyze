#!/usr/bin/env python3
"""btc_gate_log.py — 累計 gate 擋單記錄 (按 M30 bar 去重).

背景 (2026-09-24):
  引擎每次跑都會寫 gate_skips 落 btc_last_analysis.json, 但該檔每次覆蓋,
  而且 cron wrapper 靜默設計 (冇新倉/平倉唔出聲) → 用戶只見「0 單」,
  睇唔到背後擋咗幾多次、擋咩原因。用戶問「gate 太嚴?」正正因為冇數據睇。

本 module 將每次分析嘅 gate 擋單累加落日誌, 供報告顯示。

⚠️ 去重: 引擎用 M30 bar, 但 cron 每 15 分鐘跑 → 同一根 K 線會被掃 2 次。
   所以用 generated_at 嘅 M30 floor 做 key, 同一 bar 只計一次,
   否則數字會 double count (唔反映真實「有幾多單被擋」)。
"""
import json
import os
from datetime import datetime, timezone, timedelta

HKT = timezone(timedelta(hours=8))
DEFAULT_LOG = os.path.expanduser("~/.hermes/reports/btc_gate_blocks.json")


def _m30_key(dt):
    return dt.replace(minute=(dt.minute // 30) * 30, second=0, microsecond=0)


def parse_generated_at(s):
    """接受多種格式 → HKT datetime.

    實測 btc_last_analysis.json 用 "2026-09-24 08:30:11 UTC" (唔係 ISO)。
    """
    if isinstance(s, (int, float)):
        v = float(s)
        if v > 1e11:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=HKT)
    txt = str(s).strip()
    if txt.endswith(" UTC"):
        dt = datetime.strptime(txt[:-4].strip(), "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).astimezone(HKT)
    dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    return dt.astimezone(HKT) if dt.tzinfo else dt.replace(tzinfo=HKT)


def record(analysis_path, log_path=None, generated_at=None, gate_skips=None):
    """讀 analysis JSON (或直接傳入), 按 M30 bar 去重後累加到日誌.

    返回 (recorded: bool, summary: dict)
    """
    log_path = log_path or DEFAULT_LOG
    if gate_skips is None or generated_at is None:
        with open(analysis_path) as f:
            a = json.load(f)
        gate_skips = a.get("gate_skips") or {}
        generated_at = a.get("generated_at")

    reasons = {k: int(v) for k, v in (gate_skips.get("reasons") or {}).items()}
    n_raw = int(gate_skips.get("n_raw") or 0)
    if not reasons and n_raw == 0:
        return False, {}

    dt = parse_generated_at(generated_at)
    bar = _m30_key(dt)
    day = bar.strftime("%Y-%m-%d")
    bar_s = bar.strftime("%H:%M")

    log = {}
    if os.path.exists(log_path):
        with open(log_path) as f:
            log = json.load(f)

    d = log.setdefault(day, {"bars": {}, "reasons": {}, "total": 0, "raw_total": 0})
    if bar_s in d["bars"]:
        return False, {"day": day, "bar": bar_s, "dup": True,
                       "total": d["total"], "reasons": d["reasons"]}

    d["bars"][bar_s] = {"reasons": reasons, "n_raw": n_raw}
    for r, n in reasons.items():
        d["reasons"][r] = d["reasons"].get(r, 0) + n
    d["total"] = sum(d["reasons"].values())
    d["raw_total"] += n_raw

    # 只保留最近 60 日
    for old in sorted(log)[:-60]:
        del log[old]

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    tmp = log_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(log, f, indent=1, ensure_ascii=False)
    os.replace(tmp, log_path)

    return True, {"day": day, "bar": bar_s, "total": d["total"],
                  "reasons": d["reasons"], "bars": len(d["bars"])}


def today_summary(log_path=None, day=None):
    """今日累計擋單. 返回 {"total": N, "reasons": {...}, "bars": N}."""
    log_path = log_path or DEFAULT_LOG
    if not os.path.exists(log_path):
        return {"total": 0, "reasons": {}, "bars": 0}
    with open(log_path) as f:
        log = json.load(f)
    day = day or datetime.now(HKT).strftime("%Y-%m-%d")
    d = log.get(day) or {}
    return {"total": d.get("total", 0), "reasons": d.get("reasons", {}),
            "bars": len(d.get("bars", {}))}


def fmt_summary(s):
    if not s.get("total"):
        return ""
    top = sorted(s["reasons"].items(), key=lambda x: -x[1])[:3]
    detail = ", ".join(f"{r} × {n}" for r, n in top)
    return f"🚧 今日 gate 擋 {s['total']} 次 ({detail})"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--record":
        r, s = record("btc_last_analysis.json")
        print("recorded:", r, "|", s)
    print(fmt_summary(today_summary()) or "今日冇 gate 擋記錄")
