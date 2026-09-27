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


C2_REPEATS = 100              # 随机对照重复次数（单次抽样方差极大，必须取分布）
BH_ALPHA = 0.05               # 多重检验校正的显著性水平


def evaluate_windows(nav_cache: dict, ends: list, info_map: dict, *,
                     window_months: int = 18, step_months: int = 6, top_k: int = None,
                     top_pct: float = None, min_picks: int = 3,
                     per_bucket_topn: int = PER_BUCKET_TOPN, market_series: list = None,
                     c2_repeats: int = C2_REPEATS, seed: int = 42) -> dict:
    """**纯计算**：跑滚动样本外验证。

    Args:
        nav_cache: `{code: [(date_str, nav_float), ...]}` 升序
        ends:      月末日期（升序），窗口的候选切点
        info_map:  `{code: fund_type}`（用于分桶）
        window_months/step_months: 选择期长度 / 验证期长度
        top_k:     每期持有基金数；给了 `top_pct` 时以 `top_pct` 为准
        top_pct:   按候选池比例选取（如 0.1 = **十分位**）——
                   mf-alpha 用 top-decile，比固定只数更稳（池子大小可变时不会忽多忽少）
        min_picks: 选择期内至少被选中几次才算"高频"（S1 的入选条件）
        market_series: 市场指数序列 `[(date, close), ...]`（如沪深300）——
                   给了就做**因子中性 alpha**（剥离市场 beta），否则只报原始收益差

    Returns:
        `{windows: [...], summary: {...}, verdict: str}`；`summary.alpha` 是因子中性回归结果。
    """
    from .historical_recommender import HistoricalRecommender as HR
    score = HR._score_from_tuples
    fwd = HR._fwd_return_from_tuples

    codes = sorted(nav_cache)
    hold_days = step_months * TRADING_DAYS_PER_MONTH
    # 每期持有只数：优先 top_pct（按候选池比例 = **top-decile**，mf-alpha 的做法），否则用 top_k。
    # 在循环外定一次 → 各窗口持有只数一致，可比。
    k = top_k if top_k else max(1, int(round(len(codes) * (top_pct if top_pct else 0.1))))
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
                    key=lambda c: (-picked[c], -last_score.get(c, 0.0), c))[:k]

        # S2 = 选择期最后一个月的打分 TopK（跨桶按分数排序 —— 它代表"打分法当期最看好"）
        last_m = sel[-1]
        sc_last = {}
        for c in codes:
            s = score(nav_cache.get(c) or [], last_m)
            if s is not None:
                sc_last[c] = s
        s2 = sorted(sc_last, key=lambda c: (-sc_last[c], c))[:k]

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
        # C1 = 全池**等权平均**（与 S1/S2/C2 同为"等权组合"口径，才可比）。
        # ⚠️ 2026-09-27 修正：原先用**中位数**当对照 —— 收益分布右偏（均值 1.9% vs 中位 1.4%），
        # 中位数会**系统性低估**"随机持有"的期望收益，让 S1 看起来没那么差。中位数另存作参考。
        c1_r = statistics.mean(all_rets.values())
        c1_med = statistics.median(all_rets.values())
        # 随机对照：**重复 c2_repeats 次取中位** —— 单次抽样方差极大
        # （实测：中位 +0.15% 而均值 +1.85%，差一个数量级），单次结果没有可比性。
        pool = sorted(all_rets)
        c2_vals = []
        for _ in range(max(1, int(c2_repeats))):
            r_, _n = _avg(rng.sample(pool, min(k, len(pool))))
            if r_ is not None:
                c2_vals.append(r_)
        c2_r = statistics.median(c2_vals) if c2_vals else None
        # 市场同期收益（因子中性检验用；无 index 数据时为 None，此时不报 alpha）
        mkt_r = fwd(market_series, t, hold_days) if market_series else None

        windows.append({
            "as_of": t, "sel_start": sel[0], "sel_months": len(sel),
            "hold_days": hold_days, "n_pool": len(all_rets),
            "s1_n": s1_n, "s2_n": s2_n, "c2_n": len(c2_vals), "k": k,
            "s1_codes": s1, "s2_codes": s2,
            "s1": s1_r, "s2": s2_r, "c1_pool": c1_r, "c1_pool_ref": c1_med,
            "c2_random": c2_r, "mkt": mkt_r,
            "s1_minus_c1": (None if (s1_r is None or c1_r is None) else s1_r - c1_r),
            "s1_minus_c2": (None if (s1_r is None or c2_r is None) else s1_r - c2_r),
        })

    return {"windows": windows, "summary": _summarize(windows),
            "verdict": _verdict(windows)}


