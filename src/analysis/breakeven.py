"""回本门槛（P2）—— 把费率规则变成「**这笔操作要涨多少才不亏**」。

依据（`docs/审计修复记录.md` 第四批 §5 P2）
------------------------------------------
> 把费率规则（申购费 / 赎回费 FIFO / 7 天惩罚 1.5%）变成「**这笔操作要涨多少才不亏**」
> 的门槛，而不是只报一个费用数字。

三类门槛（**都不重写费率** —— 一律委托 `portfolio` 的单一真源）
--------------------------------------------------------------
| 门槛 | 什么时候发生 | 来源 |
|---|---|---|
| **申购门槛** | 买入时就已落后 | `portfolio.purchase_fee_rate(fund_name)`：C/E/I 类 **0**（改收销售服务费，已从每日净值扣）；A 类/未知 0.15% |
| **赎回门槛** | **现在**卖要付 | `portfolio.redeem_fee_rate(text, held_days)`：**持有 <7 天**取基金费率与监管下限 **1.5% 孰高**；**≥7 天为 0** |
| **免费日** | 持有满 7 天 | 由上面的规则反推 —— 告诉你"**再等几天就不用交这笔**" |

为什么要单独做这个
------------------
费率在报告里通常只作为一个**数字**出现（"费率 1.5%"），而真正影响决策的形式是**门槛**：
- "这笔现在还亏 0.4%，但赎回费是 1.5% → **现在卖是双重亏损**"；
- "再持有 **3 天**赎回费就归零了 → 别急着卖"。

⚠️ 口径（必须随结果声明）
------------------------
- 本项目赎回费真源**只对 <7 天这一档计费**（唯一明确且惩罚性的一档），
  更长持有期的阶梯费率**未采集** → 结果对"持有 30 天~1 年"的中档**是乐观的**（实际可能有 0.25%~0.5%）。
- 申购费对 A 类用**保守默认 0.15%**（真实费率采集属《数据源扩展计划书》阶段 1）。
- 管理费/托管费/销售服务费（TER）**已从每日净值里扣**，不构成门槛，故不重复计入。
"""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta

from .portfolio import SHORT_HOLD_DAYS, purchase_fee_rate, redeem_fee_rate


def _as_date(v):
    if isinstance(v, _date):
        return v
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except (TypeError, ValueError):
        return None


def lot_breakeven(lot: dict, info: dict, today: _date) -> dict:
    """单个**持仓批次**的回本门槛。

    Args:
        lot: `holdings` 行（需 `fund_code` / `shares` / `buy_amount` / 买入日）
        info: `fund_info` 行（需 `fund_name` / `redeem_fee`）
        today: 基准日（决定"持有多少天"）

    Returns:
        `{fund_code, fund_name, held_days, buy_fee_pct, redeem_fee_pct, threshold_pct,
          free_date, days_to_free, in_penalty, market_value, redeem_cost, net_if_sell,
          net_if_sell_pct}`
    """
    code = lot.get("fund_code") or lot.get("code")
    name = (info or {}).get("fund_name") or ""
    # 持有天数从**确认日**起算（与账务一致）；确认日缺失退到申请日
    d0 = _as_date(lot.get("confirm_date") or lot.get("buy_date") or lot.get("apply_date"))
    held = (today - d0).days if d0 else 0

    buy_fee = purchase_fee_rate(name)
    red_fee = redeem_fee_rate((info or {}).get("redeem_fee"), held)

    # 免费日：持有满 SHORT_HOLD_DAYS 天
    free_date = (d0 + timedelta(days=SHORT_HOLD_DAYS)) if d0 else None
    days_to_free = max((free_date - today).days, 0) if free_date else 0

    shares = float(lot.get("shares") or 0)
    cost = float(lot.get("buy_amount") or 0)
    nav = lot.get("current_nav")
    mv = (shares * float(nav)) if (nav is not None and shares) else None
    redeem_cost = (mv * red_fee) if mv is not None else None
    net = (mv - redeem_cost) if (mv is not None and redeem_cost is not None) else None

    return {
        "fund_code": code,
        "fund_name": name,
        "held_days": held,
        "buy_fee_pct": round(buy_fee * 100, 3),
        "redeem_fee_pct": round(red_fee * 100, 3),
        # 门槛 = 买入门槛 + 现在卖的门槛。买入门槛是**沉没的**（已反映在成本里），
        # 但把两者并列出来才能回答"我这笔从买入到现在一共要涨多少才不亏"。
        "threshold_pct": round((buy_fee + red_fee) * 100, 3),
        "free_date": free_date.isoformat() if free_date else None,
        "days_to_free": days_to_free,
        "in_penalty": bool(held < SHORT_HOLD_DAYS),
        "market_value": round(mv, 2) if mv is not None else None,
        "redeem_cost": round(redeem_cost, 2) if redeem_cost is not None else None,
        "net_if_sell": round(net, 2) if net is not None else None,
        "net_if_sell_pct": (round((net - cost) / cost * 100, 2)
                            if (net is not None and cost) else None),
    }


