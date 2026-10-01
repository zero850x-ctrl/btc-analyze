#!/usr/bin/env python3
"""btc_weekend_cron.py — BTC testnet auto-trade cron 入口 (no_agent watchdog)

排程 (HKT):
- 週一至五 07:00-22:50 每 15 分鐘 = `*/15 7-22 * * 1-5`
- 週六/日 10:00-22:50 每 15 分鐘 = `*/15 10-22 * * 6,0`
行為: 跑一個 auto-trade cycle; 有 notable 事件先出聲, 冇嘢 = 靜默.
"""
import subprocess
import sys
import os
from datetime import datetime, timezone, timedelta

# 2026-09-13: clone 已搬離 /tmp (macOS clean-tmps 會逐個 file 靜默清走 tracked file)
REPO = os.environ.get("BTC_REPO") or os.path.expanduser("~/repos/btc-analyze")
GIT_URL = "https://github.com/zero850x-ctrl/btc-analyze.git"
# 2026-09-27: PR#7 (phantom 修復 + P2/P1/freeze 訊號) 已 merge 入 main → pin 還原 main
BRANCH_PIN = os.environ.get("BTC_REPO_BRANCH", "main")   # 2026-09-27: PR#7 merge 後還原 main

# cron 環境 import path: 用一個有 numpy/yfinance 嘅 interpreter
CANDIDATE_PY = [sys.executable, "/opt/homebrew/bin/python3.11", "/usr/bin/python3"]
NOTABLE_KEYS = ("✅", "🔒", "❌", "⚠️", "🚨", "FLATTENED", "🧹",
                "📌", "⌛", "🎯", "🚫")   # 📌⌛🎯 = 限價掛單/過期/成交 (feat/limit-entry)
# 🚨 = 對帳重建凍結 (需人手) — office R1-B 2026-09-27
# 暫時性錯誤 (testnet down/network): crash 都唔出聲 — 下個 15min tick 自然恢復
# 2026-09-09: testnet 全面 502 每 tick crash spam; retry (底層 3x) 食唔甩長 down
SILENT_ERROR_MARKERS = ("HTTP Error 502", "HTTP Error 503", "HTTP Error 504",
                        "HTTP Error 429", "HTTP Error 500", "HTTP Error 4001",
                        "urlopen error", "URLError", "timed out", "ConnectionResetError",
                        "getaddrinfo failed")


def pick_python():
    """揀一個 import 到 numpy+yfinance 嘅 python (cached)."""
    cache = os.path.join(REPO, ".cron_python")
    if os.path.exists(cache):
        p = open(cache).read().strip()
        if os.path.exists(p):
            return p
    for cand in CANDIDATE_PY:
        if not (cand and os.path.exists(cand.replace(" ", ""))):
            continue
        r = subprocess.run([cand, "-c", "import numpy, pandas, yfinance, requests"],
                           capture_output=True, timeout=30)
        if r.returncode == 0:
            with open(cache, "w") as f:
                f.write(cand)
            return cand
    return None


def in_window():
    """HKT 工作日 07:00-22:50、週末 10:00-22:50 守衛 (cron 誤射都唔會亂開倉)."""
    now = datetime.now(timezone(timedelta(hours=8)))
    start_hour = 7 if now.weekday() in (0, 1, 2, 3, 4) else 10
    if not (start_hour <= now.hour < 23):
        return False
    if now.hour == 22 and now.minute > 50:
        return False
    return True


REQUIRED_FILES = ("btc_auto_trade_cycle.py", "btc_engine.py",
                  "binance_testnet_paper.py", "analyze_v3.py")


def _repo_env():
    env = dict(os.environ)
    env.setdefault("HOME", "/Users/gordonlui")
    return env


def _files_ok():
    """所有必需 file 就位."""
    if not os.path.isdir(REPO):
        return False
    return all(os.path.isfile(os.path.join(REPO, f)) for f in REQUIRED_FILES)


def _head_ok():
    """HEAD 可解析 (git 完整)."""
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "HEAD"],
                       capture_output=True, timeout=30, env=_repo_env())
    return r.returncode == 0


