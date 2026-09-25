"""基金净值指标 —— **单一实现**（口径 SSOT）。

以前指标散在两个地方，且**口径不一致**（2026-09-25 审计发现）：
- `scripts/calibrate_thresholds.metrics()`：几何年化（按日期跨度）、`TRADING_DAYS=244`、
  回撤/动量按日期窗口 —— 但 annual_return / ann_vol / sharpe 用**全历史**；
- `src/analysis/fund_scorer._check_sharpe()`：算术均值×**252**、`sqrt(252)`、全历史。
  → 同一只基金在两处算出**不同的夏普**，且用了被项目自己否掉的 252。

本模块统一三件事（全部有行业依据，见 `docs/参照系接入执行报告.md` §10）：

1. **评估窗口固定为 3 年**（不是"基金全历史"）。
   依据：晨星（全球基金评级标准）要求**满 36 个月**才予评级，按 **3/5/10 年**固定窗口算；
   天天基金「特色数据」也只给 **近1年/近2年/近3年** 固定窗口。
   **没有一家用全历史** —— 因为只有固定窗口，不同年龄的基金才可比。
   本项目 3 年是同时对齐：晨星的最小评级窗 + 天天基金的最长展示窗 + 既有的 756 天门槛。

2. **按日期窗口切**，不按点数。净值序列**有缺口**（实测 135/576 只点数明显偏少，
   最严重的比值仅 0.437），用点数当年数会把年化**严重高估**（最坏 ~2.3 倍）。

3. **年化用几何（日期跨度）**、年化波动用 `TRADING_DAYS`（=244，项目实测校准值；
   用 252 会把年化波动高估 3.07%）。
"""
from __future__ import annotations

from datetime import date as _date, timedelta as _timedelta

import numpy as np

#: 评估窗口（年）。3 年 = 晨星最小评级窗 = 天天基金最长展示窗。见模块 docstring。
WINDOW_YEARS = 3

#: 年化交易日数。**必须与 calibrate_thresholds.TRADING_DAYS 一致**（244，非 252）。
TRADING_DAYS = 244

#: 计算所需的最少点数
MIN_POINTS = 60


def clip_window(vals, dates=None, years: float = WINDOW_YEARS):
    """把序列截到**末尾 N 年**（按日期）。返回 (vals, dates)。

    · dates 可用 → 按日期切（**推荐**，序列有缺口时唯一正确做法）；
    · dates 缺失 → 退回点数窗口 `years * TRADING_DAYS`，并在调用方需知悉这是近似。
    """
    v = np.asarray(vals, dtype=float)
    if v.size == 0:
        return v, dates
    if dates is None or len(dates) != v.size:
        k = int(round(years * TRADING_DAYS))
        return (v[-k:], None) if k < v.size else (v, dates)
    try:
        cut = (_date.fromisoformat(str(dates[-1])) - _timedelta(days=int(round(years * 365.25)))).isoformat()
    except Exception:
        k = int(round(years * TRADING_DAYS))
        return (v[-k:], dates[-k:]) if k < v.size else (v, dates)
    import bisect
    i0 = bisect.bisect_left(list(dates), cut)
    return v[i0:], dates[i0:]


def compute(vals, dates=None, risk_free: float = 0.02, years: float = WINDOW_YEARS):
    """单只基金的窗口化指标。返回 dict；点数不足返回 None。

    keys: annual_return(%) / ann_vol(%) / max_drawdown_win(%) / max_drawdown_1y(%) /
          max_drawdown_all(%) / momentum_3m(%) / sharpe / window_points / window_years

    ⚠️ `max_drawdown_all` 是**全历史**回撤（保留作参考，**不参与同类比较**）。
    """
    v, d = clip_window(vals, dates, years)
    n = v.size
    if n < MIN_POINTS:
        return None
    v = np.asarray(v, dtype=float)

    # ---- 年化收益：几何，按日期跨度 ----
    if d is not None and len(d) == n:
        try:
            span = (_date.fromisoformat(str(d[-1])) - _date.fromisoformat(str(d[0]))).days / 365.25
        except Exception:
            span = n / TRADING_DAYS
    else:
        span = n / TRADING_DAYS
    span = max(span, 1e-6)
    total = v[-1] / v[0] - 1
    ann_ret = (1 + total) ** (1.0 / span) - 1

    # ---- 年化波动：TRADING_DAYS（244），不是 252 ----
    daily = np.diff(v) / v[:-1]
    daily = daily[np.isfinite(daily)]
    ann_vol = float(daily.std(ddof=1) * np.sqrt(TRADING_DAYS)) if daily.size > 1 else np.nan

    sharpe = float((ann_ret - risk_free) / ann_vol) if ann_vol and ann_vol > 0 and np.isfinite(ann_vol) else np.nan

    # ---- 窗口内最大回撤 ----
    peak = np.maximum.accumulate(v)
    mdd_win = float(((peak - v) / peak).max() * 100)

    # ---- 近 1 年回撤（日期窗口）----
    v1, _ = clip_window(vals, dates, 1.0)
    pk1 = np.maximum.accumulate(v1)
    mdd1y = float(((pk1 - v1) / pk1).max() * 100) if v1.size else np.nan

    # ---- 全历史回撤（仅供展示，不参与同类比较）----
    allv = np.asarray(vals, dtype=float)
    pka = np.maximum.accumulate(allv)
    mdd_all = float(((pka - allv) / pka).max() * 100) if allv.size else np.nan

    # ---- 近 3 月动量（日期窗口）----
    v3, _ = clip_window(vals, dates, 0.25)
    mom3m = float((allv[-1] / v3[0] - 1) * 100) if v3.size > 1 and v3[0] > 0 else np.nan

    return {
        "annual_return": ann_ret * 100,
        "ann_vol": ann_vol * 100,
        "max_drawdown_1y": mdd1y,
        "max_drawdown_win": mdd_win,
        "max_drawdown_all": mdd_all,
        "momentum_3m": mom3m,
        "sharpe": sharpe,
        "window_points": int(n),
        "window_years": years,
    }
