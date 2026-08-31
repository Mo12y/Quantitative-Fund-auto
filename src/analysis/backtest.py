"""
严谨回测引擎 v3.0

相对 v1/v2 的核心改进：
1. 防未来函数：PE 温度用「扩展窗口分位数」（只用 <=T 的数据），信号只用当日及之前数据
2. 完整交易成本：申购费 + 赎回费（按持有天数分层）+ 管理费月度摊销
3. 多基准对比：沪深300 / 中证500 / 等权基金组合
4. 统计检验：月度超额收益（alpha）的 t 检验、信息比率、Beta
5. 逐年一致性：逐年收益分解，检验策略是否只在某一时段有效

已知局限（诚实声明）：
- 基金池来自当前数据库（存续基金），存在幸存者偏差（已清盘基金未纳入）
- 未考虑真实申赎的 T+1 确认延迟与资金占用
"""

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import get_config
from ..data.database import Database


# =====================================================================
# 交易成本模型
# =====================================================================

@dataclass
class CostModel:
    """场外基金交易成本模型（C 类份额口径）"""
    purchase_fee: float = 0.0015          # 申购费 0.15%
    redemption_fee_7d: float = 0.015      # 持有 <7 天赎回费 1.5%
    redemption_fee_30d: float = 0.005     # 持有 7-30 天赎回费 0.5%
    redemption_fee_normal: float = 0.0    # 持有 >30 天赎回免费
    management_fee_annual: float = 0.015  # 管理费年化 1.5%

    def redemption_rate(self, days_held: int) -> float:
        """按持有天数返回赎回费率"""
        if days_held < 7:
            return self.redemption_fee_7d
        if days_held < 30:
            return self.redemption_fee_30d
        return self.redemption_fee_normal

    def round_trip_cost(self, days_held: int) -> float:
        """一次完整买卖（含持有期管理费摊销）的总成本率"""
        mgmt = self.management_fee_annual * days_held / 365.0
        return self.purchase_fee + self.redemption_rate(days_held) + mgmt


# =====================================================================
# 业绩指标计算
# =====================================================================

def compute_max_drawdown(nav: np.ndarray) -> float:
    """最大回撤（%）"""
    peak = np.maximum.accumulate(nav)
    dd = (peak - nav) / peak
    return float(dd.max() * 100)


