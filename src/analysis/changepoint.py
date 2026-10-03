"""变点检测（M5）—— 查「**风格漂移**」这个隐性风险。

依据（`docs/审计修复记录.md` 第四批 §4 M5）
------------------------------------------
> **变点检测（CUSUM / PELT）** | 风格漂移、换经理的**隐性风险**
> （**名称与类型都不变，风险特征变了**）；也可给回撤预警加一层。

为什么需要它
------------
本项目的筛选、同类分位、回撤预警**全都假设"一只基金是一个稳定的过程"**。
但一只基金可以在**不改名、不改类型**的情况下，把权益仓位从 60% 拉到 95%、
或把久期从 2 年拉到 5 年 —— 历史波动/回撤统计**突然不再代表它的未来**。
名称和类型抓不到这件事，**只有收益率序列抓得到**。

两个算法（都是文献标准，且各有分工）
------------------------------------
| 算法 | 检验什么 | 用在哪 |
|---|---|---|
| **CUSUM-of-squares**（Inclán–Tiao 1994, IT-ICSS） | **方差**是否发生一次性跃迁 | 主判据：给"显著性"一个**临界值**，不是看图画线 |
| **PELT**（Killick et al. 2012） | 序列**均值**的多个变点，带惩罚项自动定段数 | 找出**多个**变点（风格可以漂好几次），取最近一个 |

⚠️ **两个算法都检的是"收益率/滚动波动"的统计特征，不是"基金经理换没换"** ——
它给的是**怀疑的入口**（"这段的风险特征与之前不同"），不是**结论**。

⭐⭐ 判据必须带「市场对照」（本项目已经付过学费的纪律，同 P3 的 `chase_spread`）
------------------------------------------------------------------------------
2026 年 7 月全市场波动抬升，**每一只**基金都会"变点显著" ——
那不是风格漂移，是**市场 regime**。所以本模块一律算**同一时间窗内沪深300 的同样变化**，
只报**超额部分**（`excess_*`）。**只看基金自己的变化 = 把 beta 当 alpha。**

⚠️ 滚动窗口的平滑会**推迟**变点（用 60 日滚动波动，变点估计会滞后约 `window/2` ≈ 30 个交易日）
→ 输出里同时给 CUSUM（用**原始收益**，不滞后但更噪）与 PELT（用**滚动波动**，更稳但滞后），
两者的日期**不一致是正常的**，报告里如实说明。

口径与局限（必须随结果声明）
----------------------------
- 收益一律由 `nav_series.VALUATION_NAV_SQL`（累计净值优先）算出 —— **分红除息不算下跌**。
- 基准 = `index_daily` 的 `000300`（沪深300）；基准缺失时**不做对照**并明确降级，
  而不是把"无对照"当成"没漂移"。
- CUSUM-of-squares 的临界值 1.358 是 **iid 假设**下的渐近值；金融收益有波动聚集与自相关
  → 该检验**偏松**（容易报显著）。所以最终 flag **要求超额幅度也达标**，不只靠 p 值。
- 全程只读。
"""
from __future__ import annotations

import math

from .nav_series import valuation_nav

