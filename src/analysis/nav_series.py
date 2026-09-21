"""
净值的**两种用途、两种列** —— 集中一处，别在每个模块自己挑列。

## 规则

| 用途 | 列 | 例子 |
|---|---|---|
| **估值 / 收益序列**（收益、波动、回撤、动量、夏普、筛选打分、回测） | **`acc_nav` 累计净值** | `backtest`、`fund_scorer`、`historical_recommender` |
| **成交定价**（成交价、确认净值、份额折算） | **`unit_nav` 单位净值** | `portfolio._get_nav_exact` |

## 为什么

分红除息日，`unit_nav` 向下跳、`acc_nav` 不跳（累计净值把分红加了回去）。
拿 `unit_nav` 算收益，等于把"基金把一部分净值还给你"当成一次**真实下跌**：

- 回撤被凭空放大 → 质量池把好基金误判为高风险；
- 动量被压低 → 筛选排序失真；
- 回测年化被系统性低估。

本库实测：587 只有净值的基金里 **244 只历史上分过红**（债券/红利型最常见），
所以这不是理论风险。

反过来，**成交价只能用 `unit_nav`** —— 那才是当天真正买卖的价格。
`acc_nav` 是人为构造的复权序列，用它定价会买到一个不存在的价格。

## acc_nav 缺失怎么办

采集器对不合法/缺失的累计净值曾写入 **0.0**（见 `collector.py`，2026-09-21 起改为写 None），
所以不能只用 `COALESCE(acc_nav, unit_nav)` —— 0.0 不是 NULL，COALESCE 会原样返回它，
下游若再 `WHERE ... > 0` 就会**整行丢弃**而非回退。统一按行回退到 `unit_nav`。

**口径实测（2026-09-21，全量采集后重测，n=24,061 只 / 22,905,610 行）**：
`acc_nav = 0.0` 的哨兵行 124 行 / 30 只（0.0005%），`acc_nav IS NULL` 13,959 行 / 110 只
（其中分级基金 161xxx/502xxx 占绝大多数，是源头本身缺累计净值）。
两者合计 < 0.07% 的行、< 0.5% 的基金，逐行回退最多造成单只基金两三天的轻微失真，
不会整只基金退化。

> ⚠️ 早先此处写的是「实测 587 只基金中 545 只完整、42 只仅有个位数坏行」——
> 那是**迁移期的旧样本口径**（587 只深历史幸存基金）。全量采集后样本换成 24,061 只，
> 该数字已作废，按规则 11（地基再验证）重测为上面的数值。
"""
from __future__ import annotations

# 原始 SQL 用：acc_nav 有效则取之，否则回退 unit_nav。
# 需要表别名时传 alias（如 valuation_nav_sql("f") → "f.acc_nav"）。
VALUATION_NAV_SQL = "CASE WHEN acc_nav IS NOT NULL AND acc_nav > 0 THEN acc_nav ELSE unit_nav END"


def valuation_nav_sql(alias: str = "") -> str:
    """带表别名的估值净值 SQL 片段。"""
    p = f"{alias.strip().rstrip('.')}." if alias else ""
    return (f"CASE WHEN {p}acc_nav IS NOT NULL AND {p}acc_nav > 0 "
            f"THEN {p}acc_nav ELSE {p}unit_nav END")


def valuation_nav(unit, acc):
    """单点取值（标量）：`acc` 有效就用 `acc`，否则回退 `unit`。"""
    try:
        a = float(acc)
        if a > 0:
            return a
    except (TypeError, ValueError):
        pass
    try:
        return float(unit)
    except (TypeError, ValueError):
        return 0.0


def valuation_nav_series(df):
    """pandas：从含 `unit_nav`/`acc_nav` 两列的 DataFrame 得到估值净值 Series。"""
    import pandas as pd
    unit = pd.to_numeric(df["unit_nav"], errors="coerce")
    if "acc_nav" not in df.columns:
        return unit
    acc = pd.to_numeric(df["acc_nav"], errors="coerce")
    return acc.where(acc > 0, unit)
