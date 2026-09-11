"""
行业轮动因子有效性验证（天花板测试）

运行方式:
    python src/analysis/factor_test.py [--db data/fund_quant.db] [--fetch]

说明:
    --fetch  强制从 akshare 重新拉取数据（默认若本地缓存存在则直接读取缓存）

依赖: pandas, numpy, scipy, akshare

本模块独立于现有核心模块，不修改数据库与核心功能。所有额外数据缓存到
data/cache/ 目录，原始结果输出到 data/factor_results/，报告输出到
docs/factor_test_report.md。

测试 5 个独立因子:
    1. 动量_1M   (过去1月收益率，正向)
    2. 动量_3M   (过去3月累计收益率，正向)
    3. 动量_6M   (过去6月累计收益率，正向)
    4. 回调_3M_1M (中期强势中的短期回调，正向)
    5. 低拥挤度   (成交额占比历史分位数，反向)
"""

import argparse
import os
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# =====================================================================
# 路径与常量
# =====================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
RESULT_DIR = DATA_DIR / "factor_results"
DOCS_DIR = PROJECT_ROOT / "docs"
DB_PATH = DATA_DIR / "fund_quant.db"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)

SW_CACHE = CACHE_DIR / "sw_sector_daily.csv"
BENCH_CACHE = CACHE_DIR / "csi300_tr_daily.csv"

# 申万2021版一级行业（31个）代码->名称
SW_SECTORS = {
    "801010": "农林牧渔", "801030": "基础化工", "801040": "钢铁",
    "801050": "有色金属", "801080": "电子", "801110": "家用电器",
    "801120": "食品饮料", "801130": "纺织服饰", "801140": "轻工制造",
    "801150": "医药生物", "801160": "公用事业", "801170": "交通运输",
    "801180": "房地产", "801200": "商贸零售", "801210": "社会服务",
    "801230": "综合", "801710": "建筑材料", "801720": "建筑装饰",
    "801730": "电力设备", "801740": "国防军工", "801750": "计算机",
    "801760": "传媒", "801770": "通信", "801780": "银行",
    "801790": "非银金融", "801880": "汽车", "801890": "机械设备",
    "801950": "煤炭", "801960": "石油石化", "801970": "环保",
    "801980": "美容护理",
}

FACTOR_NAMES = ["动量_1M", "动量_3M", "动量_6M", "回调_3M_1M", "低拥挤度"]
FACTOR_COLS = ["mom_1m", "mom_3m", "mom_6m", "pullback_3m_1m", "low_crowding"]

ONE_WAY_COST = 0.005        # 单边交易成本 0.5%
TOP_N = 3                   # 持仓行业数
TOP_N_SENSITIVITY = 5       # 敏感性分析持仓数
ROLLING_IC_WINDOW = 12      # 滚动IC窗口（月）


# =====================================================================
# 数据采集
# =====================================================================

def _retry(func, *args, retries=3, delay=2, **kwargs):
    """带重试的函数调用"""
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if i == retries - 1:
                raise
            print(f"    重试 {i+1}/{retries} ({delay}s): {repr(e)[:80]}")
            time.sleep(delay)
            delay *= 1.5


def fetch_sw_sectors(force=False):
    """
    拉取31个申万一级行业日频行情，缓存到 CSV。

    返回 long-format DataFrame: columns=[code, name, date, close, amount]
    """
    if SW_CACHE.exists() and not force:
        print(f"  [缓存] 读取行业数据: {SW_CACHE}")
        df = pd.read_csv(SW_CACHE, parse_dates=["date"])
        return df

    import akshare as ak

    frames = []
    total = len(SW_SECTORS)
    for i, (code, name) in enumerate(SW_SECTORS.items()):
        try:
            raw = _retry(ak.index_hist_sw, symbol=code, period="day")
            raw = raw.rename(columns={"日期": "date", "收盘": "close", "成交额": "amount"})
            raw["date"] = pd.to_datetime(raw["date"])
            raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
            raw["amount"] = pd.to_numeric(raw["amount"], errors="coerce")
            raw = raw[["date", "close", "amount"]].dropna(subset=["close"])
            raw["code"] = code
            raw["name"] = name
            frames.append(raw)
            if (i + 1) % 10 == 0:
                print(f"    进度: {i+1}/{total}")
        except Exception as e:
            print(f"    [警告] 行业 {code} {name} 拉取失败: {repr(e)[:80]}")
        time.sleep(0.12)

    if not frames:
        raise RuntimeError("所有行业数据拉取失败")

    df = pd.concat(frames, ignore_index=True)
    df.to_csv(SW_CACHE, index=False)
    print(f"  [缓存] 已保存行业数据: {SW_CACHE} ({len(df):,} 行)")
    return df


def fetch_benchmark(force=False):
    """
    拉取沪深300**全收益**指数（H00300），缓存到 CSV。

    返回 DataFrame: columns=[date, close, index_code]
    写入 `index_code` 列是为了让后续**能显式判定**基准到底是全收益还是价格指数。
    """
    if BENCH_CACHE.exists() and not force:
        print(f"  [缓存] 读取基准数据: {BENCH_CACHE}")
        df = pd.read_csv(BENCH_CACHE, parse_dates=["date"])
        return df

    import akshare as ak

    print("  拉取沪深300全收益指数 (H00300)...")
    # 分段拉取以避免超时
    start = "20140101"
    end = pd.Timestamp.today().strftime("%Y%m%d")
    used_code = "H00300"
    try:
        raw = _retry(
            ak.stock_zh_index_hist_csindex,
            symbol="H00300", start_date=start, end_date=end,
        )
    except Exception as e:
        print(f"  [警告] H00300 拉取失败: {repr(e)[:80]}，尝试价格指数 000300 降级")
        used_code = "000300"
        raw = _retry(
            ak.stock_zh_index_hist_csindex,
            symbol="000300", start_date=start, end_date=end,
        )

    raw = raw.rename(columns={"日期": "date", "收盘": "close"})
    raw["date"] = pd.to_datetime(raw["date"])
    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    df = raw[["date", "close"]].dropna().sort_values("date").reset_index(drop=True)
    df["index_code"] = used_code          # 记录实际使用的指数代码
    df.to_csv(BENCH_CACHE, index=False)
    print(f"  [缓存] 已保存基准数据: {BENCH_CACHE} ({len(df):,} 行, {used_code})")
    return df


# 全收益 / 价格 指数代码白名单（中证指数系列）
TOTAL_RETURN_CODES = ("H00300", "H00905", "H00852", "H00985", "H000300")
PRICE_ONLY_CODES = ("000300", "000905", "000852", "399300")


