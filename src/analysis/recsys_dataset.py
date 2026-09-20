"""L1 评估台 —— 面板构造：(月份 t, 基金) → 特征 + 下期标签。

设计依据：`docs/基金推荐系统_ML可行性研究.md` §3 L1 / §5 最小可行第一步。

关键纪律
--------
1. **只用 t 当天及以前的净值**算特征（绝不引入未来信息）；标签用 t 之后。
2. 估值/收益一律走 **acc_nav（累计净值）**，见 `nav_series` 的规则表；
   acc_nav 非法（0）时逐行回退 unit_nav。
3. 标签 = **同类桶内**「下期风险调整收益」的横截面分位（不是绝对收益）——
   这样模型学的是"同类里谁更好"，与①"排除有坑的"、②quality_score 的语义一致。
4. 流动性/生存偏差如实披露：只用库内现有基金（作者长期采样池），无清盘基金。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..data.database import Database
from . import nav_series

# 特征清单（≤12 个，自由度约束）—— 全部 point-in-time
FEATURES = [
    "mom_3m", "mom_6m", "mom_12m",     # 动量
    "sharpe_1y", "calmar_1y",          # 风险调整收益
    "max_dd_1y", "ann_vol_1y", "downside_vol_1y",   # 风险
    "mgt_fee", "fund_size", "manager_tenure",       # 成本/规模/经理
]

LABEL_HORIZON = 21          # 下期 = 约 1 个月（21 个交易日）
MIN_HISTORY = 252           # 至少 1 年历史才算特征


def _bucket(fund_type: str) -> str:
    """粗分桶（同类比较的分母）。分不清就归 unknown，不硬套。"""
    t = str(fund_type or "")
    if "指数型" in t or "ETF" in t:
        return "index"
    if "股票型" in t:
        return "equity"
    if "混合型" in t:
        return "mixed"
    if "债券型" in t:
        return "bond"
    if "QDII" in t or "海外" in t:
        return "qdii"
    return "unknown"


def _series(db: Database, code: str) -> Optional[pd.Series]:
    """取一只基金的估值净值序列（acc_nav 优先，逐行回退 unit_nav）。"""
    rows = db.get_fund_nav(code)
    if not rows or len(rows) < MIN_HISTORY:
        return None
    dates, vals = [], []
    for r in rows:
        v = nav_series.valuation_nav(r.get("unit_nav"), r.get("acc_nav"))
        if v and v > 0:
            dates.append(str(r["nav_date"])[:10])
            vals.append(float(v))
    if len(vals) < MIN_HISTORY:
        return None
    s = pd.Series(vals, index=pd.to_datetime(dates)).sort_index()
    return s[~s.index.duplicated(keep="last")]


def _features_at(s: pd.Series, t: pd.Timestamp, fee, size, tenure) -> Optional[dict]:
    """t 时点特征：只用 <= t 的净值。历史不足/数据缺失返回 None（过滤，不填 0）。"""
    h = s.loc[:t]
    if len(h) < MIN_HISTORY:
        return None
    px = h.values
    last = px[-1]
    if last <= 0:
        return None

    def ret(days):
        if len(px) <= days or px[-days - 1] <= 0:
            return None
        return last / px[-days - 1] - 1.0

    mom3, mom6, mom12 = ret(63), ret(126), ret(252)
    if mom3 is None or mom6 is None or mom12 is None:
        return None

    w = px[-252:]
    r = w[1:] / w[:-1] - 1.0
    if len(r) < 100 or r.std() == 0:
        return None
    vol = float(r.std() * np.sqrt(252))
    neg = r[r < 0]
    dvol = float(neg.std() * np.sqrt(252)) if len(neg) > 5 else 0.0
    sharpe = float(r.mean() / r.std() * np.sqrt(252))
    peak = np.maximum.accumulate(w)
    dd = float(np.min(w / peak - 1.0))
    calmar = float(((last / w[0]) - 1.0) / abs(dd)) if dd < 0 else None

    return {
        "mom_3m": mom3, "mom_6m": mom6, "mom_12m": mom12,
        "sharpe_1y": sharpe, "calmar_1y": calmar,
        "max_dd_1y": dd, "ann_vol_1y": vol, "downside_vol_1y": dvol,
        "mgt_fee": fee, "fund_size": size, "manager_tenure": tenure,
    }


def _forward_ret(s: pd.Series, t: pd.Timestamp, horizon: int = LABEL_HORIZON) -> Optional[float]:
    """下期收益（t 之后 horizon 个交易日）。尾部不足 horizon 时返回 None（不参与）。"""
    fut = s.loc[t:]
    if len(fut) <= horizon:
        return None
    a, b = fut.values[0], fut.values[horizon]
    if a <= 0:
        return None
    return float(b / a - 1.0)


def build_panel(db: Database, start: str = "2019-01-01", end: str = "2026-08-31",
                max_funds: int = 0, codes: list = None) -> pd.DataFrame:
    """构造面板：每月最后一个交易日一个横截面。

    Returns DataFrame[date, fund_code, bucket, y_forward, y_pct, <FEATURES>]
    """
    info = {r["fund_code"]: r for r in db.get_all_funds()}
    if codes is None:
        codes = sorted(db.get_all_fund_codes())
        if max_funds:
            codes = codes[:max_funds]
    print(f"候选基金: {len(codes)} 只")

    rows = []
    for i, code in enumerate(codes):
        s = _series(db, code)
        if s is None:
            continue
        inf = info.get(code) or {}
        fee = inf.get("mgt_fee")
        size = inf.get("fund_size")
        tenure = inf.get("manager_tenure")
        try:
            bkt = _bucket(inf.get("fund_type"))
        except Exception:
            bkt = "unknown"
        # 月末交易日
        months = s.loc[start:end].index.to_series().groupby(
            [s.loc[start:end].index.year, s.loc[start:end].index.month]).max().values
        for t in months:
            t = pd.Timestamp(t)
            f = _features_at(s, t, fee, size, tenure)
            if f is None:
                continue
            y = _forward_ret(s, t)
            if y is None:
                continue
            f.update({"date": t.strftime("%Y-%m-%d"), "fund_code": code, "bucket": bkt, "y_forward": y})
            rows.append(f)
        if (i + 1) % 100 == 0:
            print(f"  进度 {i+1}/{len(codes)}  面板行数 {len(rows)}")

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # 标签：同类桶内横截面分位（0~1，越高越好）——用风险调整收益
    def _risk_adj(g):
        r = g["y_forward"] / g["ann_vol_1y"].replace(0, np.nan)
        return r
    df["_ra"] = df.groupby(["date", "bucket"], group_keys=False).apply(_risk_adj)
    df["y_pct"] = df.groupby(["date", "bucket"])["_ra"].rank(pct=True)
    df = df.drop(columns=["_ra"])
    return df


if __name__ == "__main__":
    import os
    os.environ.setdefault("QFA_MARKET_LIVE", "0")
    db = Database("data/fund_quant.db")
    panel = build_panel(db)
    print("\n面板规模:", panel.shape)
    print("月份数:", panel["date"].nunique(), "| 基金数:", panel["fund_code"].nunique())
    print(panel.groupby("date").size().tail(8))
    db.close()
