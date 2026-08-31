"""
持仓跟踪模块: 管理用户持仓，计算收益和风险指标。

功能:
- 添加/更新持仓
- 计算当前市值和浮动盈亏
- 计算组合收益率
- 风险指标（最大回撤、波动率等）
"""

import pandas as pd
import numpy as np
from datetime import datetime, date
from typing import Optional
from ..data.database import Database


class PortfolioTracker:
    """持仓跟踪器"""

    def __init__(self, db: Database):
        self.db = db

    def get_portfolio_summary(self) -> dict:
        """
        获取当前组合概况。

        Returns:
            dict: {
                'total_invested': float,     # 总投入金额
                'total_market_value': float,  # 总市值（估算）
                'total_pnl': float,           # 总浮动盈亏
                'total_return_pct': float,    # 总收益率(%)
                'holdings_detail': list,      # 每只基金的详情
                'asset_allocation': dict,     # 资产配置
            }
        """
        holdings = self.db.get_current_holdings()

        if not holdings:
            return {
                "total_invested": 0,
                "total_market_value": 0,
                "total_pnl": 0,
                "total_return_pct": 0,
                "holdings_detail": [],
                "asset_allocation": {},
                "has_holdings": False,
            }

        total_invested = 0
        total_market_value = 0
        details = []

        for h in holdings:
            invested = h["buy_amount"]
            shares = h.get("shares", 0)
            total_invested += invested

            # 获取最新净值
            latest_nav = self._get_latest_nav(h["fund_code"])
            if latest_nav is not None and shares > 0:
                current_value = shares * latest_nav
                pnl = current_value - invested
                pnl_pct = (pnl / invested) * 100
            else:
                current_value = invested  # 无法获取净值时假设不变
                pnl = 0
                pnl_pct = 0

            total_market_value += current_value

            # 获取基金类型
            fund_info = self._get_fund_info(h["fund_code"])
            fund_type = fund_info.get("fund_type", "未知") if fund_info else "未知"

            details.append({
                "holding_id": h["id"],
                "fund_code": h["fund_code"],
                "fund_name": h.get("fund_name", h["fund_code"]),
                "fund_type": fund_type,
                "buy_date": h["buy_date"],
                "buy_amount": invested,
                "buy_nav": h.get("buy_nav"),
                "current_nav": latest_nav,
                "shares": shares,
                "current_value": round(current_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "days_held": self._calc_days_held(h["buy_date"]),
            })

        total_pnl = total_market_value - total_invested
        total_return_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0

        # 资产配置
        allocation = self._calc_allocation(details)

        return {
            "total_invested": round(total_invested, 2),
            "total_market_value": round(total_market_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "holdings_detail": details,
            "asset_allocation": allocation,
            "has_holdings": True,
        }

    def add_buy_transaction(
        self,
        fund_code: str,
        fund_name: str,
        buy_date: str,
        amount: float,
        notes: str = "",
    ):
        """
        记录一次买入操作。

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            buy_date: 买入日期 (YYYY-MM-DD)
            amount: 买入金额（元）
            notes: 备注
        """
        # 获取买入日附近的净值
        buy_nav = self._get_nav_on_date(fund_code, buy_date)

        # 计算份额
        shares = amount / buy_nav if buy_nav and buy_nav > 0 else 0

        self.db.add_holding({
            "fund_code": fund_code,
            "fund_name": fund_name,
            "buy_date": buy_date,
            "buy_amount": amount,
            "buy_nav": buy_nav,
            "shares": round(shares, 2),
            "notes": notes,
        })

    def record_sell(
        self,
        holding_id: int,
        sell_date: str,
        sell_amount: float,
    ):
        """
        记录卖出操作。

        Args:
            holding_id: 持仓记录ID
            sell_date: 卖出日期
            sell_amount: 卖出金额（元）
        """
        self.db.sell_holding(holding_id, sell_date, sell_amount)

    def get_performance_history(self) -> pd.DataFrame:
        """
        计算组合历史绩效（每周一个数据点）。

        Returns:
            DataFrame: 每周组合净值
        """
        holdings = self.db.get_current_holdings()
        if not holdings:
            return pd.DataFrame()

        # 找出最早的买入日期
        all_dates = []
        for h in holdings:
            nav_records = self.db.get_fund_nav(h["fund_code"], start_date=h["buy_date"])
            for r in nav_records:
                all_dates.append(r["nav_date"])

        if not all_dates:
            return pd.DataFrame()

        unique_dates = sorted(set(all_dates))
        weekly_dates = unique_dates[::5]  # 每5个交易日取一个（约一周）

        # 对每个时间点计算组合总市值
        result = []
        for d in weekly_dates:
            total_value = 0
            for h in holdings:
                shares = h.get("shares", 0)
                nav = self._get_nav_on_date(h["fund_code"], d)
                if nav and shares:
                    total_value += shares * nav
                else:
                    total_value += h["buy_amount"]  # fallback

            result.append({
                "date": d,
                "total_value": round(total_value, 2),
            })

        return pd.DataFrame(result)

    # ========== 内部方法 ==========

    def _get_latest_nav(self, fund_code: str) -> Optional[float]:
        """获取基金最新净值"""
        nav_records = self.db.get_fund_nav(fund_code)
        if nav_records:
            return nav_records[-1].get("unit_nav")
        return None

    def _get_nav_on_date(self, fund_code: str, target_date: str) -> Optional[float]:
        """获取指定日期附近的净值（找最近的一个）"""
        nav_records = self.db.get_fund_nav(
            fund_code,
            start_date=None,  # 获取全部
        )
        if not nav_records:
            return None

        # 找最接近target_date的净值
        best = None
        best_diff = float("inf")
        for r in nav_records:
            diff = abs((r["nav_date"] - target_date).days if hasattr(r["nav_date"], "days") else abs(
                pd.to_datetime(r["nav_date"]) - pd.to_datetime(target_date)
            ).days)
            if diff < best_diff:
                best_diff = diff
                best = r

        return best.get("unit_nav") if best else None

    def _get_fund_info(self, fund_code: str) -> Optional[dict]:
        """获取基金基本信息"""
        funds = self.db.get_all_funds()
        for f in funds:
            if f["fund_code"] == fund_code:
                return f
        return None

    def _calc_days_held(self, buy_date: str) -> int:
        """计算持有了多少天"""
        try:
            buy = pd.to_datetime(buy_date)
            return (pd.Timestamp.now() - buy).days
        except Exception:
            return 0

    def _calc_allocation(self, details: list) -> dict:
        """计算资产配置比例"""
        total = sum(d["current_value"] for d in details)
        if total == 0:
            return {}

        allocation = {}
        for d in details:
            ftype = d["fund_type"]
            pct = d["current_value"] / total * 100
            if ftype not in allocation:
                allocation[ftype] = 0
            allocation[ftype] += round(pct, 1)

        return allocation