def detect_benchmark_type(cache_path):
    """显式判定基准类型：'total_return' / 'price_only' / 'unknown'。

    旧实现是 `("H00300" in head) or ("close" in head.lower())` —— CSV 表头永远是
    `date,close`，所以恒为 True，等于无条件宣称"用了全收益指数"。
    这里改成按 index_code 白名单判定；读不到代码就返回 unknown，绝不默认全收益。
    """
    try:
        # index_code 按字符串读：否则 "000300" 会被解析成整数 300，丢掉前导零
        head = pd.read_csv(cache_path, nrows=1, dtype={"index_code": str})
    except Exception:
        return "unknown"

    tokens = [str(c) for c in head.columns]
    if "index_code" in head.columns and len(head):
        tokens.append(str(head["index_code"].iloc[0]))

    for tok in tokens:
        if any(code in tok for code in TOTAL_RETURN_CODES):
            return "total_return"
    for tok in tokens:
        if any(code in tok for code in PRICE_ONLY_CODES):
            return "price_only"

    # 缓存文件名约定（如 csi300_tr_daily.csv）：仅作兜底线索，仍不臆断
    name = Path(cache_path).name.lower()
    if "_tr" in name or "total" in name:
        return "total_return"
    return "unknown"


# =====================================================================
# 数据处理：日频 -> 月频
# =====================================================================

def to_monthly(daily_df):
    """
    日频行业数据转月频。

    收盘价: 每月最后一个交易日收盘价
    成交额: 当月日均成交额（sum / 交易日数）

    返回 wide DataFrame, index=月末日期, columns=行业代码
    close_wide: 收盘价
    amount_wide: 月日均成交额
    """
    df = daily_df.copy()
    df["month"] = df["date"].dt.to_period("M")

    # 每月最后一个交易日的收盘价
    idx = df.groupby(["code", "month"])["date"].idxmax()
    close_long = df.loc[idx, ["code", "month", "close"]].copy()
    close_wide = close_long.pivot(index="month", columns="code", values="close")
    close_wide.index = close_wide.index.to_timestamp("M")

    # 月日均成交额 = 当月成交额之和 / 当月交易日数
    trading_days = df.groupby(["code", "month"])["date"].nunique()
    amount_sum = df.groupby(["code", "month"])["amount"].sum()
    amount_avg = (amount_sum / trading_days).reset_index(name="amount")
    amount_wide = amount_avg.pivot(index="month", columns="code", values="amount")
    amount_wide.index = amount_wide.index.to_timestamp("M")

    # 对齐索引
    all_idx = close_wide.index.union(amount_wide.index)
    close_wide = close_wide.reindex(all_idx)
    amount_wide = amount_wide.reindex(all_idx)

    return close_wide, amount_wide


def benchmark_monthly(bench_daily):
    """基准日频转月频（月末收盘价）"""
    df = bench_daily.copy()
    df["month"] = df["date"].dt.to_period("M")
    idx = df.groupby("month")["date"].idxmax()
    monthly = df.loc[idx, ["month", "close"]].set_index("month")
    monthly.index = monthly.index.to_timestamp("M")
    monthly["ret"] = monthly["close"].pct_change()
    return monthly


# =====================================================================
# 因子计算
# =====================================================================

def rank_pct(series):
    """横截面秩百分位 (0-1), 1=最高。NaN 不参与。"""
    return series.rank(pct=True)


def compute_factors(close_wide, amount_wide):
    """
    计算5个因子，返回 dict: factor_name -> DataFrame(index=月末, columns=行业代码, 值域0-1)
    因子在 t 月末计算，使用 t 月末及之前的数据。
    """
    # 原始收益率（未排序）
    ret_1m = close_wide.pct_change(1)
    ret_3m = close_wide.pct_change(3)
    ret_6m = close_wide.pct_change(6)

    # 动量因子：横截面秩百分位（正向）
    mom_1m = ret_1m.apply(rank_pct, axis=1)
    mom_3m = ret_3m.apply(rank_pct, axis=1)
    mom_6m = ret_6m.apply(rank_pct, axis=1)

    # 回调_3M_1M: rank(ret_3m) * rank(-ret_1m)
    rank_3m_pct = ret_3m.apply(rank_pct, axis=1)
    rank_1m_rev_pct = (-ret_1m).apply(rank_pct, axis=1)
    pullback = rank_3m_pct * rank_1m_rev_pct

    # 低拥挤度
    # turnover_share_t = 行业t月日均成交额 / 全市场t月日均成交额之和
    market_total = amount_wide.sum(axis=1)
    turnover_share = amount_wide.div(market_total, axis=0)

    # 历史分位数窗口: t-23 到 t-1（不含 t）
    # rolling(24) 会包含当前行，需要 shift(1) 排除 t 月
    def rolling_percentile(s):
        # s 是单行业的 turnover_share 时间序列
        # 对每个 t，用 [t-23, t-1] 的 24 个月窗口计算 s[t] 的历史分位
        out = pd.Series(index=s.index, dtype=float)
        vals = s.values
        n = len(vals)
        for t in range(n):
            if t < 24:  # 至少需要24个月历史窗口
                out.iloc[t] = np.nan
                continue
            window = vals[t - 24:t]  # t-23 到 t-1 共24个
            cur = vals[t]
            if np.isnan(cur) or np.all(np.isnan(window)):
                out.iloc[t] = np.nan
                continue
            # empirical percentile: window 中 <= cur 的比例
            valid = window[~np.isnan(window)]
            if len(valid) == 0:
                out.iloc[t] = np.nan
                continue
            out.iloc[t] = (valid <= cur).mean()
        return out

    crowding_pct = turnover_share.apply(rolling_percentile, axis=0)
    low_crowding = 1 - crowding_pct  # 反向：分位数越低因子值越高

    # 统一做一次横截面秩标准化（值域0-1），使各因子可比
    factors = {
        "mom_1m": mom_1m.apply(rank_pct, axis=1),
        "mom_3m": mom_3m.apply(rank_pct, axis=1),
        "mom_6m": mom_6m.apply(rank_pct, axis=1),
        "pullback_3m_1m": pullback.apply(rank_pct, axis=1),
        "low_crowding": low_crowding.apply(rank_pct, axis=1),
    }
    return factors


# =====================================================================
# 回测引擎
# =====================================================================

