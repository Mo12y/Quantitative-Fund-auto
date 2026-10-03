"""行为画像（P3）—— 从**你自己的流水**量化你自己的行为。

依据（`docs/审计修复记录.md` 第四批 §5 P3）
------------------------------------------
> **行为画像**：从流水量化自己的行为（**追高 / 割肉 / 交易频率 ↔ 收益**），
> 个人投资者最缺的就是这面镜子。

为什么它和 `counterfactual` 不同
--------------------------------
`counterfactual`（P1）回答「**如果我当时不动会怎样**」—— 比的是**结果**。
本模块回答「**我实际上是怎么做的**」—— 量的是**行为本身**，不先预设好坏：

| 维度 | 量化什么 | 为什么值得看 |
|---|---|---|
| **买入纪律** | 每笔买入前 20 个交易日的涨幅、买入价在自身历史中的分位 | "追高"是可以被直接测量的，不需要凭感觉 |
| **卖出纪律** | 每笔卖出的实现收益、持有天数、**卖后 20 日走势** | "割肉"与"卖飞"是两件事，必须分开量 |
| **交易频率** | 按月笔数、持有期分布 ↔ 该档实现收益 | "频繁交易伤收益"这句话在本项目**要么被证实要么被证伪** |

三条方法纪律（都来自本项目已经付过学费的教训）
--------------------------------------------
1. ⭐ **必须有对照**：单看"买入前涨了 3%"没有意义 ——
   要跟**同一只基金、同一段时间**所有交易日的平均涨幅比（`chase_spread`）。
   没有对照的数只能读成"市场在涨"，读不出"我在追"。
2. ⭐ **报分布必须同时报极差**（§15.1 的教训：**2 只基金**就能篡改统计结论）——
   卖出实现收益里最差/最好那两笔会单独列出。
3. ⭐ **样本不足必须直说**（§16.1）—— 本项目区间仅数月，
   `sample_adequacy()` 的报警在**表格之前**输出；此时只读"发生了什么"，不读"我做得好不好"。

口径与局限（必须随结果声明）
----------------------------
- **买入用 `transactions`（全部已确认买入，含后来被卖掉的）**；
  **卖出用 `holdings` 的 `status='sold'` 批次**（它们带 `buy_amount`/`sell_amount`）。
  ⚠️ 两类记录**不是一一对应**的：`transactions` 侧有 34 条卖出流水，而 `holdings` 侧是 30 个已卖批次
  （早期"补录-流水核对"把多笔合并成批）→ 所以**买卖两侧分别统计，不强行配对**。
- 已卖批次的 `holdings.shares` 为 **0**（未回填）→ 本模块**只用金额**算收益率，不用份额。
- **未计申赎费**：会让所有实现收益同向偏差（偏高），不改变相对排序。
- 全称只读：本模块**不写任何表**。
"""
from __future__ import annotations

from .counterfactual import benchmark_return, is_dca, sample_adequacy
from .percentiles import expanding_percentile

#: 追高判定用的回看窗口（交易日）
CHASE_LOOKBACK = 20
#: 买入价分位 ≥ 该值 → 记一笔"买在自身历史高位"
HIGH_PCT = 70.0
#: 买入价分位 ≤ 该值 → 记一笔"买在自身历史低位"
LOW_PCT = 30.0
#: 卖后走势的回看窗口（交易日）
AFTER_SELL_LOOKBACK = 20
#: 持有期分档（天）
HOLD_BUCKETS = ((0, 7, "<7 天（赎回费 1.5%）"), (7, 30, "7~30 天"),
                (30, 90, "30~90 天"), (90, 10 ** 9, "≥90 天"))


# ───────────────────────── 基础取数 ─────────────────────────

