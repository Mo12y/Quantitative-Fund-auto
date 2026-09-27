"""批次 4.3：滚动样本外（walk-forward）验证 —— 只读、离线。

要回答的问题
------------
`HistoricalRecommender.proven_winners` 用**同一窗口内已实现的前向收益**选"赢家"，
属样本内同义反复（`methodology: "in-sample"`）。本模块把它改成**样本外**问题：

    在选择期（只看得见 t 之前的数据）用打分法选一组基金，
    到验证期（t 之后的真实走势）看这组基金到底比对照好多少。

对照（缺了它结论无意义）
------------------------
- **C1 全池中位数**：同期所有候选的中位收益 —— 回答"是不是全市场都涨"
- **C2 随机等量**：从候选中随机抽同样只数（固定种子，可复现）
- **S2 选择期末打分 TopK**：打分法"当期最看好"的直接体现
- **S1 选择期高频选中**：`proven_winners` 口径的骨架（分桶后按选中次数取 TopK）

为什么滚动而不是单次切分
------------------------
单次切分只给一个点估计，无法判断"这次好"是能力还是运气。
滚动出 6~10 个**互不重叠的验证窗口** → 给分布（中位/均值/胜率），才是可证伪的结论。

复盘性设计（避坑）
------------------
- **无前视**：打分走 `_score_from_tuples`（只看 `date <= t` 的点），验证收益走
  `_fwd_return_from_tuples`（只看 `date > t` 的点）。两者在
  `tests/test_oos_validate.py::test_no_lookahead` 里被显式钉住。
- **可注入**：`evaluate_windows()` 只吃 `nav_cache + 月末日期 + info_map`，
  不碰数据库 → 合成数据可做**确定性**测试（有能力的策略要能检出、没能力的不能误报）。
- **允许负结果**：若 S1 与对照无显著差异，结论就是"打分法无选基能力"。这是可接受的结论，
  模块**不提供**任何"让它看起来更好"的开关。
"""
from __future__ import annotations

import random
import statistics

import pandas as pd

from .fund_scorer import type_bucket

TRADING_DAYS_PER_MONTH = 21
PER_BUCKET_TOPN = 30          # 与 historical_recommender._run_monthly_backtest 一致


def _minus_months(d: str, n: int) -> str:
    return (pd.Timestamp(d) - pd.DateOffset(months=n)).strftime("%Y-%m-%d")


def _plus_months(d: str, n: int) -> str:
    return (pd.Timestamp(d) + pd.DateOffset(months=n)).strftime("%Y-%m-%d")


def month_ends(start: str, end: str) -> list:
    """[start, end] 内的自然月末（ISO 字符串，可字典序比较）。"""
    rng = pd.date_range(start=pd.Timestamp(start), end=pd.Timestamp(end), freq="ME")
    return [d.strftime("%Y-%m-%d") for d in rng]