def _branch_ok():
    """HEAD 喺 BRANCH_PIN (detached HEAD 當唔 OK).

    2026-10-01 加: 同一個 REPO (~/repos/btc-analyze) 由多個 cron 共用,
    各自 BRANCH_PIN 唔一致時 — 邊個先撞到損壞就由佢 clone, 會令 repo
    變成另一個 branch。舊檢查只睇 file + HEAD, 照當健康 →
    **靜默跑錯版本落單**。必須連 branch 一齊驗。
    """
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--abbrev-ref", "HEAD"],
                       capture_output=True, text=True, timeout=30, env=_repo_env())
    return r.returncode == 0 and r.stdout.strip() == BRANCH_PIN


def _repo_healthy():
    """file 就位 + .git 完整 + 喺預期 branch。

    2026-09-12 實證: macOS periodic clean-tmps 逐個 file 清 /tmp — btc_engine.py
    唔見咗但 btc_auto_trade_cycle.py 仲喺, 舊檢查 (只睇一個 file) 當健康 →
    cycle 跑 btc_engine.py 即死 (No such file), 連續 crash。要逐個 file 查,
    而且 .git 都會被清到冇 HEAD/config (git 完全用唔到)。
    """
    return _files_ok() and _head_ok() and _branch_ok()


def ensure_repo():
    """確保 REPO 完整 (所有必需 file + 可用 .git).

    恢復次序: git checkout 補 file → 唔得就成個 repo 改名做 backup 再
    re-clone + checkout BRANCH_PIN。trade log 喺 ~/.hermes/reports, 唔受
    影響; backup 保留 local-only 檔案例如 btc_last_analysis.json。

    回傳 (repo_cycle_path, note) — note 非 None 即做過恢復 (要通知用戶)。
    """
    env = _repo_env()
    repo_cycle = os.path.join(REPO, "btc_auto_trade_cycle.py")
    if _repo_healthy():
        return repo_cycle, None
    missing = [f for f in REQUIRED_FILES if not os.path.isfile(os.path.join(REPO, f))]
    if missing:
        why = f"缺 {', '.join(missing)}"
    elif os.path.isdir(os.path.join(REPO, ".git")) and not _head_ok():
        why = "git metadata 壞"
    elif os.path.isdir(os.path.join(REPO, ".git")) and not _branch_ok():
        why = f"branch 唔對 (應該係 {BRANCH_PIN})"
    else:
        why = "repo 唔完整"
    note = f"🔧 repo 唔完整 ({why}) — 自動恢復中"
    # 1) repo 目錄喺 + .git 用得到 → 輕量 git checkout 補回被清走嘅 file
    if os.path.isdir(os.path.join(REPO, ".git")):
        subprocess.run(["git", "-C", REPO, "checkout", "--", "."],
                       capture_output=True, timeout=90, env=env)
        if _repo_healthy():
            return repo_cycle, note + " (git checkout 修好)"
        # 1b) file/git 完好但 branch 唔對 → checkout 返預期 branch
        #     (唔使 re-clone; 保住 local-only 檔案例如 btc_last_analysis.json)
        if _files_ok() and _head_ok():
            r = subprocess.run(["git", "-C", REPO, "checkout", "-q", BRANCH_PIN],
                               capture_output=True, timeout=90, env=env)
            if _repo_healthy():
                return repo_cycle, note + f" (branch 唔對 → checkout {BRANCH_PIN})"
            return repo_cycle, (note + f" (⚠️ branch 唔對而且 checkout {BRANCH_PIN} 失敗: "
                                       f"{(r.stdout + r.stderr)[-200:]!r})")
    # 2) .git 都爛埋 / file 唔喺 git 追蹤 → 原目錄改名做 backup 再 clone
    if os.path.isdir(REPO):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.rename(REPO, f"{REPO}.bak.{stamp}")
    # 3) clone 全新; clone 完 checkout BRANCH_PIN
    #    (唔 checkout 嘅話 default = main, 未 merge 嘅 fix 會消失)
    r = subprocess.run(["git", "clone", GIT_URL, REPO],
                       capture_output=True, timeout=180, env=env)
    if r.returncode == 0 and os.path.isdir(REPO) and BRANCH_PIN != "main":
        subprocess.run(["git", "-C", REPO, "checkout", "-q", BRANCH_PIN],
                       capture_output=True, timeout=60, env=env)
    if not _repo_healthy():
        raise RuntimeError(f"re-clone 失敗: {(r.stdout + r.stderr)[-300:]}")
    return repo_cycle, note + f" (re-clone {BRANCH_PIN} 完成)"


