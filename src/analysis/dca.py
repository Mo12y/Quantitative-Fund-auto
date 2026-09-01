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

# 频率 → 间隔天数
FREQ_DAYS = {
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
}


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

    def execute_installment(self, plan_id: int, buy_date: str = None) -> Dict:
        """
        执行一期定投：记录一笔买入（含"定投第N期"备注）+ 推进下一期 + 累计统计。

        Args:
            plan_id: 定投计划 ID
            buy_date: 执行日期（默认今天）
        """
        plans = self.db.get_dca_plans()
        plan = next((p for p in plans if p["id"] == plan_id), None)
        if not plan or plan["status"] != "active":
            return {"error": "未找到有效的定投计划"}

        buy_date = buy_date or pd.Timestamp.now().strftime("%Y-%m-%d")
        period_no = plan["total_periods"] + 1

        # 1. 记录买入（复用持仓跟踪，自动取净值/算份额）
        tracker = PortfolioTracker(self.db)
        tracker.add_buy_transaction(
            plan["fund_code"],
            plan["fund_name"],
            buy_date,
            plan["amount_per_period"],
            notes=f"定投第{period_no}期",
        )

        # 2. 推进计划
        new_next = self.next_run_date(plan["frequency"], buy_date)
        self.db.update_dca_plan(
            plan_id,
            next_run_date=new_next,
            total_periods=period_no,
            total_amount=round(plan["total_amount"] + plan["amount_per_period"], 2),
        )

        return {
            "ok": True,
            "period": period_no,
            "amount": plan["amount_per_period"],
            "next_run_date": new_next,
            "total_amount": round(plan["total_amount"] + plan["amount_per_period"], 2),
        }