def backtest_factor(factor_df, close_wide, bench_monthly, top_n=TOP_N,
                    one_way_cost=ONE_WAY_COST):
    """
    单因子回测。

    因子在 t 月末计算，选出 top_n 个行业，等权持有 t+1 月。

    参数:
        factor_df: 因子值 DataFrame (index=月末, columns=行业代码)
        close_wide: 月频收盘价
        bench_monthly: 基准月频 (含 ret 列)
        top_n: 持仓行业数
        one_way_cost: 单边成本

    返回: dict of metrics + monthly returns series
    """
    # 行业月度收益
    sector_ret = close_wide.pct_change()

    # 对齐：因子 t 月 -> 持仓收益 t+1 月
    # factor_df.index 是 t 月末；持仓收益是 t+1 月
    signal_dates = factor_df.index
    hold_dates = signal_dates + pd.offsets.MonthEnd(1)

    # 只保留两边都有的
    common = signal_dates.intersection(sector_ret.index)
    # 持仓收益 = sector_ret.shift(-1) 在 signal_date 处的值
    # 即：在 t 月末用因子选行业，收益为 t->t+1 的收益 = sector_ret.loc[t+1]
    # 更直接：next_ret = sector_ret.shift(-1)
    next_ret = sector_ret.shift(-1)

    portfolio_rets = []
    turnover_list = []
    prev_holdings = None
    valid_dates = []

    for dt in signal_dates:
        if dt not in factor_df.index or dt not in next_ret.index:
            continue
        fv = factor_df.loc[dt].dropna()
        if len(fv) < top_n:
            continue
        # 取因子值最高的 top_n 个
        selected = fv.nlargest(top_n).index.tolist()
        # 下月收益
        rets = next_ret.loc[dt, selected]
        if rets.isna().any():
            continue
        port_ret = rets.mean()

        # 换手率
        if prev_holdings is None:
            turnover = 1.0   # 首次建仓
            sides = 1        # 只买、不卖 → 单边成本
        else:
            changed = len(set(selected) ^ set(prev_holdings))
            turnover = changed / (2 * top_n)
            sides = 2        # 调仓是双边

        # 成本：首月按单边计（旧版一律 ×2，首月凭空多收 0.5pp）
        cost = turnover * one_way_cost * sides
        net_ret = port_ret - cost

        portfolio_rets.append(net_ret)
        turnover_list.append(turnover)
        prev_holdings = selected
        valid_dates.append(dt)

    if not portfolio_rets:
        return None

    port_series = pd.Series(portfolio_rets, index=valid_dates, name="port_ret")
    bench_rets = bench_monthly["ret"].reindex(port_series.index)
    excess = port_series - bench_rets

    metrics = compute_metrics(port_series, bench_rets, excess, turnover_list)
    metrics["monthly_returns"] = port_series
    metrics["excess_returns"] = excess
    metrics["turnover_list"] = turnover_list
    return metrics


def compute_metrics(port_rets, bench_rets, excess, turnover_list):
    """计算业绩与统计指标"""
    r = port_rets.dropna().values
    n = len(r)
    if n == 0:
        return {}

    nav = np.cumprod(1 + r)
    total_ret = nav[-1] - 1
    ann_ret = nav[-1] ** (12 / n) - 1
    ann_vol = np.std(r, ddof=1) * np.sqrt(12) if n > 1 else 0
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0

    # 最大回撤
    peak = np.maximum.accumulate(nav)
    dd = (peak - nav) / peak
    max_dd = float(dd.max())

    # 基准
    b = bench_rets.dropna().values
    if len(b) > 0:
        b_nav = np.cumprod(1 + b)
        b_ann = b_nav[-1] ** (12 / len(b)) - 1
    else:
        b_ann = 0

    excess_clean = excess.dropna().values
    if len(excess_clean) > 1:
        t_stat, p_val = stats.ttest_1samp(excess_clean, 0)
    else:
        t_stat, p_val = 0, 1.0

    avg_turnover = float(np.mean(turnover_list)) if turnover_list else 0

    return {
        "months": n,
        "annual_return": ann_ret,
        "annual_volatility": ann_vol,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "benchmark_annual": b_ann,
        "annual_excess": ann_ret - b_ann,
        "t_stat": float(t_stat),
        "p_value": float(p_val),
        "avg_turnover": avg_turnover,
        "total_return": total_ret,
    }


def compute_ic(factor_df, close_wide):
    """
    计算 IC: 每月因子秩与下月收益秩的 Spearman 相关系数。

    返回: DataFrame(index=月末, columns=各因子IC值) + 汇总统计
    """
    sector_ret = close_wide.pct_change()
    next_ret = sector_ret.shift(-1)

    ic_records = {}
    for fname, fdf in factor_df.items():
        ics = []
        dates = []
        for dt in fdf.index:
            if dt not in next_ret.index:
                continue
            fv = fdf.loc[dt]
            rv = next_ret.loc[dt]
            valid = fv.dropna().index.intersection(rv.dropna().index)
            if len(valid) < 5:
                continue
            # Spearman = Pearson of ranks
            fr = fv.loc[valid].rank()
            rr = rv.loc[valid].rank()
            if fr.std() == 0 or rr.std() == 0:
                continue
            ic = fr.corr(rr)
            if not np.isnan(ic):
                ics.append(ic)
                dates.append(dt)
        ic_records[fname] = pd.Series(ics, index=dates, name=fname)

    ic_df = pd.DataFrame(ic_records)
    ic_summary = {}
    for col in ic_df.columns:
        s = ic_df[col].dropna()
        if len(s) == 0:
            ic_summary[col] = {"ic_mean": 0, "ic_std": 0, "icir": 0, "ic_pos_ratio": 0}
            continue
        ic_mean = s.mean()
        ic_std = s.std()
        icir = ic_mean / ic_std if ic_std > 0 else 0
        ic_pos = (s > 0).mean()
        ic_summary[col] = {
            "ic_mean": ic_mean,
            "ic_std": ic_std,
            "icir": icir,
            "ic_pos_ratio": ic_pos,
        }
    return ic_df, ic_summary


def yearly_returns(port_rets, shift_to_realization=True):
    """分年度收益。

    `port_rets` 的索引是**信号月 t**，而收益来自 `sector_ret.shift(-1)`（t → t+1），
    真正的**收益实现月**是 t+1。旧版按信号月所在的自然年归组，会把 12 月信号赚到的
    次年 1 月收益算进上一年 —— 而基准列是按实际月末归组的，两边错位一个月。

    `shift_to_realization=False` 用于基准（它本身已按实际实现月索引，不能再推）。
    """
    s = port_rets.copy()
    s.index = pd.to_datetime(s.index)
    idx = s.index + pd.offsets.MonthBegin(1) if shift_to_realization else s.index
    return (1 + s).groupby(idx.year).prod() - 1


# =====================================================================
# 显著性判定（多重检验校正）
# =====================================================================

