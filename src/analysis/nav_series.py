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

采集器对不合法/缺失的累计净值写入 **0.0**（见 `collector.py`），
所以不能只用 `COALESCE(acc_nav, unit_nav)`。统一按行回退到 `unit_nav`：
实测 587 只基金中 545 只 acc_nav 完整、42 只仅有个位数坏行（多为早期数据），
逐行回退最多造成两天的轻微失真，不会整只基金退化。
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