def nav_series(conn, code, end_date=None):
    """某基金的 `[(date, unit_nav), ...]`（**升序**，只取有净值的行）。"""
    if end_date:
        rows = conn.execute(
            "SELECT nav_date, unit_nav FROM fund_nav WHERE fund_code = ?"
            " AND nav_date <= ? AND unit_nav IS NOT NULL AND unit_nav > 0"
            " ORDER BY nav_date", (code, end_date)).fetchall()
    else:
        rows = conn.execute(
            "SELECT nav_date, unit_nav FROM fund_nav WHERE fund_code = ?"
            " AND unit_nav IS NOT NULL AND unit_nav > 0 ORDER BY nav_date",
            (code,)).fetchall()
    return [(str(r[0]), float(r[1])) for r in rows]


def _index_on_or_before(series, on_date):
    """series 中**最后一个** `date <= on_date` 的下标；没有 → None。"""
    lo, hi, hit = 0, len(series) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= str(on_date):
            hit = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return hit


def _trail_at(series, i, lookback: int = CHASE_LOOKBACK):
    """下标 `i` 处**往前 `lookback` 个交易日**的涨跌幅（%）；样本不足 → None。"""
    if i is None or i - lookback < 0:
        return None
    base = series[i - lookback][1]
    if base <= 0:
        return None
    return round((series[i][1] / base - 1.0) * 100, 4)


def trailing_return(series, on_date, lookback: int = CHASE_LOOKBACK):
    """`on_date`（或之前最近交易日）**往前 `lookback` 个交易日**的涨跌幅（%）。

    样本不足（前面没有那么多交易日）→ **None，不猜**。
    """
    return _trail_at(series, _index_on_or_before(series, on_date), lookback)


def forward_return(series, on_date, lookback: int = AFTER_SELL_LOOKBACK):
    """`on_date` 之后 `lookback` 个交易日的涨跌幅（%）—— 用于"卖飞/卖对"。

    数据不够 `lookback` 个交易日 → **None**（不拿不完整的区间冒充结论）。
    """
    i = _index_on_or_before(series, on_date)
    if i is None or i + lookback >= len(series):
        return None
    base = series[i][1]
    if base <= 0:
        return None
    return round((series[i + lookback][1] / base - 1.0) * 100, 4)


def percentile_list(series, min_periods: int = 252):
    """整条净值序列的**扩张分位**（与 `series` 等长的 list，元素为 float 或 None）。

    口径直接复用 `percentiles.expanding_percentile`（项目 SSOT），**不另写一份**。
    ⚠️ 一次性整条算完再按下标取 —— 逐日调用那条 O(n²) 的实现在这里会慢到不可用
    （买入纪律要对"同基金同期**每一个**交易日"算对照）。
    ⚠️ 历史不足 `min_periods` 个交易日 → None（不做"分位"这件事，而不是给个假分位）。
    """
    if not series:
        return []
    pct = expanding_percentile([v for _, v in series], min_periods=min_periods)
    return [None if v != v else round(float(v), 2) for v in pct.values]   # NaN → None


def nav_percentile(series, on_date, min_periods: int = 252):
    """买入价在该基金**自身历史**中的扩张分位（0–100，**只用当日及之前**）。"""
    i = _index_on_or_before(series, on_date)
    if i is None:
        return None
    return percentile_list(series, min_periods)[i]


# ───────────────────────── 维度一：买入纪律 ─────────────────────────

def load_buys(conn):
    """所有已确认买入 `[{code, date, amount, note, is_dca}]`（升序）。"""
    rows = conn.execute(
        "SELECT fund_code, COALESCE(confirm_date, apply_date) d, amount, notes"
        " FROM transactions WHERE kind = 'buy' AND status = 'confirmed'"
        " ORDER BY COALESCE(confirm_date, apply_date)").fetchall()
    out = []
    for r in rows:
        amt = float(r["amount"] or 0)
        if amt <= 0 or not r["d"]:
            continue
        out.append({"code": r["fund_code"], "date": str(r["d"]), "amount": amt,
                    "note": r["notes"] or "", "is_dca": is_dca(r["notes"], amt)})
    return out


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return round((xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0), 4)


