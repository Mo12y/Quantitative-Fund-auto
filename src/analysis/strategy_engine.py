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

    def backtest_v2(
        self,
        lookback_years: int = 3,
        trading_cost_enabled: bool = True,
    ) -> dict:
        """
        回测 v2: 月频评估 + 温度阈值触发 + 交易成本模拟。

        ⚠️ LEGACY：已被 src/analysis/backtest.py 的 RigorousBacktest（v3.0）取代。
           v2 的 PE 温度分位数使用全历史数据（存在未来函数），
           v3 改用扩展窗口分位数（无未来函数）并加入多基准/t检验。
           本方法仅保留以兼容 backtest2 命令。
        """
        import akshare as ak

        # 获取 PE 温度代理数据（直接调akshare）
        try:
            pe_df = ak.stock_index_pe_lg(symbol="沪深300")
            # PE列: 日期, 指数, 等权静态PE, 静态PE, 静态PE中位数, ...
            # 用"滚动市盈率中位数"(最后一列)作为PE分位数代理
            pe_values = pd.to_numeric(pe_df.iloc[:, -1], errors='coerce')
            pe_dates = pe_df.iloc[:, 0].astype(str).tolist()
            # 构建日期→PE温度映射（用PE在所有历史中的分位数）
            pe_all = pe_values.dropna().values
            pe_date_map = {}
            for i, d in enumerate(pe_dates):
                if not pd.isna(pe_values.iloc[i]):
                    pct = (pe_all < pe_values.iloc[i]).sum() / len(pe_all) * 100
                    pe_date_map[d] = pct
        except Exception:
            pe_date_map = {}

        # 候选基金池
        cur = self.db.conn.cursor()
        cur.execute("SELECT DISTINCT fund_code FROM fund_nav")
        all_codes = [r[0] for r in cur.fetchall()]
        if not all_codes:
            return {"error": "无净值数据"}

        # 确定回测月份
        cur.execute("SELECT MAX(nav_date) FROM fund_nav")
        max_date = cur.fetchone()[0]
        months = []
        d = pd.to_datetime(max_date)
        start = d - timedelta(days=365 * lookback_years)
        while d > start:
            months.append(d.strftime("%Y-%m-%d"))
            d -= timedelta(days=30)
        months = sorted(months)

        # 沪深300基准
        try:
            hs300 = ak.stock_zh_index_daily(symbol="sh000300")
            hs300["date"] = pd.to_datetime(hs300["date"])
            hs300 = hs300.set_index("date")["close"]
        except Exception:
            hs300 = None

        strategy_returns = []
        benchmark_returns = []
        last_temp = None
        last_top3 = []

        for i, month_date in enumerate(months[:-1]):
            next_month = months[i + 1]
            try:
                # 获取该月的PE温度（从pe_date_map找最近的日期）
                pe_temp = self._find_nearest_temp(pe_date_map, month_date)

                # 温度阈值触发
                should_trade = (last_temp is None or abs(pe_temp - last_temp) >= self.TEMP_CHANGE_THRESHOLD)

                if should_trade and pe_temp > 0:
                    last_temp = pe_temp
                    new_top3 = self._select_top3_at_date(all_codes, month_date, pe_temp)

                    if new_top3 and new_top3 != last_top3:
                        # 模拟交易成本
                        if trading_cost_enabled and last_top3:
                            strategy_returns.append(-0.003)
                        last_top3 = new_top3

                # 计算本月收益
                if last_top3:
                    monthly_returns = []
                    for code in last_top3:
                        nb = self.db.get_fund_nav(code, end_date=month_date)
                        na = self.db.get_fund_nav(code, end_date=next_month)
                        if nb and na:
                            try:
                                b_val = float(nb[-1]["unit_nav"])
                                a_val = float(na[-1]["unit_nav"])
                                if b_val > 0:
                                    monthly_returns.append((a_val / b_val - 1) * 100)
                            except Exception:
                                pass
                    if monthly_returns:
                        strategy_returns.append(np.mean(monthly_returns))

                # 基准
                if hs300 is not None:
                    try:
                        b_idx = hs300.loc[hs300.index <= pd.to_datetime(month_date)]
                        a_idx = hs300.loc[hs300.index <= pd.to_datetime(next_month)]
                        if len(b_idx) > 0 and len(a_idx) > 0:
                            benchmark_returns.append((a_idx.iloc[-1] / b_idx.iloc[-1] - 1) * 100)
                    except Exception:
                        pass
            except Exception:
                continue

        if not strategy_returns:
            return {"error": "回测数据不足"}

        sr = np.array(strategy_returns)
        br = np.array(benchmark_returns) if benchmark_returns else None

        total_ret = np.prod(1 + sr / 100) - 1
        ann_ret = (1 + total_ret) ** (12 / len(sr)) - 1
        ann_vol = np.std(sr, ddof=1) * np.sqrt(12)
        sv = (ann_ret - 0.02) / (ann_vol / 100) if ann_vol > 0 else 0
        cum = np.cumprod(1 + sr / 100)
        peak = cum[0]
        max_dd = 0
        for c in cum:
            if c > peak: peak = c
            dd = (peak - c) / peak * 100
            if dd > max_dd: max_dd = dd
        win_rate = (sr > 0).sum() / len(sr) * 100

        # 统计调仓次数
        temp_changes = 0
        prev_t = None
        for m in months:
            t = self._find_nearest_temp(pe_date_map, m)
            if prev_t is not None and abs(t - prev_t) >= self.TEMP_CHANGE_THRESHOLD:
                temp_changes += 1
            prev_t = t if t > 0 else prev_t

        result = {
            "strategy": {
                "total_return": round(total_ret * 100, 1),
                "annual_return": round(ann_ret * 100, 1),
                "annual_volatility": round(ann_vol, 1),
                "sharpe": round(sv, 2),
                "max_drawdown": round(max_dd, 1),
                "win_rate": round(win_rate, 0),
                "months": len(sr),
                "temp_changes": temp_changes,
            }
        }

        if br is not None and len(br) > 0:
            b_total = np.prod(1 + br / 100) - 1
            b_ann = (1 + b_total) ** (12 / len(br)) - 1
            result["benchmark"] = {
                "total_return": round(b_total * 100, 1),
                "annual_return": round(b_ann * 100, 1),
            }
            result["alpha"] = round((ann_ret - b_ann) * 100, 1)

        return result

    def _find_nearest_temp(self, date_map: dict, target_date: str) -> float:
        """从日期-温度映射中找最接近target_date的温度"""
        best = 50.0
        best_diff = float("inf")
        for d, temp in date_map.items():
            try:
                diff = abs((pd.to_datetime(d) - pd.to_datetime(target_date)).days)
                if diff < best_diff:
                    best_diff = diff
                    best = temp
            except Exception:
                continue
        return best

    def _select_top3_at_date(self, all_codes: list, date_str: str, temp: float) -> list:
        """在指定日期用当时的净值数据选出Top3基金"""
        scores = []
        for code in all_codes:
            navs = self.db.get_fund_nav(code, end_date=date_str)
            if len(navs) < 60:
                continue
            try:
                df_nav = pd.DataFrame(navs).sort_values("nav_date")
                nav_series = df_nav.set_index("nav_date")["unit_nav"]
                if len(nav_series) >= 63:
                    mom = (nav_series.iloc[-1] / nav_series.iloc[-min(63, len(nav_series))] - 1) * 100
                else:
                    mom = 0
                rets = df_nav["unit_nav"].pct_change().dropna().values
                ann_vol = np.std(rets, ddof=1) * np.sqrt(252) if len(rets) > 20 else 30
                dd = self._calc_max_dd(df_nav["unit_nav"].values)

                if temp < 40:
                    total = mom / 80 * 40 + (100 - ann_vol) / 100 * 30 + (50 - dd) / 50 * 30
                else:
                    total = mom / 80 * 20 + (100 - ann_vol) / 100 * 30 + (50 - dd) / 50 * 50
                scores.append((code, total))
            except Exception:
                continue
        scores.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scores[:3]]

    @staticmethod
    def _calc_max_dd(nav_values: np.ndarray) -> float:
        peak = nav_values[0]
        max_dd = 0
        for p in nav_values:
            if p > peak: peak = p
            dd = (peak - p) / peak * 100
            if dd > max_dd: max_dd = dd
        return max_dd
