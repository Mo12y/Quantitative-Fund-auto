"""
基金交易规则（公募基金通用）—— T+1 确认 / T+2 起算 / 交易日历。

规则（与需求第五项一致）:
- 交易日 15:00 前提交的申请，按当日净值确认，T+1 日起计算收益（确认日为申请日的下一个交易日）。
- 交易日 15:00 后提交的申请，顺延按下一交易日净值确认（确认日再顺延一个交易日）。
- 周末与法定节假日提交的申请，顺延至下一交易日处理；非交易日不确认净值。
  周末/节假日下单**语义上等于“上一个交易日收盘后下单”**（D3），因此不再叠加
  一次 15:00 顺延 —— `after_cutoff` 只在申请日本身是交易日时生效。

确认周期按基金类型分派（`confirm_lag_for`）：**QDII / 海外基金 T+2 确认**，
其余公募基金 T+1。确认周期只影响“份额到账日”与“收益起算日”，
**不影响成交价** —— 成交价一律取生效日（`effective_apply_date`）那天的净值。

本模块是**纯逻辑**，不依赖网络/数据库：交易日历由调用方注入（可为空 → 回退“跳过周末”）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Set, Tuple

CUTOFF_HOUR = 15          # 15:00 后提交顺延
_DATE_FMT = "%Y-%m-%d"

# 确认周期（交易日）—— 从**生效日**起，再数几个交易日拿到确认日。
# QDII 跨境结算慢一拍（招募说明书通行口径：T 日买入 → T+2 确认份额），
# 境内基金按 T+1。赎回侧另有更长的到账周期，本模块暂不区分（见 resolve_sell 注释）。
DEFAULT_CONFIRM_LAG = 1
QDII_CONFIRM_LAG = 2
_QDII_MARKERS = ("QDII", "海外")


def confirm_lag_for(fund_type=None, fund_name=None) -> int:
    """按基金类型分派确认周期（交易日）：QDII / 海外 → 2，其余 → 1。

    两条线索都看：`fund_type` 里带 “QDII”（如 “指数型-海外股票”）或
    `fund_name` 里带 “QDII”/“海外”（如 “南方纳斯达克100指数发起(QDII)C”）。
    """
    text = f"{fund_type or ''} {fund_name or ''}".upper()
    if any(m in text for m in _QDII_MARKERS):
        return QDII_CONFIRM_LAG
    return DEFAULT_CONFIRM_LAG


# --------------------------------------------------------------------------
# 基础日期工具
# --------------------------------------------------------------------------

def to_date(value) -> date:
    """把 str / date / datetime 统一成 date。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], _DATE_FMT).date()


def normalize_calendar(dates: Optional[Iterable]) -> Set[str]:
    """把交易日历规整成 {'YYYY-MM-DD', ...} 集合（忽略空值）。"""
    out: Set[str] = set()
    for d in dates or []:
        if not d:
            continue
        try:
            out.add(to_date(d).strftime(_DATE_FMT))
        except Exception:
            continue
    return out


def is_weekend(d) -> bool:
    return to_date(d).weekday() >= 5


def is_trade_day(d, calendar: Optional[Set[str]] = None) -> bool:
    """是否交易日。有日历就用日历；没有日历则回退“非周末即交易日”。"""
    dd = to_date(d)
    if calendar:
        s = dd.strftime(_DATE_FMT)
        if s in calendar:
            return True
        # 日历只覆盖“已发生且有数据”的区间：
        #  区间内不在集合 → 确实是节假日/停牌日 → 非交易日
        #  区间外（尤其今天/未来，净值尚未公布）→ 不能用过期日历否定，回退“跳过周末”
        try:
            lo, hi = min(calendar), max(calendar)
            if lo <= s <= hi:
                return False
        except Exception:
            pass
        return not is_weekend(dd)
    return not is_weekend(dd)


def next_trade_day(d, calendar: Optional[Set[str]] = None, include_self: bool = False) -> date:
    """返回 d 之后的第一个交易日（include_self=True 时 d 本身是交易日则返回 d）。"""
    dd = to_date(d)
    if include_self and is_trade_day(dd, calendar):
        return dd
    cur = dd + timedelta(days=1)
    # 最多向前找一年，避免脏日历导致死循环
    for _ in range(400):
        if is_trade_day(cur, calendar):
            return cur
        cur += timedelta(days=1)
    return cur


def prev_trade_day(d, calendar: Optional[Set[str]] = None, include_self: bool = False) -> date:
    """返回 d 之前的第一个交易日。"""
    dd = to_date(d)
    if include_self and is_trade_day(dd, calendar):
        return dd
    cur = dd - timedelta(days=1)
    for _ in range(400):
        if is_trade_day(cur, calendar):
            return cur
        cur -= timedelta(days=1)
    return cur


# --------------------------------------------------------------------------
# 申请 → 确认 → 起算
# --------------------------------------------------------------------------

def is_after_cutoff(when) -> bool:
    """提交时间是否在 15:00 之后（只看时分；date 类型视为 15:00 前）。"""
    if isinstance(when, datetime):
        return (when.hour, when.minute) >= (CUTOFF_HOUR, 0)
    return False


