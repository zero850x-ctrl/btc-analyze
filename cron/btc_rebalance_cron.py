#!/usr/bin/env python3
"""btc_rebalance_cron.py — BTC 60/40 再平衡月度檢查 (no_agent).

每月 1 號 09:00 HKT 跑。邏輯:
  - 季度月 (1/4/7/10) → 固定再平衡
  - 非季度月 → 只有偏離 > BAND (5%) 才做
  - 差額 < MIN_TRADE_USD ($50) → 唔做

輸出 = 推送訊息 (stdout 即 telegram)。
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HKT = timezone(timedelta(hours=8))
PY = "/opt/homebrew/bin/python3.11"
REPO = os.environ.get("BTC_REPO") or os.path.expanduser("~/repos/btc-analyze")
# 2026-10-01: 改返 "main"。
# 原本 pin "fix/btc-exit-symmetry" — 但該 branch 已落後 main 7 個 commit
# (缺 PR#7 phantom-sell 修復: binance_testnet_paper.py 少 222 行、
#  btc_auto_trade_cycle.py 少 202 行)。
# ⚠️ 而 REPO 係同 btc_weekend_cron.py **共用** 嘅同一個目錄。
#    呢個 cron 跑 08/12/16/20/22 點, weekend cron 跑 07-22 每 15 分。
#    repo 損壞時若由呢個 cron 先復原 → clone 落舊 branch 落共用 path →
#    weekend cron 照跑舊 code **靜默落單**。BRANCH_PIN 必須同 main 對齊。
BRANCH_PIN = os.environ.get("BTC_REPO_BRANCH", "main")
REQUIRED = ("btc_rebalance.py", "binance_testnet_paper.py")


def _healthy():
    if not os.path.isdir(os.path.join(REPO, ".git")):
        return False, "冇 .git"
    for f in REQUIRED:
        if not os.path.exists(os.path.join(REPO, f)):
            return False, f"缺 {f}"
    r = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=REPO,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False, "git HEAD 壞"
    # 2026-10-01: branch 驗證 — 同 REPO 由多個 cron 共用, 只睇 file + HEAD
    # 會令「錯 branch」嘅 repo 被當健康 → 靜默跑錯版本。
    b = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO,
                       capture_output=True, text=True)
    if b.returncode != 0:
        return False, "攞唔到 branch"
    cur = b.stdout.strip()
    if cur != BRANCH_PIN:
        return False, f"branch 唔對 ({cur} ≠ {BRANCH_PIN})"
    return True, ""


def _files_ok_and_head():
    """file 就位 + HEAD 可解析 (branch 未必對) — 判斷可否用輕量 checkout 修 branch。"""
    if not os.path.isdir(os.path.join(REPO, ".git")):
        return False
    if not all(os.path.exists(os.path.join(REPO, f)) for f in REQUIRED):
        return False
    r = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=REPO,
                       capture_output=True, text=True)
    return r.returncode == 0


def ensure_repo():
    ok, why = _healthy()
    if ok:
        return ""
    r = subprocess.run(["git", "checkout", "--", "."], cwd=REPO,
                       capture_output=True, text=True)
    ok, why = _healthy()
    if ok:
        return f"🔧 repo 唔完整 ({why}) — 已 checkout 還原"
    # 2026-10-01: file/git 完好但 branch 唔對 → 直接 checkout 返預期 branch。
    # 唔可以跌落下面 re-clone: 會令共用 repo 變成錯版本之餘, 仲會刪走
    # local-only 檔案。呢條 path 係「branch 唔對」嘅正常修法。
    if _files_ok_and_head():
        r = subprocess.run(["git", "checkout", "-q", BRANCH_PIN], cwd=REPO,
                           capture_output=True, text=True)
        ok, why2 = _healthy()
        if ok:
            return f"🔧 repo branch 唔對 — 已 checkout {BRANCH_PIN}"
        return f"❌ branch 唔對 ({why}) 而且 checkout {BRANCH_PIN} 失敗: {r.stderr.strip()[:200]}"
    stamp = subprocess.run(["date", "+%Y%m%d%H%M%S"], capture_output=True,
                           text=True).stdout.strip()
    broken = f"{REPO}.broken.{stamp}"
    if os.path.isdir(REPO):
        subprocess.run(["mv", REPO, broken], capture_output=True)
    remote = subprocess.run(["git", "config", "--get", "remote.origin.url"],
                            cwd=os.path.dirname(REPO) or ".", capture_output=True, text=True)
    url = remote.stdout.strip() or "https://github.com/zero850x-ctrl/btc-analyze.git"
    r = subprocess.run(["git", "clone", "-b", BRANCH_PIN, url, REPO],
                       capture_output=True, text=True)
    ok, why = _healthy()
    if ok:
        return f"🔧 repo 損壞 — 已重新 clone ({BRANCH_PIN})，舊 copy 喺 {broken}"
    return f"❌ repo 無法恢復 ({why}); clone 錯誤: {r.stderr.strip()[:200]}"


def main():
    note = ensure_repo()
    if note.startswith("❌"):
        print(note)
        return 1
    r = subprocess.run([PY, "btc_rebalance.py"], cwd=REPO,
                       capture_output=True, text=True, timeout=180)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        lines = ([note] if note else []) + [f"❌ 再平衡 crash (exit {r.returncode})"]
        if err:
            lines.append(err[-600:])
        print("\n".join(lines))
        return 1

    # 判斷有冇真正動作: "唔需要再平衡" / "唔做" = 冇動作
    no_action = ("唔需要再平衡" in out) or ("唔做" in out) or ("< minNotional" in out)
    hour = datetime.now(HKT).hour
    is_daily_report = hour >= 22          # 22:00 場出日結

    lines = []
    if note:
        lines.append(note)
    if not no_action:
        # 有再平衡 → 一定出聲
        lines.append(out)
    elif is_daily_report:
        lines.append("⚖️ " + out.replace("\n", "\n⚖️ "))
    elif lines:
        pass                              # 只有 repo note, 唔出靜默報告
    if not lines:
        return 0                          # 靜默: 冇動作 + 唔係日結時間
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
