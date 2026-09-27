"""XIRR —— 现金流年化内部收益率（批次 3「统一评价口径」的地基）。

为什么必须有它
--------------
《外部参考整合方案与任务书》§暂不执行·批次 3 记下的核心问题：

    实测原型显示点位分档"**收益率 83.60%** vs 固定 **38.51%**"看似更优，
    但它是**少投钱**（¥21,000 vs ¥91,000）导致的，**绝对盈利反而更少**。
    → **不可用收益率直接比较**，必须先有 XIRR。

根因：`收益率 = 盈亏 / 投入成本` 是**没有时间维度的**指标。每月投 1000 连投 12 个月，
与一次性投 12000，两者分母都是 12000，但资金实际占用时间完全不同 —— 前者平均只占用半年。
XIRR 把每笔现金流按**实际持有天数**折现，得到的才是可比的口径。

口径
----
- 求 `r` 使 `Σ CFᵢ / (1+r)^(daysᵢ/365) = 0`（`daysᵢ` 自首笔现金流起算）
- **负 = 投入（现金流出）**，**正 = 收回（现金流入）**
- 返回**年化**小数（`0.10` = 年化 10%）
- 至少要有**一笔正、一笔负**，否则无解 → 返回 `None`（不编造）
- 一年按 **365** 天（不是 365.25，也不是交易日数 —— IR 是日历口径）

求解
----
牛顿法为主（快），失败则**二分法兜底**（稳）。不依赖 scipy。
"""
from __future__ import annotations

from datetime import date as _date

DAYS_PER_YEAR = 365.0
_BRACKET_LO, _BRACKET_HI = -0.9999, 10.0     # 二分区间：-99.99% ~ +1000%
_TOL = 1e-9
_MAX_ITER = 200


def _to_date(d):
    if isinstance(d, _date):
        return d
    return _date.fromisoformat(str(d)[:10])


def _npv(rate: float, flows: list, t0) -> float:
    return sum(a / (1.0 + rate) ** ((d - t0).days / DAYS_PER_YEAR) for d, a in flows)


def _npv_deriv(rate: float, flows: list, t0) -> float:
    """dNPV/dr。用于牛顿法。"""
    out = 0.0
    for d, a in flows:
        t = (d - t0).days / DAYS_PER_YEAR
        out += -t * a / (1.0 + rate) ** (t + 1.0)
    return out


def xirr(cashflows: list, guess: float = 0.1) -> float | None:
    """现金流 → 年化内部收益率（小数）。无解返回 `None`。

    Args:
        cashflows: `[(date_str | date, amount), ...]`，负=投入、正=收回；
                   金额为 0 的条目自动忽略。
        guess: 牛顿法初值。

    Returns:
        年化收益率小数（如 `0.0832` = 8.32%），或 `None`。
    """
    flows = [(_to_date(d), float(a)) for d, a in (cashflows or []) if a]
    if len(flows) < 2:
        return None
    has_neg = any(a < 0 for _, a in flows)
    has_pos = any(a > 0 for _, a in flows)
    if not (has_neg and has_pos):
        return None                     # 全同号 → 无 IRR（不猜）
    flows.sort(key=lambda x: x[0])
    t0 = flows[0][0]

    # ① 牛顿法
    r = float(guess)
    for _ in range(_MAX_ITER):
        try:
            f = _npv(r, flows, t0)
        except (OverflowError, ZeroDivisionError):
            break
        if abs(f) < 1e-6:
            return r
        try:
            df = _npv_deriv(r, flows, t0)
        except (OverflowError, ZeroDivisionError):
            break
        if df == 0 or df != df:         # 导数为 0 或 NaN
            break
        step = f / df
        r_new = r - step
        if r_new <= -1.0 or r_new != r_new:
            break
        if abs(r_new - r) < _TOL:
            return r_new
        r = r_new

    # ② 二分法兜底（牛顿不收敛时）
    lo, hi = _BRACKET_LO, _BRACKET_HI
    try:
        f_lo, f_hi = _npv(lo, flows, t0), _npv(hi, flows, t0)
    except (OverflowError, ZeroDivisionError):
        return None
    if f_lo * f_hi > 0:
        return None                     # 区间内无符号变化 → 无解
    for _ in range(_MAX_ITER):
        mid = (lo + hi) / 2.0
        try:
            f_mid = _npv(mid, flows, t0)
        except (OverflowError, ZeroDivisionError):
            return None
        if abs(f_mid) < 1e-6 or (hi - lo) < _TOL:
            return mid
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def money_weighted_note() -> str:
    """给报告用的一句话：为什么展示 XIRR 而不是收益率。"""
    return ("**XIRR（资金加权年化）**：每笔现金流按实际持有天数折现，"
            "因此**不同投入节奏的方案可以直接比较**；"
            "而「盈亏 ÷ 投入成本」没有时间维度 —— 定投方案会因资金平均占用时间短而被系统性低估。")
