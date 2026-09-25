"""现金分红 / 红利再投 —— 检测与入账（实现 `docs/现金分红设计方案.md` §3/§5）。

**为什么需要**：`holdings` 只有 `buy`/`sell` 流水，没有分红入账路径。
基金现金分红时**单位净值（`unit_nav`）向下跳**而**累计净值（`acc_nav`）不跳**，
而本项目市值 = `份额 × unit_nav` → 除息日当天**总资产凭空少掉一笔分红**
（净值下挫、份额没变、现金没入账），收益率与已实现/未实现盈亏跟着失真。

> 实测：用户 7 只持仓在全部历史净值上**未检出任何除息日**，所以这是**通用化阻塞项**，
> 不是当下的账目错误（设计稿 §1）。

**口径（§2）**：除息日由 `diff = acc_nav − unit_nav` 的**台阶式上跳**唯一确定，
跳幅即每份分红；再投净值默认取除息日当日净值。

**自动落账（用户 2026-09-16 拍板，D4）**：不设人工确认环节 —— 因此检测器必须
自带**拒收条件**（宁可漏、不可错），三条同时满足才允许自动入账。

**铁律**：金额写错会污染成本与收益（分红是对**人**的负债）。所以
· 每笔落账都把证据写进流水（`dividend_per_share`、`apply_date`）；
· 幂等键 `(holding_id, apply_date, kind)` —— 重跑检测不会重复入账。
"""
from __future__ import annotations

#: 跳幅容差（设计稿 §3 建议值）
TOL = 1e-4

#: 允许自动落账的两种流水 kind
KIND_REINVEST = "div_reinvest"
KIND_CASH = "div_cash"

POLICY_REINVEST = "reinvest"
POLICY_CASH = "cash"


def detect_dividends(rows, tol: float = TOL):
    """从**单位/累计净值**序列里检出除息日。

    Args:
        rows: 升序的净值行，每行需含 `nav_date` / `unit_nav` / `acc_nav`
              （dict 或 sqlite3.Row 均可）

    Returns:
        (events, suspects)：`events` 为**通过全部拒收条件**、可自动落账的除息日；
        `suspects` 为形状像分红但**未通过条件**的候选（含 `reason`），只展示不落账。
        每项：`{date, per_share, unit_nav, acc_nav, unit_fall}`

    ⚠️ 拒收条件（设计稿 §3，系"自动落账"的安全底线）—— **条件是两条，不是原文写的那三条**：

        ① 每份分红落在 `(0, unit_nav)` 内，且当日 `unit_nav > 0` —— 排除脏数据（acc 被写成 0／异常跳变）；
        ② `acc_nav >= unit_nav` —— 否则该基金累计净值不可信，**整条序列弃用**。

    🔴 **设计稿原文的第 1 条（`jump` 必须≈当日 `unit_nav` 跌幅）已删除 —— 它数学上是错的**
    （2026-09-25 实测发现）。推导：设当日真实收益 r，
        acc₁  = acc₀(1+r)                （累计净值含分红，不因分红下挫）
        unit₁ = unit₀(1+r) − d           （单位净值被每份分红 d 打掉）
        → jump = diff₁ − diff₀ = diff₀·r + d
        → fall = unit₀ − unit₁ = d − unit₀·r
        → **jump − fall = r·acc₀ ≈ r × 1.1**
    即 `jump ≈ fall` **只在"当日涨跌恰为 0"时成立**；日常 0.1% 的波动就产生 1.1e-3 的偏差，
    远大于容差 1e-4 → **真分红被大面积误拒**。实测（本机真实净值）：
        000016 华夏纯债C 设计稿预期 10 次 → 含该条只检出 3 次；**去掉后 10 次** ✓
        000033 易方达信用债C 预期 26 次 → 含该条 3 次；**去掉后 26 次** ✓
        000025 大摩双利增强C 预期 3 次 → 含该条 1 次；**去掉后 3 次** ✓
    而该条想防的「份额折算 / 净值归一化」**本来就不产生 jump**（unit 与 acc 同幅下挫 → diff 不变），
    已被 `jump > tol` 挡掉 —— 所以它既冗余又有害。
    （用户 27 只持仓在两种口径下都是 0 笔，去掉后不会引入误报。）
    """
    events, suspects = [], []
    seq = []
    for r in rows:
        try:
            u = float(r["unit_nav"])
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        try:
            a = float(r["acc_nav"]) if r["acc_nav"] is not None else None
        except (TypeError, ValueError, KeyError, IndexError):
            a = None
        d = r["nav_date"] if not hasattr(r, "keys") or "nav_date" in r.keys() else None
        if d is None:
            continue
        if u <= 0 or a is None:
            continue                      # 无累计净值 → 无法判断，跳过（不猜）
        if a < u - tol:                    # 条件③：整条序列不可信
            return [], [{"date": str(d), "reason": "acc_nav < unit_nav（该基金累计净值数据不可信，整条序列弃用）"}]
        seq.append((str(d), u, a))

    run = None                             # 上一日：(diff, unit)
    for d, u, a in seq:
        diff = a - u
        if run is not None:
            prev_diff, prev_u = run
            jump = diff - prev_diff
            if jump > tol:
                ev = {"date": d, "per_share": round(jump, 6), "unit_nav": u,
                      "acc_nav": a, "unit_fall": round(prev_u - u, 6)}
                # 复现设计稿所需的 `unit_fall` 仍记录（审计用），但**不作为拒收条件** —— 见上文推导。
                if not (0 < jump < u):
                    ev["reason"] = "每份分红不在 (0, unit_nav) 内（脏数据），拒收"
                    suspects.append(ev)
                else:
                    events.append(ev)      # 条件①②全过 → 可自动落账
        run = (diff, u)
    return events, suspects


