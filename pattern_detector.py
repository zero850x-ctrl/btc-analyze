#!/usr/bin/env python3
"""pattern_detector.py — 平行通道 + 雙頂/雙底 形態偵測.

引擎只出過 1 個 Channel (pool 2394 之中) → 平行通道要靠呢個獨立 detector。
雙頂/雙底引擎有出, 但呢度用**自己嘅定義**重測 (唔受引擎 entry 質素影響)。

⚠️ look-ahead 規則 (最重要):
  swing point 喺 j 確認需要 j+lookback 根 bar。所有信號只可以用 index <= i 嘅資訊,
  進場一律用 i+1 嘅 open。函數回傳嘅 signal index 就係「確認 bar」i。

函數:
  find_swings(H,L,lookback)                  → 已確認 swing 點
  double_top_bottom(...)                     → 雙頂/雙底 (頸線突破確認)
  parallel_channel(...)                      → 每根 bar 嘅通道狀態 (上下軌 + 斜率)
  channel_signals(ch, breakout/meanrev)      → 通道突破 / 邊界反轉信號
  combo_dtb_at_channel(...)                  → 雙頂/底 出現喺通道邊界 (confluence)
"""
import numpy as np


# ── swing 偵測 ──────────────────────────────────────────────────────────
def find_swings(H, L, lookback=3):
    """回傳 (highs, lows) — 各為 list of (idx, price).

    swing high at j ⟺ H[j] 係 H[j-lb .. j+lb] 嘅嚴格最大值。
    ⚠️ 只有當 bar index >= j+lookback 時才知道 → 呼叫者要自己檢查 confirmed。
    """
    n = len(H)
    highs, lows = [], []
    for j in range(lookback, n - lookback):
        w = H[j - lookback: j + lookback + 1]
        if H[j] == w.max() and (w == H[j]).sum() == 1:      # 嚴格 (避免平台重複)
            highs.append((j, float(H[j])))
        w2 = L[j - lookback: j + lookback + 1]
        if L[j] == w2.min() and (w2 == L[j]).sum() == 1:
            lows.append((j, float(L[j])))
    return highs, lows


# ── 雙頂 / 雙底 ─────────────────────────────────────────────────────────
def double_top_bottom(H, L, C, A, lookback=3, tol_atr=0.5, depth_atr=1.0,
                      min_gap=5, max_gap=80, swings=None):
    """雙頂 (SELL) / 雙底 (BUY) — 頸線突破確認.

    條件:
      1. 兩個同類 swing (頂/底), 價差 <= tol_atr × ATR
      2. 中間反向 swing 深度 >= depth_atr × ATR
      3. 兩點相距 min_gap..max_gap 根 bar
      4. 確認 = 收市穿頸線 (中間嗰個 trough/peak)

    回傳 list of dict(idx=確認bar, side, neckline, p1, p2, kind)
    """
    n = len(H)
    if swings is None:
        swings = find_swings(H, L, lookback)
    sh, sl = swings
    out = []

    def _scan(pts, is_top):
        for a in range(len(pts)):
            j1, p1 = pts[a]
            for b in range(a + 1, len(pts)):
                j2, p2 = pts[b]
                gap = j2 - j1
                if gap < min_gap:
                    continue
                if gap > max_gap:
                    break
                atr = A[j2]
                if not np.isfinite(atr) or atr <= 0:
                    continue
                if abs(p1 - p2) > tol_atr * atr:
                    continue
                if is_top:
                    mid = float(L[j1:j2 + 1].min())
                    depth = min(p1, p2) - mid
                else:
                    mid = float(H[j1:j2 + 1].max())
                    depth = mid - max(p1, p2)
                if depth < depth_atr * atr:
                    continue
                # 確認: 由 j2+lookback 起, 首根收市穿頸線
                start = j2 + lookback
                for i in range(start, min(start + max_gap, n)):
                    if is_top and C[i] < mid:
                        out.append({"idx": i, "side": "SELL", "neckline": mid,
                                    "p1": p1, "p2": p2, "kind": "double_top",
                                    "j1": j1, "j2": j2})
                        break
                    if (not is_top) and C[i] > mid:
                        out.append({"idx": i, "side": "BUY", "neckline": mid,
                                    "p1": p1, "p2": p2, "kind": "double_bottom",
                                    "j1": j1, "j2": j2})
                        break

    _scan(sh, True)
    _scan(sl, False)
    out.sort(key=lambda x: x["idx"])
    return out


