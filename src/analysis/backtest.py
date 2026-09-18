"""
严谨回测引擎 v3.0

相对 v1/v2 的核心改进：
1. 防未来函数：PE 温度用「扩展窗口分位数」（只用 <=T 的数据），信号只用当日及之前数据
2. 交易成本计入指标：申购费（按份额类别）+ 赎回费（真源见 portfolio.redeem_fee_rate），
   全部流进 strategy_monthly 的**净收益**（旧版扣在一个没人读的变量上）
   —— **管理费不再单独计提**：净值本身已扣除管理费，再扣一次属重复计费
3. 多基准对比：沪深300 / 中证500（按**日期**对齐，非位置截断）
4. 统计检验：月度超额收益（alpha）的 t 检验、Beta
5. 逐年一致性：按**自然年**分解收益

已知局限（诚实声明）：
- 基金池来自当前数据库（存续基金），存在幸存者偏差（已清盘基金未纳入）
- 未考虑真实申赎的 T+1 确认延迟与资金占用
- README 里承诺的「等权基金组合」基准**未实现**，实际只有两个指数基准
"""

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import get_config
from ..data.database import Database
from .portfolio import redeem_fee_rate, purchase_fee_rate
from .nav_series import valuation_nav, valuation_nav_series
from .risk_free import RISK_FREE_ANNUAL


# =====================================================================
# 交易成本模型
# =====================================================================

@dataclass
class CostModel:
    """场外基金交易成本模型（C 类份额口径）。

    赎回费率的**唯一真源**是 `portfolio.redeem_fee_rate`（持有 <7 天取基金自身费率
    与监管下限 1.5% 孰高；≥7 天为 0）—— 与持仓账务同一口径。
    旧版这里自建了「7–30 天 0.5%」这一档，与账务真源冲突，会系统性高估成本。

    申购费率按**份额类别**（C/E/I 类不收前端申购费）—— 见 `portfolio.purchase_fee_rate`。

    **管理费默认为 0，这是有意的**：回测标的是基金公布的**单位净值**，而基金的管理费/
    托管费/销售服务费**每日从基金资产中计提，公告净值已扣除**（016453 费率页原文：
    「无需投资者在每笔交易中另行支付」）。所以再按月扣一次管理费就是**重复计费** ——
    旧版硬编码 1.5%/年，5 年回测会凭空低估约 7%，而且 1.5% 本身也是真实运作费
    （该基金三项合计 0.75%）的 2 倍。
    只有当回测标的换成**指数收益**（不含管理费）时，才需要把这里设回非 0。

    fee_scale 只用于敏感性/对照实验（0 = 零费率，用来隔离"成本是否进入指标"）。
    """
    purchase_fee: float = 0.0015          # 申购费 0.15%（仅 A 类/未知份额用）
    management_fee_annual: float = 0.0    # 管理费年化：净值已含，默认不再重复扣
    fee_scale: float = 1.0                # 1=按真实费率计；0=零费率对照

    def redemption_rate(self, days_held: int, redeem_fee_text=None) -> float:
        """赎回费率（委托给 portfolio 的单一真源）。"""
        return self.fee_scale * redeem_fee_rate(redeem_fee_text, days_held)

    def purchase_rate(self, fund_name: str = None) -> float:
        """前端申购费率。

        传了 `fund_name` 就按**份额类别**判断 —— C/E/I 类不收前端申购费（改收销售
        服务费，已从每日净值里扣）→ 0；A 类/推断不出类别 → `purchase_fee`。
        不传则退回 `purchase_fee`（用于"不知道具体标的"的粗略估算）。
        """
        if fund_name is None:
            return self.fee_scale * self.purchase_fee
        return self.fee_scale * purchase_fee_rate(fund_name, default=self.purchase_fee)

    def management_rate_annual(self) -> float:
        """月度摊销用的年化管理费率。**默认 0**（净值已含管理费，不再重复扣）。"""
        return self.fee_scale * self.management_fee_annual

    def round_trip_cost(self, days_held: int, redeem_fee_text=None) -> float:
        """一次完整买卖（含持有期管理费摊销）的总成本率"""
        mgmt = self.management_rate_annual() * days_held / 365.0
        return (self.purchase_rate()
                + self.redemption_rate(days_held, redeem_fee_text)
                + mgmt)