def effective_apply_date(apply_date, calendar: Optional[Set[str]] = None) -> str:
    """生效日 = **该笔按哪一天的净值定价**。

    - 申请日本身是交易日 → 就是申请日（15:00 前下单按当日净值成交）；
    - 申请日是非交易日（周末/节假日）→ 顺延到下一个交易日。

    注意：这与“确认日”不同。确认日 = 生效日的下一个交易日（份额到账/可查询的日子），
    而成交价用的是**生效日**的净值 —— 见本模块开头的规则说明。
    本函数只做这一件事，不改动任何已有的日期推算逻辑。
    """
    d = to_date(apply_date)
    effective = d if is_trade_day(d, calendar) else next_trade_day(d, calendar)
    return effective.strftime(_DATE_FMT)


def resolve_apply(
    apply_date,
    after_cutoff: bool = False,
    calendar: Optional[Set[str]] = None,
    confirm_lag: int = DEFAULT_CONFIRM_LAG,
) -> Tuple[str, str]:
    """
    计算 (确认日, 收益起算日)。

    - 非交易日的申请先顺延到下一个交易日作为“生效日”；
    - 15:00 前：确认日 = 生效日之后的第 `confirm_lag` 个交易日；
    - 15:00 后（**仅当申请日本身是交易日**）：再顺延 1 个交易日；
    - 收益起算日 = 确认日的下一个交易日。

    `confirm_lag` 由调用方按基金类型分派（见 `confirm_lag_for`）：QDII T+2、其余 T+1。
    本函数**不参与定价** —— 成交价一律用 `effective_apply_date`。
    """
    d = to_date(apply_date)
    on_trade_day = is_trade_day(d, calendar)
    effective = d if on_trade_day else next_trade_day(d, calendar)
    confirm = next_trade_day(effective, calendar)
    # 15:00 是**交易日专有**概念：非交易日没有"15:00 前/后"这回事，
    # 它的顺延本身已经表达了"上一交易日收盘后下单"的语义，不能再叠加一次。
    if after_cutoff and on_trade_day:
        confirm = next_trade_day(confirm, calendar)
    for _ in range(max(0, int(confirm_lag) - DEFAULT_CONFIRM_LAG)):
        confirm = next_trade_day(confirm, calendar)
    accrual_start = next_trade_day(confirm, calendar)
    return confirm.strftime(_DATE_FMT), accrual_start.strftime(_DATE_FMT)


def resolve_sell(
    apply_date,
    after_cutoff: bool = False,
    calendar: Optional[Set[str]] = None,
    confirm_lag: int = DEFAULT_CONFIRM_LAG,
) -> Tuple[str, str]:
    """
    卖出的 (确认日, 资金到账日)。
    确认规则与买入一致；公募基金赎回款一般确认后 1 个交易日内到账，
    这里取“确认日的下一个交易日”为到账日。

    `confirm_lag` 默认 1：**赎回侧暂不按基金类型分派** —— QDII 的实际赎回确认
    比申购更久（常见 T+3~T+10），但本项目尚无对应证据，且本次范围只到申购确认。
    参数已就位，等有权威口径再在调用方传入。
    """
    confirm, _ = resolve_apply(apply_date, after_cutoff, calendar, confirm_lag)
    payout = next_trade_day(confirm, calendar)
    return confirm, payout.strftime(_DATE_FMT)


def holding_status(confirm_date, today=None, calendar: Optional[Set[str]] = None) -> str:
    """买入确认日未到 → pending_confirm；已到 → holding。"""
    today = to_date(today) if today else date.today()
    return "holding" if to_date(confirm_date) <= today else "pending_confirm"


def count_trade_days(start, end, calendar: Optional[Set[str]] = None) -> int:
    """[start, end] 之间的交易日个数（用于定投期数推算）。"""
    s, e = to_date(start), to_date(end)
    if e < s:
        return 0
    n, cur = 0, s
    while cur <= e:
        if is_trade_day(cur, calendar):
            n += 1
        cur += timedelta(days=1)
    return n


def _add_months(d: date, n: int) -> date:
    """月份推进（处理月末溢出，如 1-31 + 1 月 → 2-28/29）"""
    y, m = d.year, d.month + n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    last = date(y, 12, 31) if m == 12 else (date(y, m + 1, 1) - timedelta(days=1))
    return date(y, m, min(d.day, last.day))


def period_dates(
    start,
    frequency: str,
    end=None,
    calendar: Optional[Set[str]] = None,
    max_periods: int = 3000,
) -> list:
    """
    推算定投期次日期（第 7 项）。

    - 按频率产生锚点：daily=每交易日 / weekly=每7天 / biweekly=每14天 / monthly=每月同日；
    - **不在非交易日扣款**：锚点落在周末/节假日时顺延到下一个交易日，并与前一期**去重**
      （避免周末两天塌缩成两期）；
    - 返回 [{'period_no': 1, 'date': 'YYYY-MM-DD'}, ...]（升序）。
    """
    s = to_date(start)
    e = to_date(end) if end else date.today()
    if e < s:
        return []
    out, cur, guard, last_added = [], s, 0, None
    while cur <= e and guard < max_periods:
        anchor = cur if is_trade_day(cur, calendar) else next_trade_day(cur, calendar)
        if anchor <= e and anchor != last_added:
            out.append({"period_no": len(out) + 1, "date": anchor.strftime(_DATE_FMT)})
            last_added = anchor
        if frequency == "daily":
            cur = cur + timedelta(days=1)
        elif frequency == "biweekly":
            cur = cur + timedelta(days=14)
        elif frequency == "monthly":
            cur = _add_months(cur, 1)
        else:                                   # weekly（默认）
            cur = cur + timedelta(days=7)
        guard += 1
    return out