def analyze(db, today=None) -> dict:
    """全部持仓的红本门槛汇总。**只读**。"""
    today = _as_date(today) or _date.today()
    conn = db.conn
    rows = conn.execute(
        "SELECT h.*, f.fund_name, f.redeem_fee FROM holdings h"
        " LEFT JOIN fund_info f ON f.fund_code = h.fund_code").fetchall()
    if not rows:
        return {"error": "无持仓"}

    lots = []
    # 最新净值：`holdings` 表**没有** current_nav（净值在 fund_nav），
    # 且只能按"持仓涉及的基金"逐只查 —— 全表 `MAX(nav_date) GROUP BY fund_code`
    # 要扫 2,290 万行。持仓通常只有个位数基金，逐只查是毫秒级。
    nav_map = {}
    for code in {r["fund_code"] for r in rows}:
        q = conn.execute(
            "SELECT unit_nav FROM fund_nav WHERE fund_code = ? AND unit_nav IS NOT NULL"
            " ORDER BY nav_date DESC LIMIT 1", (code,)).fetchone()
        if q and q[0]:
            nav_map[code] = float(q[0])

    for r in rows:
        d = dict(r)
        if (d.get("shares") or 0) <= 0:
            continue
        d["current_nav"] = nav_map.get(d.get("fund_code"))
        lots.append(lot_breakeven(d, d, today))
    if not lots:
        return {"error": "无有效持仓批次（份额都为 0）"}

    pen = [x for x in lots if x["in_penalty"]]
    soon = sorted([x for x in lots if x["days_to_free"] > 0],
                  key=lambda x: x["days_to_free"])
    total_mv = sum(x["market_value"] or 0 for x in lots)
    total_cost = sum(x["redeem_cost"] or 0 for x in lots)

    return {
        "today": today.isoformat(),
        "n_lots": len(lots),
        "lots": lots,
        "n_in_penalty": len(pen),
        "penalty_value": round(sum(x["market_value"] or 0 for x in pen), 2),
        "penalty_cost": round(sum(x["redeem_cost"] or 0 for x in pen), 2),
        "next_free": ([{"fund_code": x["fund_code"], "fund_name": x["fund_name"],
                        "free_date": x["free_date"], "days_to_free": x["days_to_free"]}
                       for x in soon[:5]] or None),
        "total_market_value": round(total_mv, 2),
        "total_redeem_cost": round(total_cost, 2),
        "notes": [
            "门槛 = 申购费率 + **现在赎回**的费率。申购费是**沉没的**（已反映在成本里），"
            "并列出来是为了回答『从买入到现在一共要涨多少才不亏』。",
            "⚠️ 本项目赎回费真源**只对「<7 天」这一档计费**（唯一明确且惩罚性的一档）；"
            "更长持有期的阶梯费率**未采集** → 对『持有 30 天~1 年』的中档结果**偏乐观**。",
            "管理费 / 托管费 / 销售服务费（TER）**已从每日净值扣除**，不构成门槛，故不重复计入。",
            "A 类申购费用**保守默认 0.15%**（真实费率采集属《数据源扩展计划书》阶段 1）。",
        ],
    }
