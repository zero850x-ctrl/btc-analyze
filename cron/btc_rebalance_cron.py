#!/usr/bin/env python3
"""btc_rebalance_cron.py — BTC 60/40 再平衡月度檢查 (no_agent).

每月 1 號 09:00 HKT 跑。邏輯:
  - 季度月 (1/4/7/10) → 固定再平衡
  - 非季度月 → 只有偏離 > BAND (5%) 才做
  - 差額 < MIN_TRADE_USD ($50) → 唔做

輸出 = 推送訊息 (stdout 即 telegram)。
"""
import fcntl
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

HKT = timezone(timedelta(hours=8))
PY = "/opt/homebrew/bin/python3.11"
REPO = os.environ.get("BTC_REPO") or os.path.expanduser("~/repos/btc-analyze")
# 2026-10-01: 改返 "main"。
# 原本 pin "fix/btc-exit-symmetry" — 但該 branch 已落後 main 7 個 commit
# (缺 PR#7 phantom-sell 修復: binance_testnet_paper.py 少 168 行、
#  btc_auto_trade_cycle.py 少 202 行)。
# ⚠️ 而 REPO 係同 btc_weekend_cron.py **共用** 嘅同一個目錄。
#    呢個 cron 跑 08/12/16/20/22 點, weekend cron 跑 07-22 每 15 分。
#    repo 損壞時若由呢個 cron 先復原 → clone 落舊 branch 落共用 path →
#    weekend cron 照跑舊 code **靜默落單**。BRANCH_PIN 必須同 main 對齊。
BRANCH_PIN = os.environ.get("BTC_REPO_BRANCH", "main")
REQUIRED = ("btc_rebalance.py", "binance_testnet_paper.py")
GIT_URL = "https://github.com/zero850x-ctrl/btc-analyze.git"

# git 指令一律要 timeout — cron 冇人看, hang 住會令之後每個 tick 都塞車
# (2026-10-01 GLM review: 原本呢個檔嘅 git call 冇 timeout, 其他兩個有)
GIT_TIMEOUT = 30


class RepoUnavailable(RuntimeError):
    """復原失敗 → 唔可以保證 repo 係預期版本 → **必須停止落單** (fail-safe).

    2026-10-01 GLM review 捉到: 原本 checkout 失敗只係回傳 "❌ ..." 字串,
    但 main() 之後**照樣跑** btc_rebalance.py (真單)。改為 raise, 由 main()
    統一 fail-safe。
    """


# 跨 cron 互斥鎖 — REPO 同 btc_weekend_cron.py 共用, 兩邊都會觸發復原。
# 復原 = LOCK_EX (獨佔), 落單 = LOCK_SH (共享, 可並存但擋住復原)。
_LOCK_PATH = os.path.join(os.path.expanduser("~/.hermes/reports"),
                          f".repo_recover_{os.path.basename(REPO)}.lock")


@contextmanager
def _repo_lock(exclusive, timeout=300):
    """攞唔到鎖就 raise — 寧願唔跑, 都唔好喺狀態不明嘅 repo 上落單。"""
    os.makedirs(os.path.dirname(_LOCK_PATH), exist_ok=True)
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    f = open(_LOCK_PATH, "w")
    try:
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(f, mode | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    raise RepoUnavailable(
                        f"攞唔到 repo 鎖 ({_LOCK_PATH}, {'獨佔' if exclusive else '共享'}) "
                        f"超過 {timeout}s — 另一個 cron 可能做緊復原")
                time.sleep(1)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        finally:
            f.close()


def _git(*args):
    """git -C REPO … (唔用 cwd=REPO — REPO 唔存在時 cwd 會直接 FileNotFoundError)。"""
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True,
                          text=True, timeout=GIT_TIMEOUT)


def _healthy():
    """回傳 (ok, why)。branch 都要對 — 見 BRANCH_PIN 註解。"""
    if not os.path.isdir(os.path.join(REPO, ".git")):
        return False, "冇 .git"
    for f in REQUIRED:
        if not os.path.exists(os.path.join(REPO, f)):
            return False, f"缺 {f}"
    try:
        r = _git("rev-parse", "--verify", "HEAD")
    except subprocess.TimeoutExpired:
        return False, "git 指令 timeout"
    if r.returncode != 0:
        return False, "git HEAD 壞"
    # branch 驗證: 只睇 file + HEAD 會令「錯 branch」嘅 repo 被當健康
    # → 靜默跑錯版本。detached HEAD 時 --abbrev-ref 會成功回傳 "HEAD"。
    try:
        b = _git("rev-parse", "--abbrev-ref", "HEAD")
    except subprocess.TimeoutExpired:
        return False, "git 指令 timeout"
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
    try:
        return _git("rev-parse", "--verify", "HEAD").returncode == 0
    except subprocess.TimeoutExpired:
        return False