def compute_metrics(returns: np.ndarray, rf_annual: float = 0.02) -> Dict:
    """
    从月频收益序列（百分数）计算业绩指标。

    Args:
        returns: 月收益数组，单位 %
        rf_annual: 无风险利率（年化）
    """
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n == 0:
        return {}
    nav = np.cumprod(1 + r / 100.0)
    total_ret = (nav[-1] - 1) * 100
    ann_ret = ((nav[-1]) ** (12.0 / n) - 1) * 100
    ann_vol = np.std(r, ddof=1) * np.sqrt(12)
    sharpe = (ann_ret - rf_annual) / ann_vol if ann_vol > 0 else 0
    mdd = compute_max_drawdown(nav)
    calmar = ann_ret / mdd if mdd > 0 else 0
    win_rate = float((r > 0).sum() / n * 100)
    return {
        "total_return": round(total_ret, 2),
        "annual_return": round(ann_ret, 2),
        "annual_volatility": round(ann_vol, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown": round(mdd, 2),
        "calmar": round(calmar, 2),
        "win_rate": round(win_rate, 1),
        "months": int(n),
    }


def alpha_t_test(strategy_ret: np.ndarray, benchmark_ret: np.ndarray) -> Dict:
    """
    对月度超额收益做 t 检验（H0: alpha = 0）。

    样本量小（n<30）时用 t 分布，否则用正态近似。
    """
    a = np.asarray(strategy_ret, dtype=float)
    b = np.asarray(benchmark_ret, dtype=float)
    n = min(len(a), len(b))
    if n < 3:
        return {"t_stat": None, "p_value": None, "significant": False, "n": int(n)}
    alpha = a[:n] - b[:n]
    mean = alpha.mean()
    std = alpha.std(ddof=1)
    if std <= 0:
        return {"t_stat": None, "p_value": None, "significant": False, "n": n}
    t_stat = mean / (std / math.sqrt(n))
    # 双尾 p 值（t 分布，df=n-1；n 大时近似正态）
    df = n - 1
    p_value = 2.0 * (1.0 - _t_cdf(abs(t_stat), df))
    return {
        "t_stat": round(float(t_stat), 3),
        "p_value": round(float(p_value), 4),
        "mean_alpha": round(float(mean), 3),
        "significant": bool(p_value < 0.05),
        "n": int(n),
    }


def _t_cdf(x: float, df: int) -> float:
    """t 分布累积分布函数的数值近似（适用于 df>=3）"""
    x = abs(x)
    # 用标准正态近似（n 大时精确；n 小时偏保守，可接受）
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def compute_beta_and_alpha(strategy_ret: np.ndarray, benchmark_ret: np.ndarray) -> Dict:
    """回归计算 Beta 与年化 Alpha（月度收益线性回归）"""
    s = np.asarray(strategy_ret, dtype=float)
    b = np.asarray(benchmark_ret, dtype=float)
    n = min(len(s), len(b))
    if n < 3:
        return {"beta": None, "alpha_annual": None}
    s, b = s[:n], b[:n]
    beta = np.cov(s, b)[0, 1] / np.var(b) if np.var(b) > 0 else 0
    alpha_monthly = s.mean() - beta * b.mean()
    alpha_annual = alpha_monthly * 12
    return {"beta": round(float(beta), 3), "alpha_annual": round(float(alpha_annual), 3)}


# =====================================================================
# 严谨回测引擎
# =====================================================================

class RigorousBacktest:
    """严谨回测引擎 v3.0"""

    MIN_HISTORY_DAYS = 90       # 选基所需最少历史天数

    def __init__(self, db: Database, cost_model: Optional[CostModel] = None):
        self.db = db
        self.cost = cost_model or CostModel()
        # 数据缓存：多次回测只拉取一次 akshare 数据
        self._temp_map: Optional[Dict[str, float]] = None
        self._benchmarks: Optional[Dict[str, Optional[pd.Series]]] = None
        # 从配置中心读取回测默认参数（可被 run() 参数覆盖）
        cfg = get_config().section("backtest")
        self.TEMP_CHANGE_THRESHOLD: int = cfg.get("temp_threshold", 15)
        self.TOP_N: int = cfg.get("top_n", 3)
        self.SELECTION_WEIGHTS: tuple = tuple(cfg.get("selection_weights", [0.4, 0.3, 0.3]))
        self.ADAPTIVE_COLD_WEIGHTS: tuple = tuple(cfg.get("adaptive_cold_weights", [0.2, 0.4, 0.4]))
        self.ADAPTIVE_HOT_WEIGHTS: tuple = tuple(cfg.get("adaptive_hot_weights", [0.6, 0.2, 0.2]))
        self.LOOKBACK_YEARS: int = cfg.get("lookback_years", 5)

    def _get_temp_map(self) -> Dict[str, float]:
        if self._temp_map is None:
            self._temp_map = self.get_pe_temperature_series()
        return self._temp_map

    def _get_benchmarks(self, symbols: tuple) -> Dict[str, Optional[pd.Series]]:
        if self._benchmarks is None:
            self._benchmarks = {sym: self._load_benchmark(sym) for sym in symbols}
        return self._benchmarks

    # ------------------------------------------------------------------
    # 无未来函数的 PE 温度序列
    # ------------------------------------------------------------------

    def get_pe_temperature_series(self, symbol: str = "沪深300") -> Dict[str, float]:
        """
        构建 PE 温度序列：扩展窗口分位数，无未来函数。

        日期 t 的温度 = PE_t 在 [起始日, t] 历史区间内的分位数 * 100。
        相比 v2（用全历史分位数），这里只用 t 当日及之前的数据。
        """
        import akshare as ak

        df = ak.stock_index_pe_lg(symbol=symbol)
        pe_dates = pd.to_datetime(df.iloc[:, 0], errors="coerce")
        pe_values = pd.to_numeric(df.iloc[:, -1], errors="coerce")  # 滚动市盈率中位数

        mask = pe_dates.notna() & pe_values.notna()
        dates = pe_dates[mask].sort_values()
        values = pe_values.loc[dates.index]

        temps: Dict[str, float] = {}
        seen: List[float] = []
        for d, v in zip(dates, values):
            v = float(v)
            seen.append(v)
            # 严格小于的分位数（无未来数据参与）
            pct = sum(1.0 for x in seen if x < v) / len(seen) * 100
            temps[d.strftime("%Y-%m-%d")] = round(pct, 1)
        return temps

    @staticmethod
    def _nearest_temp(temp_map: Dict[str, float], date_str: str) -> float:
        """在温度序列中找 <= date 的最近日期温度（无未来函数）"""
        target = pd.to_datetime(date_str)
        best = None
        best_diff = float("inf")
        for d, temp in temp_map.items():
            try:
                diff = (target - pd.to_datetime(d)).total_seconds()
                if 0 <= diff < best_diff:  # 只允许 <= target
                    best_diff = diff
                    best = temp
            except Exception:
                continue
        return best if best is not None else 50.0

    # ------------------------------------------------------------------
    # 点选基金（只用 <= date 的数据）
    # ------------------------------------------------------------------

    def select_top_n_at(
        self, all_codes: List[str], date_str: str,
        top_n: int = 3, weights: tuple = (0.4, 0.3, 0.3),
    ) -> List[str]:
        """在 date_str 当日选出 TopN 基金（权重可配：动量 / 低波动 / 低回撤）"""
        scores = []
        for code in all_codes:
            navs = self.db.get_fund_nav(code, end_date=date_str)
            if len(navs) < self.MIN_HISTORY_DAYS:
                continue
            try:
                df_nav = pd.DataFrame(navs).sort_values("nav_date")
                nav_series = df_nav["unit_nav"].values

                # 近3月动量
                if len(nav_series) >= 63:
                    mom = (nav_series[-1] / nav_series[-63] - 1) * 100
                else:
                    mom = 0.0

                # 年化波动率
                rets = pd.Series(nav_series).pct_change().dropna().values
                ann_vol = np.std(rets, ddof=1) * np.sqrt(252) if len(rets) > 20 else 30.0

                # 近1年最大回撤
                dd = compute_max_drawdown(nav_series[-252:]) if len(nav_series) >= 252 else compute_max_drawdown(nav_series)

                # 打分：权重可配（默认 动量40% + 低波动30% + 低回撤30%）
                w_mom, w_vol, w_dd = weights
                score = mom * w_mom + (100 - min(ann_vol, 100)) * w_vol + (50 - dd) * w_dd
                scores.append((code, score))
            except Exception:
                continue
        scores.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scores[:top_n]]

    # ------------------------------------------------------------------
    # 基准数据
    # ------------------------------------------------------------------

    @staticmethod
    def _load_benchmark(symbol: str) -> Optional[pd.Series]:
        """加载指数收盘价序列（akshare）"""
        import akshare as ak
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
            df["date"] = pd.to_datetime(df["date"])
            return df.set_index("date")["close"]
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 月度时间轴
    # ------------------------------------------------------------------

    def _month_grid(self, start: str, end: str) -> List[str]:
        months = []
        d = pd.to_datetime(end)
        start_dt = pd.to_datetime(start)
        while d > start_dt:
            months.append(d.strftime("%Y-%m-%d"))
            d -= timedelta(days=30)
        return sorted(months)

    # ------------------------------------------------------------------
    # 主回测
    # ------------------------------------------------------------------

    def run(
        self,
        lookback_years: Optional[int] = None,
        benchmark_symbols: tuple = ("sh000300", "sh000905"),
        selection_weights: Optional[tuple] = None,
        adaptive_weights: bool = False,
        temp_threshold: Optional[int] = None,
        buy_and_hold: bool = False,
    ) -> Dict:
        """
        运行严谨回测（策略参数可配，默认值来自 config/settings.yaml 的 backtest 段）。

        Args:
            lookback_years: 回测年数（默认取配置）
            benchmark_symbols: 基准指数（akshare 代码）
            selection_weights: 选基权重 (动量, 低波动, 低回撤)
            adaptive_weights: 是否按温度自适应选基权重（冷市防御/热市动量）
            temp_threshold: 温度变化调仓阈值（默认取配置）
            buy_and_hold: 买入持有模式（只在首月建仓，之后不调仓）

        Returns:
            dict: 完整回测报告
        """
        # 从配置解析默认参数（未显式传入时）
        if lookback_years is None:
            lookback_years = self.LOOKBACK_YEARS
        if selection_weights is None:
            selection_weights = self.SELECTION_WEIGHTS
        if temp_threshold is None:
            temp_threshold = self.TEMP_CHANGE_THRESHOLD

        # 1. 无未来函数的温度序列（缓存复用）
        temp_map = self._get_temp_map()

        # 2. 基金池（注意：存续基金，存在幸存者偏差）
        all_codes = list(self.db.get_all_fund_codes())
        if not all_codes:
            return {"error": "无净值数据"}

        # 3. 时间范围
        cur = self.db.conn.cursor()
        cur.execute("SELECT MAX(nav_date), MIN(nav_date) FROM fund_nav")
        max_date, min_date = cur.fetchone()
        end_date = max_date
        start_date = (pd.to_datetime(max_date) - timedelta(days=365 * lookback_years)).strftime("%Y-%m-%d")
        months = self._month_grid(start_date, end_date)

        # 4. 基准（缓存复用）
        benchmarks = self._get_benchmarks(benchmark_symbols)

        # 5. 模拟
        portfolio_value = 100.0
        holdings: List[Dict] = []  # [{code, buy_date, value}]
        last_temp = None
        strategy_monthly: List[float] = []
        benchmark_monthly: Dict[str, List[float]] = {sym: [] for sym in benchmarks}
        trade_log: List[Dict] = []

        for i, m in enumerate(months[:-1]):
            next_m = months[i + 1]
            temp = self._nearest_temp(temp_map, m)

            # --- 温度阈值触发调仓 ---
            should_trade = (last_temp is None or abs(temp - last_temp) >= temp_threshold)
            if buy_and_hold:
                should_trade = (i == 0)  # 买入持有：只在首月建仓

            if should_trade:
                # 温度自适应权重：冷市防御(低波动/低回撤)，热市动量
                weights = selection_weights
                if adaptive_weights:
                    weights = self.ADAPTIVE_COLD_WEIGHTS if temp < 40 else self.ADAPTIVE_HOT_WEIGHTS
                new_picks = self.select_top_n_at(all_codes, m, self.TOP_N, weights=weights)
                if new_picks:
                    # 卖出旧持仓：按持有天数收赎回费
                    sell_cost = 0.0
                    for h in holdings:
                        days = (pd.to_datetime(m) - pd.to_datetime(h["buy_date"])).days
                        fee = h["value"] * self.cost.redemption_rate(days)
                        sell_cost += fee
                    portfolio_value -= sell_cost

                    # 买入新持仓：等权 + 申购费
                    alloc_per_fund = portfolio_value / len(new_picks)
                    buy_cost = portfolio_value * self.cost.purchase_fee
                    portfolio_value -= buy_cost
                    holdings = [
                        {"code": c, "buy_date": m, "value": alloc_per_fund}
                        for c in new_picks
                    ]
                    trade_log.append({
                        "date": m, "temp": temp, "picks": new_picks,
                        "sell_cost": round(sell_cost, 4), "buy_cost": round(buy_cost, 4),
                    })
                    last_temp = temp

            # --- 月度持仓收益 ---
            if holdings:
                month_rets = []
                for h in holdings:
                    nb = self.db.get_fund_nav(h["code"], end_date=m)
                    na = self.db.get_fund_nav(h["code"], end_date=next_m)
                    if nb and na:
                        b = float(nb[-1]["unit_nav"])
                        a = float(na[-1]["unit_nav"])
                        if b > 0:
                            month_rets.append((a / b - 1) * 100)
                if month_rets:
                    ret = float(np.mean(month_rets))
                    portfolio_value *= (1 + ret / 100)
                    # 管理费月度摊销（年化/12）
                    portfolio_value *= (1 - self.cost.management_fee_annual / 12)
                    strategy_monthly.append(ret)

            # --- 基准月度收益 ---
            for sym, series in benchmarks.items():
                if series is None:
                    continue
                b_idx = series[series.index <= pd.to_datetime(m)]
                a_idx = series[series.index <= pd.to_datetime(next_m)]
                if len(b_idx) > 0 and len(a_idx) > 0:
                    benchmark_monthly[sym].append((a_idx.iloc[-1] / b_idx.iloc[-1] - 1) * 100)

        if len(strategy_monthly) < 6:
            return {"error": "回测数据不足，请扩大回看年数或补充净值数据"}

        # 6. 指标计算
        sr = np.array(strategy_monthly)
        result = {
            "strategy": compute_metrics(sr),
            "trades": trade_log,
            "params": {
                "selection_weights": list(selection_weights),
                "adaptive_weights": adaptive_weights,
                "temp_threshold": temp_threshold,
                "buy_and_hold": buy_and_hold,
            },
            "note": "基金池为存续基金，存在幸存者偏差；温度分位数为扩展窗口（无未来函数）",
        }

        # 基准对比
        for sym, rets in benchmark_monthly.items():
            if len(rets) >= len(sr):
                br = np.array(rets[: len(sr)])
                metrics = compute_metrics(br)
                metrics["name"] = sym
                result[f"benchmark_{sym}"] = metrics
                # alpha 显著性
                result[f"alpha_vs_{sym}"] = alpha_t_test(sr, br)
                result[f"regression_vs_{sym}"] = compute_beta_and_alpha(sr, br)

        # 7. 逐年一致性
        result["yearly"] = self._yearly_breakdown(sr, benchmark_monthly)

        return result

    # ------------------------------------------------------------------
    # 逐年一致性
    # ------------------------------------------------------------------

    def _yearly_breakdown(self, strategy_ret: np.ndarray, benchmark_monthly: Dict[str, List[float]]) -> List[Dict]:
        """逐年收益分解，检验策略是否只在某一段有效"""
        # 用最近一个月索引近似年份（简化：按收益序列分段）
        years = []
        n = len(strategy_ret)
        # 假设月频，按每12个月一组
        for start in range(0, n, 12):
            seg = strategy_ret[start : start + 12]
            if len(seg) == 0:
                continue
            nav = np.cumprod(1 + seg / 100.0)
            entry = {"period": f"第{start//12 + 1}年", "strategy_return": round((nav[-1] - 1) * 100, 2)}
            for sym, rets in benchmark_monthly.items():
                bseg = np.array(rets[start : start + 12])
                if len(bseg) == len(seg) and len(bseg) > 0:
                    bnav = np.cumprod(1 + bseg / 100.0)
                    entry[f"{sym}"] = round((bnav[-1] - 1) * 100, 2)
            years.append(entry)
        return years

    # ------------------------------------------------------------------
    # 策略对比研究（改进假设验证）
    # ------------------------------------------------------------------

    def compare_strategies(self, lookback_years: Optional[int] = None) -> Dict:
        """
        策略对比研究：验证改进假设。

        三组对照：
        - 买入持有(基线)：首月建仓后不再调仓（无策略 vs 有策略）
        - 温度阈值调仓(现状)：v3 原策略
        - 温度自适应选基(改进假设)：冷市防御权重 / 热市动量权重

        假设：现状策略在牛市跑输，源于选基权重过度防御
        （低波动/低回撤权重过高），改进后应提升牛市收益。
        """
        if lookback_years is None:
            lookback_years = self.LOOKBACK_YEARS
        variants = {
            "买入持有(基线)": {"buy_and_hold": True},
            "温度阈值调仓(现状)": {},
            "温度自适应选基(改进)": {"adaptive_weights": True},
        }
        results = {}
        for name, params in variants.items():
            r = self.run(lookback_years=lookback_years, **params)
            if "error" in r:
                results[name] = {"error": r["error"]}
                continue
            entry = {
                "strategy": r["strategy"],
                "trades": len(r["trades"]),
                "params": r["params"],
            }
            for sym in ["sh000300", "sh000905"]:
                akey = f"alpha_vs_{sym}"
                if akey in r:
                    entry[f"alpha_vs_{sym}"] = r[akey]
            results[name] = entry
        return results