def _binom_p(k: int, n: int) -> float:
    """单尾二项检验 `P(X ≥ k | p=0.5)`：逐窗胜率是否显著高于掷硬币。

    用非参数检验的理由：窗口收益分布明显非正态（实测中位与均值差一个数量级），
    t 检验不适用；而"逐窗赢/输"是干净的伯努利序列。
    """
    from math import comb
    if n <= 0:
        return 1.0
    return min(1.0, sum(comb(n, i) for i in range(k, n + 1)) / (2.0 ** n))


def _bh_adjust(pvals: list) -> list:
    """Benjamini-Hochberg FDR 校正。

    为什么要校正：我们一次跑 **4 个比较**（S1/S2 × 全池中位/随机）。
    只挑最小的那个 p 报"显著"，本身就是数据窥探 —— 这正是 betalens 的
    `Robust / Lucky Factors` 模块要防的事。
    """
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [1.0] * m
    prev = 1.0
    for pos in range(m - 1, -1, -1):
        i = order[pos]
        val = min(prev, pvals[i] * m / (pos + 1))
        adj[i] = val
        prev = val
    return adj


def _ols_alpha(y: list, x: list) -> dict:
    """一元 OLS：`y = α + β·x`。返回 `{alpha, beta, t_alpha, r2, n}`（n<3 → None）。

    用于**因子中性检验**：把策略超额收益对市场超额收益回归，取 **α** ——
    回答"跑赢的那部分，是不是只因为承担了更多市场 beta"。
    这是 mf-alpha 的 `factor-neutral alpha verification` 的最小可用形态。
    """
    n = len(y)
    if n < 3 or len(x) != n:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx <= 0:
        return None
    beta = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / sxx
    alpha = my - beta * mx
    resid = [yi - alpha - beta * xi for xi, yi in zip(x, y)]
    ss_res = sum(r * r for r in resid)
    ss_tot = sum((yi - my) ** 2 for yi in y)
    r2 = (1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    dof = n - 2
    if dof <= 0 or ss_res <= 0:
        return {"alpha": alpha, "beta": beta, "t_alpha": None, "r2": r2, "n": n}
    se = (ss_res / dof * (1.0 / n + mx * mx / sxx)) ** 0.5
    return {"alpha": alpha, "beta": beta,
            "t_alpha": (alpha / se) if se > 0 else None, "r2": r2, "n": n}


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
    c1 = [w["c1_pool"] for w in windows]
    c2 = [w["c2_random"] for w in windows]
    s2 = [w["s2"] for w in windows]
    dif = [w["s1_minus_c1"] for w in windows if w["s1_minus_c1"] is not None]
    dif2 = [w["s1_minus_c2"] for w in windows if w["s1_minus_c2"] is not None]
    # S2 的两条对照差（window 里没预存，这里现算）
    s2m1 = [w["s2"] - w["c1_pool"] for w in windows
            if w["s2"] is not None and w["c1_pool"] is not None]
    s2m2 = [w["s2"] - w["c2_random"] for w in windows
            if w["s2"] is not None and w["c2_random"] is not None]

    # ── 多重检验：4 个比较（S1/S2 × 中位/随机）都算 p，再做 BH 校正 ──
    labels = ["s1_vs_c1", "s1_vs_c2", "s2_vs_c1", "s2_vs_c2"]
    groups = [dif, dif2, s2m1, s2m2]
    p_raw = [_binom_p(sum(1 for x in g if x > 0), len(g)) for g in groups]
    p_adj = _bh_adjust(p_raw)
    win_counts = {lb: {"win": sum(1 for x in g if x > 0), "n": len(g)}
                  for lb, g in zip(labels, groups)}

    # ── 因子中性 alpha：**相对对照**的超额（S1 − C1）对市场同期收益回归，取截距 ──
    # ⚠️ 2026-09-27 修正：y 必须用**相对**超额，不能用 S1 的绝对收益。
    # 用绝对值测的是"基金组合 vs 沪深300"（实测 α=+1.31%、t=22.3 高度显著）——
    # 但那只是"基金池整体（含债基、且是存续基金）跑赢股指"，与"打分法能不能跑赢随机持有"无关。
    # 换成相对超额后，α 才回答我们真正关心的问题：剥离市场 beta 后还剩多少。
    trip = [(w["s1"] - w["c1_pool"], w["mkt"]) for w in windows
            if w["s1"] is not None and w["c1_pool"] is not None and w.get("mkt") is not None]
    alpha = _ols_alpha([a for a, _ in trip], [b for _, b in trip]) if len(trip) >= 3 else None

    return {
        "n_windows": len(windows),
        "span": "%s ~ %s" % (windows[0]["as_of"], windows[-1]["as_of"]),
        "s1_frequent_pick": _dist(s1),
        "s2_top_score": _dist(s2),
        "c1_pool": _dist(c1),
        "c2_random": _dist(c2),
        "s1_minus_c1": _dist(dif),
        "s1_minus_c2": _dist(dif2),
        "win_rate_vs_c1": (round(100.0 * sum(1 for x in dif if x > 0) / len(dif), 1)
                           if dif else None),
        "win_rate_vs_c2": (round(100.0 * sum(1 for x in dif2 if x > 0) / len(dif2), 1)
                           if dif2 else None),
        # 显著性（**校正后**才是可引用的那个）
        "p_raw": [round(p, 5) for p in p_raw],
        "p_adjusted": [round(p, 5) for p in p_adj],
        "p_labels": labels,
        "win_counts": win_counts,
        "alpha": alpha,          # {alpha, beta, t_alpha, r2, n} 或 None
        "market_windows": len(trip),
        "bh_alpha": BH_ALPHA,
    }


def _verdict(windows: list) -> str:
    """结论判定。**故意不给"通过/不通过"的漂亮话**，只描述分布事实。"""
    if len(windows) < 3:
        return "窗口不足 3 个，无法判定（样本太少，任何结论都是运气）"
    s = _summarize(windows)
    d, c = s["s1_minus_c1"], s["s1_minus_c2"]
    parts = ["S1 超额（vs 全池中位）中位 %s pp，逐窗胜率 %s%%"
             % (d["median"] if d else "n/a", s["win_rate_vs_c1"]),
             "S1 超额（vs 随机）中位 %s pp，逐窗胜率 %s%%"
             % (c["median"] if c else "n/a", s["win_rate_vs_c2"])]
    # 判据：超额中位 > 0 **且** BH 校正后 p < 0.05 才算"有迹象"（单看未校正的 p 会误导）
    p_adj = s.get("p_adjusted") or [1.0]
    ok = bool(d and d["median"] > 0) and (p_adj[0] is not None and p_adj[0] < s.get("bh_alpha", 0.05))
    parts.append("BH 校正后最小 p = %s（4 个比较）" % (min(p_adj) if p_adj else "n/a"))
    a = s.get("alpha")
    if a:
        parts.append("因子中性 α（相对）= %+.2f%%（β=%.2f，t(α)=%s，R²=%.2f，n=%d）"
                     % (a["alpha"], a["beta"],
                        ("%.2f" % a["t_alpha"]) if a["t_alpha"] is not None else "n/a",
                        a["r2"], a["n"]))
    else:
        parts.append("因子中性 α：未计算（缺市场序列）")
    parts.append("判定：%s" % ("有超出对照的迹象（仍需更大样本/更多窗口）"
                              if ok else "**看不出优于对照的选基能力**"))
    return "；".join(parts)


# =====================================================================
# 数据接入层（薄）：只负责取数，计算全在 evaluate_windows
# =====================================================================

def run(db, *, lookback_years: float = 5.0, window_months: int = 18, step_months: int = 6,
        top_k: int = None, top_pct: float = 0.1, min_picks: int = 3, per_group: int = 60,
        market_code: str = "000300", seed: int = 42) -> dict:
    """从库里取数并跑滚动样本外验证（只读）。

    候选池复用 `HistoricalRecommender._get_candidates`（分层随机抽样、固定种子）
    以保证与既有 in-sample 引擎**同一池子**，差别只在时间窗切分。
    市场序列取本地 `index_daily`（默认沪深300），供**因子中性 alpha** 使用；
    取不到就不报 alpha（不编造）。
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

    # 市场序列（沪深300，本地 index_daily）——供因子中性 alpha；取不到就不报 alpha
    market_series, market_note = [], "缺 index_daily 数据"
    try:
        rows = db.get_index_daily(market_code)
        market_series = [(str(r["trade_date"]), float(r["close"]))
                         for r in (rows or []) if r.get("close")]
        if market_series:
            market_note = "%s（%d 个交易日）" % (market_code, len(market_series))
    except Exception as e:                                   # noqa: BLE001
        market_note = "读取失败：%s" % str(e)[:60]

    out = evaluate_windows(nav_cache, ends, info_map, window_months=window_months,
                           step_months=step_months, top_k=top_k, top_pct=top_pct,
                           min_picks=min_picks, market_series=market_series or None, seed=seed)
    out["meta"] = {"codes": len(codes), "nav_loaded": len(nav_cache),
                   "pool": "%d 只（分层随机，seed=%d）" % (len(codes), seed),
                   "window_months": window_months, "step_months": step_months,
                   "top_k": (out["windows"][0]["k"] if out["windows"] else top_k),
                   "top_pct": top_pct, "min_picks": min_picks, "market": market_note,
                   "c2_repeats": C2_REPEATS,
                   "methodology": "out-of-sample (walk-forward, 选择期与验证期不重叠)"}
    return out