#: 滚动波动/β 的窗口（交易日）
DEFAULT_WINDOW = 60
#: 出一个"结论"所需的最少观测（约一年）
MIN_HISTORY = 252
#: 变点两侧各自所需的最少观测
MIN_SEGMENT = 40
#: 判定「风格漂移」的超额相对波动变化门槛（**事前写死**，避免事后挪门槛）
VOL_SHIFT_REL = 0.30
#: 判定「风格漂移」的 β 变化门槛（绝对值）
BETA_SHIFT_ABS = 0.50
#: Inclán–Tiao 统计量的 5% 渐近临界值
IT_CRIT_5PCT = 1.358
#: 基准指数（β 的分母、波动的对照）
BENCH_CODE = "000300"
BENCH_NAME = "沪深300"
#: β 的**市场结构对照**指数（中证500）——
#: β 的"对照"不能用沪深300 自己（它对自己的 β 恒为 1）。
#: 用另一个宽基对沪深300 的 β 变化，代表"市场层面的共同移动"是否也变了。
CONTROL_CODE = "000905"
CONTROL_NAME = "中证500"
#: PELT 的惩罚倍率（**由纯噪声校准得到，不是拍的**）：
#: 对"60 日滚动波动 + 纯高斯噪声"序列，倍率 ≥800 时**误报 0 个**；
#: 取 1600 留一倍余量（实测 1.2× 与 2.5× 的真实波动跃迁仍能在真值附近被检出）。
#: ⚠️ 改 `DEFAULT_WINDOW` 必须重新校准（滚动窗口变了，平滑程度就变了）。
PELT_PENALTY_MULT = 1600.0


# ───────────────────────── 工具 ─────────────────────────

def _mean(x):
    return sum(x) / len(x) if x else 0.0


def _std(x):
    n = len(x)
    if n < 2:
        return 0.0
    m = _mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (n - 1))


def ann_vol_pct(returns, periods: int = 244):
    """年化波动（%）。`periods=244` 与本项目 `nav_metrics` 的 A 股口径一致。

    ⚠️ 这里**不套用** `nav_metrics.MIN_ANN_VOL_FOR_RATIO` 的值域守卫 ——
    那条守卫是给**比率型**指标（夏普/索提诺）用的；波动率本身再小也是有效读数。
    """
    if len(returns) < 2:
        return None
    return round(_std(returns) * math.sqrt(periods) * 100, 4)


def daily_returns(pairs):
    """`[(date, nav), ...]` → `[(date, r), ...]`（简单收益，首日无收益）。"""
    out = []
    for i in range(1, len(pairs)):
        p0, p1 = pairs[i - 1][1], pairs[i][1]
        if p0 > 0:
            out.append((pairs[i][0], p1 / p0 - 1.0))
    return out


def beta(fund_returns, bench_returns):
    """按日期对齐后算 β = cov(f, b) / var(b)。对齐后不足 20 个点 → None。"""
    bmap = dict(bench_returns)
    pairs = [(r, bmap[d]) for d, r in fund_returns if d in bmap]
    if len(pairs) < 20:
        return None
    f = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    mf, mb = _mean(f), _mean(b)
    cov = sum((f[i] - mf) * (b[i] - mb) for i in range(len(f)))
    var = sum((v - mb) ** 2 for v in b)
    return None if var <= 0 else round(cov / var, 4)


def rolling_vol(x, window, periods: int = 244):
    """滚动年化波动（%）。前 `window-1` 项为 None。"""
    out = [None] * len(x)
    for i in range(window - 1, len(x)):
        seg = x[i - window + 1:i + 1]
        out[i] = round(_std(seg) * math.sqrt(periods) * 100, 6)
    return out


# ───────────────────────── 算法原语 ─────────────────────────

def cusum_squares(returns, crit: float = IT_CRIT_5PCT):
    """**Inclán–Tiao (1994) CUSUM-of-squares** —— 方差是否发生一次性跃迁。

    定义 `D_k = C_k/C_n − k/n`（`C_k` = 前 k 项平方和），统计量
    `IT = sqrt(n/2) · max_k |D_k|`；`IT ≥ 1.358` 即 5% 显著。

    Returns: `{ok, k, D, it, crit, significant}`；样本不足 → `ok=False`。
    """
    x = list(returns)
    n = len(x)
    if n < 30:
        return {"ok": False, "reason": "样本不足（<30 个收益点）"}
    s2 = [v * v for v in x]
    total = sum(s2)
    if total <= 0:
        return {"ok": False, "reason": "收益率全为 0，无法检验方差"}
    cum, best_d, best_k = 0.0, 0.0, 0
    for k in range(1, n):
        cum += s2[k - 1]
        d = cum / total - k / n
        if abs(d) > abs(best_d):
            best_d, best_k = d, k
    it = math.sqrt(n / 2.0) * abs(best_d)
    return {"ok": True, "k": best_k, "D": round(best_d, 6), "it": round(it, 4),
            "crit": crit, "significant": it >= crit}