def ensure_repo():
    """確保 repo 可用, 回傳 note ("" = 冇做過嘢)。

    ⚠️ 復原失敗會 raise RepoUnavailable — caller **唔可以**當無事發生照跑。
    """
    ok, _ = _healthy()
    if ok:
        return ""
    with _repo_lock(exclusive=True):
        # 攞到鎖之後再驗一次: btc_weekend_cron 可能喺我哋等鎖期間已經修好
        ok, _ = _healthy()
        if ok:
            return ""
        return _recover()


def _recover():
    """實際復原。呼叫者必須已持有 _repo_lock(exclusive=True)。"""
    _, why0 = _healthy()      # 記住最初原因 — checkout 成功後 why 會變空
    try:
        _git("checkout", "--", ".")
    except subprocess.TimeoutExpired:
        pass
    ok, why = _healthy()
    if ok:
        return f"🔧 repo 唔完整 ({why0}) — 已 checkout 還原"
    # file/git 完好但 branch 唔對 → 直接 checkout 返預期 branch。
    # 唔可以跌落下面 re-clone: 會令共用 repo 變成錯版本之餘, 仲會刪走
    # local-only 檔案。呢條 path 係「branch 唔對」嘅正常修法。
    if _files_ok_and_head():
        r = _git("checkout", "-q", BRANCH_PIN)
        ok, why2 = _healthy()
        if ok:
            return f"🔧 repo branch 唔對 — 已 checkout {BRANCH_PIN}"
        # ⚠️ fail-safe: 唔可以喺已知 branch 唔對 (即跑緊錯版本) 嘅 repo 上落單
        raise RepoUnavailable(
            f"❌ branch 唔對 ({why}) 而且 checkout {BRANCH_PIN} 失敗: "
            f"{(r.stderr or r.stdout).strip()[:200]} — 為安全起見唔跑")
    stamp = datetime.now(HKT).strftime("%Y%m%d%H%M%S")
    broken = f"{REPO}.broken.{stamp}"
    if os.path.isdir(REPO):
        os.rename(REPO, broken)
    r = subprocess.run(["git", "clone", "-b", BRANCH_PIN, GIT_URL, REPO],
                       capture_output=True, text=True, timeout=180)
    ok, why = _healthy()
    if ok:
        return f"🔧 repo 損壞 — 已重新 clone ({BRANCH_PIN})，舊 copy 喺 {broken}"
    raise RepoUnavailable(
        f"❌ repo 無法恢復 ({why}); clone 錯誤: {(r.stderr or '').strip()[:200]}")


def main():
    try:
        note = ensure_repo()
    except RepoUnavailable as e:
        # fail-safe: repo 狀態唔明 → 唔跑落單, 但要出聲
        print(f"❌ 唔夠安全跑再平衡: {e}")
        return 1
    except Exception as e:
        print(f"❌ repo restore 失敗: {e}")
        return 1

    try:
        # 落單期間持共享鎖 — 擋住共用 REPO 嘅另一個 cron 做 checkout / re-clone
        with _repo_lock(exclusive=False, timeout=600):
            r = subprocess.run([PY, "btc_rebalance.py"], cwd=REPO,
                               capture_output=True, text=True, timeout=180)
    except RepoUnavailable as e:
        print(f"❌ 唔夠安全跑再平衡: {e}")
        return 1

    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        lines = ([note] if note else []) + [f"❌ 再平衡 crash (exit {r.returncode})"]
        if err:
            lines.append(err[-600:])
        print("\n".join(lines))
        return 1

    # 判斷有冇真正動作: "唔需要再平衡" / "唔做" = 冇動作。
    # ⚠️ 呢度靠 substring match btc_rebalance.py 嘅**業務輸出文字** —
    #    改咗 btc_rebalance.py 嘅字眼就要同步改呢度 (而家已入版控, 起碼睇得到)。
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