def buy_discipline(conn, span_end=None):
    """买入纪律：**追高**有多少，用"同基金同期"做对照来判。

    对每笔买入算两个量：
      · `trail20`  —— 买入前 20 个交易日的涨幅（%）。正数 = 在涨的过程中买。
      · `pct`      —— 买入价在该基金自身历史中的扩张分位（0–100）。

    ⭐ **对照（关键）**：单看"买入前平均涨 2%"读不出任何东西 —— 那可能只是市场在涨。
    所以对每只被买过的基金，还算**同一区间内每一个交易日**的两个量，
    再取「你的买入中位数 − 该基金全期中位数」：

      · `chase_spread` —— trail20 的差。**正数才叫追高**：你买的日子比这只基金平常的日子更「涨过」。
      · `pct_spread`   —— 分位的差。**正数**说明你买在比平常更高的位置。

    ⚠️ **对照窗口用「全局区间」而不是「该基金自己的买卖区间」**：
    若按后者，只买过 1 次的基金，其区间会塌缩成那**一天** → 差值恒为 0
    （2026-10-03 首版踩到：13 只基金里 7 只恒为 0，`chase_spread` 中位数直接假成 0.0）。
    窗口下界 = **全体首笔买入日**；上界 = `span_end`，未给时退回**该基金自身最新净值日**
    （同样不塌缩）。这样只买过 1 次的基金也有真实对照。

    Returns 见 `analyze()` 的 `buy` 字段。
    """
    if conn is None:
        return {"n_buys": 0}
    buys = load_buys(conn)
    if not buys:
        return {"n_buys": 0}

    span_start = min(b["date"] for b in buys)

    by_code = {}
    for b in buys:
        by_code.setdefault(b["code"], []).append(b)

    trail, pcts = [], []
    spreads_t, spreads_p, per_fund = [], [], {}
    for code, bs in by_code.items():
        series = nav_series(conn, code)
        if not series:
            continue
        pct_list = percentile_list(series)

        def _t(d):
            return _trail_at(series, _index_on_or_before(series, d))

        def _p(d):
            i = _index_on_or_before(series, d)
            return None if i is None else pct_list[i]

        my_t = [t for t in (_t(b["date"]) for b in bs) if t is not None]
        my_p = [p for p in (_p(b["date"]) for b in bs) if p is not None]

        # 对照：窗口内**每一个交易日**（上界未给 → 该基金自身最新净值日，不塌缩）
        lo = _index_on_or_before(series, span_start)
        hi = (len(series) - 1 if span_end is None
              else _index_on_or_before(series, span_end))
        base_t, base_p = [], []
        if lo is not None and hi is not None:
            for j in range(max(lo, 0), hi + 1):
                t = _trail_at(series, j)
                if t is not None:
                    base_t.append(t)
                if pct_list[j] is not None:
                    base_p.append(pct_list[j])

        m_my_t, m_base_t = _median(my_t), _median(base_t)
        m_my_p, m_base_p = _median(my_p), _median(base_p)
        rec = {"n_buys": len(bs), "median_trail20": m_my_t,
               "fund_median_trail20": m_base_t,
               "median_pct": m_my_p, "fund_median_pct": m_base_p}
        if m_my_t is not None and m_base_t is not None:
            spreads_t.append(round(m_my_t - m_base_t, 4))
            rec["spread_trail20"] = spreads_t[-1]
        if m_my_p is not None and m_base_p is not None:
            spreads_p.append(round(m_my_p - m_base_p, 4))
            rec["spread_pct"] = spreads_p[-1]
        per_fund[code] = rec
        trail.extend(my_t)
        pcts.extend(my_p)

    n_high = sum(1 for p in pcts if p >= HIGH_PCT)
    n_low = sum(1 for p in pcts if p <= LOW_PCT)
    n_pos = sum(1 for t in trail if t > 0)
    base_pcts = [v["fund_median_pct"] for v in per_fund.values()
                 if v.get("fund_median_pct") is not None]
    base_trails = [v["fund_median_trail20"] for v in per_fund.values()
                   if v.get("fund_median_trail20") is not None]
    return {
        "n_buys": len(buys),
        "n_distinct_buy_dates": len({b["date"] for b in buys}),
        "span_used": [span_start, span_end],
        "n_with_trail": len(trail),
        "median_trail20": _median(trail),
        "median_trail20_baseline": _median(base_trails),
        "frac_bought_after_rise": (round(n_pos / len(trail), 4) if trail else None),
        "trail_range": ([round(min(trail), 4), round(max(trail), 4)] if trail else None),
        "n_with_pct": len(pcts),
        "median_pct": _median(pcts),
        "median_pct_baseline": _median(base_pcts),
        "n_high": n_high, "n_low": n_low,
        "frac_high": (round(n_high / len(pcts), 4) if pcts else None),
        "chase_spread": _median(spreads_t),
        "pct_spread": _median(spreads_p),
        "n_funds_with_baseline": len(spreads_t),
        "per_fund": per_fund,
    }