def evaluate_windows(nav_cache: dict, ends: list, info_map: dict, *,
                     window_months: int = 18, step_months: int = 6, top_k: int = 10,
                     min_picks: int = 3, per_bucket_topn: int = PER_BUCKET_TOPN,
                     seed: int = 42) -> dict:
    """**纯计算**：跑滚动样本外验证。

    Args:
        nav_cache: `{code: [(date_str, nav_float), ...]}` 升序
        ends:      月末日期（升序），窗口的候选切点
        info_map:  `{code: fund_type}`（用于分桶）
        window_months/step_months: 选择期长度 / 验证期长度
        top_k:     每期持有基金数
        min_picks: 选择期内至少被选中几次才算"高频"（S1 的入选条件）

    Returns:
        `{windows: [...], summary: {...}, verdict: str}`
    """
    from .historical_recommender import HistoricalRecommender as HR
    score = HR._score_from_tuples
    fwd = HR._fwd_return_from_tuples

    codes = sorted(nav_cache)
    hold_days = step_months * TRADING_DAYS_PER_MONTH
    rng = random.Random(seed)
    windows = []

    for i, t in enumerate(ends):
        sel = [m for m in ends[:i] if m >= _minus_months(t, window_months)]
        if len(sel) < 3:
            continue
        # 验证期必须有数据：t 之后 step_months 个月仍落在样本内
        if _plus_months(t, step_months) > ends[-1]:
            continue

        # ── 选择期：逐月打分（分桶各取 TopN），累计被选中次数 ──
        picked, last_score = {}, {}
        for m in sel:
            per_bucket = {}
            for c in codes:
                s = score(nav_cache.get(c) or [], m)
                if s is None:
                    continue
                per_bucket.setdefault(type_bucket(info_map.get(c, "")), []).append((c, s))
            for _b, lst in per_bucket.items():
                lst.sort(key=lambda x: (-x[1], x[0]))       # 代码做次级键 → 结果可复现
                for c, s in lst[:per_bucket_topn]:
                    picked[c] = picked.get(c, 0) + 1
                    last_score[c] = s

        if not picked:
            continue

        # S1 = proven_winners 口径（高频 + 选择期末分数）
        s1 = sorted([c for c, n in picked.items() if n >= min_picks],
                    key=lambda c: (-picked[c], -last_score.get(c, 0.0), c))[:top_k]

        # S2 = 选择期最后一个月的打分 TopK（跨桶按分数排序 —— 它代表"打分法当期最看好"）
        last_m = sel[-1]
        sc_last = {}
        for c in codes:
            s = score(nav_cache.get(c) or [], last_m)
            if s is not None:
                sc_last[c] = s
        s2 = sorted(sc_last, key=lambda c: (-sc_last[c], c))[:top_k]

        # ── 验证期：真实持有收益 ──
        def _ret(c):
            return fwd(nav_cache.get(c) or [], t, hold_days)

        all_rets = {c: r for c in codes if (r := _ret(c)) is not None}
        if len(all_rets) < 5:
            continue

        def _avg(cs):
            v = [all_rets[c] for c in cs if c in all_rets]
            return (statistics.mean(v) if v else None), len(v)

        s1_r, s1_n = _avg(s1)
        s2_r, s2_n = _avg(s2)
        c1_r = statistics.median(all_rets.values())
        pool = sorted(all_rets)
        c2_codes = rng.sample(pool, min(top_k, len(pool)))
        c2_r, _ = _avg(c2_codes)

        windows.append({
            "as_of": t, "sel_start": sel[0], "sel_months": len(sel),
            "hold_days": hold_days, "n_pool": len(all_rets),
            "s1_n": s1_n, "s2_n": s2_n,
            "s1_codes": s1, "s2_codes": s2,
            "s1": s1_r, "s2": s2_r, "c1_median": c1_r, "c2_random": c2_r,
            "s1_minus_c1": (None if (s1_r is None or c1_r is None) else s1_r - c1_r),
            "s1_minus_c2": (None if (s1_r is None or c2_r is None) else s1_r - c2_r),
        })

    return {"windows": windows, "summary": _summarize(windows),
            "verdict": _verdict(windows)}