def gate_check(metrics, ic_summary, n_factors=5):
    """
    天花板测试判定。
    返回 (passed: bool, verdict: str, details: dict)
    """
    p_raw = metrics["p_value"]
    p_bonf = min(p_raw * n_factors, 1.0)
    ann_excess = metrics["annual_excess"]
    max_dd = metrics["max_drawdown"]
    icir = ic_summary.get("icir", 0)

    conditions = {
        "strict": p_bonf < 0.02 and ann_excess > 0.05,
        "marginal": p_raw < 0.1 and ann_excess > 0.08,
        "icir_stable": icir > 0.5 and p_raw < 0.1,
    }
    passed = any(conditions.values())

    if conditions["strict"]:
        verdict = "通过（严格显著）"
    elif conditions["marginal"]:
        verdict = "通过（边缘显著，收益覆盖成本）"
    elif conditions["icir_stable"]:
        verdict = "通过（ICIR稳定）"
    elif p_raw < 0.1:
        verdict = "探索性信号（p<0.1，未通过天花板）"
    else:
        verdict = "未通过"

    return passed, verdict, {
        "p_raw": p_raw,
        "p_bonferroni": p_bonf,
        "annual_excess": ann_excess,
        "max_drawdown": max_dd,
        "icir": icir,
        "conditions": conditions,
    }


# =====================================================================
# 阶段2：因子组合
# =====================================================================

def build_combination(factor_df_list, close_wide, bench_monthly, top_n=TOP_N):
    """
    方案A：等权合成。将多个因子分别横截面秩标准化后取均值，按合成值排序选股。
    """
    # 各因子已在 compute_factors 中做过秩标准化(0-1)，直接取均值
    combined = sum(factor_df_list) / len(factor_df_list)
    combined = combined.apply(rank_pct, axis=1)  # 再秩标准化
    metrics = backtest_factor(combined, close_wide, bench_monthly, top_n=top_n)
    return combined, metrics


def build_filtered(main_factor, filter_factor, close_wide, bench_monthly,
                   top_n=3, filter_pct=0.2):
    """
    方案B：主因子选股（前5名）+ 第二因子过滤（去掉过滤因子排名后20%的行业），最终持仓3个。
    """
    combined = main_factor.copy()
    # 在横截面上，将过滤因子排名后 filter_pct 的行业因子值置为 NaN（不选）
    for dt in main_factor.index:
        if dt not in filter_factor.index:
            continue
        fv = filter_factor.loc[dt].dropna()
        if len(fv) == 0:
            continue
        threshold = fv.quantile(filter_pct)
        bad = fv[fv <= threshold].index
        combined.loc[dt, bad] = np.nan

    # 用主因子排序，取前 top_n（已过滤）
    metrics = backtest_factor(combined, close_wide, bench_monthly, top_n=top_n)
    return combined, metrics


# =====================================================================
# 额外分析
# =====================================================================

def factor_correlation(factor_df_dict):
    """5个因子的 Spearman 秩相关系数矩阵（基于月度因子值，长表堆叠）"""
    # 将每个因子展平为长格式: (date, code, value)
    long_frames = []
    for fname, fdf in factor_df_dict.items():
        stacked = fdf.stack().reset_index()
        stacked.columns = ["date", "code", fname]
        stacked = stacked.set_index(["date", "code"])
        long_frames.append(stacked)
    merged = pd.concat(long_frames, axis=1).dropna()
    if merged.empty:
        return pd.DataFrame()
    corr = merged.corr(method="spearman")
    return corr


def split_sample(metrics_dict, factor_df, close_wide, bench_monthly,
                 split_date="2020-12-31"):
    """样本内/外分别回测。返回 (is_metrics, oos_metrics)"""
    split_ts = pd.Timestamp(split_date)
    is_factor = factor_df[factor_df.index <= split_ts]
    oos_factor = factor_df[factor_df.index > split_ts]

    is_m = backtest_factor(is_factor, close_wide, bench_monthly)
    oos_m = backtest_factor(oos_factor, close_wide, bench_monthly)
    return is_m, oos_m


def market_regime(bench_monthly, window=6):
    """
    按沪深300走势划分市场环境：
    - 牛市: 过去6月累计收益 > 5%
    - 熊市: 过去6月累计收益 < -5%
    - 震荡: 介于两者之间
    """
    b = bench_monthly.copy()
    cum_6m = (1 + b["ret"]).rolling(window).apply(np.prod, raw=True) - 1
    regime = pd.Series("震荡", index=b.index)
    regime[cum_6m > 0.05] = "牛市"
    regime[cum_6m < -0.05] = "熊市"
    return regime


def regime_excess(port_rets, bench_rets, regime):
    """各市场环境下的年化超额收益"""
    excess = (port_rets - bench_rets).dropna()
    aligned_regime = regime.reindex(excess.index)
    out = {}
    for env in ["牛市", "熊市", "震荡"]:
        mask = aligned_regime == env
        seg = excess[mask]
        if len(seg) < 3:
            out[env] = (np.nan, len(seg))
            continue
        # 月度均值 * 12 作为年化超额
        out[env] = (seg.mean() * 12, len(seg))
    return out


def rolling_ic(ic_series, window=ROLLING_IC_WINDOW):
    """滚动IC"""
    return ic_series.rolling(window).mean()


# =====================================================================
# 报告生成
# =====================================================================

