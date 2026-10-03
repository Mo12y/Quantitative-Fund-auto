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
        ],
    }