def _seg_cost(ps, ps2, i, j):
    """区间 `x[i:j]` 的 L2（均值漂移）代价 = Σ(x−mean)²。用前缀和 O(1) 求。"""
    m = j - i
    s = ps[j] - ps[i]
    return (ps2[j] - ps2[i]) - s * s / m


def pelt(x, penalty=None, min_size: int = 5):
    """**PELT**（Killick et al. 2012）—— 变点检测，段数由惩罚项自动决定。

    代价函数取 **L2（均值漂移）**；惩罚项缺省用
    `PELT_PENALTY_MULT · 2σ²·ln n`，其中 σ 由**相邻差分**稳健估计（`σ ≈ sd(Δx)/√2`）。

    ⚠️ 那个 `PELT_PENALTY_MULT` **不是拍的**：滚动平滑序列的相邻差分极小，
    直接用 BIC 的 `2σ²ln n` 会在纯噪声上找出二十几个"变点"（实测 22 个，纯属人工产物）。
    倍率由「60 日滚动波动 + 纯高斯噪声」校准（见模块常量注释）。**不做这件事 = 把噪声当变点。**

    ⚠️ 这里**不做剪枝**（PELT 的 pruning 有实现细节风险，而本项目的序列 ≤ 数千点，
    O(n²) 完全够用）—— 宁可慢一点，也不要一个"快但偶发错"的实现。

    Returns: 变点下标列表（**升序，不含 0 与 n**）；序列太短 → `[]`。
    """
    x = list(x)
    n = len(x)
    if n < 2 * min_size:
        return []
    ps = [0.0] * (n + 1)
    ps2 = [0.0] * (n + 1)
    for i, v in enumerate(x):
        ps[i + 1] = ps[i] + v
        ps2[i + 1] = ps2[i] + v * v
    if penalty is None:
        inc = [x[i + 1] - x[i] for i in range(n - 1)]
        sigma = max(_std(inc) / math.sqrt(2.0), 1e-12)
        penalty = PELT_PENALTY_MULT * 2.0 * sigma * sigma * math.log(n)

    F = [0.0] + [float("inf")] * n
    prev = [0] * (n + 1)
    for t in range(1, n + 1):
        best, best_s = float("inf"), 0
        for s in range(0, t - min_size + 1):
            c = F[s] + _seg_cost(ps, ps2, s, t) + penalty
            if c < best:
                best, best_s = c, s
        F[t], prev[t] = best, best_s

    cps, t = [], n
    while t > 0:
        s = prev[t]
        if s > 0:
            cps.append(s)
        t = s
    return sorted(cps)


# ───────────────────────── 取数 ─────────────────────────

def fund_returns(db_or_conn, code, start=None, end=None):
    """某基金的 `[(date, r), ...]`（**估值净值口径**，见 `nav_series`）。"""
    conn = getattr(db_or_conn, "conn", db_or_conn)
    q = ("SELECT nav_date, unit_nav, acc_nav FROM fund_nav WHERE fund_code = ?"
         " AND unit_nav IS NOT NULL")
    args = [code]
    if start:
        q += " AND nav_date >= ?"
        args.append(start)
    if end:
        q += " AND nav_date <= ?"
        args.append(end)
    q += " ORDER BY nav_date"
    pairs = []
    for r in conn.execute(q, args):
        nav = valuation_nav(r[1], r[2])
        if nav > 0:
            pairs.append((str(r[0]), nav))
    return daily_returns(pairs)