# =====================================================================
# 业绩指标计算
# =====================================================================

def compute_max_drawdown(nav: np.ndarray) -> float:
    """最大回撤（%）"""
    peak = np.maximum.accumulate(nav)
    dd = (peak - nav) / peak
    return float(dd.max() * 100)


def compute_metrics(returns: np.ndarray, rf_annual: float = RISK_FREE_ANNUAL) -> Dict:
    """
    从月频收益序列（百分数）计算业绩指标。

    **单位约定（务必看清）**：
    - 入参 `returns`：月收益，单位 **百分数**（1.5 表示 1.5%）
    - 入参 `rf_annual`：无风险利率（年化），单位 **小数**（`0.02` 表示 2%），
      默认值来自单一真源 `risk_free.RISK_FREE_ANNUAL`（批次 4.7，全项目统一），
      与 `vol_predictor._portfolio_metrics` 同语义
    - **返回值**：`total_return` / `annual_return` / `annual_volatility` /
      `max_drawdown` 均为**百分数**；`sharpe` 无量纲

    （`vol_predictor._portfolio_metrics` 的返回是**小数**，格式化成文本时
    必须走 `fmt_pct`，不要与这里的值混排。）

    Args:
        returns: 月收益数组，单位 %
        rf_annual: 无风险利率（年化），单位小数，0.02 = 2%
    """
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n == 0:
        return {}
    nav = np.cumprod(1 + r / 100.0)
    total_ret = (nav[-1] - 1) * 100
    ann_ret = ((nav[-1]) ** (12.0 / n) - 1) * 100
    ann_vol = np.std(r, ddof=1) * np.sqrt(12)
    # ann_ret 是**百分数**（如 12.86），rf_annual 是**小数**（如 0.02）。
    # 旧实现直接 `ann_ret - rf_annual` → 只扣了 0.02 个百分点而不是 2 个百分点
    # （差 100 倍，无风险利率形同没扣）。这里把 rf 归一到百分数再相减。
    sharpe = (ann_ret - rf_annual * 100.0) / ann_vol if ann_vol > 0 else 0
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
    # 双尾 p 值（真正的 t 分布，df=n-1）
    df = n - 1
    p_value = 2.0 * (1.0 - _t_cdf(abs(t_stat), df))
    return {
        "t_stat": round(float(t_stat), 3),
        "p_value": round(float(p_value), 4),
        "mean_alpha": round(float(mean), 3),
        "significant": bool(p_value < 0.05),
        "n": int(n),
    }


