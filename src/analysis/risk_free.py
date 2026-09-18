"""全项目无风险利率的**单一真源**（批次 4.7）。

统一为年化小数口径：`0.02 = 2%`。

修复前四处各写各的：
- `backtest.compute_metrics` 默认 0.02（小数，正确）
- `vol_predictor._portfolio_metrics` 默认 0.02（小数，正确）
- `historical_recommender._score_from_tuples` 硬编码 **0.03**（与其他不一致）
- `fund_scorer.__init__` 默认 0.02

改利率只许改这一处；任何模块不得再自造 `rf = 0.0x` 字面量。
"""

RISK_FREE_ANNUAL = 0.02