# ───────────────────────── 维度二：卖出纪律 ─────────────────────────

def held_days(buy_date, sell_date):
    from datetime import date as _d
    try:
        return (_d.fromisoformat(str(sell_date)[:10]) - _d.fromisoformat(str(buy_date)[:10])).days
    except (TypeError, ValueError):
        return None


def sell_discipline(conn):
    """卖出纪律：**割肉**（亏损卖出）与**卖飞**（卖后又涨）分开量。

    · 实现收益用 `holdings` 已卖批次的 `sell_amount / buy_amount − 1`（**只用金额**，
      因为已卖批次的 `shares` 为 0）。
    · **卖后走势** `after20`：卖出日之后 20 个交易日的涨跌幅。
      > 0 表示"卖了之后又涨了"（卖飞），< 0 表示"躲过了下跌"。
      ⚠️ 只有卖出日距最新净值 ≥ 20 个交易日的批次才算得出 → 覆盖率一并报告，
      **不拿能算的那部分冒充全部**。
    """
    if conn is None:
        return {"n_lots": 0}
    rows = conn.execute(
        "SELECT fund_code, buy_date, buy_amount, sell_date, sell_amount"
        " FROM holdings WHERE status = 'sold' ORDER BY sell_date").fetchall()
    lots = []
    for r in rows:
        try:
            ba, sa = float(r["buy_amount"] or 0), float(r["sell_amount"] or 0)
        except (TypeError, ValueError):
            continue
        if ba <= 0 or sa <= 0:
            continue
        lots.append({"code": r["fund_code"], "buy_date": str(r["buy_date"]),
                     "sell_date": str(r["sell_date"]),
                     "ret_pct": round((sa / ba - 1.0) * 100, 4),
                     "held_days": held_days(r["buy_date"], r["sell_date"]),
                     "amount": round(sa, 2)})
    if not lots:
        return {"n_lots": 0}

    series_cache = {}
    for lot in lots:
        if lot["code"] not in series_cache:
            series_cache[lot["code"]] = nav_series(conn, lot["code"])
        lot["after20"] = forward_return(series_cache[lot["code"]], lot["sell_date"])

    rets = [x["ret_pct"] for x in lots]
    n_loss = sum(1 for x in rets if x < 0)
    after = [x["after20"] for x in lots if x["after20"] is not None]
    n_sold_too_early = sum(1 for a in after if a > 0)
    worst = min(lots, key=lambda x: x["ret_pct"])
    best = max(lots, key=lambda x: x["ret_pct"])

    # ⚠️ 「笔数 ≠ 独立决策数」：同一天卖出的多笔其实是**同一个决策**
    # （与 M3 的"有效 N"同源）。30 个批次可能只落在 8~10 个卖出日上 →
    # 按**笔**算中位数会被"某个卖出日"整体绑架。所以按决策日再汇总一份。
    by_date = {}
    for x in lots:
        by_date.setdefault(x["sell_date"], []).append(x)
    by_sell_date = []
    for d, g in sorted(by_date.items()):
        aft = [x["after20"] for x in g if x["after20"] is not None]
        by_sell_date.append({
            "date": d, "n_lots": len(g),
            "median_ret_pct": _median([x["ret_pct"] for x in g]),
            "after20_pct": _median(aft),
        })

    return {
        "n_lots": len(lots),
        "n_distinct_sell_dates": len(by_date),
        "by_sell_date": by_sell_date,
        "median_ret_pct": _median(rets),
        "worst_pct": worst["ret_pct"], "worst_code": worst["code"],
        "worst_buy_date": worst["buy_date"], "worst_sell_date": worst["sell_date"],
        "best_pct": best["ret_pct"], "best_code": best["code"],
        "range": [round(min(rets), 4), round(max(rets), 4)],
        "n_loss": n_loss, "frac_loss": round(n_loss / len(rets), 4),
        "n_with_followup": len(after),
        "n_sold_too_early": n_sold_too_early,
        "frac_sold_too_early": (round(n_sold_too_early / len(after), 4) if after else None),
        "median_after20_pct": _median(after),
        "median_after20_pct_by_date": _median(
            [d["after20_pct"] for d in by_sell_date if d["after20_pct"] is not None]),
        "lots": lots,
    }