def _betacf(a: float, b: float, x: float) -> float:
    """正则化不完全贝塔函数的连分式（Lentz 法）。"""
    MAXIT, EPS, FPMIN = 300, 3e-16, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = FPMIN if abs(d) < FPMIN else d
        c = 1.0 + aa / c
        c = FPMIN if abs(c) < FPMIN else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = FPMIN if abs(d) < FPMIN else d
        c = 1.0 + aa / c
        c = FPMIN if abs(c) < FPMIN else c
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """正则化不完全贝塔函数 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _t_cdf_numerical(x: float, df: int) -> float:
    """t 分布 CDF 的数值实现（不完全贝塔函数），作为无 scipy 时的回退。"""
    x = float(x)
    if df <= 0:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))
    p = 0.5 * _betainc(df / 2.0, 0.5, df / (df + x * x))
    return 1.0 - p if x > 0 else p


def _t_cdf(x: float, df: int) -> float:
    """t 分布累积分布函数 CDF(x; df)。

    scipy 在 requirements 里是**可选**依赖，所以优先用 scipy、不可用时走
    上面的数值回退（两者在测试里逐点对齐）。

    旧实现直接返回标准正态 CDF、参数 df 根本没用上 —— 小样本（df 十几二十）
    下尾部概率被低估、p 值偏小、显著性被夸大。
    """
    try:
        from scipy import stats as _st
        return float(_st.t.cdf(float(x), df))
    except Exception:
        return _t_cdf_numerical(x, df)


def compute_beta_and_alpha(strategy_ret: np.ndarray, benchmark_ret: np.ndarray) -> Dict:
    """回归计算 Beta 与年化 Alpha（月度收益线性回归）"""
    s = np.asarray(strategy_ret, dtype=float)
    b = np.asarray(benchmark_ret, dtype=float)
    n = min(len(s), len(b))
    if n < 3:
        return {"beta": None, "alpha_annual": None}
    s, b = s[:n], b[:n]
    # 分子分母统一用样本协方差/样本方差（ddof=1）；旧版分子 ddof=1、分母 ddof=0
    cov = np.cov(s, b, ddof=1)[0, 1]
    var_b = np.var(b, ddof=1)
    beta = cov / var_b if var_b > 0 else 0
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
        """获取温度序列（带缓存，多次回测只拉一次 akshare）"""
        if self._temp_map is None:
            self._temp_map = self.get_pe_temperature_series()
        return self._temp_map

    def _get_benchmarks(self, symbols: tuple) -> Dict[str, Optional[pd.Series]]:
        """获取基准指数收盘序列（带缓存）"""
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
                # 估值/打分一律用累计净值（分红除息日单位净值下挫会被误判成下跌）
                nav_series = valuation_nav_series(df_nav).values

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
        """自然**月末**网格。

        旧版 `d -= timedelta(days=30)` 每期只有 30 天，12 期 = 360 天而非一年，
        而 compute_metrics 按 ×12 年化 → 年化收益与波动率系统性偏乐观，且日期
        逐月漂移（08-07、07-08、06-10…）。改用自然月末，年化按实际期间数。
        """
        start_dt = pd.to_datetime(start)
        end_dt = pd.to_datetime(end)
        if pd.isna(start_dt) or pd.isna(end_dt) or end_dt <= start_dt:
            return []
        return [d.strftime("%Y-%m-%d")
                for d in pd.date_range(start=start_dt, end=end_dt, freq="ME")]

    def _fund_names(self, codes) -> Dict[str, str]:
        """code -> fund_name（一次拉取，供**份额类别**判断用）。查不到记为 ''。"""
        out: Dict[str, str] = {}
        for c in codes:
            try:
                info = self.db.get_fund_info(c) or {}
            except Exception:
                info = {}
            out[c] = info.get("fund_name") or ""
        return out

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
        # 份额类别查表：申购费按 C/A 类分派（见 portfolio.purchase_fee_rate）
        name_map = self._fund_names(all_codes)

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
        holdings: List[Dict] = []  # [{code, buy_date, value}]  value 始终按市值维护
        last_temp = None
        strategy_returns: Dict[str, float] = {}                       # 收益实现月 -> 净收益%
        benchmark_returns: Dict[str, Dict[str, float]] = {sym: {} for sym in benchmarks}
        trade_log: List[Dict] = []

        for i, m in enumerate(months[:-1]):
            next_m = months[i + 1]
            temp = self._nearest_temp(temp_map, m)
            pv_start = portfolio_value     # 期初市值（本月所有费用计入之前）

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
                    n_new = len(new_picks)
                    cur = self._holdings_by_code(holdings)
                    # 成本前等权目标；新组合之外的基金目标为 0 → 视作全额卖出
                    pre_target = portfolio_value / n_new
                    target0 = {c: pre_target for c in new_picks}
                    sell_amt = {c: max(0.0, v - target0.get(c, 0.0)) for c, v in cur.items()}

                    # 赎回费：按各持仓**当期市值**与真实持有天数计
                    # （旧版用 h["value"]，而它从建仓后再没按市值更新过）
                    sell_cost = 0.0
                    for h in holdings:
                        c = h["code"]
                        amt = sell_amt.get(c, 0.0)
                        if amt <= 0 or cur.get(c, 0.0) <= 0:
                            continue
                        amt *= h["value"] / cur[c]      # 该笔在同类持仓中占的份额
                        days = (pd.to_datetime(m) - pd.to_datetime(h["buy_date"])).days
                        sell_cost += amt * self.cost.redemption_rate(days)
                    portfolio_value -= sell_cost

                    # 申购费：只对**实际加仓**的成交额计（旧版对整仓计费，
                    # 选中同一批基金原封不动持有也会被收一次申购费）。
                    # 费率按**份额类别**逐只判断：C/E/I 类不收前端申购费 → 0。
                    post_target = portfolio_value / n_new
                    kept = {c: max(0.0, cur.get(c, 0.0) - sell_amt.get(c, 0.0)) for c in new_picks}
                    buy_notional, buy_cost = 0.0, 0.0
                    for c in new_picks:
                        amt = max(0.0, post_target - kept[c])
                        if amt <= 0:
                            continue
                        buy_notional += amt
                        buy_cost += amt * self.cost.purchase_rate(name_map.get(c, ""))
                    portfolio_value -= buy_cost

                    holdings = [
                        {"code": c, "buy_date": m, "value": post_target}
                        for c in new_picks
                    ]
                    trade_log.append({
                        "date": m, "temp": temp, "picks": new_picks,
                        "sell_cost": round(sell_cost, 4), "buy_cost": round(buy_cost, 4),
                        "sell_notional": round(sum(sell_amt.values()), 2),
                        "buy_notional": round(buy_notional, 2),
                    })
                    last_temp = temp

            # --- 月度持仓收益 ---
            if holdings:
                rets = self._monthly_returns(holdings, m, next_m)
                if rets:
                    tot = sum(h["value"] for h in holdings)
                    if tot > 0:
                        port_ret = sum(h["value"] * rets.get(h["code"], 0.0)
                                       for h in holdings) / tot
                        # 管理费月度摊销：组合与逐笔价值同步扣减，保证账实一致
                        decay = (1 - self.cost.management_rate_annual() / 12)
                        portfolio_value *= (1 + port_ret / 100) * decay
                        for h in holdings:
                            h["value"] *= (1 + rets.get(h["code"], 0.0) / 100) * decay
                        # 净收益 = 期末 / 期初 − 1：申购费、赎回费、管理费**全部**流进指标。
                        # 旧版这里是 append(ret)（毛收益），cost 扣在 portfolio_value 上却
                        # 从不被 _build_report 读取 → 所有指标都是费用前的。
                        if pv_start > 0:
                            strategy_returns[next_m] = (portfolio_value / pv_start - 1) * 100

            # --- 基准月度收益（按日期记录，供按日期对齐） ---
            for sym, series in benchmarks.items():
                if series is None:
                    continue
                b_idx = series[series.index <= pd.to_datetime(m)]
                a_idx = series[series.index <= pd.to_datetime(next_m)]
                if len(b_idx) > 0 and len(a_idx) > 0:
                    benchmark_returns[sym][next_m] = (a_idx.iloc[-1] / b_idx.iloc[-1] - 1) * 100

        if len(strategy_returns) < 6:
            return {"error": "回测数据不足，请扩大回看年数或补充净值数据"}

        # 6. 汇总报告（指标 + 多基准对比 + 显著性 + 逐年一致性）
        return self._build_report(
            strategy_returns, benchmark_returns, trade_log,
            params={
                "selection_weights": list(selection_weights),
                "adaptive_weights": adaptive_weights,
                "temp_threshold": temp_threshold,
                "buy_and_hold": buy_and_hold,
            },
        )

    # ------------------------------------------------------------------
    # 回测辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _holdings_by_code(holdings) -> Dict[str, float]:
        """把逐笔持仓按基金代码汇总成 市值 dict（同一只基金可能有多笔）。"""
        out: Dict[str, float] = {}
        for h in holdings:
            out[h["code"]] = out.get(h["code"], 0.0) + h["value"]
        return out

    def _monthly_returns(self, holdings, m: str, next_m: str) -> Dict[str, float]:
        """各持仓当月收益（%）：code -> ret

        取**累计净值**：这是"这只基金这个月替你赚了多少"，
        单位净值会把分红除息当成下跌，把回测年化系统性压低。
        """
        out: Dict[str, float] = {}
        for h in holdings:
            nb = self.db.get_fund_nav(h["code"], end_date=m)
            na = self.db.get_fund_nav(h["code"], end_date=next_m)
            if nb and na:
                b = valuation_nav(nb[-1]["unit_nav"], nb[-1].get("acc_nav"))
                a = valuation_nav(na[-1]["unit_nav"], na[-1].get("acc_nav"))
                if b > 0:
                    out[h["code"]] = (a / b - 1) * 100
        return out

    def _build_report(
        self,
        strategy_returns: Dict[str, float],
        benchmark_returns: Dict[str, Dict[str, float]],
        trade_log: list,
        params: dict,
    ) -> Dict:
        """汇总回测报告：策略指标 + 多基准对比 + alpha 显著性 + 逐年一致性

        策略与基准一律**按日期对齐**（inner join）。旧版用 `rets[:len(sr)]`
        按位置截断，等于拿策略的月份去比基准**最早**的那些月份。
        """
        sr = pd.Series(strategy_returns).sort_index()
        result = {
            "strategy": compute_metrics(sr.values),
            "trades": trade_log,
            "params": params,
            "note": ("基金池为存续基金，存在幸存者偏差；温度分位数为扩展窗口（无未来函数）；"
                     "指标为扣除申购费/赎回费/管理费后的**净收益**；"
                     "策略与基准按日期 inner join 对齐"),
            "cost_model": {
                "purchase_fee": self.cost.purchase_fee,
                "purchase_fee_note": "A 类/未知份额用此默认值；C/E/I 类前端申购费为 0"
                                     "（见 portfolio.purchase_fee_rate）",
                "redemption_fee_lt7d": round(self.cost.redemption_rate(3), 4),
                "redemption_fee_ge7d": round(self.cost.redemption_rate(30), 4),
                "management_fee_annual": self.cost.management_fee_annual,
                "fee_scale": self.cost.fee_scale,
                "round_trip_cost_1y": round(self.cost.round_trip_cost(365), 4),
                "source": "portfolio.redeem_fee_rate / purchase_fee_rate（与持仓账务同一真源）",
            },
        }
        for sym, rets in benchmark_returns.items():
            br = pd.Series(rets).sort_index()
            joined = pd.concat([sr.rename("s"), br.rename("b")], axis=1, join="inner").dropna()
            if len(joined) < 3:
                continue
            s_a = joined["s"].values
            b_a = joined["b"].values
            metrics = compute_metrics(b_a)
            metrics["name"] = sym
            metrics["aligned_months"] = int(len(joined))
            result[f"benchmark_{sym}"] = metrics
            result[f"alpha_vs_{sym}"] = alpha_t_test(s_a, b_a)
            result[f"regression_vs_{sym}"] = compute_beta_and_alpha(s_a, b_a)
        result["yearly"] = self._yearly_breakdown(sr, benchmark_returns)
        return result

    # ------------------------------------------------------------------
    # 逐年一致性
    # ------------------------------------------------------------------

    def _yearly_breakdown(
        self,
        strategy_ret: pd.Series,
        benchmark_returns: Dict[str, Dict[str, float]],
    ) -> List[Dict]:
        """按**自然年**分解收益。

        旧版把收益序列每 12 期切一块并标成"第N年"—— 起止落在年中间，不是自然年，
        且基准用同位置切片，等于拿"平移过的因子序列"比"未平移的基准"。
        收益实现月（next_m）才是归属年份，这里直接按该月份的自然年分组。
        """
        if len(strategy_ret) == 0:
            return []
        years = []
        for year, seg in strategy_ret.groupby(strategy_ret.index.str[:4]):
            nav = np.cumprod(1 + seg.values / 100.0)
            entry = {
                "period": str(year),
                "months": int(len(seg)),
                "strategy_return": round(float((nav[-1] - 1) * 100), 2),
            }
            for sym, rets in benchmark_returns.items():
                br = pd.Series(rets).sort_index()
                common = seg.index.intersection(br.index)
                if len(common) == 0:
                    continue
                bnav = np.cumprod(1 + br.loc[sorted(common)].values / 100.0)
                entry[sym] = round(float((bnav[-1] - 1) * 100), 2)
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
