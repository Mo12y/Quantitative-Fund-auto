"""
定投（Dollar-Cost Averaging）管理模块。

功能:
- 注册/查看/暂停/恢复定投计划（基金、每期金额、频率）
- 判断计划是否到期（next_run_date <= 今天）
- 执行一期定投：记录买入 + 推进下一期 + 累计统计
- 报告展示定投状态
"""

from datetime import timedelta
from typing import Dict, List, Optional

import pandas as pd

from ..data.database import Database
from .portfolio import PortfolioTracker
from . import trade_rules

# 频率 → 间隔天数
FREQ_DAYS = {
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
}


def match_periods_to_holdings(periods: list, holdings: list) -> Dict:
    """纯函数：按 (fund_code, 日期) 把未挂凭证的期次与买入记录配对。

    历史期次是旧版写进去的，holding_id 为空，只能靠
    `planned_date == holdings.buy_date` 且同 fund_code 来配对。

    Returns: {"links": [{period_id, period_no, date, status, holding_id}], "ambiguous": [...]}
    """
    by_date: Dict[str, list] = {}
    for h in holdings:
        by_date.setdefault(str(h["buy_date"]), []).append(h)

    links, ambiguous = [], []
    for p in periods:
        if p.get("holding_id") is not None:
            continue
        cand = by_date.get(str(p["planned_date"])) or []
        if len(cand) == 1:
            links.append({"period_id": p["id"], "period_no": p["period_no"],
                          "date": p["planned_date"], "status": p["status"],
                          "holding_id": cand[0]["id"]})
        elif len(cand) > 1:
            ambiguous.append({"period_id": p["id"], "period_no": p["period_no"],
                              "date": p["planned_date"],
                              "candidates": [c["id"] for c in cand]})
    return {"links": links, "ambiguous": ambiguous}