# ───────────────────────── 维度三：交易频率 ↔ 收益 ─────────────────────────

def frequency(conn):
    """频率与持有期：按月笔数、持有期分档 ↔ 该档**实现收益**。

    ⚠️ 这是**相关性而非因果**：持有期短的批次少赚，可能是因为"卖太早"，
    也可能是因为"这些批次本来就买在下跌里"。所以它只回答
    「**我的短持批次收益是什么样**」，不回答「**是不是短持造成的**」。
    """
    n_buys = conn.execute("SELECT COUNT(*) FROM transactions"
                          " WHERE kind='buy' AND status='confirmed'").fetchone()[0]
    n_sells = conn.execute("SELECT COUNT(*) FROM transactions"
                           " WHERE kind='sell' AND status='confirmed'").fetchone()[0]
    first = conn.execute("SELECT MIN(d) FROM (SELECT COALESCE(confirm_date,apply_date) d"
                         " FROM transactions WHERE kind='buy' AND status='confirmed')").fetchone()[0]
    last = conn.execute("SELECT MAX(d) FROM (SELECT COALESCE(confirm_date,apply_date) d"
                        " FROM transactions WHERE kind='buy' AND status='confirmed')").fetchone()[0]

    months = None
    if first and last:
        from datetime import date as _d
        try:
            days = (_d.fromisoformat(str(last)[:10]) - _d.fromisoformat(str(first)[:10])).days
            months = max(days / 30.44, 1.0 / 30.44)
        except (TypeError, ValueError):
            months = None

    # 按月笔数（买 + 卖）
    monthly = {}
    for kind, tbl in (("buy", "buy"), ("sell", "sell")):
        for (d,) in conn.execute(
                "SELECT COALESCE(confirm_date,apply_date) d FROM transactions"
                " WHERE kind=? AND status='confirmed'", (kind,)):
            if d:
                monthly.setdefault(str(d)[:7], {"buy": 0, "sell": 0})[kind] += 1

    # 持有期分档 ↔ 实现收益（复用卖出侧口径）
    lots = sell_discipline(conn).get("lots", [])
    buckets = []
    for lo, hi, label in HOLD_BUCKETS:
        sub = [x for x in lots if x["held_days"] is not None and lo <= x["held_days"] < hi]
        buckets.append({"label": label, "n": len(sub),
                        "median_ret_pct": _median([x["ret_pct"] for x in sub]),
                        "invested": round(sum(x["amount"] for x in sub), 2)})

    return {
        "n_buys": n_buys, "n_sells": n_sells,
        "first_buy": str(first) if first else None,
        "last_buy": str(last) if last else None,
        "months": round(months, 2) if months else None,
        "buys_per_month": (round(n_buys / months, 2) if months else None),
        "sells_per_month": (round(n_sells / months, 2) if months else None),
        "monthly": dict(sorted(monthly.items())),
        "hold_buckets": buckets,
        "median_held_days": _median([x["held_days"] for x in lots
                                     if x["held_days"] is not None]),
        "n_under_7d": sum(1 for x in lots if (x["held_days"] or 999) < 7),
    }


# ───────────────────────── 汇总 ─────────────────────────