def post_dividend(db, holding: dict, event: dict, policy: str = None) -> dict:
    """把一笔已确认的分红按 `dividend_policy` 落账（设计稿 §5）。

    · `reinvest`（默认）：`shares += 金额/再投净值`；成本与份额成本**不动** →
      经济实质是"什么都没发生"（总资产、总成本都不变）。
    · `cash`：`cash_balance += 金额`；份额不动。

    Returns: `{"posted": bool, "kind": ..., "amount": ..., "shares": ..., "reason": ...}`

    **幂等**：键 `(holding_id, apply_date, kind)` —— 同一天同一笔只记一次，重跑不会重复入账。
    """
    hid = holding.get("id")
    shares = float(holding.get("shares") or 0)
    if not hid or shares <= 0:
        return {"posted": False, "reason": "份额为 0 / 无持仓 id，不落账"}
    pol = (policy or holding.get("dividend_policy") or POLICY_REINVEST).strip().lower()
    if pol not in (POLICY_REINVEST, POLICY_CASH):
        pol = POLICY_REINVEST
    per_share = float(event.get("per_share") or 0)
    nav_r = float(event.get("unit_nav") or 0)
    if per_share <= 0 or nav_r <= 0:
        return {"posted": False, "reason": "每份分红或再投净值为 0，不落账"}
    amount = round(shares * per_share, 4)
    kind = KIND_REINVEST if pol == POLICY_REINVEST else KIND_CASH

    cur = db.conn.cursor()
    # 幂等键：同 (holding_id, apply_date, kind) 已存在 → 跳过
    dup = cur.execute(
        "SELECT id FROM transactions WHERE holding_id=? AND apply_date=? AND kind=?",
        (hid, event["date"], kind)).fetchone()
    if dup:
        return {"posted": False, "reason": "已入账过（幂等键命中）", "kind": kind}

    if pol == POLICY_REINVEST:
        new_shares = round(amount / nav_r, 6)
        cur.execute("UPDATE holdings SET shares = shares + ? WHERE id = ?", (new_shares, hid))
        cur.execute(
            "INSERT INTO transactions (holding_id, fund_code, kind, apply_date, confirm_date,"
            " confirm_nav, shares, amount, fee, status, dividend_per_share)"
            " VALUES (?,?,?,?,?,?,?,?,0,?,?)",
            (hid, holding.get("fund_code"), kind, event["date"], event["date"],
             nav_r, new_shares, 0.0, "confirmed", per_share))
        ret = {"posted": True, "kind": kind, "amount": amount, "shares": new_shares}
    else:
        cur.execute("UPDATE holdings SET cash_balance = COALESCE(cash_balance,0) + ? WHERE id = ?",
                    (amount, hid))
        cur.execute(
            "INSERT INTO transactions (holding_id, fund_code, kind, apply_date, confirm_date,"
            " confirm_nav, shares, amount, fee, status, dividend_per_share)"
            " VALUES (?,?,?,?,?,?,0,?,0,?,?)",
            (hid, holding.get("fund_code"), kind, event["date"], event["date"],
             nav_r, amount, "confirmed", per_share))
        ret = {"posted": True, "kind": kind, "amount": amount, "shares": 0.0}
    db.conn.commit()
    return ret


def scan_holding(db, holding: dict):
    """对单只持仓跑一遍检测（只读净值，不落账）。

    Returns: `{"events": [...], "suspects": [...], "policy": ...}`
    """
    rows = db.get_fund_nav(holding["fund_code"],
                           start_date=holding.get("accrual_start")
                           or holding.get("confirm_date") or holding.get("buy_date"))
    events, suspects = detect_dividends(rows)
    return {"events": events, "suspects": suspects,
            "policy": holding.get("dividend_policy") or POLICY_REINVEST}