# ── 平行通道 ────────────────────────────────────────────────────────────
def parallel_channel(H, L, A, win=150, lookback=3, min_pts=2,
                     slope_tol_atr=0.05, width_min_atr=0.8, r2_min=0.70,
                     resid_max_atr=1.5, swings=None):
    """每根 bar 回傳通道狀態 or None.

    做法: 窗口內已確認嘅 swing highs 擬合上軌, swing lows 擬合下軌。
    要求:
      1. 每條線 R² >= r2_min (swing 點真係成一直線)
      2. swing 點到線嘅平均殘差 <= resid_max_atr × ATR
      3. 兩條線斜率相差 <= slope_tol_atr × ATR/bar (真正平行)
      4. 通道闊度 >= width_min_atr × ATR

    ⚠️ slope_tol_atr 唔可以太鬆: 0.25×ATR/bar (≈75 點/bar, 150 bar = 11% 走勢)
    之下 99.4% 嘅 bar 都會「通過」→ 平行條件形同虛設。0.05 才真正篩到。

    回傳 list (index 對齊 bar): None 或 dict(slope, up, lo, width, n_up, n_dn, r2)
    """
    n = len(H)
    if swings is None:
        swings = find_swings(H, L, lookback)
    sh, sl = swings
    sh_i = np.array([p[0] for p in sh] if sh else [], dtype=int)
    sh_v = np.array([p[1] for p in sh] if sh else [], dtype=float)
    sl_i = np.array([p[0] for p in sl] if sl else [], dtype=int)
    sl_v = np.array([p[1] for p in sl] if sl else [], dtype=float)

    def _fit(x, y):
        """回傳 (slope, intercept, r2, mean_abs_resid) — 唔夠點/常數 x 回 None."""
        if len(x) < 2 or len(np.unique(x)) < 2:
            return None
        a, b = np.polyfit(x, y, 1)
        pred = a * x + b
        ss_res = float(((y - pred) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return a, b, r2, float(np.abs(y - pred).mean())

    out = [None] * n
    # 用 searchsorted 取窗口 (swing index 本身已排序) — 比每 bar 布林遮罩快一個數量級
    # (69k bars × 2.5k swings 嘅遮罩 = 3.5億 ops, 會拖到幾分鐘)
    for i in range(win, n):
        lo_i, hi_i = i - win, i - lookback        # 只用已確認 swing
        a0 = int(np.searchsorted(sh_i, lo_i, "left"))
        a1 = int(np.searchsorted(sh_i, hi_i, "right"))
        b0 = int(np.searchsorted(sl_i, lo_i, "left"))
        b1 = int(np.searchsorted(sl_i, hi_i, "right"))
        if a1 - a0 < min_pts or b1 - b0 < min_pts:
            continue
        atr = A[i]
        if not np.isfinite(atr) or atr <= 0:
            continue
        xi, yi = sh_i[a0:a1], sh_v[a0:a1]
        xj, yj = sl_i[b0:b1], sl_v[b0:b1]
        f1 = _fit(xi, yi)
        f2 = _fit(xj, yj)
        if f1 is None or f2 is None:
            continue
        sa1, _, r2_up, res_up = f1
        sa2, _, r2_dn, res_dn = f2
        # 1+2: 線性擬合質素
        if r2_up < r2_min or r2_dn < r2_min:
            continue
        if res_up > resid_max_atr * atr or res_dn > resid_max_atr * atr:
            continue
        # 3: 平行
        if abs(sa1 - sa2) > slope_tol_atr * atr:
            continue
        a = (sa1 + sa2) / 2.0                     # 共同斜率
        b_up = yi.mean() - a * xi.mean()
        b_dn = yj.mean() - a * xj.mean()
        up = a * i + b_up
        dn = a * i + b_dn
        if up <= dn:
            continue
        if up - dn < width_min_atr * atr:         # 4: 唔可以太窄
            continue
        out[i] = {"slope": float(a), "up": float(up), "lo": float(dn),
                  "width": float(up - dn), "n_up": int(len(xi)), "n_dn": int(len(xj)),
                  "r2_up": float(r2_up), "r2_dn": float(r2_dn),
                  "res_up": float(res_up), "res_dn": float(res_dn),
                  "atr": float(atr)}
    return out


def channel_signals(ch, H, L, C, A, mode="breakout", k_atr=0.25):
    """通道信號.

    breakout: 收市穿軌 + k×ATR 緩衝 → 順方向
    meanrev:  bar 內觸軌但收市返回軌內 (拒絕) → 反方向
    """
    n = len(C)
    out = []
    for i in range(n):
        c = ch[i]
        if c is None:
            continue
        a = A[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if mode == "breakout":
            if C[i] > c["up"] + k_atr * a:
                out.append({"idx": i, "side": "BUY", "up": c["up"], "lo": c["lo"],
                            "slope": c["slope"]})
            elif C[i] < c["lo"] - k_atr * a:
                out.append({"idx": i, "side": "SELL", "up": c["up"], "lo": c["lo"],
                            "slope": c["slope"]})
        elif mode == "meanrev":
            if H[i] >= c["up"] and C[i] < c["up"]:
                out.append({"idx": i, "side": "SELL", "up": c["up"], "lo": c["lo"],
                            "slope": c["slope"]})
            elif L[i] <= c["lo"] and C[i] > c["lo"]:
                out.append({"idx": i, "side": "BUY", "up": c["up"], "lo": c["lo"],
                            "slope": c["slope"]})
    return out


def combo_dtb_at_channel(H, L, C, A, ch, lookback=3, near_atr=1.0, tol_atr=0.5,
                         depth_atr=1.0, min_gap=5, max_gap=80, dtbs=None):
    """雙頂/雙底 出現喺通道邊界 (confluence) — 用戶要求嘅組合.

    雙頂 + 第二個頂接近**上軌** → SELL (阻力匯合)
    雙底 + 第二個底接近**下軌** → BUY (支持匯合)
    """
    if dtbs is None:
        dtbs = double_top_bottom(H, L, C, A, lookback=lookback, tol_atr=tol_atr,
                                 depth_atr=depth_atr, min_gap=min_gap, max_gap=max_gap)
    out = []
    for sig in dtbs:
        i, j2 = sig["idx"], sig["j2"]
        c = ch[j2]                      # 用形態完成嗰刻嘅通道
        if c is None:
            continue
        a = A[j2]
        if not np.isfinite(a) or a <= 0:
            continue
        if sig["side"] == "SELL":
            # 第二個頂要貼近上軌
            if abs(sig["p2"] - c["up"]) <= near_atr * a:
                out.append({**sig, "up": c["up"], "lo": c["lo"]})
        else:
            if abs(sig["p2"] - c["lo"]) <= near_atr * a:
                out.append({**sig, "up": c["up"], "lo": c["lo"]})
    return out


# ── self-test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import pandas as pd
    df = pd.read_csv("~/.hermes/reports/btc_bars_30m.csv".replace("~", __import__("os").path.expanduser("~")))
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    A = tr.rolling(14).mean().values
    print(f"bars {len(H)}")
    sw = find_swings(H, L, 3)
    print(f"swings: highs {len(sw[0])}, lows {len(sw[1])}")
    dtb = double_top_bottom(H, L, C, A, swings=sw)
    import collections
    print(f"double top/bottom signals: {len(dtb)}  "
          f"{collections.Counter(x['kind'] for x in dtb)}")
    ch = parallel_channel(H, L, A, win=150, swings=sw)
    valid = sum(1 for x in ch if x)
    print(f"channel valid bars: {valid}/{len(H)} ({valid / len(H) * 100:.1f}%)")
    for mode in ("breakout", "meanrev"):
        s = channel_signals(ch, H, L, C, A, mode=mode)
        print(f"channel {mode} signals: {len(s)}")
    cmb = combo_dtb_at_channel(H, L, C, A, ch)
    print(f"combo (DTB @ channel boundary): {len(cmb)}")
