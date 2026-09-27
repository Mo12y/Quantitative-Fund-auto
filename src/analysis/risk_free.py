"""全项目无风险利率的**单一真源**（批次 4.7 / 2026-09-27 取值更新）。

统一为年化小数口径：`0.0168 = 1.68%`。

取值依据（2026-09-27 实测）
---------------------------
中国 10 年期国债收益率（`akshare.bond_zh_us_rate()` 的「中国国债收益率10年」列）：

    2026-09-22  1.6791%
    2026-09-23  1.6807%
    2026-09-24  1.6738%   ← 取此值，四舍五入为 0.0168

此前写死 `0.02`（2.0%），**偏高约 0.32pp**。同类开源项目（`sososun/mutual-fund-skills`）
取 1.8%（其 2025-02 的 10Y 国债值）—— 说明大家都用"当期 10Y 国债"，只是取值时点不同。
本值按**最近可得的实测值**更新。

**更新方式**（改这一处即可）：
```python
import akshare as ak
df = ak.bond_zh_us_rate(start_date="2026-09-01")
print(df[["日期", "中国国债收益率10年"]].dropna().tail(3))
```
⚠️ 改本值会让所有**夏普比率**变化 → 参照系缓存需重算：
`python -c "from src.analysis import peer_percentile as p; p.save_cache(p.build_distributions())"`

历史沿革
--------
修复前四处各写各的：
- `backtest.compute_metrics` 默认 0.02（小数，正确）
- `vol_predictor._portfolio_metrics` 默认 0.02（小数，正确）
- `historical_recommender._score_from_tuples` 硬编码 **0.03**（与其他不一致）
- `fund_scorer.__init__` 默认 0.02

改利率只许改这一处；任何模块不得再自造 `rf = 0.0x` 字面量
（`nav_metrics.compute` 的默认值也从这里取，不再自己写 0.02）。
"""

# 中国 10 年期国债收益率（2026-09-24 实测 1.6738%）
RISK_FREE_ANNUAL = 0.0168