def main():
    if not in_window():
        return  # 窗口外靜默

    # cron daemon 可能冇 HOME → python load 唔到 user site-packages (numpy 等)
    env = dict(os.environ)
    env.setdefault("HOME", "/Users/gordonlui")

    recover_note = None
    try:
        _, recover_note = ensure_repo()
    except Exception as e:
        print(f"❌ repo restore 失敗: {e}")
        return

    py = pick_python()
    if not py:
        print("❌ 搵唔到有 numpy+yfinance 嘅 python interpreter")
        return

    prepend = [recover_note] if recover_note else []

    r = subprocess.run(
        [py, "btc_auto_trade_cycle.py"],
        cwd=REPO, capture_output=True, text=True, timeout=280, env=env,
    )
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        # 暫時性錯誤 (testnet down/network) → 靜默, 等 15min 後自然恢復
        if any(m in out for m in SILENT_ERROR_MARKERS):
            if prepend:
                print("\n".join(prepend))
            return
        # 真 crash → 出聲 (deliver 為 error alert), 但 exit 0 避免每 15 分鐘重複警報
        print("\n".join(prepend + [f"❌ cycle crash (exit {r.returncode}):\n{out[-800:]}"]))
        return
    lines = [l for l in out.splitlines() if any(k in l for k in NOTABLE_KEYS)]
    if prepend or lines:
        print("\n".join(prepend + lines))

    # 記錄 gate 擋單 (累加, 按 M30 bar 去重) — 令「0 單」可解釋
    try:
        subprocess.run([py, "btc_gate_log.py", "--record"], cwd=REPO,
                       capture_output=True, text=True, timeout=30)
    except Exception:
        pass

    # 每小時整點 (HKT xx:00-xx:04 tick) → status report (冇買賣都有報告)
    now_hkt = datetime.now(timezone(timedelta(hours=8)))
    if now_hkt.minute < 5:
        report = hourly_status()
        if report:
            print(report)


def hourly_status():
    """整點報告: BTC 價 + live 倉浮動 + 累計 R."""
    try:
        sys.path.insert(0, REPO)
        from binance_testnet_paper import load_log, current_price

        px = current_price()
        log_d = load_log()
        live = [o for o in log_d["orders"] if o.get("status") in ("FILLED_ENTRY", "OCO_PLACED")]
        closed = [o for o in log_d["orders"] if o.get("status") == "CLOSED"]
        sum_r = sum(o.get("r_multiple") or 0 for o in closed)
        wins = sum(1 for o in closed if (o.get("r_multiple") or 0) > 0)

        lines = [f"⏰ {datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M')} HKT 報告 — BTC ${px:,.0f}"]
        if live:
            for o in live:
                direction = -1 if o["side"] == "SELL" else 1
                fl = (px - o["entry_fill"]) * o["qty"] * direction
                emoji = "📈" if fl >= 0 else "📉"
                lines.append(f"  {o['side']} {o['pattern'][:14]} @{o['entry_fill']:,.0f} → {emoji} {fl:+.2f} USDT")
        else:
            lines.append("  (冇 live 倉)")
        if closed:
            lines.append(f"  已平倉 {len(closed)} 筆: sumR {sum_r:+.2f} (勝 {wins}/{len(closed)})")
            # 最後平倉日期 — 令「冇新單」一眼睇得出係幾時開始
            last = max((o.get("closed_time") or o.get("seeded_time") or "")
                       for o in closed)
            if last:
                lines.append(f"  最後平倉: {last[:10]}")

        # gate 擋單 — 令「0 新單」可解釋 (用戶 09-27 問「still 25 筆??」正因冇呢個)
        try:
            import btc_gate_log
            gs = btc_gate_log.today_summary()
            if gs["total"]:
                lines.append(f"  {btc_gate_log.fmt_summary(gs)}")
                lines.append(f"     (最新: {btc_gate_log.latest_reason()})")
            elif not live:
                lines.append("  ✅ 今日冇 setup 產生 (未觸發 gate)")
        except Exception:
            pass
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ status report 失敗: {e}"


if __name__ == "__main__":
    main()
