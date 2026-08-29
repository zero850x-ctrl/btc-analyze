#!/usr/bin/env python3
"""Sanity check: vendor 檔案同上游 xauusd-analyze-v3 main 一致."""
import hashlib, sys

PAIRS = [
    ("analyze_v3.py", "/tmp/xauusd-analyze-v3/analyze_v3.py"),
    ("paper_trade.py", "/tmp/xauusd-analyze-v3/paper_trade.py"),
]

def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:12]

ok = True
for local, upstream in PAIRS:
    h1, h2 = sha(local), sha(upstream)
    status = "OK" if h1 == h2 else "DRIFT"
    if h1 != h2:
        ok = False
    print(f"{status}: {local} ({h1}) vs upstream ({h2})")
sys.exit(0 if ok else 1)
