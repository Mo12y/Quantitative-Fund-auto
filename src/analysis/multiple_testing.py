"""多重检验的**有效 N** 修正（M3）。

依据（`docs/审计修复记录.md` 第四批 §2 推论 2）
---------------------------------------------
> **多重检验的分母该用有效 N**：本项目 BH 校正用的是**名义 N**。
> 债券组真实独立检验数只有 **47%**（1314 / 2785）。
>
> ⚠️ **2026-10-03 更正**：审计原话紧接着写的"用名义 N 会让门限**偏松**"**方向说反了**。
> 按 BH 公式（p_adj = p × M / rank），**M 越大 → p_adj 越大 → 越难显著 → 门限更严**。
> 所以把相关的检验当独立检验，是**过度惩罚（偏严）**，不是偏松。
> 实测：p = [0.01, 0.02, 0.03, 0.04] → 名义 m=4 得 p_adj=0.04；有效 m=2 得 p_adj=0.02。

方法（Cheverud–Nyholt，与 `scripts/analyze_peer_structure.py` 同一实现）
------------------------------------------------------------------------
     M_eff = 1 + (M − 1) · (1 − Var(λ)/M)

其中 `λ` 是**检验统计量之间的相关矩阵**的特征值（降序）。含义：

- 检验**彼此独立** → 相关矩阵 ≈ I → `Var(λ) ≈ 0` → `M_eff ≈ M`（退化为名义 N）
- 检验**高度相关** → 特征值分布集中 → `Var(λ)` 大 → `M_eff < M`（**有效 N 变小**）

为什么"变小"是对的
------------------
BH 门限是 `p × M / rank` —— `M` 越大门限**越松**（越容易判定显著）。
同类基金的指标**高度同涨同跌**（实测 ρ̄ = 0.61~0.70），
拿 M 个高度相关的检验当 M 个独立检验用，等于**自欺欺人地放宽了门槛**。
用 `M_eff` 校正 → 门限**更松**（因为独立检验本就少），但**更贴近真实信息量**。

⚠️ 局限（必须随结果声明）
------------------------
Cheverud–Nyholt 假设**近似一维的公共因子结构**（本项目实测 PC1 占 63~73%，
符合）。若相关结构更复杂，`M_eff` 会高估独立数（偏保守地偏向"不显著"）。
"""
from __future__ import annotations

import math


def cheverud_nyholt(corr) -> float:
    """相关矩阵 → 有效独立检验数 `M_eff`。

    Args:
        corr: `M×M` 相关矩阵（对称、对角为 1）。也接受 list of list。

    Returns:
        `M_eff`（≥1）。空/单元素 → 原样返回个数。
    """
    try:
        import numpy as np
    except ImportError:                                   # pragma: no cover
        return float("nan")
    C = np.asarray(corr, dtype=float)
    if C.ndim != 2 or C.shape[0] != C.shape[1] or C.shape[0] == 0:
        return float("nan")
    m = C.shape[0]
    if m <= 1:
        return float(m)
    C = np.nan_to_num(C, nan=0.0)
    vals = np.linalg.eigvalsh(C)[::-1]
    total = float(vals.sum())
    if total <= 0:
        return float("nan")
    # 数值保护：特征值可能因浮点误差出现极小负数 → 归零后再算方差
    vals = np.clip(vals, 0.0, None)
    m_eff = 1.0 + (m - 1) * (1.0 - float(vals.var(ddof=1)) / m)
    return float(min(max(m_eff, 1.0), float(m)))


def corr_from_series(series_list) -> "object":
    """一组等长序列 → 相关矩阵（失败给 None，**不猜**）。"""
    try:
        import numpy as np
    except ImportError:                                   # pragma: no cover
        return None
    rows = []
    for s in (series_list or []):
        # ⚠️ **不能在这里"过滤掉 None"** —— 那会让序列**错位**（后续元素前移，日期对不上），
        # 算出来的相关是假的。保留 NaN，留到矩阵层面按**整行**剔除。
        rows.append([(float(x) if x is not None else float("nan")) for x in (s or [])])
    if len(rows) < 2:
        return None
    n = min(len(r) for r in rows)
    if n < 3:
        return None
    X = np.asarray([r[:n] for r in rows], dtype=float)
    if not np.isfinite(X).all():
        # 含 NaN 的序列直接剔除，避免把缺失当 0 相关
        keep = [i for i in range(X.shape[0]) if np.isfinite(X[i]).all()]
        if len(keep) < 2:
            return None
        X = X[keep]
    sd = X.std(axis=1, keepdims=True)
    sd[sd == 0] = 1e-12
    Z = (X - X.mean(axis=1, keepdims=True)) / sd
    return (Z @ Z.T) / X.shape[1]


def bh_adjust(pvals, m_eff: float = None) -> list:
    """Benjamini-Hochberg FDR 校正，**分母可用有效 N**。

    Args:
        pvals: 各检验的 p 值
        m_eff: 有效独立检验数；`None` = 用名义个数（旧行为，保持向后兼容）。
               **注意方向**：`m_eff` 更小 → 校正后 p 更小 → **更易判显著**（门限更松），
               但这是对的：相关的检验不该被重复惩罚。

    Returns:
        校正后的 p 值列表（与输入同序，单调非减地"变保守"）。
    """
    m = len(pvals)
    if m == 0:
        return []
    m_use = float(m_eff) if (m_eff and math.isfinite(float(m_eff))) else float(m)
    m_use = min(max(m_use, 1.0), float(m))
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [1.0] * m
    prev = 1.0
    for pos in range(m - 1, -1, -1):
        i = order[pos]
        val = min(prev, pvals[i] * m_use / (pos + 1))
        adj[i] = min(val, 1.0)
        prev = val
    return adj


def describe(m_nominal: int, m_eff: float) -> str:
    """一句话说明（可直接贴报告）。"""
    if not m_eff or not math.isfinite(float(m_eff)):
        return "多重检验：M_eff 不可用，退回名义 N=%d" % m_nominal
    ratio = float(m_eff) / max(m_nominal, 1)
    tag = "独立" if ratio > 0.95 else ("高度相关" if ratio < 0.6 else "中度相关")
    return ("多重检验：名义 N=%d，**有效 N=%.1f**（%.0f%%，%s）——"
            "门限按有效 N 算（比名义 N **更松**：相关的检验不该被重复惩罚）"
            % (m_nominal, float(m_eff), ratio * 100, tag))