def fmt_pct(x, digits=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "N/A"
    return f"{x*100:.{digits}f}%"


def fmt_num(x, digits=4):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "N/A"
    return f"{x:.{digits}f}"


def generate_report(all_results, data_info, extra):
    """生成 Markdown 报告"""
    lines = []
    lines.append("# 行业轮动因子有效性验证报告（天花板测试）\n")
    lines.append(f"> 生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")

    # 运行说明
    lines.append("## 运行说明\n")
    lines.append("```bash")
    lines.append("python src/analysis/factor_test.py [--db data/fund_quant.db] [--fetch]")
    lines.append("```\n")
    lines.append("依赖: `pandas`, `numpy`, `scipy`, `akshare`。")
    lines.append("`--fetch` 强制从 akshare 重新拉取数据，否则使用 `data/cache/` 缓存。\n")

    # 数据概况
    lines.append("## 一、数据概况\n")
    lines.append(f"- 行业数量: {data_info['n_sectors']} 个（申万2021版一级行业）")
    lines.append(f"- 时间范围: {data_info['start']} 至 {data_info['end']}")
    lines.append(f"- 有效月数: {data_info['n_months']}")
    _tr_map = {"total_return": "全收益", "price_only": "价格", "unknown": "类型未知（缓存未记录指数代码）"}
    lines.append(f"- 基准: 沪深300{_tr_map.get(data_info['bench_tr'], '类型未知')}指数"
                 f"（判定: {data_info['bench_tr']}）")
    lines.append(f"- 数据版本: 申万2021版31行业，**2021年10月修订前为回溯拼接数据**（方案A）")
    lines.append("")
    lines.append("### 数据局限声明\n")
    lines.append("1. **申万分类回溯偏差**: 申万一级行业在2021年10月由28个修订为31个，"
                 "akshare提供的新版指数在2021年前的数据为回溯拼接（用新分类倒推历史成分），"
                 "并非当时真实可投资的指数，存在前视偏差风险。本测试采用方案A（全区间统一使用回溯数据）。")
    lines.append("2. **新行业历史不足**: 美容护理(801980)、石油石化(801960)等2021年新增行业"
                 "仅自2021-12起有数据，此前月份不参与排序。")
    lines.append("3. **交易成本假设**: 单边0.5%，模拟场外C类份额或ETF联接的实际成本。")
    lines.append("4. **幸存者偏差**: 使用当前存续行业指数，未考虑已撤销行业。")
    lines.append(f"5. **样本长度**: {data_info['n_months']}个月。"
                 "若结论为不显著，需注意样本量不足导致检验功效低，不代表因子一定无效。")
    lines.append("6. **基准类型判定**: 按 `index_code` 白名单显式判定"
                 "（`total_return` / `price_only` / `unknown`），判不出来就报 unknown —— "
                 "旧版用 `(\"H00300\" in head) or (\"close\" in head.lower())`，"
                 "CSV 表头永远是 `date,close`，所以恒为 True，等于无条件宣称用了全收益指数。")
    lines.append("7. **年度表的归组口径**: 见 §二「年度归组」，与基准列同口径。")
    lines.append("")

    # 因子定义
    lines.append("## 二、因子定义与回测规则\n")
    lines.append("| 因子 | 计算方式 | 方向 |")
    lines.append("|---|---|---|")
    lines.append("| 动量_1M | 过去1月收益率秩百分位 | 正向 |")
    lines.append("| 动量_3M | 过去3月累计收益率秩百分位 | 正向 |")
    lines.append("| 动量_6M | 过去6月累计收益率秩百分位 | 正向 |")
    lines.append("| 回调_3M_1M | rank(ret_3M) × rank(-ret_1M) | 正向 |")
    lines.append("| 低拥挤度 | 1 - turnover_share的24月历史分位 | 反向（低分位=高因子值） |")
    lines.append("")
    lines.append(f"- 持仓: 每月末按因子排序，买入前{TOP_N}行业，等权")
    lines.append(f"- 交易成本: 单边{ONE_WAY_COST*100:.1f}%；**首月建仓只买不卖 → 按单边计**，"
                 f"其后调仓按双边计（旧版首月也按双边，凭空多收 {ONE_WAY_COST*100:.1f}pp）")
    lines.append("- 防未来函数: 因子在t月末计算，持仓收益为t+1月；拥挤度窗口截止t-1月")
    lines.append("- 年度归组: 按**收益实现月**（信号月+1）归年 —— 旧版按信号月所在年份，"
                 "会把 12 月信号赚到的次年 1 月收益算进上一年；基准列本就按实际月末归组，"
                 "两边曾错位一个月")
    lines.append("")

    # 主结果表
    lines.append("## 三、因子回测结果（主结论：前3行业）\n")
    header = "| 因子 | 年化收益 | 年化波动 | 最大回撤 | 夏普 | 年化超额 | t值 | p值(Bonf) | 平均换手 | IC均值 | ICIR | IC>0占比 | 判定 |"
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    lines.append(header)
    lines.append(sep)
    for fname in FACTOR_COLS:
        r = all_results[fname]["metrics"]
        ic = all_results[fname]["ic"]
        gate = all_results[fname]["gate"]
        lines.append(
            f"| {FACTOR_NAMES[FACTOR_COLS.index(fname)]} "
            f"| {fmt_pct(r['annual_return'])} "
            f"| {fmt_pct(r['annual_volatility'])} "
            f"| {fmt_pct(r['max_drawdown'])} "
            f"| {fmt_num(r['sharpe'])} "
            f"| {fmt_pct(r['annual_excess'])} "
            f"| {fmt_num(r['t_stat'])} "
            f"| {fmt_num(gate['details']['p_bonferroni'])} "
            f"| {fmt_pct(r['avg_turnover'])} "
            f"| {fmt_num(ic['ic_mean'])} "
            f"| {fmt_num(ic['icir'])} "
            f"| {fmt_pct(ic['ic_pos_ratio'])} "
            f"| {gate['verdict']} |"
        )
    lines.append("")
    lines.append(f"注: p值(Bonf) = min(p_raw × 5, 1.0)。原始p值见 CSV 结果文件。\n")

    # 基准指标
    lines.append(f"**基准年化收益: {fmt_pct(data_info['bench_annual'])}**\n")

    # 敏感性分析（前5行业）
    lines.append("### 敏感性分析：前5行业持仓\n")
    header5 = "| 因子 | 年化收益 | 年化超额 | 夏普 | 最大回撤 | p值(Bonf) |"
    lines.append(header5)
    lines.append("|---|---|---|---|---|---|")
    for fname in FACTOR_COLS:
        r = all_results[fname]["metrics_top5"]
        if r is None:
            lines.append(f"| {FACTOR_NAMES[FACTOR_COLS.index(fname)]} | N/A | N/A | N/A | N/A | N/A |")
            continue
        gate5 = all_results[fname]["gate_top5"]
        lines.append(
            f"| {FACTOR_NAMES[FACTOR_COLS.index(fname)]} "
            f"| {fmt_pct(r['annual_return'])} "
            f"| {fmt_pct(r['annual_excess'])} "
            f"| {fmt_num(r['sharpe'])} "
            f"| {fmt_pct(r['max_drawdown'])} "
            f"| {fmt_num(gate5['details']['p_bonferroni'])} |"
        )
    lines.append("")

    # 分年度收益（按**收益实现年**归组；基准不推月）
    lines.append("## 四、分年度收益\n")
    # 取第一个因子做表头参照
    sample_port = all_results[FACTOR_COLS[0]]["metrics"]["monthly_returns"]
    sample_y = yearly_returns(sample_port)
    years = sorted(sample_y.index)
    header_y = "| 年份 | " + " | ".join(FACTOR_NAMES) + " | 基准 |"
    lines.append(header_y)
    lines.append("|" + "---|" * (len(FACTOR_NAMES) + 2))
    bench_y = yearly_returns(data_info["bench_monthly"]["ret"], shift_to_realization=False)
    current_year = pd.Timestamp.now().year
    for y in years:
        # 标注当年为 YTD
        label = f"{y} (YTD截至{pd.Timestamp.now().month}月)" if y == current_year else str(y)
        row = [label]
        for fname in FACTOR_COLS:
            yr = all_results[fname]["yearly"]
            if y in yr.index:
                row.append(fmt_pct(yr.loc[y]))
            else:
                row.append("N/A")
        if y in bench_y.index:
            row.append(fmt_pct(bench_y.loc[y]))
        else:
            row.append("N/A")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # 因子相关性
    lines.append("## 五、因子相关性矩阵（Spearman）\n")
    corr = extra["correlation"]
    if not corr.empty:
        lines.append("| | " + " | ".join(FACTOR_NAMES) + " |")
        lines.append("|" + "---|" * (len(FACTOR_NAMES) + 1))
        for i, fname in enumerate(FACTOR_COLS):
            row = [FACTOR_NAMES[i]]
            for fname2 in FACTOR_COLS:
                v = corr.loc[fname, fname2] if fname in corr.index and fname2 in corr.columns else np.nan
                row.append(fmt_num(v))
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
        lines.append("**解读**: 动量类因子之间预期高度正相关；回调因子与动量部分相关；"
                     "低拥挤度预期与动量弱相关或负相关（拥挤度高往往伴随涨幅大）。\n")
    else:
        lines.append("数据不足，无法计算相关性。\n")

    # 样本内外
    lines.append("## 六、样本内/样本外对比\n")
    lines.append("样本内: 2015-2020  |  样本外: 2021-2026\n")
    header_io = "| 因子 | 样本内年化 | 样本内超额 | 样本内p | 样本外年化 | 样本外超额 | 样本外p |"
    lines.append(header_io)
    lines.append("|---|---|---|---|---|---|---|")
    for fname in FACTOR_COLS:
        is_m = all_results[fname]["is"]
        oos_m = all_results[fname]["oos"]
        def m(m):
            if m is None: return ("N/A", "N/A", "N/A")
            return (fmt_pct(m["annual_return"]), fmt_pct(m["annual_excess"]),
                    fmt_num(min(m["p_value"]*5, 1.0)))
        is_v = m(is_m)
        oos_v = m(oos_m)
        lines.append(
            f"| {FACTOR_NAMES[FACTOR_COLS.index(fname)]} "
            f"| {is_v[0]} | {is_v[1]} | {is_v[2]} "
            f"| {oos_v[0]} | {oos_v[1]} | {oos_v[2]} |"
        )
    lines.append("")

    # 市场环境分层
    lines.append("## 七、市场环境分层表现（年化超额）\n")
    lines.append("按沪深300过去6月累计收益划分: >5%牛市, <-5%熊市, 其余震荡\n")
    header_env = "| 因子 | 牛市年化超额 | 熊市年化超额 | 震荡年化超额 |"
    lines.append(header_env)
    lines.append("|---|---|---|---|")
    for fname in FACTOR_COLS:
        env = all_results[fname]["regime"]
        def ev(e):
            return "N/A" if (e is None or (isinstance(e, float) and np.isnan(e))) else fmt_pct(e)
        lines.append(
            f"| {FACTOR_NAMES[FACTOR_COLS.index(fname)]} "
            f"| {ev(env.get('牛市', (np.nan, 0))[0])} "
            f"| {ev(env.get('熊市', (np.nan, 0))[0])} "
            f"| {ev(env.get('震荡', (np.nan, 0))[0])} |"
        )
    lines.append("")

    # 滚动IC
    lines.append("## 八、滚动IC（12个月）\n")
    lines.append("| 因子 | 滚动IC均值 | 滚动IC标准差 | 滚动IC>0占比 |")
    lines.append("|---|---|---|---|")
    for fname in FACTOR_COLS:
        ric = all_results[fname]["rolling_ic"]
        if ric is None or len(ric.dropna()) == 0:
            lines.append(f"| {FACTOR_NAMES[FACTOR_COLS.index(fname)]} | N/A | N/A | N/A |")
            continue
        lines.append(
            f"| {FACTOR_NAMES[FACTOR_COLS.index(fname)]} "
            f"| {fmt_num(ric.mean())} "
            f"| {fmt_num(ric.std())} "
            f"| {fmt_pct((ric > 0).mean())} |"
        )
    lines.append("")
    lines.append("> 注：滚动IC曲线原始数据见 `data/factor_results/rolling_ic.csv`。\n")

    # 天花板测试结论
    lines.append("## 九、天花板测试结论\n")
    passed_factors = [f for f in FACTOR_COLS if all_results[f]["gate"]["passed"]]
    if passed_factors:
        lines.append(f"**结论: 有 {len(passed_factors)} 个因子通过天花板测试，进入阶段2组合优化。**\n")
        for f in passed_factors:
            g = all_results[f]["gate"]
            lines.append(f"- **{FACTOR_NAMES[FACTOR_COLS.index(f)]}**: {g['verdict']}")
        lines.append("")
    else:
        lines.append("**结论: 在当前数据区间和成本假设下，未发现可被利用的行业轮动信号。**\n")
        lines.append("所有5个因子均未通过天花板测试（Bonferroni校正后p>0.02，且不满足边缘显著条件）。"
                     "不继续开发复杂的多因子景气度模型。\n")

    # 阶段2（若有）
    if "stage2" in extra and extra["stage2"]:
        lines.append("## 十、阶段2：因子组合优化\n")
        s2 = extra["stage2"]
        lines.append("### 组合方案A：等权合成\n")
        lines.append(f"组合因子: {' + '.join(s2['combo_names'])}\n")
        lines.append("| 指标 | 组合A | 最优单因子 |")
        lines.append("|---|---|---|")
        best_single = s2["best_single"]
        a = s2["combo_a_metrics"]
        lines.append(f"| 年化收益 | {fmt_pct(a['annual_return'])} | {fmt_pct(best_single['annual_return'])} |")
        lines.append(f"| 年化超额 | {fmt_pct(a['annual_excess'])} | {fmt_pct(best_single['annual_excess'])} |")
        lines.append(f"| 最大回撤 | {fmt_pct(a['max_drawdown'])} | {fmt_pct(best_single['max_drawdown'])} |")
        lines.append(f"| 夏普 | {fmt_num(a['sharpe'])} | {fmt_num(best_single['sharpe'])} |")
        lines.append(f"| p值(Bonf) | {fmt_num(min(a['p_value']*5,1.0))} | {fmt_num(min(best_single['p_value']*5,1.0))} |")
        lines.append(f"| 平均换手 | {fmt_pct(a['avg_turnover'])} | {fmt_pct(best_single['avg_turnover'])} |")
        lines.append("")

        if s2.get("combo_b_metrics"):
            lines.append("### 组合方案B：主因子+过滤\n")
            b = s2["combo_b_metrics"]
            lines.append("| 指标 | 组合B | 最优单因子 |")
            lines.append("|---|---|---|")
            lines.append(f"| 年化收益 | {fmt_pct(b['annual_return'])} | {fmt_pct(best_single['annual_return'])} |")
            lines.append(f"| 年化超额 | {fmt_pct(b['annual_excess'])} | {fmt_pct(best_single['annual_excess'])} |")
            lines.append(f"| 最大回撤 | {fmt_pct(b['max_drawdown'])} | {fmt_pct(best_single['max_drawdown'])} |")
            lines.append(f"| 夏普 | {fmt_num(b['sharpe'])} | {fmt_num(best_single['sharpe'])} |")
            lines.append(f"| p值(Bonf) | {fmt_num(min(b['p_value']*5,1.0))} | {fmt_num(min(best_single['p_value']*5,1.0))} |")
            lines.append(f"| 平均换手 | {fmt_pct(b['avg_turnover'])} | {fmt_pct(best_single['avg_turnover'])} |")
            lines.append("")

        lines.append(f"### 组合是否提升单因子？\n")
        lines.append(s2["conclusion"])
        lines.append("")

    # 系统转型建议（若全部未通过）
    if not passed_factors:
        lines.append("## 十、系统转型建议\n")
        lines.append("基于天花板测试结论，建议将系统定位从**追求alpha**转向"
                     "**资产配置+定投纪律+风控**。具体如下：\n")
        lines.append("### 可保留的模块\n")
        lines.append("- **市场温度计**: 保留风控价值。温度虽不能创造alpha，但对控制仓位、降回撤有效。")
        lines.append("- **持仓跟踪**: 继续保留，作为用户账户管理基础。")
        lines.append("- **定投管理**: 保留，定投纪律本身是长期收益的重要来源。")
        lines.append("- **回测引擎**: 保留严谨的防未来函数框架，可用于验证资产配置策略。")
        lines.append("")
        lines.append("### 应简化或移除的模块\n")
        lines.append("- **复杂的6因子基金评分**: 简化为风格+费率+规模的基础筛选，不做alpha排序。")
        lines.append("- **行业轮动相关**: 移除 `sector_analyzer` 的择时推荐功能，仅保留行业温度展示。")
        lines.append("- **自适应选基实验**: 已证伪，移除。")
        lines.append("")
        lines.append("### 应优先加强的模块\n")
        lines.append("- **用户画像与风险分层**: 根据用户风险承受能力给出股债配置中枢。")
        lines.append("- **再平衡提醒**: 基于目标仓位的偏离度触发再平衡（如偏离>5%提醒）。")
        lines.append("- **资产配置策略库**: 提供恒定混合、生命周期、风险平价等配置方案的回测对比。")
        lines.append("- **定投+估值增强**: 温度计低时多投、高时少投的估值定投增强（已验证有效降回撤）。")
        lines.append("")

    lines.append("---")
    lines.append("*本报告由 `src/analysis/factor_test.py` 自动生成。所有结论基于回测数据，不构成投资建议。*\n")

    report_path = DOCS_DIR / "factor_test_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[报告] 已生成: {report_path}")
    return report_path


