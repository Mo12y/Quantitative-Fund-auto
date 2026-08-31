"""
策略引擎 v2.0: 月频评估 + 温度阈值触发调仓 + 交易成本模拟。

v1 → v2 核心变化:
1. 调仓频率: 每周 → 每月（减少交易成本）
2. 因子权重: 固定 → 根据市场状态动态调整
3. 调仓触发: 每次都换 → 温度变化>15°才调仓
4. 交易成本: 忽略 → 纳入申购/赎回费模拟

核心理念:
  不追求"选到最好的基金"，而是追求"在合理的时间以合理的成本
  持有合理的基金"。
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Tuple
from ..data.database import Database
from .fund_scorer import FundScreener
from .thermometer import MarketThermometer

# 场外基金交易成本
TRADING_COST = {
    "purchase_fee": 0.0015,    # C类申购费 0.15%
    "redemption_fee_7d": 0.015,  # 持有<7天赎回费 1.5%
    "redemption_fee_30d": 0.005, # 持有7-30天赎回费 0.5%
    "redemption_fee_normal": 0.0, # 持有>30天赎回免费
}


class StrategyEngine:
    """策略引擎 v2.0

    决策流程:
    1. 每月评估一次市场温度
    2. 温度变化 > 15° → 重新计算目标仓位并调仓
    3. 温度变化 < 15° → 持有不变（避免无谓的交易成本）
    4. 调仓时根据市场状态使用不同的因子权重
    """

    # 调仓阈值
    TEMP_CHANGE_THRESHOLD = 15  # 温度变化超过此值才调仓

    # 不同市场状态下的因子权重
    MARKET_STATE_WEIGHTS = {
        # 熊市(冷): 防御为主，重回撤和费率，轻动量
        "cold": {
            "momentum": 0.10, "sharpe": 0.20, "drawdown": 0.35,
            "fee": 0.20, "size": 0.10, "manager": 0.05,
        },
        # 偏冷: 逐步加仓，均衡权重
        "cool": {
            "momentum": 0.15, "sharpe": 0.25, "drawdown": 0.25,
            "fee": 0.15, "size": 0.15, "manager": 0.05,
        },
        # 适中: 均衡
        "normal": {
            "momentum": 0.20, "sharpe": 0.25, "drawdown": 0.20,
            "fee": 0.15, "size": 0.15, "manager": 0.05,
        },
        # 偏热: 重动量(趋势跟踪)，但降低仓位
        "warm": {
            "momentum": 0.30, "sharpe": 0.20, "drawdown": 0.15,
            "fee": 0.15, "size": 0.15, "manager": 0.05,
        },
        # 过热: 防御为主
        "hot": {
            "momentum": 0.10, "sharpe": 0.20, "drawdown": 0.35,
            "fee": 0.20, "size": 0.10, "manager": 0.05,
        },
    }

    # 不同市场状态下的基金类型偏好
    MARKET_STATE_FUND_TYPES = {
        "cold":  ["混合型", "股票型"],          # 敢于买股票型
        "cool":  ["混合型", "股票型"],
        "normal":["混合型", "指数型", "股票型"],
        "warm":  ["混合型", "指数型"],          # 减配股票型
        "hot":   ["债券型", "混合型"],          # 几乎只买债券
    }

    def __init__(self, db: Database):
        self.db = db
        self.screener = FundScreener(db)
        self.thermometer = MarketThermometer(db)
        self._last_temp = None
        self._last_rebalance_date = None

    def should_rebalance(self) -> Tuple[bool, str]:
        """
        判断当前是否应该调仓。

        Returns:
            (是否调仓, 原因)
        """
        temp_data = self.thermometer.get_temperature()
        current_temp = temp_data["temperature"]

        # 首次运行，总是调仓
        if self._last_temp is None:
            self._last_temp = current_temp
            self._last_rebalance_date = datetime.now().strftime("%Y-%m-%d")
            return True, "首次评估，建立基准仓位"

        temp_change = abs(current_temp - self._last_temp)

        if temp_change >= self.TEMP_CHANGE_THRESHOLD:
            direction = "升温" if current_temp > self._last_temp else "降温"
            self._last_temp = current_temp
            self._last_rebalance_date = datetime.now().strftime("%Y-%m-%d")
            return True, f"温度{direction}{temp_change:.0f}°(阈值≥{self.TEMP_CHANGE_THRESHOLD}°)，触发调仓"

        return False, f"温度变化{temp_change:.0f}°(阈值<{self.TEMP_CHANGE_THRESHOLD}°)，保持不动"

    def get_recommended_funds(self, top_n: int = 10) -> pd.DataFrame:
        """
        根据当前市场状态，使用适配的因子权重进行基金评分。

        Returns:
            DataFrame: 推荐基金排名
        """
        temp_data = self.thermometer.get_temperature()
        level = temp_data["level"]  # cold/cool/normal/warm/hot

        # 获取当前市场状态对应的因子权重
        weights = self.MARKET_STATE_WEIGHTS.get(level, self.MARKET_STATE_WEIGHTS["normal"])
        fund_types = self.MARKET_STATE_FUND_TYPES.get(level, ["混合型", "指数型", "股票型"])

        # 使用质量筛选（不再打分排名）
        df = self.screener.screen_funds(fund_types=fund_types, max_results=top_n)
        return df

    def get_weekly_suggestion(self) -> dict:
        """
        生成每周操作建议（每月才评估是否调仓）。

        Returns:
            dict: {
                'action': 'buy' | 'hold' | 'reduce' | 'sell',
                'reason': str,
                'recommended_funds': DataFrame,
                'target_equity_pct': float,
                'temp_data': dict,
                'cost_estimate': dict | None,  # 如果调仓，估算交易成本
            }
        """
        temp_data = self.thermometer.get_temperature()
        should_rebalance, reason = self.should_rebalance()

        result = {
            "temp_data": temp_data,
            "should_rebalance": should_rebalance,
            "reason": reason,
            "target_equity_pct": temp_data["target_equity_pct"],
            "recommended_funds": None,
            "cost_estimate": None,
        }

        if should_rebalance:
            result["recommended_funds"] = self.get_recommended_funds(top_n=10)
            result["cost_estimate"] = self._estimate_rebalance_cost()

        return result

    def _estimate_rebalance_cost(self) -> dict:
        """估算调仓的交易成本"""
        holdings = self.db.get_current_holdings()
        if not holdings:
            return {"total_cost": 0, "breakdown": [], "note": "空仓，无卖出成本"}

        total_cost = 0
        breakdown = []

        for h in holdings:
            invested = h["buy_amount"]
            days_held = self._days_held(h["buy_date"])

            if days_held < 7:
                fee_rate = TRADING_COST["redemption_fee_7d"]
            elif days_held < 30:
                fee_rate = TRADING_COST["redemption_fee_30d"]
            else:
                fee_rate = TRADING_COST["redemption_fee_normal"]

            sell_cost = invested * fee_rate
            total_cost += sell_cost
            breakdown.append({
                "fund_name": h.get("fund_name", h["fund_code"]),
                "invested": invested,
                "days_held": days_held,
                "sell_fee": round(sell_cost, 2),
                "fee_rate": f"{fee_rate*100:.1f}%",
            })

        return {
            "total_cost": round(total_cost, 2),
            "breakdown": breakdown,
            "note": f"总交易成本约¥{total_cost:.2f}，占仓位{total_cost/10000*100 if sum(h['buy_amount'] for h in holdings) > 0 else 0:.2f}%",
        }

    @staticmethod
    def _days_held(buy_date: str) -> int:
        """计算持有天数"""
        try:
            buy = pd.to_datetime(buy_date)
            return (pd.Timestamp.now() - buy).days
        except Exception:
            return 999  # 无法解析视为长期持有
