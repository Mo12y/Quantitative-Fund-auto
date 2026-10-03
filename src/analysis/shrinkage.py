"""经验贝叶斯收缩（Shrinkage）—— 把单只基金的估计值朝同类组均值收缩。

依据（`docs/审计修复记录.md` 第四批 §1 结论 A）
----------------------------------------------
用 **Lo (2002)** 的解析标准误（年化夏普）：`SE ≈ sqrt((1 + SR²/2) / T)`，T = 年数。

| T | SE | 95% 置信区间宽度 |
|---|---|---|
| 3 年 | **≈ 0.58** | **±1.13** |

**含义**：一只基金显示 3 年夏普 1.0，统计上**无法**与真值 0.0 区分。
而本项目正是用 **3 年夏普做同类分位排序** —— 等于**在噪音里排序**。
这与既有的 OOS 负结论（S1 − 等权 −0.09pp）互为印证：不是"策略没调好"，
而是**单期估计本身不携带排序信息**。

方法（James–Stein / 经验贝叶斯）
--------------------------------
模型：`观测 SRᵢ = 真值 θᵢ + 噪声 εᵢ`，`εᵢ ~ N(0, SEᵢ²)`，`θᵢ ~ N(μ, τ²)`。
后验均值即收缩估计：

    SRᵢ_shrunk = wᵢ · SRᵢ + (1 − wᵢ) · μ ,   wᵢ = τ² / (τ² + SEᵢ²)

`τ²`（组间**真实**方差）用矩估计：`τ² = max(0, Var(SR) − mean(SE²))`
—— 从观测方差里扣掉噪声方差，剩下的才是真信号。

⚠️ 局限（必须随结果一起声明，不能只报一个漂亮的收缩值）
-----------------------------------------------------
同类基金平均相关 ρ̄ = **0.61~0.70**（结论 B）。这让 `Var(SR)` 里混入**共同因子**的波动
→ `τ²` **偏乐观** → 权重 `w` 偏大 → **收缩偏弱**（比理论最优更靠近原始值）。
方向上这是**保守**的（不会过度收缩），但别把它当成"已最优"。

另外记一下审计里那次**自我作废**：`1 − SE²/SD²` 那个"信度比"式子
**假设各基金估计相互独立**，而 ρ̄ 高时该假设不成立 —— 所以本模块**不用**它，
只用适用范围明确的 Lo (2002) SE。
"""
from __future__ import annotations

import math

#: 哪些指标适用夏普型（比率类）标准误。收益/波动是"均值/标准差"型，不是同一套。
SHARPE_LIKE = ("sharpe",)


def lo_se(sr: float, years: float) -> float:
    """Lo (2002) 年化夏普的解析标准误：`sqrt((1 + SR²/2) / T)`。

    Args:
        sr: 年化夏普（如 1.0）
        years: 样本年数（如 3.0）

    Returns:
        标准误（如 3 年、SR=1 → ≈0.61）。`years <= 0` → `inf`（无信息）。
    """
    try:
        y = float(years)
        s = float(sr)
    except (TypeError, ValueError):
        return float("inf")
    if y <= 0 or not math.isfinite(y):
        return float("inf")
    v = (1.0 + s * s / 2.0) / y
    return math.sqrt(v) if v > 0 else float("inf")


def estimate_tau2(values: list, years: float) -> float:
    """组内**真实**方差的矩估计：`max(0, Var(观测) − mean(噪声方差))`。

    `Var` 用**样本方差**（ddof=1）。样本 < 2 → 0（无信息 → 完全收缩到均值）。
    """
    vals = [float(v) for v in (values or []) if v is not None and math.isfinite(float(v))]
    n = len(vals)
    if n < 2:
        return 0.0
    mu = sum(vals) / n
    var_obs = sum((v - mu) ** 2 for v in vals) / (n - 1)
    se2 = [lo_se(v, years) ** 2 for v in vals]
    se2 = [x for x in se2 if math.isfinite(x)]
    if not se2:
        return 0.0
    return max(0.0, var_obs - (sum(se2) / len(se2)))


def eb_shrink(value: float, mu: float, tau2: float, years: float) -> tuple:
    """把单个值朝组均值收缩。返回 `(shrunk, weight)`。

    `weight = τ²/(τ²+SE²)` —— 它会**随 |SR| 上升而下降**
    （Lo 的 SE 随 SR 增大：`1 + SR²/2`）：
    夏普越极端的基金，其估计越不可信 → 权重越低 → **收得越狠**。这是对的方向。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return (float(mu), 0.0)
    if not math.isfinite(v):
        return (float(mu), 0.0)
    se2 = lo_se(v, years) ** 2
    t2 = max(0.0, float(tau2))
    denom = t2 + se2
    w = (t2 / denom) if denom > 0 and math.isfinite(denom) else 0.0
    return (w * v + (1.0 - w) * float(mu), w)


def shrink_array(values: list, years: float) -> dict:
    """对一组同类基金的夏普做整体收缩（便利函数，供 `build_distributions` 用）。

    Returns:
        `{mu, tau2, shrunk:[...], weights:[...], mean_weight, n}`
        —— `mean_weight` 是**平均信度**，是"这组到底有没有排序信息"的直接读数：
        它接近 0 表示几乎所有横截面差异都是噪音（正是结论 A 的情形）。
    """
    vals = [float(v) for v in (values or []) if v is not None and math.isfinite(float(v))]
    n = len(vals)
    if n == 0:
        return {"mu": None, "tau2": 0.0, "shrunk": [], "weights": [],
                "mean_weight": None, "n": 0}
    mu = sum(vals) / n
    tau2 = estimate_tau2(vals, years)
    shrunk, weights = [], []
    for v in vals:
        s, w = eb_shrink(v, mu, tau2, years)
        shrunk.append(s)
        weights.append(w)
    return {"mu": mu, "tau2": tau2, "shrunk": shrunk, "weights": weights,
            "mean_weight": sum(weights) / n, "n": n}


def diagnosis(values: list, years: float) -> str:
    """一句话诊断（可直接贴进报告）：这组的横截面差异有多少是真信号。"""
    d = shrink_array(values, years)
    if not d["n"]:
        return "收缩诊断：无样本"
    if not d["mean_weight"]:
        return ("收缩诊断：平均信度 **0%%** —— 横截面差异**全部**是噪音，"
                "该指标不携带排序信息（T=%.1f 年，SE≈%.2f）" % (years, lo_se(1.0, years)))
    return ("收缩诊断：平均信度 **%.0f%%**（T=%.1f 年）："
            "排序时应按收缩后的值，而非原始点估计" % (d["mean_weight"] * 100, years))
