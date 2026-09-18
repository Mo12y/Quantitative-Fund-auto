"""
时点隔离的**半开区间**约定 —— 集中一处，别在各模块自己写比较。

## 为什么要有这个模块

"某条数据算不算落在窗口里"这种事，散在各处自己写 `<=` 迟早出错。参考
`TradingAgents/dataflows/date_window.py` 的教训（它们为此写过三个回归测试）：

- **#1126 上界用闭区间 → 跨窗口**：窗口是**按天**给的（`end` 是个日期），
  但被比较的是**带时分秒**的时点。`t <= end` 会在 end 当天 00:00 之后立刻失效，
  把"end 当天下午发布的内容"错误地排除掉；反过来若把上界写成次日零点再用 `<=`，
  又会把"次日 00:00 整"这条不属于本窗口的记录放进来。
  → 正确写法：**下界闭、上界开**，上界取 `end + 1 天` 的零点。
- **#992 绕过过滤**：只要有第二条取数路径不走这个窗口函数，隔离就形同虚设。
- **#1007 全局新闻注入未来文章**：拿"当前时刻"的数据去回答历史时点的查询。

## 本项目的映射

本项目已有等价的业务规则（`trade_rules`）：15:00 切点、非交易日顺延、T+1 确认。
本模块**不改动**那些算术（铁律），只提供：

- `day_window(start, end)` / `in_window(t, start, end)`：把"按天的窗口"落成半开区间；
- `include_dateless(end, ...)`：**无日期**的条目该怎么办（回测排除、实时保留）；
- `pricing_day_window(day)`：某一天作为**定价日**时的窗口（单日，上界开到次日零点）。

定价日窗口单日的意义：成交价只取**生效日**那一天的净值，
所以"这笔用哪天的价"= 一个单日窗口 —— 这正是 `effective_apply_date`
比 `confirm_date` 更适合定价的原因（后者还叠了 15:00 切点与到账时点）。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Tuple

from .trade_rules import to_date

__all__ = [
    "day_window",
    "in_window",
    "include_dateless",
    "pricing_day_window",
    "LIVE_GRACE_DAYS",
]

# 实时窗口的宽限：`end` 距今天不超过这么多天时，视为"实时查询"。
# 用来决定**无日期条目**的取舍（见 include_dateless）。
LIVE_GRACE_DAYS = 1


def _midnight(d, tz) -> datetime:
    return datetime.combine(to_date(d), time.min, tzinfo=tz)


def _as_aware(t: datetime, tz) -> datetime:
    """把时点统一成带时区；naive 的按 `tz`（默认 UTC）解释。"""
    if t.tzinfo is None:
        return t.replace(tzinfo=tz or timezone.utc)
    return t


def day_window(start, end, tz=None) -> Tuple[datetime, datetime]:
    """返回覆盖 `[start, end]` **两个整天**的半开区间 `[start 00:00, end+1天 00:00)`。

    上界开是为了让"end 当天 23:59"落在窗口内，而"次日 00:00 整"落在窗口外。
    """
    tz = tz or timezone.utc
    lo = _midnight(start, tz)
    hi = _midnight(end, tz) + timedelta(days=1)
    if hi <= lo:
        raise ValueError(f"窗口为空: start={start} end={end}")
    return lo, hi


def in_window(t: datetime, start, end, tz=None) -> bool:
    """时点 `t` 是否落在 `[start, end]` 覆盖的整天窗口内。

    下界**闭**（start 当天 00:00 算在窗口内），上界**开**（end 次日 00:00 不算）。
    """
    lo, hi = day_window(start, end, tz)
    return lo <= _as_aware(t, tz) < hi


def pricing_day_window(day, tz=None) -> Tuple[datetime, datetime]:
    """某一天作为**定价日**时的窗口：单日 `[day 00:00, day+1天 00:00)`。

    成交价只取生效日那一天的净值，所以"这笔用哪天的价"就是一个单日窗口。
    """
    return day_window(day, day, tz)


def include_dateless(end, now: Optional[date] = None, tz=None) -> bool:
    """**无日期**的条目是否该纳入。

    - 回测窗口（`end` 明显早于今天）→ **排除**：无法证明它不是"未来的信息"，
      纳入等于把不知道时间的东西当成了历史数据。
    - 实时窗口（`end` 就是今天或最近）→ 保留：缺时间戳不影响它当下的可用性。

    与 TradingAgents 的 `date_window.py` 同口径：宁可少用一条，也不把未来混进历史。
    """
    e = to_date(end)
    n = to_date(now) if now is not None else date.today()
    return e >= n - timedelta(days=LIVE_GRACE_DAYS)