def _verdict(buy, sell, freq, sample):
    """一句话结论 —— **样本不足时不下判断**。"""
    if not sample.get("sufficient"):
        return ("⏸️ **样本不足，本期不出行为结论** —— 只给出可观测量"
                "（买在什么位置、持有多少天、卖后怎么走），"
                "不判断「我是不是在追高/割肉」。理由见上方样本充分性声明。")
    parts = []
    if buy.get("chase_spread") is not None:
        s = buy["chase_spread"]
        parts.append("买入前 20 日涨幅比同基金平常日子 %s %.2fpp" %
                     ("高" if s > 0 else "低", abs(s)))
    if sell.get("frac_loss") is not None:
        parts.append("亏损卖出占 %.0f%%" % (sell["frac_loss"] * 100))
    if freq.get("median_held_days") is not None:
        parts.append("中位持有 %.0f 天" % freq["median_held_days"])
    return "；".join(parts) if parts else "数据不足以形成结论。"


def analyze(db) -> dict:
    """行为画像总入口。**只读**。

    Returns:
        `{span, sample, benchmark_pct, benchmark_name, buy, sell, frequency,
          verdict, notes}`；无流水时返回 `{"error": ...}`。
    """
    conn = db.conn
    buys = load_buys(conn)
    if not buys:
        return {"error": "无买入流水，无法画像"}

    end_date = db.get_latest_nav_date()
    start = buys[0]["date"]

    buy = buy_discipline(conn, span_end=end_date)
    sell = sell_discipline(conn)
    freq = frequency(conn)
    sample = sample_adequacy(start, end_date or start)

    notes = [
        "买入侧用 `transactions` 全部已确认买入；卖出侧用 `holdings` 的 `status='sold'` 批次"
        "（带 buy/sell 金额）—— 两者**不是一一对应**，故**分别统计、不强行配对**。",
        "已卖批次的 `holdings.shares` 为 0（未回填）→ 实现收益**只用金额**算，不用份额。",
        "⭐ **追高必须看对照**：`chase_spread` = 你的买入前 20 日涨幅中位数 − 同基金同期"
        "所有交易日的该中位数；`pct_spread` 同理、换成「买入价在自身历史中的分位」。"
        "**只有它们为正，才说明你买得比平常更「涨过」/更高**；"
        "单看绝对涨幅或绝对分位，读到的可能只是市场在涨、或基金本来就在长期上行。",
        "对照窗口取「全体首笔买入日 ~ 最新净值日」的**每一个交易日**（不是各基金自己的买卖区间）"
        "—— 否则只买过一次的基金对照会塌缩成一天、差值恒为 0。",
        "⭐ **报分布同时报极差**：卖出侧最差/最好两笔单独列出（教训：2 只基金就能篡改统计结论）。",
        "⚠️ **笔数 ≠ 独立决策数**（与 M3「有效 N」同源）：同一卖出日的多笔其实是**同一个决策** —— "
        "本库 30 个已卖批次只落在少数几个卖出日上 → 按**笔**算的中位数会被某一天整体绑架。"
        "所以同时给出 `by_sell_date`（按决策日）与 `n_distinct_sell_dates`；"
        "**读『卖出时机好不好』要看决策日那一栏**，不是笔数那一栏。",
        "**卖后 20 日走势**只对「卖出日距最新净值 ≥20 个交易日」的批次可算 → 覆盖率已给出，"
        "未覆盖的批次**不推断**。",
        "⚠️ 频率↔收益是**相关不是因果**：短持批次收益低，可能是卖太早，也可能是本来就买在下跌里。",
        "未计申购费/赎回费（同向偏差，不改相对排序）。区间短 → 读法见样本充分性声明。",
    ]
    return {
        "span": "%s ~ %s" % (start, end_date),
        "sample": sample,
        "benchmark_pct": benchmark_return(conn, start, end_date) if end_date else None,
        "benchmark_name": "沪深300",
        "buy": buy, "sell": sell, "frequency": freq,
        "verdict": _verdict(buy, sell, freq, sample),
        "notes": notes,
    }
