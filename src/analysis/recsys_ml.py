"""L1 模型与评估：quality_score(基线) / rank_even / ridge(numpy) 三模型同窗口对打。

纪律（见 `docs/基金推荐系统_ML可行性研究.md` §4 验收标准）：
- **purged + embargo walk-forward**：预测 t 月时，训练集只含 `date <= t - embargo` 的行
  （embargo = 标签跨越的月数），杜绝标签重叠期泄进训练集；不随机划分。
- **同窗口**：三个模型在同一 OOS 月份集合上评分。
- **统计量**：IC(Spearman) / ICIR / Top-K 超额 / 配对 t（DM 的简化版，same window）。
- 目标 y_pct 已是**同类桶内分位** → IC 衡量的是"排序对不对"。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .recsys_dataset import FEATURES

# 特征方向：+1 越大越好，-1 越小越好
DIRECTION = {
    "mom_3m": +1, "mom_6m": +1, "mom_12m": +1,
    "sharpe_1y": +1, "calmar_1y": +1,
    "max_dd_1y": +1,          # 回撤是负数，越接近 0 越好 → +1
    "ann_vol_1y": -1, "downside_vol_1y": -1,
    "mgt_fee": -1, "fund_size": +1, "manager_tenure": +1,
}

# quality_score 基线的"因子组"权重（config/settings.yaml scorer 段）
BASELINE_GROUPS = {
    "momentum": (["mom_3m", "mom_6m", "mom_12m"], 0.25),
    "sharpe": (["sharpe_1y"], 0.25),
    "drawdown": (["max_dd_1y"], 0.20),
    "fee": (["mgt_fee"], 0.15),
    "size": (["fund_size"], 0.10),
    "manager": (["manager_tenure"], 0.05),
}


def _rank_pct(s: pd.Series, direction: int) -> pd.Series:
    """横截面分位（0~1，越大越好）。缺失保持 NaN（不填 0 —— 那会伪装成"最差"。）"""
    r = s.rank(pct=True)
    return r if direction > 0 else (1.0 - r)


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    for f in FEATURES:
        d[f] = pd.to_numeric(d[f], errors="coerce")
    return d


def score_baseline(df: pd.DataFrame) -> pd.Series:
    """① 现有 6 因子手工权重基线（各因子组内先取分位均值，再按权重加权）。"""
    d = df
    g = pd.DataFrame(index=d.index)
    for _, (cols, _w) in BASELINE_GROUPS.items():
        parts = [pd.Series(_rank_pct(d[c], DIRECTION[c]), index=d.index) for c in cols]
        g["_".join(cols)] = pd.concat(parts, axis=1).mean(axis=1, skipna=True)
    out = pd.Series(0.0, index=d.index)
    wsum = pd.Series(0.0, index=d.index)
    for (name, (cols, w)) in BASELINE_GROUPS.items():
        col = "_".join(cols)
        out = out.add(g[col].fillna(0) * w, fill_value=0)
        wsum = wsum.add(g[col].notna().astype(float) * w, fill_value=0)
    return (out / wsum.replace(0, np.nan)).rename("quality_score_base")


def score_rank_even(df: pd.DataFrame) -> pd.Series:
    """② 等权 rank 组合（不估权重，纯横截面分位平均）。"""
    parts = [pd.Series(_rank_pct(df[c], DIRECTION[c]), index=df.index) for c in FEATURES]
    return pd.concat(parts, axis=1).mean(axis=1, skipna=True).rename("rank_even")


def _standardize(X: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    return (X - mu) / sd


def fit_ridge(train: pd.DataFrame, lam: float = 10.0):
    """③ Ridge 闭式解（numpy 十行）：只用训练集估 mu/sd/权重。

    缺失处理：用**训练集的中位数**填充（并把中位数一起存下来给预测用）。
    为什么不丢行：`fund_size`/`manager_tenure` 在库内覆盖很低（只有原采样池有），
    丢行会把样本砍到几乎为零 —— 那等于"用缺失当筛子"，不是建模。
    """
    X = train[FEATURES].to_numpy(dtype=float)
    y = train["y_pct"].to_numpy(dtype=float)
    ok = np.isfinite(y)
    X, y = X[ok], y[ok]
    if len(X) < 50:
        return None
    med = np.nanmedian(X, axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    nanmask = ~np.isfinite(X)
    X = np.where(nanmask, med, X)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = _standardize(X, mu, sd)
    Z = np.hstack([np.ones((len(Z), 1)), Z])          # 截距
    A = Z.T @ Z + lam * np.eye(Z.shape[1])
    A[0, 0] -= lam                                     # 不惩罚截距
    beta = np.linalg.solve(A, Z.T @ y)
    return {"mu": mu, "sd": sd, "beta": beta, "med": med}


def predict_ridge(model, df: pd.DataFrame) -> pd.Series:
    if model is None:
        return pd.Series(np.nan, index=df.index)
    X = df[FEATURES].to_numpy(dtype=float)
    nanmask = ~np.isfinite(X)
    X = np.where(nanmask, model["med"], X)
    Z = _standardize(X, model["mu"], model["sd"])
    Z = np.hstack([np.ones((len(Z), 1)), Z])
    return pd.Series(Z @ model["beta"], index=df.index, name="ridge")


def walk_forward(panel: pd.DataFrame, oos_start: str = "2021-07-01",
                 embargo_months: int = 2, k: int = 5) -> dict:
    """按月滚动：训练 <= t - embargo，预测 t。返回逐月 IC 与 Top-K 超额。

    **embargo 为什么默认 2 个月**（不是 1）：
    t 月的标签是 "t 月末 → t 月末 + 21 交易日"，即标签窗口约等于 [t, t+1 月]。
    若训练集含 t-1 月的行，那一行的标签会读到 **t 月（测试月）的净值** —— 这是泄露。
    要让"训练集的每个标签都在 t 之前完全实现"，训练必须截止到 t-2 月。
    （`recsys_dataset.LABEL_HORIZON` 改大时，embargo 要同步 ≥ ceil(horizon/21)+1。）
    """
    d = _prep(panel)
    months = sorted(d["date"].unique())
    oos = [m for m in months if m >= oos_start]
    recs = []
    for t in oos:
        # embargo：标签跨越 1 个月 → 训练集必须早于 t 的前一个月（purge 重叠期）
        cutoff_idx = months.index(t) - embargo_months
        if cutoff_idx <= 0:
            continue
        cutoff = months[cutoff_idx]
        train = d[d["date"] <= cutoff]
        test = d[d["date"] == t]
        if len(train) < 200 or len(test) < 5:
            continue
        model = fit_ridge(train)
        s = pd.DataFrame({
            "y": test["y_pct"].to_numpy(),
            "bucket": test["bucket"].to_numpy(),
            "fwd": test["y_forward"].to_numpy(),
            "base": score_baseline(test).to_numpy(),
            "even": score_rank_even(test).to_numpy(),
            "ridge": predict_ridge(model, test).to_numpy(),
        })
        row = {"date": t, "n": len(test)}
        for m in ("base", "even", "ridge"):
            col = s[["y", m]].dropna()
            row[f"ic_{m}"] = (col["y"].corr(col[m], method="spearman")
                              if len(col) >= 5 else np.nan)
            if len(s.dropna(subset=[m])) >= k:
                top = s.dropna(subset=[m]).nlargest(k, m)
                bench = float(np.nanmean(s["fwd"]))          # 当月等权基准
                row[f"topk_{m}"] = float(np.nanmean(top["fwd"]) - bench)
        recs.append(row)
    return pd.DataFrame(recs)


def summarize(wf: pd.DataFrame) -> pd.DataFrame:
    """汇总：IC 均值 / ICIR / t 值 / Top-K 平均超额 / 胜率。"""
    out = []
    for m, name in (("base", "quality_score(基线)"), ("even", "等权rank"), ("ridge", "Ridge")):
        ic = wf[f"ic_{m}"].dropna()
        tk = wf[f"topk_{m}"].dropna()
        if len(ic) == 0:
            continue
        icir = ic.mean() / ic.std() if ic.std() else np.nan
        tval = ic.mean() / (ic.std() / np.sqrt(len(ic))) if ic.std() else np.nan
        out.append({
            "模型": name, "OOS月数": len(ic),
            "IC均值": round(float(ic.mean()), 4),
            "ICIR": round(float(icir), 3),
            "IC_t值": round(float(tval), 2),
            "IC>0占比": f"{float((ic > 0).mean() * 100):.0f}%",
            "TopK平均超额": f"{float(tk.mean() * 100):.2f}%" if len(tk) else "n/a",
            "TopK胜率": f"{float((tk > 0).mean() * 100):.0f}%" if len(tk) else "n/a",
        })
    return pd.DataFrame(out)
