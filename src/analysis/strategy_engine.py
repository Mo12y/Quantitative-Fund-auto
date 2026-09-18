"""
策略引擎 v2.0: 月频评估 + 温度阈值触发调仓 + 交易成本模拟。

v1 → v2 核心变化:
1. 调仓频率: 每周 → 每月（减少交易成本）
2. 调仓触发: 每次都换 → 温度变化>15°才调仓（上次温度持久化，跨进程生效）
3. 交易成本: 忽略 → 按**实际调仓成交额**估算（费率取自 portfolio 单一真源）

说明: 基金选择走 FundScreener 的**质量筛选**（只排除有坑的），不做因子加权打分，
因此不存在"按市场状态动态调整因子权重"这回事。

核心理念:
  不追求"选到最好的基金"，而是追求"在合理的时间以合理的成本
  持有合理的基金"。
"""

import pandas as pd
from datetime import datetime
from typing import Tuple
from ..data.database import Database
from .fund_scorer import FundScreener
from .thermometer import MarketThermometer
from .portfolio import redeem_fee_rate, DEFAULT_PURCHASE_FEE

# 前端申购费同样走 portfolio 的单一真源（DEFAULT_PURCHASE_FEE / purchase_fee_rate），
# 本模块不再自建常量。赎回费一律走 portfolio.redeem_fee_rate。


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

    # 上次评估状态的持久化键（存 analysis_snapshot，跨进程生效）
    STATE_KEY = "strategy_last_temp"
    STATE_MAX_AGE = 7 * 86400  # 7 天；超过视为过期，重新建立基准

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
        self._load_state()

    def _load_state(self):
        """从库里恢复上次评估的温度与日期（超过 STATE_MAX_AGE 视为过期）。"""
        if self.db is None:
            return
        try:
            st = self.db.get_analysis_snapshot(self.STATE_KEY, self.STATE_MAX_AGE)
        except Exception:
            st = None
        if not st:
            return
        try:
            self._last_temp = float(st["temp"])
            self._last_rebalance_date = st.get("date")
        except Exception:
            self._last_temp = None

    def _save_state(self):
        """持久化上次评估状态（db=None 的纯逻辑用法下静默跳过）。"""
        if self.db is None:
            return
        try:
            self.db.set_analysis_snapshot(
                self.STATE_KEY,
                {"temp": self._last_temp, "date": self._last_rebalance_date})
        except Exception:
            pass

    def should_rebalance(self) -> Tuple[bool, str]:
        """
        判断当前是否应该调仓。

        上次温度持久化到 DB（7 天内有效），因此阈值判断跨进程/跨次运行都成立 ——
        不再出现"每次 CLI 都是首次评估"导致 15° 阈值永不触发。

        Returns:
            (是否调仓, 原因)
        """
        temp_data = self.thermometer.get_temperature()
        current_temp = temp_data["temperature"]

        # 无历史基准（首跑 / 状态过期），总是调仓
        if self._last_temp is None:
            self._last_temp = current_temp
            self._last_rebalance_date = datetime.now().strftime("%Y-%m-%d")
            self._save_state()
            return True, "首次评估，建立基准仓位"

        temp_change = abs(current_temp - self._last_temp)

        if temp_change >= self.TEMP_CHANGE_THRESHOLD:
            direction = "升温" if current_temp > self._last_temp else "降温"
            self._last_temp = current_temp
            self._last_rebalance_date = datetime.now().strftime("%Y-%m-%d")
            self._save_state()
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

        # 按市场状态收窄可投的基金类型（评分统一走质量筛选，不做权重加权）
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

    def _holding_market_value(self, h: dict) -> float:
        """持仓市值 = 份额 × 最新单位净值；缺数据退回买入金额。"""
        try:
            shares = h.get("shares") or 0
            if shares:
                row = self.db.get_latest_fund_nav(h["fund_code"])
                nav = float(row["unit_nav"]) if row and row.get("unit_nav") is not None else None
                if nav:
                    return shares * nav
        except Exception:
            pass
        return float(h.get("buy_amount") or 0)

    def _estimate_rebalance_cost(self) -> dict:
        """估算调仓的交易成本。

        口径修正（旧版有两个错误）：
        1. 费率：改用 portfolio.redeem_fee_rate 单一真源（<7 天 1.5%，≥7 天 0），
           不再自建"7-30 天 0.5%"这张重复且与真实账务不一致的表。
        2. 基数：只对**实际需要变动的成交额**（|目标权益% − 当前权益%| × 总市值）计费，
           不再假设"把全部持仓卖一遍"。
        """
        holdings = self.db.get_current_holdings()
        if not holdings:
            return {"total_cost": 0, "breakdown": [], "note": "空仓，无交易成本"}

        pos = [(h, self._holding_market_value(h)) for h in holdings]
        total_mv = sum(mv for _, mv in pos)
        if total_mv <= 0:
            return {"total_cost": 0, "breakdown": [], "note": "无有效市值数据"}

        temp_data = self.thermometer.get_temperature()
        target_eq = float(temp_data.get("target_equity_pct") or 0)
        equity_mv = sum(mv for h, mv in pos if self._is_equity(h.get("fund_code", "")))
        current_eq = equity_mv / total_mv * 100
        delta_pp = target_eq - current_eq
        traded = abs(delta_pp) / 100.0 * total_mv   # 实际需要买卖的成交额

        total_cost = 0.0
        breakdown = []
        if traded <= 0:
            return {"total_cost": 0, "breakdown": [], "note": "仓位已在目标附近，无交易成本"}

        if delta_pp < 0:
            # 减仓：按市值等比例卖出，逐只按真实持有天数计赎回费
            for h, mv in pos:
                if mv <= 0:
                    continue
                sell_amt = traded * (mv / total_mv)
                days_held = self._days_held(h.get("buy_date"))
                info = {}
                try:
                    info = self.db.get_fund_info(h["fund_code"]) or {}
                except Exception:
                    pass
                rate = redeem_fee_rate(info.get("redeem_fee"), days_held)
                cost = sell_amt * rate
                total_cost += cost
                breakdown.append({
                    "fund_name": h.get("fund_name", h["fund_code"]),
                    "action": "卖出",
                    "traded": round(sell_amt, 2),
                    "days_held": days_held,
                    "fee_rate": f"{rate*100:.2f}%",
                    "fee": round(cost, 2),
                })
        else:
            # 加仓：按前端申购费对买入额计费。
            # 这里**不知道**最终会买哪只（候选由质量筛选池给出），所以只能用默认费率；
            # 真实买入时（`portfolio.add_buy_transaction` / 回测）会按份额类别逐只判断：
            # C/E/I 类不收前端申购费 → 0。所以本估算对 C 类是**高估**的。
            cost = traded * DEFAULT_PURCHASE_FEE
            total_cost += cost
            breakdown.append({
                "fund_name": "(按质量筛选池买入)",
                "action": "买入",
                "traded": round(traded, 2),
                "days_held": None,
                "fee_rate": f"{DEFAULT_PURCHASE_FEE*100:.2f}%",
                "fee": round(cost, 2),
                "note": "未指定具体标的，按默认费率估；C/E/I 类实际为 0",
            })

        return {
            "total_cost": round(total_cost, 2),
            "traded_amount": round(traded, 2),
            "breakdown": breakdown,
            "note": (f"按实际调仓成交额¥{traded:.2f}（目标权益{target_eq:.1f}% vs 当前{current_eq:.1f}%）"
                     f"估算交易成本约¥{total_cost:.2f}，"
                     f"占调仓成交额{total_cost/traded*100 if traded else 0:.2f}%"),
        }

    @staticmethod
    def _days_held(buy_date: str) -> int:
        """计算持有天数"""
        try:
            buy = pd.to_datetime(buy_date)
            return (pd.Timestamp.now() - buy).days
        except Exception:
            return 999  # 无法解析视为长期持有

    def _is_equity(self, fund_code: str) -> bool:
        """是否为权益类基金（股票/混合/指数/QDII），用于权益仓位口径。"""
        try:
            info = self.db.get_fund_info(fund_code) or {}
        except Exception:
            return False
        ftype = str(info.get("fund_type", "") or "")
        return any(t in ftype for t in ("股票", "混合", "指数", "QDII"))
