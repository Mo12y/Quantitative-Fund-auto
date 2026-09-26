"""组合重叠度（相关性级）—— 回答「我的分散是不是假的」（设计稿 §4.4 / 数据源计划书 阶段 3 的替代实现）。

为什么是"相关性级"而不是"成分股穿透"（2026-09-26 只读实测，见 `.workbuddy/memory/2026-09-26.md`）：

| 档位 | 实测结果（用户 7 只持仓） |
|:--|:--|
| 标的级（跟踪标的是否同一个） | **0 检出** —— 7 只分别跟踪 中证500 / 无 / 纳斯达克100 / 科创芯片 / 上海金 / 富时亚太 / 创业板AI |
| **相关性级（近 1 年周收益）** | **5/21 对 r ≥ 0.60**，核心簇 `信澳业绩驱动 ~ 创业板AI = 0.88`、`科创芯片 ~ 中证500 = 0.79` |

也就是说：**主题词层面看着分散（芯片 / AI / 中证500 / 主动混合），"会不会一起跌"这一维并不分散** ——
而板块关键词法看不见这一层。成分股穿透（联接基金→ETF→成分股）成本高、且季报滞后 1–3 个月，
对"全是指数/联接"的持仓与相关性级结论基本等价，故本模块选相关性级。

口径（每一条都可追溯）
--------------------
- 净值列走 SSOT `nav_series.VALUATION_NAV_SQL`（累计净值优先、逐行回退）——**不自己挑列**
- 周频：每自然周取**最后一个**净值点；周收益 = 相邻周比值
- 窗口默认 **400 天（≈56 周）**；少于 `min_weeks=20` 周 → **不产出结果**（由调用方声明"未评估"）
- 相关性 = Pearson r（周收益序列，取两序列等长的尾部）

⚠️ **阈值是政策、不是口径**：本模块只算 r；"r ≥ 多少算重叠"由
`user_constraint` 的约束参数决定（画像键 `corr_max_r`，默认不启用）。
该阈值属**统计判断值**（本项目吃过"拍脑袋阈值"的亏），启用时必须在 UI/文档里标明性质。
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from .nav_series import VALUATION_NAV_SQL

DEFAULT_WINDOW_DAYS = 400      # ≈1 年（56 周）
DEFAULT_MIN_WEEKS = 20


def weekly_returns(conn, codes, since: str = None) -> dict:
    """批量取**周收益**序列 → `{code: np.ndarray}`（一次查询，避免逐只查库）。

    codes 为空/无净值 → 该 code 不出现在结果里（调用方按"无数据"处理，不猜）。
    """
    codes = [str(c) for c in (codes or []) if c]
    if not codes:
        return {}
    since = since or (date.today() - timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat()
    ph = ",".join("?" * len(codes))
    rows = conn.execute(
        "SELECT fund_code, nav_date, %s AS v FROM fund_nav "
        "WHERE fund_code IN (%s) AND nav_date >= ? ORDER BY fund_code, nav_date" % (VALUATION_NAV_SQL, ph),
        list(codes) + [since]).fetchall()

    weekly = {}
    for code, d, v in rows:                      # 已按 code, date 排序 → 同周后写覆盖前写=取最后一点
        if v is None:
            continue
        try:
            y, w, _ = date.fromisoformat(str(d)[:10]).isocalendar()
        except ValueError:
            continue
        weekly.setdefault(str(code), {})[(y, w)] = float(v)

    out = {}
    for code, wk in weekly.items():
        ks = sorted(wk)
        vals = np.array([wk[k] for k in ks], dtype=float)
        if len(vals) < 3:
            continue
        rets = np.diff(vals) / vals[:-1]
        rets = rets[np.isfinite(rets)]
        if rets.size:
            out[code] = rets
    return out


def overlap_map(conn, candidate_codes, held_codes, since: str = None,
                min_weeks: int = DEFAULT_MIN_WEEKS) -> dict:
    """候选 × 持仓的**最大相关性** → `{candidate: {"max_r": float, "against": str, "n": int}}`。

    - 只包含**两者都有足够周数（≥ min_weeks）**的候选；其余不出现（调用方声明"未评估"）。
    - 自己与自己（候选就是已持有的那只）**不参与**：那种情况由 `holding_overlap` 的
      "已持有"判定处理，这里重复算 r=1.00 只会产生误导性的理由。
    - `against` = 相关性最高的那只持仓代码；`n` = 参与计算的周数（可解释性要用）。
    """
    held = [str(c) for c in (held_codes or []) if c]
    cands = [str(c) for c in (candidate_codes or []) if c]
    if not held or not cands:
        return {}
    series = weekly_returns(conn, sorted(set(held) | set(cands)), since)
    out = {}
    for c in cands:
        rc = series.get(c)
        if rc is None or rc.size < min_weeks:
            continue
        best_r, best_code, best_n = None, None, None
        for h in held:
            if h == c:
                continue
            rh = series.get(h)
            if rh is None or rh.size < min_weeks:
                continue
            n = min(rc.size, rh.size)
            if n < min_weeks:
                continue
            r = float(np.corrcoef(rc[-n:], rh[-n:])[0, 1])
            if not np.isfinite(r):
                continue
            if best_r is None or r > best_r:
                best_r, best_code, best_n = r, h, n
        if best_r is not None:
            out[c] = {"max_r": round(best_r, 4), "against": best_code, "n": int(best_n)}
    return out