def _dist(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    return {"n": len(v), "mean": round(statistics.mean(v), 2),
            "median": round(statistics.median(v), 2),
            "min": round(min(v), 2), "max": round(max(v), 2)}


def _summarize(windows: list) -> dict:
    if not windows:
        return {"n_windows": 0}
    s1 = [w["s1"] for w in windows]
    c1 = [w["c1_median"] for w in windows]
    c2 = [w["c2_random"] for w in windows]
    s2 = [w["s2"] for w in windows]
    dif = [w["s1_minus_c1"] for w in windows if w["s1_minus_c1"] is not None]
    dif2 = [w["s1_minus_c2"] for w in windows if w["s1_minus_c2"] is not None]
    return {
        "n_windows": len(windows),
        "span": "%s ~ %s" % (windows[0]["as_of"], windows[-1]["as_of"]),
        "s1_frequent_pick": _dist(s1),
        "s2_top_score": _dist(s2),
        "c1_pool_median": _dist(c1),
        "c2_random": _dist(c2),
        "s1_minus_c1": _dist(dif),
        "s1_minus_c2": _dist(dif2),
        # 逐窗胜率：S1 跑赢全池中位/随机 的窗口占比
        "win_rate_vs_c1": (round(100.0 * sum(1 for x in dif if x > 0) / len(dif), 1)
                           if dif else None),
        "win_rate_vs_c2": (round(100.0 * sum(1 for x in dif2 if x > 0) / len(dif2), 1)
                           if dif2 else None),
    }


def _verdict(windows: list) -> str:
    """结论判定。**故意不给"通过/不通过"的漂亮话**，只描述分布事实。"""
    if len(windows) < 3:
        return "窗口不足 3 个，无法判定（样本太少，任何结论都是运气）"
    s = _summarize(windows)
    d, c = s["s1_minus_c1"], s["s1_minus_c2"]
    parts = []
    parts.append("S1 超额（vs 全池中位）中位 %s pp，逐窗胜率 %s%%"
                 % (d["median"] if d else "n/a", s["win_rate_vs_c1"]))
    parts.append("S1 超额（vs 随机）中位 %s pp，逐窗胜率 %s%%"
                 % (c["median"] if c else "n/a", s["win_rate_vs_c2"]))
    # 判据：逐窗胜率 >70% 且超额中位 >0 才算"有迹象"；否则如实说"看不出能力"
    ok = (s["win_rate_vs_c1"] or 0) >= 70 and (d and d["median"] > 0)
    parts.append("判定：%s" % ("有超出对照的迹象（仍需更大样本/多参数校正）"
                              if ok else "**看不出优于对照的选基能力**"))
    return "；".join(parts)


# =====================================================================
# 数据接入层（薄）：只负责取数，计算全在 evaluate_windows
# =====================================================================

def run(db, *, lookback_years: float = 5.0, window_months: int = 18, step_months: int = 6,
        top_k: int = 10, min_picks: int = 3, per_group: int = 60, seed: int = 42) -> dict:
    """从库里取数并跑滚动样本外验证（只读）。

    候选池复用 `HistoricalRecommender._get_candidates`（分层随机抽样、固定种子）
    以保证与既有 in-sample 引擎**同一池子**，差别只在时间窗切分。
    """
    from datetime import date, timedelta

    from .historical_recommender import HistoricalRecommender as HR

    hr = HR(db)
    codes = hr._get_candidates(per_group=per_group, seed=seed)
    if len(codes) < 20:
        return {"error": "候选池不足 20 只（实测 %d）" % len(codes)}

    latest = db.get_latest_nav_date()
    if not latest:
        return {"error": "库内无净值"}
    start = (date.fromisoformat(latest) - timedelta(days=int(lookback_years * 365))).isoformat()
    ends = month_ends(start, latest)
    if len(ends) < window_months // 3 + 3:
        return {"error": "月末样本不足：%d 个" % len(ends)}

    since = (pd.Timestamp(ends[0]) - pd.DateOffset(days=150)).strftime("%Y-%m-%d")
    nav_cache = {c: hr._load_nav_tuples(c, since) for c in codes}
    nav_cache = {c: v for c, v in nav_cache.items() if v}
    info_map = {f["fund_code"]: (f.get("fund_type") or "") for f in db.get_all_funds()}

    out = evaluate_windows(nav_cache, ends, info_map, window_months=window_months,
                           step_months=step_months, top_k=top_k, min_picks=min_picks,
                           seed=seed)
    out["meta"] = {"codes": len(codes), "nav_loaded": len(nav_cache),
                   "pool": "%d 只（分层随机，seed=%d）" % (len(codes), seed),
                   "window_months": window_months, "step_months": step_months,
                   "top_k": top_k, "min_picks": min_picks,
                   "methodology": "out-of-sample (walk-forward, 选择期与验证期不重叠)"}
    return out