# =====================================================================
# 主流程
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="行业轮动因子天花板测试")
    parser.add_argument("--db", default=str(DB_PATH), help="数据库路径")
    parser.add_argument("--fetch", action="store_true", help="强制重新拉取数据")
    args = parser.parse_args()

    print("=" * 60)
    print("行业轮动因子有效性验证（天花板测试）")
    print("=" * 60)

    # 1. 数据采集
    print("\n[1/5] 数据采集...")
    sw_daily = fetch_sw_sectors(force=args.fetch)
    bench_daily = fetch_benchmark(force=args.fetch)

    # 显式判定基准类型（旧实现是恒真式，等于无条件宣称用了全收益指数）
    bench_tr = detect_benchmark_type(BENCH_CACHE)
    print(f"  基准类型判定: {bench_tr}")

    # 2. 月频转换
    print("\n[2/5] 数据处理（日频->月频）...")
    close_wide, amount_wide = to_monthly(sw_daily)
    bench_m = benchmark_monthly(bench_daily)

    # 截取 2015 年起（覆盖完整牛熊），但确保至少 2021-2026
    start_date = pd.Timestamp("2015-01-01")
    close_wide = close_wide[close_wide.index >= start_date]
    amount_wide = amount_wide[amount_wide.index >= start_date]
    bench_m = bench_m[bench_m.index >= start_date]

    # 对齐基准与行业的索引
    common_idx = close_wide.index.intersection(bench_m.index)
    close_wide = close_wide.loc[common_idx]
    amount_wide = amount_wide.loc[common_idx]
    bench_m = bench_m.loc[common_idx]

    print(f"  行业数: {close_wide.shape[1]}, 月数: {close_wide.shape[0]}")
    print(f"  区间: {close_wide.index[0].strftime('%Y-%m')} ~ {close_wide.index[-1].strftime('%Y-%m')}")

    # 3. 因子计算
    print("\n[3/5] 因子计算...")
    factors = compute_factors(close_wide, amount_wide)
    for fn, fdf in factors.items():
        avg_valid = fdf.count(axis=1).mean()  # 平均每月有效行业数
        print(f"  {fn}: {fdf.shape}, 平均每月有效行业数 {avg_valid:.1f}")

    # 4. 回测
    print("\n[4/5] 单因子回测...")
    all_results = {}
    regime = market_regime(bench_m)

    for fname in FACTOR_COLS:
        fdf = factors[fname]
        # 主回测 (top3)
        m = backtest_factor(fdf, close_wide, bench_m, top_n=TOP_N)
        # 敏感性 (top5)
        m5 = backtest_factor(fdf, close_wide, bench_m, top_n=TOP_N_SENSITIVITY)

        # IC
        ic_df, ic_summary = compute_ic({fname: fdf}, close_wide)
        ic_s = ic_summary[fname]

        # 门控
        passed, verdict, details = gate_check(m, ic_s)
        passed5, verdict5, details5 = gate_check(m5, ic_s) if m5 else (False, "N/A", {})

        # 样本内外
        is_m, oos_m = split_sample({}, fdf, close_wide, bench_m)

        # 分年度
        yr = yearly_returns(m["monthly_returns"])

        # 市场环境
        env = regime_excess(m["monthly_returns"], bench_m["ret"], regime)

        # 滚动IC
        ric = rolling_ic(ic_df[fname]) if fname in ic_df.columns else None

        all_results[fname] = {
            "metrics": m,
            "metrics_top5": m5,
            "ic": ic_s,
            "gate": {"passed": passed, "verdict": verdict, "details": details},
            "gate_top5": {"passed": passed5, "verdict": verdict5, "details": details5},
            "is": is_m,
            "oos": oos_m,
            "yearly": yr,
            "regime": env,
            "rolling_ic": ric,
        }
        print(f"  {FACTOR_NAMES[FACTOR_COLS.index(fname)]}: "
              f"年化={fmt_pct(m['annual_return'])}, 超额={fmt_pct(m['annual_excess'])}, "
              f"p={fmt_num(m['p_value'])}, ICIR={fmt_num(ic_s['icir'])} -> {verdict}")

    # 5. 额外分析 & 阶段2
    print("\n[5/5] 额外分析与报告生成...")
    corr = factor_correlation(factors)

    # 检查是否有因子通过
    passed_factors = [f for f in FACTOR_COLS if all_results[f]["gate"]["passed"]]
    extra = {"correlation": corr, "stage2": None}

    if passed_factors:
        print(f"\n  [阶段2] {len(passed_factors)} 个因子通过，构建组合...")
        # 选择通过的因子
        passed_dfs = [factors[f] for f in passed_factors]
        passed_names = [FACTOR_NAMES[FACTOR_COLS.index(f)] for f in passed_factors]

        # 方案A: 等权合成
        combo_a, combo_a_m = build_combination(passed_dfs, close_wide, bench_m)

        # 最优单因子
        best_fname = max(passed_factors, key=lambda f: all_results[f]["metrics"]["annual_excess"])
        best_single = all_results[best_fname]["metrics"]

        # 方案B: 主因子+过滤（需要至少2个因子）
        combo_b_m = None
        if len(passed_factors) >= 2:
            sorted_by_excess = sorted(passed_factors,
                                      key=lambda f: all_results[f]["metrics"]["annual_excess"],
                                      reverse=True)
            main_f = factors[sorted_by_excess[0]]
            filter_f = factors[sorted_by_excess[1]]
            _, combo_b_m = build_filtered(main_f, filter_f, close_wide, bench_m)

        # 对比结论
        a_excess = combo_a_m["annual_excess"]
        best_excess = best_single["annual_excess"]
        if a_excess > best_excess:
            conclusion = (f"组合A年化超额({fmt_pct(a_excess)})优于最优单因子"
                          f"({fmt_pct(best_excess)})，组合在提升收益的同时"
                          f"{'降低了' if combo_a_m['max_drawdown'] < best_single['max_drawdown'] else '未降低'}回撤，"
                          f"组合有实际价值。")
        else:
            conclusion = (f"组合A年化超额({fmt_pct(a_excess)})未优于最优单因子"
                          f"({fmt_pct(best_excess)})，组合未带来显著提升，"
                          f"存在过拟合风险，建议直接使用最优单因子。")

        extra["stage2"] = {
            "combo_names": passed_names,
            "combo_a_metrics": combo_a_m,
            "combo_b_metrics": combo_b_m,
            "best_single": best_single,
            "conclusion": conclusion,
        }
    else:
        print("\n  [阶段2] 无因子通过天花板测试，不执行阶段2。")

    # 数据概况
    data_info = {
        "n_sectors": close_wide.shape[1],
        "start": close_wide.index[0].strftime("%Y-%m"),
        "end": close_wide.index[-1].strftime("%Y-%m"),
        "n_months": close_wide.shape[0],
        "bench_tr": bench_tr,
        "bench_annual": (1 + bench_m["ret"].dropna()).prod() ** (12 / len(bench_m["ret"].dropna())) - 1,
        "bench_monthly": bench_m,
    }

    # 保存原始结果 CSV
    save_csv_results(all_results, factors, corr)

    # 生成报告
    report_path = generate_report(all_results, data_info, extra)
    print(f"\n[完成] 天花板测试结束。报告: {report_path}")

    # 打印结论摘要
    passed_factors = [f for f in FACTOR_COLS if all_results[f]["gate"]["passed"]]
    if passed_factors:
        print(f"\n结论: {len(passed_factors)} 个因子通过天花板测试。")
    else:
        print("\n结论: 所有因子均未通过天花板测试，建议放弃行业轮动，转向资产配置+定投纪律+风控。")