class DcaManager:
    """定投计划管理器"""

    _trade_dates_cache = None  # 类级缓存：A股交易日集合

    @classmethod
    def _get_trade_dates(cls):
        """
        获取 A 股交易日历（akshare，节假日准确；失败降级为 None → 只跳过周末）。

        Returns:
            set[str] 或 None（降级模式）
        """
        if cls._trade_dates_cache is None:
            try:
                import akshare as ak
                df = ak.tool_trade_date_hist_sina()
                dates = sorted(pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d"))
                cls._trade_dates_cache = set(dates)
            except Exception:
                cls._trade_dates_cache = "fallback"
        return None if cls._trade_dates_cache == "fallback" else cls._trade_dates_cache

    @classmethod
    def _next_trading_day(cls, date_str: str) -> str:
        """返回 date_str 之后的第一个交易日（跳过周末与法定节假日）"""
        trade_dates = cls._get_trade_dates()
        d = pd.to_datetime(date_str) + timedelta(days=1)
        if trade_dates is None:
            # 降级：只跳过周末
            while d.weekday() >= 5:
                d += timedelta(days=1)
            return d.strftime("%Y-%m-%d")
        while d.strftime("%Y-%m-%d") not in trade_dates:
            d += timedelta(days=1)
        return d.strftime("%Y-%m-%d")

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def next_run_date(frequency: str, from_date: str) -> str:
        """
        计算下一期日期：
        - daily → 下一个交易日（跳过周末/节假日）
        - 其他 → 按频率间隔天数推进
        """
        if frequency == "daily":
            return DcaManager._next_trading_day(from_date)
        days = FREQ_DAYS.get(frequency, 7)
        return (pd.to_datetime(from_date) + timedelta(days=days)).strftime("%Y-%m-%d")

    def get_status(self, today: str = None) -> List[Dict]:
        """
        获取所有激活定投计划的状态（含是否到期）。

        Args:
            today: 基准日期（默认今天），格式 YYYY-MM-DD
        """
        today = today or pd.Timestamp.now().strftime("%Y-%m-%d")
        result = []
        for p in self.db.get_dca_plans():
            if p["status"] != "active":
                continue
            next_run = p.get("next_run_date") or p["start_date"]
            result.append({
                **p,
                "next_run_date": str(next_run),
                "due": str(next_run) <= today,
            })
        return result

    def execute_installment(self, plan_id: int, buy_date: str = None, period_no: int = None) -> Dict:
        """
        执行一期定投：记录一笔买入（含"定投第N期"备注）+ 推进下一期 + 累计统计。

        Args:
            plan_id: 定投计划 ID
            buy_date: 执行日期（默认今天/该期计划日）
            period_no: 指定期号（缺省=最早的待投期次）
        """
        plans = self.db.get_dca_plans()
        plan = next((p for p in plans if p["id"] == plan_id), None)
        if not plan or plan["status"] != "active":
            return {"error": "未找到有效的定投计划"}

        bdate = buy_date or pd.Timestamp.now().strftime("%Y-%m-%d")
        # 关联期次：按指定期号，或最早未执行的期次
        periods = self.db.get_dca_periods(plan_id)
        target = None
        if period_no:
            target = next((p for p in periods if p["period_no"] == period_no), None)
        if target is None:
            pend = [p for p in periods if p["status"] == "pending" and str(p["planned_date"]) <= bdate]
            target = pend[0] if pend else None
        if target:
            bdate = str(target["planned_date"])
        period_no = target["period_no"] if target else (plan["total_periods"] + 1)

        if target is None:
            # 期次表里没有这一期（计划从未 sync，或历史期次都已执行/跳过）→ 补建一行。
            # 保证「已执行」永远有买入凭证可依（决策 B）。
            pid = self.db.upsert_dca_period(plan_id, period_no, bdate,
                                            plan["amount_per_period"])
            target = {"id": pid, "period_no": period_no, "planned_date": bdate}

        # 1. 记录买入（复用持仓跟踪，自动取净值/算份额 + T+1 确认）
        tracker = PortfolioTracker(self.db)
        hid = tracker.add_buy_transaction(
            plan["fund_code"],
            plan["fund_name"],
            bdate,
            plan["amount_per_period"],
            notes=f"定投第{period_no}期",
        )

        # 2. 期次对账：先给该期挂上买入凭证，再据此派生计划汇总值
        if target:
            self.db.update_dca_period(target["id"], status="executed",
                                      executed_date=bdate, holding_id=hid)

        # 3. 推进计划。total_periods/total_amount 一律**派生**自期次表，
        #    不再用「期号」或「旧值 + 每期金额」累加（旧写法可能重复累加或回退）。
        new_next = self.next_run_date(plan["frequency"], bdate)
        total_periods = self.db.count_dca_periods(plan_id, "executed")
        total_amount = round(self.db.sum_dca_periods(plan_id, "executed"), 2)
        self.db.update_dca_plan(
            plan_id,
            next_run_date=new_next,
            total_periods=total_periods,
            total_amount=total_amount,
        )

        return {
            "ok": True,
            "period": period_no,
            "amount": plan["amount_per_period"],
            "buy_date": bdate,
            "next_run_date": new_next,
            "total_amount": total_amount,
            "holding_id": hid,
        }

    # =================================================================
    # 第 7 项：期次自动同步与补录
    # =================================================================

    def _calendar(self) -> set:
        try:
            return self.db.get_trade_date_set()
        except Exception:
            return set()

    def sync_plan(self, plan: dict, today: str = None) -> Dict:
        """把计划的应投期次落到 dca_periods，并对账状态。返回该计划的期次统计。"""
        today = today or pd.Timestamp.now().strftime("%Y-%m-%d")
        periods = trade_rules.period_dates(plan["start_date"], plan.get("frequency", "weekly"),
                                           today, self._calendar())
        for p in periods:
            self.db.upsert_dca_period(plan["id"], p["period_no"], p["date"],
                                      plan["amount_per_period"])

        # 决策 B：`executed` 由买入凭证（holding_id）派生，不再按 period_no 盲目"补齐为已执行"。
        # 旧逻辑 `period_no <= total_periods → executed` 会在「先补录最近一期、再回补历史」时
        # 凭空造出**没有买入**的已执行期次：界面显示"已投 N 期"，实际一笔没买。
        #
        # 这里只做安全方向的对账 + 只读统计：
        #   - 有凭证但还没标 executed → 补标（安全方向）
        #   - 已标 executed 却没有凭证 → 计为 unlinked（历史遗留），**不静默翻回 pending**，
        #     否则会把真实投过的期次误判成"未投"，诱导用户重复扣款。
        rows = self.db.get_dca_periods(plan["id"])
        for r in rows:
            if r["holding_id"] is not None and r["status"] != "executed":
                self.db.update_dca_period(r["id"], status="executed",
                                          executed_date=r["executed_date"] or r["planned_date"])

        rows = self.db.get_dca_periods(plan["id"])
        executed = sum(1 for r in rows if r["status"] == "executed")
        unlinked = sum(1 for r in rows if r["status"] == "executed" and r["holding_id"] is None)
        pending = [r for r in rows if r["status"] == "pending" and str(r["planned_date"]) <= today]

        # total_periods / total_amount 是派生值，不再是可被期号覆盖的可变字段
        self.db.update_dca_plan(plan["id"], last_synced_at=today, total_periods=executed)
        return {
            "plan_id": plan["id"], "expected": len(rows),
            "executed": executed, "unlinked": unlinked,
            "pending": len(pending),
            "last_pending": pending[-1] if pending else None,
        }

    # =================================================================
    # 期次 ↔ 买入凭证 的关联（历史数据补链）
    # =================================================================

    def link_periods(self, plan_id: int = None, dry_run: bool = True) -> Dict:
        """把 dca_periods 与买入凭证（holdings）按 (fund_code, 日期) 关联起来。

        历史期次是用旧版写入的，`holding_id` 为空，凭证只能靠
        `planned_date == holdings.buy_date` 且 `fund_code` 相同来配对。
        默认 dry_run=True 只报告不写库；写库属于批量 UPDATE 既有记录，需显式确认。
        """
        plans = [p for p in self.db.get_dca_plans() if plan_id is None or p["id"] == plan_id]
        links, ambiguous = [], []
        for plan in plans:
            holds = [dict(r) for r in self.db.conn.execute(
                "SELECT id, fund_code, buy_date, status FROM holdings WHERE fund_code = ?",
                (plan["fund_code"],))]
            m = match_periods_to_holdings(self.db.get_dca_periods(plan["id"]), holds)
            for l in m["links"]:
                links.append({**l, "plan_id": plan["id"]})
            ambiguous.extend({**a, "plan_id": plan["id"]} for a in m["ambiguous"])
        if not dry_run:
            for l in links:
                self.db.link_dca_period(l["period_id"], l["holding_id"])
        return {"dry_run": dry_run, "linked": links, "ambiguous": ambiguous,
                "count": len(links)}

    def sync_all(self, today: str = None, auto_execute_latest: bool = True) -> Dict:
        """
        同步所有激活计划；按约定**只自动补录最近一期应投**，其余留作“待补录”。

        Returns:
            dict: {plans:[...], auto_executed:[...], pending_total:int}
        """
        today = today or pd.Timestamp.now().strftime("%Y-%m-%d")
        summaries, auto = [], []
        for plan in self.db.get_dca_plans(status="active"):
            st = self.sync_plan(plan, today)
            if auto_execute_latest and st["pending"] > 0 and st["last_pending"]:
                r = self.execute_installment(plan["id"], period_no=st["last_pending"]["period_no"])
                if r.get("ok"):
                    auto.append({"plan_id": plan["id"], "fund_name": plan["fund_name"],
                                 "period": r["period"], "amount": r["amount"], "date": r["buy_date"]})
                    st = self.sync_plan(plan, today)      # 重新对账
            summaries.append({**st, "fund_name": plan["fund_name"], "fund_code": plan["fund_code"],
                              "frequency": plan.get("frequency")})
        return {"plans": summaries, "auto_executed": auto,
                "pending_total": sum(s["pending"] for s in summaries)}

    def backfill(self, plan_id: int, today: str = None) -> Dict:
        """一键补录：把该计划所有到期未投的期次依次执行。"""
        today = today or pd.Timestamp.now().strftime("%Y-%m-%d")
        plan = next((p for p in self.db.get_dca_plans() if p["id"] == plan_id), None)
        if not plan:
            return {"error": "未找到该定投计划"}
        self.sync_plan(plan, today)
        done = []
        rows = self.db.get_dca_periods(plan_id)
        pend = [r for r in rows if r["status"] == "pending" and str(r["planned_date"]) <= today]
        for r in pend:
            res = self.execute_installment(plan_id, period_no=r["period_no"])
            if res.get("ok"):
                done.append({"period": res["period"], "date": res["buy_date"], "amount": res["amount"]})
        return {"ok": True, "executed": done, "count": len(done)}
