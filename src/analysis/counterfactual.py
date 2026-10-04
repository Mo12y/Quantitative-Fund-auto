"""反事实归因（P1）—— 回答「**如果我当时不动会怎样**」。

依据（`docs/审计修复记录.md` 第四批 §5 P1）
------------------------------------------
> 现有 OOS 只验证了"**策略在池子上**能不能选基"，从未验证"**我自己的操作**值不值"。
> 把手里的数据（`holdings` / `transactions` / 曲线 / 调仓快照）用来回答：
> **如果当时完全不动？如果照温度信号动？如果只定投？**
> —— 这是真金白银用户最该有、而任何通用工具都给不了的东西。

三个反事实（用户 2026-10-03 确认这三个"符合正常交易逻辑"）
--------------------------------------------------------
| # | 假设 | 定义 |
|---|---|---|
| ① | **完全不动** | 所有**买入**都留到期末、**从不卖出**（含实际被卖掉的那些份额） |
| ② | **只定投** | 只保留**定投笔**，同样从不卖出 |
| ③ | **照温度信号动** | 与实际**同日期**买入，但金额按温度放大/缩小（温度低多投） |

为什么必须用 **XIRR** 比
------------------------
四个情形的**投入总额不同**（③ 当然不同，①②也不同），
"盈亏 ÷ 投入"没有时间维度、不可比。XIRR 把每笔按实际持有天数折现 → 可比。
（这正是批次 3 补 XIRR 的直接用途。）

口径与局限（必须随结果声明）
----------------------------
- **定投识别是启发式**：`notes` 含"定投" **或** 金额 == 10.00 元。
  实测两者不完全重合（notes 命中 21 笔 / 金额命中 33 笔），差异来自早期"流水导入"未标注。
  → 结果里会同时给出**两种口径的笔数**，让人看到这个不确定度，而不是藏起来。
- **① 的"完全不动"是理想化的**：它假设你**从不卖出**，包括那次转换。
  现实中可能有流动性需求 —— 它回答的是"操作本身有没有正贡献"，不是"你该不该卖"。
- **未计**：申购费 / 赎回费（净值已扣管理费托管费，但申赎费是额外现金支出）。
  费率会让所有情形**同向变差**，不改变相对结论，但要记住绝对收益偏乐观。
- 起点取**实际第一笔买入日**，止点取**最新净值日**；所有情形用同一对起止点。
"""
from __future__ import annotations

import statistics

from .xirr import xirr

#: 定投识别（启发式，见模块 docstring「口径与局限」）
DCA_AMOUNT = 10.0
DCA_NOTE_KEY = "定投"


def is_dca(note, amount) -> bool:
    """该笔买入是否算定投：notes 含"定投" **或** 金额等于 10.00。"""
    if note and DCA_NOTE_KEY in str(note):
        return True
    try:
        return abs(float(amount) - DCA_AMOUNT) < 0.005
    except (TypeError, ValueError):
        return False


def temp_multiplier(temp):
    """温度 → 投入倍率（③ 的策略）。

    `2.0 − (温度 − 30)/40 × 1.5`，夹在 **[0.5, 2.0]**：
    30° → 2.0 倍，50° → 1.25 倍，70° → 0.5 倍。单调递减、连续、无档位跳变
    （与 `thermometer` 的枢轴模型同一纪律）。
    """
    if temp is None:
        return 1.0
    try:
        t = float(temp)
    except (TypeError, ValueError):
        return 1.0
    m = 2.0 - (t - 30.0) / 40.0 * 1.5
    return min(max(m, 0.5), 2.0)