def bench_returns(db_or_conn, start=None, end=None, code: str = BENCH_CODE):
    """基准指数的 `[(date, r), ...]`（同 `index_daily` 的收盘价）。"""
    conn = getattr(db_or_conn, "conn", db_or_conn)
    q = "SELECT trade_date, close FROM index_daily WHERE index_code = ? AND close IS NOT NULL"
    args = [code]
    if start:
        q += " AND trade_date >= ?"
        args.append(start)
    if end:
        q += " AND trade_date <= ?"
        args.append(end)
    q += " ORDER BY trade_date"
    pairs = []
    for r in conn.execute(q, args):
        try:
            v = float(r[1])
        except (TypeError, ValueError):
            continue
        if v > 0:
            pairs.append((str(r[0]), v))
    return daily_returns(pairs)


# ───────────────────────── 应用层 ─────────────────────────

def _segment_stats(rets, bench_map):
    b = [(d, bench_map[d]) for d, _ in rets if d in bench_map]
    return {
        "n": len(rets),
        "start": rets[0][0] if rets else None,
        "end": rets[-1][0] if rets else None,
        "ann_vol_pct": ann_vol_pct([r for _, r in rets]),
        "beta": beta(rets, b),
    }


def analyze(db, code, *, window: int = DEFAULT_WINDOW,
            min_history: int = MIN_HISTORY, name: str = None) -> dict:
    """单只基金的风格漂移检测。**只读**。

    流程：
      ① 取估值净值 → 日收益；
      ② **CUSUM-of-squares** 给显著性 + 主变点（用原始收益，不滞后）；
      ③ **PELT** 在滚动波动序列上找多个变点（更稳但滞后 `window/2`）；
      ④ 以主变点切两段，算各自的年化波动 / β；
      ⑤ ⭐ **市场对照**：对**同样两个日期窗**算沪深300 的波动/β 变化，
         **只报超额部分** —— 否则 7 月全市场波动抬升会被读成"我的基金风格漂移"。
    """
    rets = fund_returns(db, code)
    res = {"code": code, "name": name, "n_returns": len(rets),
           "span": ("%s ~ %s" % (rets[0][0], rets[-1][0])) if rets else None,
           "window": window, "bench": BENCH_NAME}

    if len(rets) < min_history:
        res.update({"ok": False, "flag": "历史不足", "alerts": [],
                    "reason": "收益点 %d < %d（约一年）→ 不做变点判断"
                              % (len(rets), min_history),
                    "notes": ["历史不足 → **不做变点判断**（不猜）。"
                              "本模块要求 ≥ %d 个收益点（约一年）。" % min_history]})
        return res

    dates = [d for d, _ in rets]
    r = [v for _, v in rets]
    res["ok"] = True

    # ② CUSUM-of-squares：主变点 + 显著性
    cus = cusum_squares(r)
    res["cusum_squares"] = cus
    if not cus.get("ok"):
        res.update({"flag": "无法检验", "alerts": [], "notes": [cus.get("reason") or "CUSUM 无法检验"],
                    "reason": cus.get("reason")})
        return res
    k = cus["k"]
    res["cp_date"] = dates[k]

    # ③ PELT：滚动波动上的多个变点（更稳、但滞后 window/2）
    rv = rolling_vol(r, min(window, max(10, len(r) // 4)))
    valid_idx = [i for i, v in enumerate(rv) if v is not None]
    pelt_cps = []
    if len(valid_idx) >= 2 * MIN_SEGMENT:
        comp = [rv[i] for i in valid_idx]
        for c in pelt(comp, min_size=MIN_SEGMENT):
            pelt_cps.append(dates[valid_idx[c]])
    res["pelt_change_points"] = pelt_cps
    res["pelt_latest"] = pelt_cps[-1] if pelt_cps else None

    # ④ 两段统计
    if k < MIN_SEGMENT or len(r) - k < MIN_SEGMENT:
        res.update({"flag": "段太短", "alerts": [],
                    "reason": "主变点两侧观测不足（<%d）→ 不算两段统计" % MIN_SEGMENT,
                    "notes": ["主变点两侧观测不足（<%d）→ 不算两段统计（不猜）。" % MIN_SEGMENT]})
        return res

    brets = bench_returns(db)
    bmap = dict(brets)
    ref_rets, test_rets = rets[:k], rets[k:]
    res["ref"] = _segment_stats(ref_rets, bmap)
    res["test"] = _segment_stats(test_rets, bmap)
    res["bench_available"] = bool(brets)
    ctrl_rets = bench_returns(db, code=CONTROL_CODE)
    res["control_available"] = bool(ctrl_rets)

    # ⑤ 市场对照（同样两个日期窗）
    def _vol_of(seg, pool):
        sub = [(d, v) for d, v in pool if seg["start"] <= d <= seg["end"]]
        return ann_vol_pct([v for _, v in sub]) if len(sub) >= 2 else None

    bench_ref_vol = _vol_of(res["ref"], brets)
    bench_test_vol = _vol_of(res["test"], brets)
    fv_ref, fv_test = res["ref"]["ann_vol_pct"], res["test"]["ann_vol_pct"]

    vol = {"ref_pct": fv_ref, "test_pct": fv_test,
           "delta_pp": round(fv_test - fv_ref, 4) if None not in (fv_ref, fv_test) else None}
    if None not in (fv_ref, fv_test, bench_ref_vol, bench_test_vol):
        vol["bench_ref_pct"] = bench_ref_vol
        vol["bench_test_pct"] = bench_test_vol
        vol["bench_delta_pp"] = round(bench_test_vol - bench_ref_vol, 4)
        # 相对变化之差（超额）—— 只有它才是"这只基金自己的漂移"
        rel_f = (fv_test / fv_ref - 1.0) if fv_ref else None
        rel_b = (bench_test_vol / bench_ref_vol - 1.0) if bench_ref_vol else None
        if rel_f is not None and rel_b is not None:
            vol["rel_change"] = round(rel_f, 4)
            vol["bench_rel_change"] = round(rel_b, 4)
            vol["excess_rel_change"] = round(rel_f - rel_b, 4)
    res["vol"] = vol

    # β：基金自身的 β 变化，对照 = **中证500 对沪深300 的 β 在同两窗的变化**
    # ⚠️ 不能用沪深300 自己当对照 —— 它对自己的 β 恒为 1，差值恒 0，
    #    这会让 `excess_delta` **永远等于 delta**（首版就是这个 bug）。
    beta_info = {"ref": res["ref"]["beta"], "test": res["test"]["beta"]}
    if None not in (beta_info["ref"], beta_info["test"]):
        beta_info["delta"] = round(beta_info["test"] - beta_info["ref"], 4)
    c_ref = _beta_in_window(ctrl_rets, bmap, res["ref"]["start"], res["ref"]["end"])
    c_test = _beta_in_window(ctrl_rets, bmap, res["test"]["start"], res["test"]["end"])
    beta_info["control_name"] = CONTROL_NAME
    if None not in (c_ref, c_test):
        beta_info["control_ref"] = round(c_ref, 4)
        beta_info["control_test"] = round(c_test, 4)
        beta_info["control_delta"] = round(c_test - c_ref, 4)
        if beta_info.get("delta") is not None:
            beta_info["excess_delta"] = round(beta_info["delta"] - (c_test - c_ref), 4)
    res["beta"] = beta_info

    # 判定（判据写死在模块顶部常量，不随数据挪）
    alerts = []
    exc = vol.get("excess_rel_change")
    if cus.get("significant") and exc is not None and exc >= VOL_SHIFT_REL:
        alerts.append("波动跃迁：超额相对变化 %+.0f%%（≥ %.0f%%）" % (exc * 100, VOL_SHIFT_REL * 100))
    exb = beta_info.get("excess_delta")
    if exb is not None and abs(exb) >= BETA_SHIFT_ABS:
        alerts.append("β 漂移：超额变化 %+.2f（≥ %.2f）" % (exb, BETA_SHIFT_ABS))
    res["alerts"] = alerts
    res["flag"] = "⚠️ 疑似风格漂移" if alerts else "无显著漂移"
    res["notes"] = _notes(res)
    return res


def _beta_in_window(ctrl_rets, bmap, start, end):
    """控制指数在 `[start, end]` 窗内对基准的 β；样本不足 → None。"""
    sub = [(d, v) for d, v in ctrl_rets if start <= d <= end]
    b = [(d, bmap[d]) for d, _ in sub if d in bmap]
    return beta(sub, b) if len(b) >= 20 else None


def _notes(res):
    n = [
        "变点**不是结论**，是怀疑的入口：它说「这段的风险特征与之前不同」，"
        "不说「基金经理换了」或「风格变坏了」。",
        "⭐ 判据一律带**市场对照**（同日期窗的沪深300）→ 只报**超额**变化。"
        "没有对照时，7 月这种全市场波动抬升会把**每一只**基金都判成漂移。",
        "β 的对照不能用沪深300 自己（它对自己的 β 恒为 1）→ 用 **%s 对沪深300** "
        "在同两窗的 β 变化代表「市场层面的共同移动是否也变了」。"
        % CONTROL_NAME,
        "CUSUM-of-squares 用**原始收益**（不滞后但更噪）；PELT 用 60 日**滚动波动**"
        "（更稳但变点估计滞后约 window/2 ≈ 30 个交易日）。两者日期不一致**是正常的**。",
        "IT 统计量的 1.358 临界值是 **iid 渐近值**，金融收益有波动聚集 → 该检验**偏松**；"
        "所以 flag **同时要求超额幅度达标**，不只靠显著性。",
        "⚠️ **本模块只说「风险特征变了」，不说「为什么变」** —— 换经理、改仓位、"
        "还是**标的本身的波动regime**（黄金/商品/QDII 尤其如此）都可能，需人工判断。"
        "β 对**与大盘低相关**的品种（黄金、纯债）解释力有限，低相关时 β 不稳定。",
    ]
    if not res.get("bench_available"):
        n.append("⚠️ **基准序列缺失 → 本轮没有市场对照**，`vol`/`beta` 里没有 `excess_*` 字段，"
                 "此时**不应**把变化读成风格漂移。")
    elif not res.get("control_available"):
        n.append("⚠️ **中证500 对照序列缺失 → β 无市场对照**，`beta.excess_delta` 不可用。")
    return n


def scan(db, codes=None, **kw) -> dict:
    """批量检测。`codes` 缺省 = **当前持仓的基金**（只读）。返回按告警排序的结果。

    ⚠️ **必须按基金代码去重** —— `holdings` 是**批次**表，同一只基金可能有多个持仓批次
    （2026-10-03 实测：29 个持仓批次只对应 13 只基金），不去重会重复报告同一只基金。
    """
    if codes is None:
        seen, codes = set(), []
        for h in db.get_current_holdings():
            c = h["fund_code"]
            if c not in seen:
                seen.add(c)
                codes.append(c)
    codes = list(dict.fromkeys(codes))
    names = {}
    for c in codes:
        row = db.conn.execute("SELECT fund_name FROM fund_info WHERE fund_code = ?",
                              (c,)).fetchone()
        names[c] = row[0] if row else None
    out = [analyze(db, c, name=names.get(c), **kw) for c in codes]
    out.sort(key=lambda x: (not x.get("alerts"), x.get("code") or ""))
    return {"n": len(out), "alerts": sum(1 for x in out if x.get("alerts")),
            "results": out, "notes": [
                "扫描范围：%s" % ("当前持仓 %d 只" % len(codes)),
                "判据与局限见 `src/analysis/changepoint.py` 模块 docstring（全程只读）。",
            ]}