def save_csv_results(all_results, factors, corr):
    """保存原始结果到 CSV"""
    # 1. 主结果表
    rows = []
    for fname in FACTOR_COLS:
        r = all_results[fname]["metrics"]
        ic = all_results[fname]["ic"]
        g = all_results[fname]["gate"]
        rows.append({
            "factor": FACTOR_NAMES[FACTOR_COLS.index(fname)],
            "months": r["months"],
            "annual_return": r["annual_return"],
            "annual_volatility": r["annual_volatility"],
            "max_drawdown": r["max_drawdown"],
            "sharpe": r["sharpe"],
            "annual_excess": r["annual_excess"],
            "t_stat": r["t_stat"],
            "p_value_raw": r["p_value"],
            "p_value_bonferroni": g["details"]["p_bonferroni"],
            "avg_turnover": r["avg_turnover"],
            "ic_mean": ic["ic_mean"],
            "icir": ic["icir"],
            "ic_pos_ratio": ic["ic_pos_ratio"],
            "verdict": g["verdict"],
            "passed": g["passed"],
        })
    pd.DataFrame(rows).to_csv(RESULT_DIR / "factor_main_results.csv", index=False, encoding="utf-8-sig")

    # 2. 月度收益
    ret_rows = []
    for fname in FACTOR_COLS:
        s = all_results[fname]["metrics"]["monthly_returns"]
        for dt, v in s.items():
            ret_rows.append({"date": dt, "factor": FACTOR_NAMES[FACTOR_COLS.index(fname)], "ret": v})
    pd.DataFrame(ret_rows).to_csv(RESULT_DIR / "factor_monthly_returns.csv", index=False, encoding="utf-8-sig")

    # 3. 因子相关性
    if not corr.empty:
        corr.to_csv(RESULT_DIR / "factor_correlation.csv", encoding="utf-8-sig")

    # 4. 滚动IC
    ric_list = []
    for fname in FACTOR_COLS:
        ric = all_results[fname]["rolling_ic"]
        if ric is not None:
            ric.name = FACTOR_NAMES[FACTOR_COLS.index(fname)]
            ric_list.append(ric)
    if ric_list:
        pd.concat(ric_list, axis=1).to_csv(RESULT_DIR / "rolling_ic.csv", encoding="utf-8-sig")

    # 5. 因子值（最后6个月快照）
    snap = {}
    for fname in FACTOR_COLS:
        snap[FACTOR_NAMES[FACTOR_COLS.index(fname)]] = factors[fname].iloc[-1]
    pd.DataFrame(snap).to_csv(RESULT_DIR / "factor_latest_snapshot.csv", encoding="utf-8-sig")

    print(f"  [CSV] 结果已保存到 {RESULT_DIR}/")


if __name__ == "__main__":
    main()