def load_buy_flows(conn, include_transfer_target=True):
    """取**所有已确认买入**（含后来被卖出的）。

    Returns: `[{code, date, amount, note, is_dca}, ...]` 按日期升序。
    """
    rows = conn.execute(
        "SELECT fund_code, COALESCE(confirm_date, apply_date) d, amount, notes, kind"
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


def nav_lookup(conn, code, on_or_before: str, tol_days: int = 10):
    """买入日（或之前最近）的单位净值。取不到返回 None（**不猜**）。"""
    r = conn.execute(
        "SELECT unit_nav FROM fund_nav WHERE fund_code = ? AND nav_date <= ?"
        " ORDER BY nav_date DESC LIMIT 1", (code, on_or_before)).fetchone()
    if r and r[0]:
        return float(r[0])
    return None


def simulate(buys, conn, end_date, scale=None, label=""):
    """把一组买入**全部持有到期末**（从不卖出），算投入 / 市值 / 盈亏 / XIRR。

    Args:
        buys: `load_buy_flows` 的输出（或其子集）
        conn: 只读连接
        end_date: 期末（用其单位净值估值）
        scale: `{日期: 倍率}` 或 `callable(date) -> 倍率`；`None` = 1.0（实际金额）
        label: 结果里回填的名称

    Returns:
        `{label, n_buys, invested, shares, end_value, pnl, pnl_pct, xirr_pct,
           first_date, last_date, missing_nav}` —— 取不到净值的笔**计入 missing_nav**，
        不静默丢弃（否则投入与市值会对不上）。
    """
    if not buys:
        return {"label": label, "n_buys": 0, "invested": 0.0, "end_value": 0.0,
                "pnl": 0.0, "pnl_pct": None, "xirr_pct": None,
                "first_date": None, "last_date": None, "missing_nav": 0}

    def factor(d):
        if scale is None:
            return 1.0
        return float(scale(d)) if callable(scale) else float(scale.get(d, 1.0))

    cashflows, invested, end_value, missing = [], 0.0, 0.0, 0
    first_d, last_d = None, None
    for b in buys:
        f = factor(b["date"])
        amt = round(b["amount"] * f, 2)
        if amt <= 0:
            continue
        buy_nav = nav_lookup(conn, b["code"], b["date"])
        end_nav = nav_lookup(conn, b["code"], end_date)
        if not buy_nav or not end_nav:
            missing += 1
            continue
        shares = amt / buy_nav
        cashflows.append((b["date"], -amt))
        invested += amt
        end_value += shares * end_nav
        first_d = first_d or b["date"]
        last_d = b["date"]

    if invested <= 0:
        return {"label": label, "n_buys": 0, "invested": 0.0, "end_value": 0.0,
                "pnl": 0.0, "pnl_pct": None, "xirr_pct": None,
                "first_date": None, "last_date": None, "missing_nav": missing}

    # 期末清算（视作一笔流入）→ 才能求 XIRR
    cf = cashflows + [(end_date, round(end_value, 2))]
    r = xirr(cf)
    pnl = end_value - invested
    return {"label": label, "n_buys": len(cashflows), "invested": round(invested, 2),
            "end_value": round(end_value, 2), "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / invested * 100, 2) if invested else None,
            "xirr_pct": round(r * 100, 2) if r is not None else None,
            "first_date": first_d, "last_date": last_d, "missing_nav": missing}


def sample_adequacy(start_date: str, end_date: str) -> dict:
    """样本充分性声明 —— **这一节是给"别被短期数据迷惑"用的**。

    用户 2026-10-03 的原话：「不要被短期数据迷惑了，只是市场正常波动，
    只不过我开始投的时候刚好熊市亏钱」。**这条提醒是对的，而且与 M1 同源**：

    · M1 实测：连 **3 年**夏普的组间真实方差都被估为 0（无排序信息）；
    · 本模块的区间通常只有 **几个月** → 信噪比只会更低。

    所以这里显式区分两件事：
      ① 「这段时间**发生了什么**」—— 可观测量，本模块能给；
      ② 「**我的操作好不好**」—— 需要长样本 + 剥离 beta，本模块**给不了**。
    """
    from datetime import date as _d

    try:
        d0 = _d.fromisoformat(str(start_date)[:10])
        d1 = _d.fromisoformat(str(end_date)[:10])
    except (TypeError, ValueError):
        return {"years": None, "days": None, "sufficient": False,
                "warning": "起止日期无法解析，无法判断样本充分性"}
    days = max((d1 - d0).days, 0)
    years = days / 365.25
    if years >= 2.0:
        return {"years": round(years, 2), "days": days, "sufficient": True,
                "warning": None}
    return {
        "years": round(years, 2), "days": days, "sufficient": False,
        "warning": (
            "⚠️ 区间仅 **%.1f 个月（%.2f 年）** —— **不足以判断操作能力**。"
            "理由有两条，都不靠直觉："
            "① 单期收益的信噪比极低（M1 实测：连 3 年夏普的组间真实方差都是 0）；"
            "② 起点若恰逢熊市/牛市，会把 **beta 记成 alpha**。"
            "→ 本结果只能读「这段时间发生了什么」，**不能**读「我的操作好不好」。"
            % (days / 30.0, years)),
    }


def benchmark_return(conn, start_date: str, end_date: str, code: str = "000300"):
    """同期基准涨跌（%）。把 **beta** 从 **alpha** 里分出来读。

    依据：如果区间内基准本身在跌，那"亏钱"首先是市场（beta），
    不是操作（alpha）。不给基准就报绝对收益，等于把 beta 算进 alpha。
    """
    r0 = conn.execute("SELECT close FROM index_daily WHERE index_code = ? AND trade_date <= ?"
                      " ORDER BY trade_date DESC LIMIT 1", (code, start_date)).fetchone()
    r1 = conn.execute("SELECT close FROM index_daily WHERE index_code = ? AND trade_date <= ?"
                      " ORDER BY trade_date DESC LIMIT 1", (code, end_date)).fetchone()
    if not r0 or not r1 or not r0[0]:
        return None
    try:
        return round((float(r1[0]) / float(r0[0]) - 1.0) * 100, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _nav_path(conn, code, start_date, end_date):
    """某基金在 `[start_date, end_date]` 的 `[(date, unit_nav), ...]`（升序）。

    用于**同标的卖出规则**模拟：逐笔买入要跟踪它自己的净值路径。
    """
    rows = conn.execute(
        "SELECT nav_date, unit_nav FROM fund_nav WHERE fund_code = ?"
        " AND nav_date >= ? AND nav_date <= ? AND unit_nav IS NOT NULL"
        " ORDER BY nav_date", (code, start_date, end_date)).fetchall()
    return [(str(r[0]), float(r[1])) for r in rows if r[1]]


#: 内置卖出规则：(键, 人类可读名, 判定函数)
#: 判定函数签名 `(nav, buy_nav, date, temp) -> bool`；`temp` 为当日温度（可能 None）
_BUILTIN_RULES = (
    ("never", "从不卖（买入持到底）", lambda nav, b, d, t: False),
    ("tp10", "涨 10% 止盈", lambda nav, b, d, t: nav >= b * 1.10),
    ("tp20", "涨 20% 止盈", lambda nav, b, d, t: nav >= b * 1.20),
    ("sl5", "跌 5% 止损", lambda nav, b, d, t: nav <= b * 0.95),
    ("sl10", "跌 10% 止损", lambda nav, b, d, t: nav <= b * 0.90),
    ("temp70", "温度 > 70° 时卖", lambda nav, b, d, t: (t is not None and t > 70.0)),
)


def simulate_sell_rules(db, *, rules=None, end_date=None) -> dict:
    """**固定买入清单**，只变**卖出规则** —— 纯分离"卖出时机的价值"。

    为什么需要它（`compare()` 的局限）：那四个情形**标的构成不同**
    （定投笔偏纳斯达克 QDII、主动笔偏 A 股/黄金），差异里混着「策略」与「标的」两个因素，
    无法判断"到底是我卖得好，还是我碰巧买到了好标的"。

    本函数**冻结买入**（同一批基金、同一批日期、同一批金额），只让**卖出规则**变化：

    | 规则 | 含义 |
    |---|---|
    | `never` | 从不卖（基准） |
    | `tp10` / `tp20` | 单笔浮盈 ≥10% / ≥20% 就卖 |
    | `sl5` / `sl10` | 单笔浮亏 ≥5% / ≥10% 就卖 |
    | `temp70` | 当日市场温度 > 70° 就卖 |

    实现：对每笔买入跟踪它**自己的净值路径**，规则首次触发即在该日卖出（按当日净值结算）；
    到期末仍未卖出的，按期末净值估值。全程只读。

    Returns:
        `{end_date, n_buys, results: [{key, label, invested, end_value, pnl,
          pnl_pct, xirr_pct, n_sold, first_sell_date}], notes: [...]}`
    """
    conn = db.conn
    end = end_date or db.get_latest_nav_date()
    buys = load_buy_flows(conn)
    if not buys or not end:
        return {"error": "无买入流水或最新净值日"}

    # 温度查表（提前取一次，避免逐日查库）
    temp_rows = conn.execute(
        "SELECT trade_date, temperature FROM market_temperature"
        " WHERE temperature IS NOT NULL ORDER BY trade_date").fetchall()
    temps = [(str(r[0]), float(r[1])) for r in temp_rows]

    def temp_on(d):
        """当日或之前最近一次的温度（前向填充；没有更早的 → None）。"""
        lo, hi, hit = 0, len(temps) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if temps[mid][0] <= d:
                hit = temps[mid][1]
                lo = mid + 1
            else:
                hi = mid - 1
        return hit

    # 预取每笔买入的净值路径。
    # ⚠️ 缓存键必须是 `(code, date)` 而**不是** `code` ——
    # 同一只基金常有多笔买入（定投），若按 code 缓存，后一笔会命中前一笔的路径，
    # 于是 `path[0]` 取到**前一笔的日期**的净值，把买入成本算错。
    # （2026-10-03 被测试抓到：两笔 100 元分别按 1.0 / 1.2 买入，市值却按 200 份算。）
    cache = {}
    prepared = []
    for b in buys:
        key = (b["code"], b["date"])
        if key not in cache:
            cache[key] = _nav_path(conn, b["code"], b["date"], end)
        path = cache[key]
        if not path:
            continue
        buy_nav = path[0][1]
        if buy_nav <= 0:
            continue
        prepared.append((b, buy_nav, path))

    if not prepared:
        return {"error": "所有买入都取不到净值路径"}

    use = rules or _BUILTIN_RULES
    results = []
    for key, label, decide in use:
        cashflows, invested, end_value, proceeds = [], 0.0, 0.0, 0.0
        n_sold, first_sell = 0, None
        for b, buy_nav, path in prepared:
            amt = b["amount"]
            shares = amt / buy_nav
            invested += amt
            cashflows.append((b["date"], -amt))
            sold = None
            for d, nav in path:
                if d == b["date"]:
                    continue
                if decide(nav, buy_nav, d, temp_on(d)):
                    sold = (d, nav)
                    break
            if sold:
                d, nav = sold
                got = round(shares * nav, 2)
                cashflows.append((d, got))
                proceeds += got                 # ⚠️ 卖出回款必须累计
                n_sold += 1
                if first_sell is None:
                    first_sell = d
            else:
                end_value += shares * path[-1][1]
        cashflows.append((end, round(end_value, 2)))
        r = xirr(cashflows)
        # ⚠️ 「回收总额」= 未卖部分的期末市值 + 已卖部分的回款。
        # 曾经只用 `end_value` 算 pnl → 卖得越多 pnl 越"惨"（回款没算进去），
        # 会出现「XIRR 为正、pnl 为负」的自相矛盾。2026-10-03 修正。
        total_value = end_value + proceeds
        pnl = total_value - invested
        results.append({
            "key": key, "label": label, "invested": round(invested, 2),
            "end_value": round(end_value, 2),           # 仍保留：未卖部分的期末市值
            "proceeds": round(proceeds, 2),             # 已卖部分的回款
            "total_value": round(total_value, 2),       # 回收总额（pnl 的口径）
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / invested * 100, 2) if invested else None,
            "xirr_pct": round(r * 100, 2) if r is not None else None,
            "n_sold": n_sold, "n_buys": len(prepared), "first_sell_date": first_sell,
        })
    return {
        "end_date": end, "n_buys": len(prepared),
        "span": "%s ~ %s" % (prepared[0][0]["date"], end),
        "results": results,
        "notes": [
            "**买入被冻结**（同一批基金/日期/金额），只有**卖出规则**在变 → "
            "差异纯粹来自『卖出时机』，不再混入『标的构成』。",
            "仍未卖出的部分按**期末净值**估值（视作清算），否则 XIRR 无解。",
            "单笔规则按『该笔自己的买入净值』判断浮盈浮亏，不是组合整体。",
            "未计申赎费（会让所有规则同向变差，不改相对排序）。",
        ],
    }


def compare(db) -> dict:
    """四个情形并排（实际 / 完全不动 / 只定投 / 照温度信号动）。**只读**。"""
    from .portfolio import PortfolioTracker

    conn = db.conn
    end_date = db.get_latest_nav_date()
    buys = load_buy_flows(conn)
    if not buys or not end_date:
        return {"error": "无买入流水或最新净值日，无法归因"}

    dca_buys = [b for b in buys if b["is_dca"]]

    # ③ 温度倍率：按买入日期查温度
    def temp_scale(d):
        r = conn.execute("SELECT temperature FROM market_temperature WHERE trade_date <= ?"
                         " ORDER BY trade_date DESC LIMIT 1", (d,)).fetchone()
        return temp_multiplier(r[0] if r else None)

    pt = PortfolioTracker(db)
    actual = pt.get_xirr()

    return {
        "end_date": end_date,
        "span": "%s ~ %s" % (buys[0]["date"], end_date),
        "n_buys_total": len(buys),
        "n_buys_dca": len(dca_buys),
        "dca_rule": "notes 含「定投」或金额 == ¥%.2f（启发式，见模块 docstring）" % DCA_AMOUNT,
        # ⭐ 这两项是「别被短期数据迷惑」的机制化落实（用户 2026-10-03 提醒）
        "sample": sample_adequacy(buys[0]["date"], end_date),
        "benchmark_pct": benchmark_return(conn, buys[0]["date"], end_date),
        "benchmark_name": "沪深300",
        "cases": {
            "actual": {"label": "实际（你自己操作的）",
                       "n_buys": actual.get("n_flows"), "invested": actual.get("invested"),
                       "end_value": actual.get("settled_value"),
                       "pnl": (round((actual.get("settled_value") or 0)
                                     - (actual.get("invested") or 0), 2)),
                       "xirr_pct": actual.get("xirr_pct")},
            "hold_all": simulate(buys, conn, end_date, label="完全不动（买了就不卖）"),
            "dca_only": simulate(dca_buys, conn, end_date, label="只定投（只留 10 元笔、不卖）"),
            "temp_tilt": simulate(buys, conn, end_date, scale=temp_scale,
                                  label="照温度信号动（温度低多投）"),
        },
        "notes": [
            "四个情形**投入总额不同**，所以只能比 XIRR（资金加权年化），不能比盈亏额。",
            "未计申购费/赎回费 —— 会让所有情形同向变差，不改变相对排序。",
            "① 假设从不卖出（含那次转换）；它回答『操作本身有没有正贡献』，不是『你该不该卖』。",
            "⭐ **先减掉 beta 再读 alpha**：若同期基准（沪深300）本身在跌，那『亏钱』首先是**市场**，"
            "不是操作。只看绝对收益会把 beta 记成 alpha —— 这是本项目反复强调的读法纪律。",
            "⚠️ **归因局限**：各情形**标的构成不同**（定投笔偏 QDII、主动笔偏 A 股/黄金）→ "
            "差异里混着「策略」与「标的」两个因素。要纯分离请看 `same_fund_counterfactual()`。",
        ],
    }


def same_fund_counterfactual(db, end_date=None, min_buys: int = 3,
                             min_amount: float = 100.0) -> dict:
    """**同标的**反事实：锁死**同一只基金**与**总投入**，只变**买入时机**。

    ## 为什么需要它（`docs/审计修复记录.md` §19 的归因局限）

    `compare()` 的四个情形**标的构成不同** —— 定投笔偏纳斯达克 QDII、主动笔偏 A 股 / 黄金，
    所以它们的差异里混着「**策略**」与「**标的**」两个因素，无法归因给哪一个。
    本函数把两个混淆项都锁死：**同一只基金、同一笔总投入**，于是三者唯一的差别是**时机**：

      ① **实际操作** —— 真实的分笔日期与金额
      ② **一次性**   —— 首笔日把总额一次投入
      ③ **等额定投** —— 首笔日 ~ 末笔日之间，按该基金**真实交易日**等额 N 笔（N = 实际笔数）

    于是 `实际 − 一次性` 就是"**分期 vs 一次**"的时机价值，`实际 − 等额定投` 是
    "**随意择时 vs 机械定投**"的差异 —— 都**不含标的因素**。

    ⚠️ 仍然**不含费率**（会让三者同向变差、不改相对排序）；
    ⚠️ 仍然是**持有到期末不卖**（回答的是"买入时机"，不是"该不该卖"）。

    Returns: `{end_date, funds: [...], summary: {...}, notes: [...]}`。**只读。**
    """
    conn = db.conn
    end_date = end_date or db.get_latest_nav_date()
    buys = load_buy_flows(conn)
    if not buys or not end_date:
        return {"error": "无买入流水或最新净值日，无法做同标的反事实"}

    by_code: dict = {}
    for b in buys:
        by_code.setdefault(b["code"], []).append(b)

    funds = []
    n_skip_buys = n_skip_amt = 0
    for code, bs in sorted(by_code.items()):
        if len(bs) < min_buys:
            n_skip_buys += 1
            continue
        total = round(sum(x["amount"] for x in bs), 2)
        if total < min_amount:
            n_skip_amt += 1
            continue
        d0, d1 = bs[0]["date"], bs[-1]["date"]
        row = conn.execute("SELECT fund_name FROM fund_info WHERE fund_code = ?",
                           (code,)).fetchone()
        name = (row[0] if row else "") or ""

        # ② 一次性：首笔日一次投入总额
        lump = [{"code": code, "date": d0, "amount": total, "note": "lump", "is_dca": False}]

        # ③ 等额定投：用**该基金真实交易日**等间隔取 N 个日期（避免落在非交易日）
        n = len(bs)
        path = _nav_path(conn, code, d0, d1)
        even_dca = None
        if len(path) >= n:
            idx = [round(i * (len(path) - 1) / (n - 1)) for i in range(n)]
            even_dca = [{"code": code, "date": path[j][0], "amount": round(total / n, 2),
                         "note": "even_dca", "is_dca": True} for j in idx]

        a = simulate(bs, conn, end_date, label="实际操作")
        l = simulate(lump, conn, end_date, label="一次性")
        d = simulate(even_dca, conn, end_date, label="等额定投") if even_dca else None

        row_out = {
            "code": code, "name": name, "n_buys": n, "invested": total,
            "span": "%s ~ %s" % (d0, d1),
            "actual": a, "lump": l, "even_dca": d,
        }
        for key, other in (("vs_lump", l), ("vs_even_dca", d)):
            row_out[key] = (round((a["pnl_pct"] or 0) - (other["pnl_pct"] or 0), 2)
                            if other and a.get("pnl_pct") is not None
                            and other.get("pnl_pct") is not None else None)
        funds.append(row_out)

    if not funds:
        return {"end_date": end_date, "funds": [],
                "summary": {"n_funds": 0, "n_funds_total_bought": len(by_code),
                            "skipped_few_buys": n_skip_buys,
                            "skipped_small_amount": n_skip_amt},
                "notes": ["没有满足条件的基金（需 ≥%d 笔买入且总额 ≥ ¥%.0f）："
                          "买入过的 %d 只里，%d 只笔数不足、%d 只总额太小。"
                          % (min_buys, min_amount, len(by_code), n_skip_buys, n_skip_amt)]}

    def _agg(key):
        vals = [f[key] for f in funds if f.get(key) is not None]
        if not vals:
            return None
        vals_sorted = sorted(vals)
        return {"n": len(vals), "median": round(statistics.median(vals), 2),
                "mean": round(statistics.fmean(vals), 2),
                "worse": sum(1 for v in vals if v < 0),
                "better": sum(1 for v in vals if v > 0)}

    return {
        "end_date": end_date,
        "funds": funds,
        "summary": {
            "n_funds": len(funds),
            "invested_total": round(sum(f["invested"] for f in funds), 2),
            # ⚠️ 样本局限**必须可见**：n 很小，结论不能推广（本项目读法纪律）
            "n_funds_total_bought": len(by_code),
            "skipped_few_buys": n_skip_buys,
            "skipped_small_amount": n_skip_amt,
            "vs_lump": _agg("vs_lump"),
            "vs_even_dca": _agg("vs_even_dca"),
        },
        "notes": [
            "锁死了**同一只基金 + 同一笔总投入**，所以差异**只来自买入时机**，不含标的因素。",
            "（对照）`compare()` 的四个情形标的构成不同 —— 那才是它无法归因的原因。",
            "⚠️ 仍未计申购费/赎回费；⚠️ 仍是**持有到期末不卖** → 回答的是「买入时机」，"
            "不是「该不该卖」。卖出规则的价值见 `simulate_sell_rules()`。",
            "⭐ 读法：`vs_lump` 为正 = 「分期买」比「一次买」好；`vs_even_dca` 为正 = "
            "「你的随意择时」比「机械等额定投」好。两者都为正才说明**择时**真有价值。",
            "⚠️ **样本很小，结论不可推广** —— 计入 %d 只，另有 %d 只因买入笔数不足、"
            "%d 只因总额太小被排除。单只基金的 pp 差异受区间影响极大，只看中位数会over-read。"
            % (len(funds), n_skip_buys, n_skip_amt),
        ],
    }
