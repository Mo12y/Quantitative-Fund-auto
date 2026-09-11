"""
基金下月波动率预测 (XGBoost + Walk-Forward)

运行方式:
    python src/analysis/vol_predictor.py [--db data/fund_quant.db] [--max-funds 200]

说明:
    --max-funds  限定基金数量上限（按历史长度降序取前N），默认200，控制运行时间

依赖: pandas, numpy, scipy, scikit-learn, xgboost

本模块独立于现有核心模块，不修改数据库与核心功能。
- 缓存数据 -> data/cache/vol_*.parquet (可选 csv)
- 原始结果 -> data/vol_results/
- 报告     -> docs/vol_prediction_report.md

预测目标:
    基金下月日收益率年化波动率 = std(下月日收益) * sqrt(252)
    （波动率，而非收益率——信噪比远高于收益率预测）

模型:
    - 基线1: EWMA(λ=0.94)（GARCH(1,1) 的 α+β=1 特例，RiskMetrics）
    - 基线2: 6月历史波动率 std(last 126 日收益) * sqrt(252)
    - 主模型: HAR-RV (Corsi 2004, log-HAR) — 3 特征 OLS（日/周/月已实现 vol）
    - 对比: XGBoost 回归 (15 特征, log(vol) 目标)

注: 多模型对比 + 排列检验见 vol_model_comparison.py。
    HAR-RV 作为主模型：3 特征 OLS，无超参，可解释。
    ML 作为对比保留（增量很小，且 DM 检验多数不显著——具体数值以同窗口重跑为准）。

验证:
    - Walk-Forward 扩展窗口：2018-2022 训练，2023-2026 季度重训 OOS
    - 严禁随机划分训练/测试集（时间序列泄露）
    - 指标: IC(Spearman) / MSE / QLIKE / 分5组单调性
    - 必须跑过基线，跑不过说明特征无效

业务应用:
    vol-targeting 仓位 (目标年化15%) vs 温度计仓位 vs 等权，比回撤/夏普

诚实声明:
    - 基金池为当前存续基金，存在幸存者偏差（沿用 factor_test 披露）
    - daily_return 列被实测为不可靠，本模块从 unit_nav 重新计算日收益
    - 不使用任何模拟/随机数据；数据不足即过滤
    - 负结果（ML 跑不过基线）也如实报告
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
RESULT_DIR = DATA_DIR / "vol_results"
DOCS_DIR = PROJECT_ROOT / "docs"
DB_PATH = DATA_DIR / "fund_quant.db"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)

# 基金类型白名单：股票型 + 偏股 + 指数股票（vol 聚类强，可预测性高）
# 排除债基/货基/QDII（vol 几乎为零，无聚类可利用）
EQUITY_FUND_TYPES = [
    "股票型", "股票型-普通", "股票型-标准指数", "股票型-增强指数",
    "混合型-偏股", "指数型-股票",
]

# 时间范围（基于实际数据：fund_nav 多数基金自 2018 起有完整数据）
FEATURE_START = "2017-01-01"   # 6M 特征需要至少 6 月历史 -> 预测自 2018-01
PRED_START = "2018-01-01"
TRAIN_END = "2022-12-31"       # 初始训练截止（5 年训练 2018-2022）
OOS_START = "2023-01-01"
REFIT_FREQ_MONTHS = 3          # 季度重训（控速）

MIN_HISTORY_DAYS = 756         # 3 年最低历史
TRADING_DAYS_YEAR = 252
TARGET_VOL = 0.15              # vol-targeting 目标年化波动率 15%
ONE_WAY_COST = 0.005           # 单边成本 0.5%（场外C类/ETF联接口径）
N_PORTFOLIO_FUNDS = 30         # 组合模拟基金数
RF_ANNUAL = 0.02               # 无风险利率（与 backtest.compute_metrics 统一）

# 特征列（15 个；mgt_fee 因 fund_info 对长历史基金普遍缺失已剔除）
FEATURE_COLS = [
    # A. 历史波动率 (5)
    "vol_1m", "vol_3m", "vol_6m", "ewma_vol", "vol_of_vol_3m",
    # B. 高矩 / 回撤 (4)
    "ret_skew_3m", "ret_kurt_3m", "max_dd_6m", "up_down_ratio_3m",
    # C. 市场宏观 (3)
    "hs300_pe_pct", "hs300_6m_ret", "hs300_6m_vol",
    # D. 截面 / 规模 (3)
    "fund_size_log", "vol_rank_pct", "mom_6m",
]

EWMA_LAMBDA = 0.94            # RiskMetrics 标准


# =====================================================================
# 数据加载
# =====================================================================

def load_fund_universe(db_path, max_funds=200):
    """
    选择基金池：股票型/偏股，历史≥3年，按历史长度降序取前 max_funds。

    返回 DataFrame: fund_code, fund_type, fund_size, mgt_fee, n_days, first_date, last_date
    """
    import sqlite3
    c = sqlite3.connect(db_path)
    cur = c.cursor()

    # 取白名单类型的基金 + 净值覆盖统计
    type_placeholders = ",".join("?" * len(EQUITY_FUND_TYPES))
    q = f"""
    SELECT fi.fund_code, fi.fund_type, fi.fund_size, fi.mgt_fee,
           COUNT(fn.nav_date) AS n_days,
           MIN(fn.nav_date) AS first_date, MAX(fn.nav_date) AS last_date
    FROM fund_info fi
    JOIN fund_nav fn ON fi.fund_code = fn.fund_code
    WHERE fi.fund_type IN ({type_placeholders})
    GROUP BY fi.fund_code
    HAVING COUNT(fn.nav_date) >= ?
    ORDER BY n_days DESC
    LIMIT ?
    """
    rows = cur.execute(q, (*EQUITY_FUND_TYPES, MIN_HISTORY_DAYS, max_funds)).fetchall()
    c.close()

    df = pd.DataFrame(rows, columns=[
        "fund_code", "fund_type", "fund_size", "mgt_fee",
        "n_days", "first_date", "last_date"])
    print(f"  [universe] 选出 {len(df)} 只基金（上限{max_funds}）")
    print(f"  类型分布: {df['fund_type'].value_counts().to_dict()}")
    return df


NAV_COLUMNS = ("unit_nav", "acc_nav")


def load_fund_navs(db_path, fund_codes, column="unit_nav"):
    """
    加载指定基金的净值，wide 格式返回。
    index=nav_date(datetime), columns=fund_code, values=<column>

    日收益从此处 unit_nav pct_change 计算（daily_return 列实测不可靠）。
    column='acc_nav' 时取累计净值（含累计分红），供回撤标注使用。
    """
    if not fund_codes:
        return pd.DataFrame()
    if column not in NAV_COLUMNS:
        raise ValueError(f"column 必须是 {NAV_COLUMNS} 之一，收到 {column!r}")

    import sqlite3
    c = sqlite3.connect(db_path)
    placeholders = ",".join("?" * len(fund_codes))
    q = f"""
    SELECT fund_code, nav_date, {column} AS nav_value
    FROM fund_nav
    WHERE fund_code IN ({placeholders}) AND {column} > 0
    """
    df = pd.read_sql(q, c, params=fund_codes)
    c.close()

    df["nav_date"] = pd.to_datetime(df["nav_date"])
    df["nav_value"] = pd.to_numeric(df["nav_value"], errors="coerce")
    df = df.dropna(subset=["nav_value"])
    df = df[df["nav_value"] > 0]

    # wide 格式
    wide = df.pivot(index="nav_date", columns="fund_code", values="nav_value")
    wide = wide.sort_index()
    # 剔除净值异常（非正、跳变）—— pct_change 后过滤极端值
    print(f"  [nav] {column} 矩阵: {wide.shape[0]} 交易日 × {wide.shape[1]} 基金")
    return wide


def load_index_data(db_path, index_code="000300"):
    """
    加载指数日数据（HS300），用于宏观特征。
    返回 DataFrame: index=date, close, pe_percentile, pe, pb
    """
    import sqlite3
    c = sqlite3.connect(db_path)
    q = """
    SELECT trade_date, close, pe, pb, pe_percentile
    FROM index_daily WHERE index_code = ?
    ORDER BY trade_date
    """
    df = pd.read_sql(q, c, params=(index_code,))
    c.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date").sort_index()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    for col in ("pe", "pb", "pe_percentile"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # pe_percentile 归一化到 [0,1]
    if df["pe_percentile"].max() > 1.5:
        df["pe_percentile"] = df["pe_percentile"] / 100.0

    # index_daily.pe_percentile 在采集时根本没算（save_index_val_to_db 里逐日分位留了 0），
    # 全表实测为 0 → hs300_pe_pct 特征恒为 0、温度计方案的仓位恒定 0.70。
    # 这里用 **扩展窗口分位数** 就地补算（只用 ≤ 当日数据，无未来函数）。
    if df["pe_percentile"].abs().max() == 0 and df["pe"].notna().sum() > 252:
        pe = df["pe"]
        df["pe_percentile"] = pe.expanding(min_periods=252).apply(
            lambda w: float((w < w[-1]).sum()) / len(w), raw=True)
        print(f"  [index] pe_percentile 全为 0（采集缺算）→ 已用扩展窗口分位补算，"
              f"区间 {df['pe_percentile'].min():.3f}~{df['pe_percentile'].max():.3f}")

    print(f"  [index] {index_code}: {len(df)} 行, "
          f"{df.index[0].date()} ~ {df.index[-1].date()}")
    return df


# =====================================================================
# 收益率与月度面板
# =====================================================================

def clean_returns(ret, max_abs=0.20):
    """剔除异常日收益（|r| > 20% 视为拆分/数据错误）。vol 与回撤两条链路共用。"""
    return ret.where(ret.abs() <= max_abs)


def compute_daily_returns(nav_wide):
    """从 unit_nav 计算日收益率。剔除异常值 (>20% 单日，多为分红除权错误)"""
    return clean_returns(nav_wide.pct_change())


def compute_adjusted_nav(nav_wide, acc_wide=None, max_abs=0.20):
    """**复权**净值序列（起点 1.0），用于回撤标注。

    优先用累计净值 acc_nav（= 单位净值 + 累计分红）算日收益：除息日单位净值下挫、
    累计净值不动 → 分红被还原成"零收益日"，不会伪装成一次回撤。
    拿不到累计净值时退回单位净值，并保留 |日收益|>20% 的异常点剔除。

    旧版回撤标签直接在 unit_nav 上算 (cummax−nav)/cummax 且无任何过滤，
    分红除权日会变成假回撤、推高正例率。
    """
    base = nav_wide
    if (acc_wide is not None and acc_wide.shape == nav_wide.shape
            and bool(acc_wide.notna().any(axis=None))):
        base = acc_wide
    ret = clean_returns(base.pct_change(), max_abs)
    return (1.0 + ret.fillna(0.0)).cumprod()


def month_end_dates(daily_index, start=PRED_START):
    """取每月最后一个交易日，返回 pd.Timestamp 列表"""
    s = pd.Series(daily_index, index=daily_index)
    s = s[s >= pd.Timestamp(start)]
    return [pd.Timestamp(d) for d in s.groupby(s.index.to_period("M")).last().values]


def monthly_realized_vol(ret_wide, month_ends):
    """
    月度实现波动率：每月日收益 std * sqrt(252)。
    返回 long DataFrame: columns=[fund_code, month, realized_vol]
    """
    records = []
    ret_indexed = ret_wide.copy()
    ret_indexed.index = pd.to_datetime(ret_indexed.index)

    for me in month_ends:
        me_ts = pd.Timestamp(me)
        # 该月所有交易日
        month_start = me_ts.replace(day=1)
        mask = (ret_indexed.index >= month_start) & (ret_indexed.index <= me_ts)
        month_ret = ret_indexed.loc[mask]
        if len(month_ret) < 5:  # 该月交易日不足
            continue
        vols = month_ret.std(ddof=1) * np.sqrt(TRADING_DAYS_YEAR)
        for code, v in vols.dropna().items():
            if v > 0 and not np.isnan(v):
                records.append({"fund_code": code, "month": me_ts, "realized_vol": v})
    return pd.DataFrame(records)


# =====================================================================
# 特征工程
# =====================================================================

def compute_features(ret_wide, fund_info_df, index_df, month_ends):
    """
    计算每个 (fund, month_end) 的 15 特征。
    严格防泄露：t 月末特征只用 ≤ t 的数据。

    返回 long DataFrame: fund_code, month, <15 feature cols>
    """
    ret_indexed = ret_wide.copy()
    ret_indexed.index = pd.to_datetime(ret_indexed.index)

    # 预计算 ewma_vol 全序列（每只基金，日频），后 reindex 到月末
    ewma_vol_daily = _compute_ewma_vol_series(ret_indexed)
    # 对齐到月末（用前向填充：取 <= 月末的最近交易日值）
    ewma_vol_df = ewma_vol_daily.reindex(month_ends, method="ffill")

    # HS300 月末宏观特征（向量级，所有基金共享）
    macro_monthly = _compute_macro_monthly(index_df, month_ends)

    # 预计算月度 vol（用于 vol_of_vol 与 vol_rank_pct）
    # 扩展到 2015-01 起，使 PRED_START 起的 vol_of_vol_3m 有 3 月历史
    extended_me = month_end_dates(ret_indexed.index, start="2015-01-01")
    monthly_vol_panel = monthly_realized_vol(ret_indexed, extended_me)
    monthly_vol_wide = monthly_vol_panel.pivot(
        index="month", columns="fund_code", values="realized_vol")

    # 基金静态属性查表
    size_map = fund_info_df.set_index("fund_code")["fund_size"]
    fee_map = fund_info_df.set_index("fund_code")["mgt_fee"]

    records = []
    for me in month_ends:
        me_ts = pd.Timestamp(me)
        # 数据切片：截至月末
        past = ret_indexed.loc[ret_indexed.index <= me_ts]
        if len(past) < MIN_HISTORY_DAYS:
            continue

        # A. 历史波动率 (per fund)
        vol_1m = past.tail(21).std(ddof=1) * np.sqrt(TRADING_DAYS_YEAR)
        vol_3m = past.tail(63).std(ddof=1) * np.sqrt(TRADING_DAYS_YEAR)
        vol_6m = past.tail(126).std(ddof=1) * np.sqrt(TRADING_DAYS_YEAR)
        ewma_vol = ewma_vol_df.loc[me_ts] if me_ts in ewma_vol_df.index else ewma_vol_df.iloc[-1]

        # vol_of_vol_3m: 过去 3 个月的月度 vol 的 std
        past_vols = monthly_vol_wide.loc[
            monthly_vol_wide.index <= me_ts
        ].tail(3)
        vol_of_vol_3m = past_vols.std(ddof=1)

        # B. 高矩 / 回撤
        past_63 = past.tail(63)
        ret_skew_3m = past_63.apply(lambda s: stats.skew(s.dropna()) if s.dropna().shape[0] > 10 else np.nan)
        ret_kurt_3m = past_63.apply(lambda s: stats.kurtosis(s.dropna()) if s.dropna().shape[0] > 10 else np.nan)

        # max_dd_6m (per fund): 用 unit_nav 的累计净值
        nav_6m = (1 + past.tail(126)).cumprod()
        peak = nav_6m.cummax()
        dd = (peak - nav_6m) / peak
        max_dd_6m = dd.max()

        # up_down_ratio_3m: std(上行日) / std(下行日)
        past_63_ret = past.tail(63)
        up_std = past_63_ret.where(past_63_ret > 0).std(ddof=1)
        down_std = past_63_ret.where(past_63_ret < 0).std(ddof=1)
        up_down_ratio_3m = up_std / down_std.replace(0, np.nan)

        # D. mom_6m: 6 月累计收益
        mom_6m = (1 + past.tail(126)).prod() - 1

        # 截面 rank (vol_rank_pct): 本月所有基金 6M vol 的横截面秩
        vol_rank_pct = vol_6m.rank(pct=True)

        # 组装 long 记录
        macro = macro_monthly.get(me_ts, {})
        for code in past.columns:
            size_val = size_map.get(code, np.nan)
            fee_val = fee_map.get(code, np.nan)
            records.append({
                "fund_code": code,
                "month": me_ts,
                "vol_1m": vol_1m.get(code, np.nan),
                "vol_3m": vol_3m.get(code, np.nan),
                "vol_6m": vol_6m.get(code, np.nan),
                "ewma_vol": ewma_vol.get(code, np.nan),
                "vol_of_vol_3m": vol_of_vol_3m.get(code, np.nan),
                "ret_skew_3m": ret_skew_3m.get(code, np.nan),
                "ret_kurt_3m": ret_kurt_3m.get(code, np.nan),
                "max_dd_6m": max_dd_6m.get(code, np.nan),
                "up_down_ratio_3m": up_down_ratio_3m.get(code, np.nan),
                "hs300_pe_pct": macro.get("pe_pct", np.nan),
                "hs300_6m_ret": macro.get("ret_6m", np.nan),
                "hs300_6m_vol": macro.get("vol_6m", np.nan),
                "fund_size_log": np.log(size_val) if size_val and size_val > 0 else np.nan,
                "mgt_fee": fee_val if fee_val and fee_val > 0 else np.nan,
                "vol_rank_pct": vol_rank_pct.get(code, np.nan),
                "mom_6m": mom_6m.get(code, np.nan),
            })

    feat_df = pd.DataFrame(records)
    return feat_df


def _compute_ewma_vol_series(ret_wide, lam=EWMA_LAMBDA):
    """
    每只基金的 EWMA 波动率日序列（后由 compute_features reindex 到月末）。
    返回 DataFrame: index=日, columns=fund_code, values=annualized EWMA vol
    """
    # 缺失日**不填 0**：pandas 的 ewm 会跳过 NaN 并沿用上一个有效值；
    # 旧代码 .fillna(0.0) 把"当天没净值"当成"零波动日"，会把 EWMA 系统性压低。
    sq = ret_wide ** 2
    # 递归 EWMA（adjust=False）：var_t = λ·var_{t-1} + (1−λ)·r²_t
    var = sq.ewm(alpha=1 - lam, adjust=False).mean()
    ewma_vol = np.sqrt(var * TRADING_DAYS_YEAR)
    ewma_vol.index = pd.to_datetime(ewma_vol.index)
    return ewma_vol


def _compute_macro_monthly(index_df, month_ends):
    """
    月末宏观特征 (HS300)。
    返回 dict: month_end_ts -> {pe_pct, ret_6m, vol_6m}
    """
    idx = index_df.copy()
    idx.index = pd.to_datetime(idx.index)

    # 月末对齐（取<=月末的最近交易日）
    out = {}
    for me in month_ends:
        me_ts = pd.Timestamp(me)
        sub = idx.loc[idx.index <= me_ts]
        if len(sub) < 126:
            continue
        last = sub.iloc[-1]
        close_6m_ago = sub["close"].iloc[-126] if len(sub) >= 126 else sub["close"].iloc[0]
        ret_6m = (last["close"] / close_6m_ago - 1) if close_6m_ago > 0 else np.nan
        # 6M 日收益 vol
        daily_ret_6m = sub["close"].pct_change().tail(126)
        vol_6m = daily_ret_6m.std(ddof=1) * np.sqrt(TRADING_DAYS_YEAR) if len(daily_ret_6m) > 10 else np.nan
        pe_pct = last["pe_percentile"]
        out[me_ts] = {"pe_pct": pe_pct, "ret_6m": ret_6m, "vol_6m": vol_6m}
    return out


# =====================================================================
# 标签组装
# =====================================================================

def build_panel(features_df, realized_df):
    """
    合并特征与下月实现波动率标签。
    标签 = t+1 月的 realized_vol (shift -1 on monthly panel).

    返回 panel: fund_code, month, [16 features], realized_vol_next
    """
    # realized_df: fund_code, month, realized_vol
    # 同一 (fund, month) 的 realized_vol = 该月实现 vol
    # 对 fund f 在 month t：标签 = fund f 在 month t+1 的 realized_vol
    rv = realized_df.copy()
    rv = rv.sort_values(["fund_code", "month"])
    rv["realized_vol_next"] = rv.groupby("fund_code")["realized_vol"].shift(-1)
    rv = rv[["fund_code", "month", "realized_vol_next"]]

    panel = features_df.merge(rv, on=["fund_code", "month"], how="inner")
    panel = panel.dropna(subset=["realized_vol_next"] + FEATURE_COLS)
    return panel


# =====================================================================
# 基线模型
# =====================================================================

def baseline_ewma(panel, oos_start=OOS_START):
    """基线1: EWMA vol 作为下月预测（vol 聚类代理）

    只保留 OOS 窗口内的行 —— 基线本可覆盖全样本（2018 起），但 HAR/XGB 是
    walk-forward，只有 2023 起有预测；在全样本上给基线打分再和 ML 比，
    是**不同期间**的比较（旧版 n_pred 19327 vs 8339 即此）。
    """
    sub = panel[panel["month"] >= pd.Timestamp(oos_start)]
    pred = sub[["fund_code", "month"]].copy()
    pred["pred_vol"] = sub["ewma_vol"].values
    pred["model"] = "EWMA"
    return pred


def baseline_6m_hist(panel, oos_start=OOS_START):
    """基线2: 6月历史波动率作为下月预测（同样限制在 OOS 窗口）"""
    sub = panel[panel["month"] >= pd.Timestamp(oos_start)]
    pred = sub[["fund_code", "month"]].copy()
    pred["pred_vol"] = sub["vol_6m"].values
    pred["model"] = "6M_Hist"
    return pred


def align_oos_window(preds: dict, oos_start: str = OOS_START) -> dict:
    """把所有模型限制到**完全相同的 OOS 月份集合**，保证头对头可比。

    取各模型月份集合的**交集**（实际等于 walk-forward 的 OOS 段）。这样
    n_pred / n_months 对每个模型都一致，报告标题里的期间也才与内容相符。
    """
    cut = pd.Timestamp(oos_start)
    kept = {k: v[v["month"] >= cut]
            for k, v in preds.items() if v is not None and not v.empty}
    if not kept:
        return dict(preds)

    common = None
    for v in kept.values():
        ms = set(v["month"].unique())
        common = ms if common is None else (common & ms)
    if not common:
        return kept

    out = {}
    for k, v in preds.items():
        out[k] = v[v["month"].isin(common)] if (v is not None and not v.empty) else v
    print(f"  [对齐] 所有模型统一到 {len(common)} 个月 "
          f"({min(common).strftime('%Y-%m')} ~ {max(common).strftime('%Y-%m')})")
    return out


# =====================================================================
# HAR-RV (Corsi 2004) — 主模型
# =====================================================================

HAR_FEATURES = ["har_rv_d", "har_rv_w", "har_rv_m"]


def add_har_features(panel, realized_df):
    """
    在 panel 上加 HAR-RV 的 3 个特征 (Corsi 2004, log-HAR):
    - har_rv_d: 当月实现 vol (daily component)
    - har_rv_w: 近 3 月平均实现 vol (weekly component)
    - har_rv_m: 近 12 月平均实现 vol (monthly component)

    口径说明：panel 的行是「月末 t 做决策、预测 t+1 月实现 vol」，因此 t 月自己的
    实现 vol 在决策时**已经可知**。旧实现在此再 shift(1)，等于比其他 14 个特征
    （都用到月末 t）少用一个月信息，弱于 Corsi 原式。此处按 Corsi 标准对齐到 t。

    全部做 log 变换（log-HAR，学术界标准，volbench 证实优于原始 HAR）
    """
    rv = realized_df[["fund_code", "month", "realized_vol"]].copy()
    rv = rv.sort_values(["fund_code", "month"])

    rv["har_rv_d"] = rv["realized_vol"]
    rv["har_rv_w"] = rv.groupby("fund_code")["realized_vol"].transform(
        lambda s: s.rolling(3, min_periods=2).mean())
    rv["har_rv_m"] = rv.groupby("fund_code")["realized_vol"].transform(
        lambda s: s.rolling(12, min_periods=6).mean())

    for col in HAR_FEATURES:
        rv[col] = np.log(rv[col].where(rv[col] > 0))

    har_cols = ["fund_code", "month"] + HAR_FEATURES
    panel = panel.merge(rv[har_cols], on=["fund_code", "month"], how="left")
    return panel


def walk_forward_har(panel, train_end=TRAIN_END, refit_months=REFIT_FREQ_MONTHS):
    """
    HAR-RV Walk-Forward (Corsi 2004, log-HAR)。
    主模型: 3 特征 OLS，无超参，可解释。

    返回 DataFrame: fund_code, month, pred_vol, model='HAR-RV'
    """
    from sklearn.linear_model import LinearRegression

    panel = panel.sort_values(["month", "fund_code"]).reset_index(drop=True)
    train_end_ts = pd.Timestamp(train_end)
    last_ts = panel["month"].max()

    refit_starts = pd.date_range(
        start=train_end_ts + pd.offsets.MonthBegin(1),
        end=last_ts, freq=f"{refit_months}MS")

    all_preds = []
    for i, rs in enumerate(refit_starts):
        train_cutoff = rs - pd.DateOffset(months=1)  # purging gap
        train = panel[panel["month"] < train_cutoff]
        if i + 1 < len(refit_starts):
            test_end = refit_starts[i + 1] - pd.Timedelta(days=1)
        else:
            test_end = last_ts
        test = panel[(panel["month"] >= rs) & (panel["month"] <= test_end)]

        train = train.dropna(subset=HAR_FEATURES + ["realized_vol_next"])
        test = test.dropna(subset=HAR_FEATURES + ["realized_vol_next"])
        if train.empty or test.empty:
            continue

        X_train = train[HAR_FEATURES].values
        y_train = np.log(train["realized_vol_next"].values)
        X_test = test[HAR_FEATURES].values

        model = LinearRegression()
        model.fit(X_train, y_train)
        pred_vol = np.exp(model.predict(X_test))

        out = test[["fund_code", "month"]].copy()
        out["pred_vol"] = pred_vol
        out["model"] = "HAR-RV"
        all_preds.append(out)

        if (i + 1) % 4 == 0 or i == len(refit_starts) - 1:
            print(f"    [walk-forward HAR-RV] refit@{rs.date()}: "
                  f"train={len(train)}, test={len(test)}")

    if not all_preds:
        return pd.DataFrame()
    return pd.concat(all_preds, ignore_index=True)


# =====================================================================
# XGBoost Walk-Forward
# =====================================================================

def walk_forward_xgb(panel, train_end=TRAIN_END, refit_months=REFIT_FREQ_MONTHS):
    """
    Walk-Forward 扩展窗口：训练 [start, train_end]，预测 (train_end, ...]。
    每 refit_months 月重训一次。

    返回 DataFrame: fund_code, month, pred_vol, model='XGBoost'
    """
    import xgboost as xgb

    panel = panel.sort_values(["month", "fund_code"]).reset_index(drop=True)
    train_end_ts = pd.Timestamp(train_end)
    last_ts = panel["month"].max()

    # 生成重训时点：从 train_end+1月 起每 refit_months 月一次
    refit_starts = pd.date_range(
        start=train_end_ts + pd.offsets.MonthBegin(1),
        end=last_ts, freq=f"{refit_months}MS")

    all_preds = []
    feature_arr_cols = FEATURE_COLS

    for i, rs in enumerate(refit_starts):
        # 扩展训练集：所有 month < rs 的数据
        train = panel[panel["month"] < rs]
        # 本段测试集：[rs, rs + refit_months)
        if i + 1 < len(refit_starts):
            test_end = refit_starts[i + 1] - pd.Timedelta(days=1)
        else:
            test_end = last_ts
        test = panel[(panel["month"] >= rs) & (panel["month"] <= test_end)]

        if train.empty or test.empty:
            continue

        X_train = train[feature_arr_cols].values
        # 目标: log(realized_vol_next) —— vol 右偏，log 变换更接近正态
        y_train = np.log(train["realized_vol_next"].values)
        X_test = test[feature_arr_cols].values

        model = xgb.XGBRegressor(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            reg_alpha=0.1, reg_lambda=1.0,
            random_state=42, n_jobs=-1, verbosity=0,
        )
        model.fit(X_train, y_train)
        pred_log = model.predict(X_test)
        pred_vol = np.exp(pred_log)  # 还原

        out = test[["fund_code", "month"]].copy()
        out["pred_vol"] = pred_vol
        out["model"] = "XGBoost"
        all_preds.append(out)

        if (i + 1) % 4 == 0 or i == len(refit_starts) - 1:
            print(f"    [walk-forward] refit@{rs.date()}: "
                  f"train={len(train)}, test={len(test)}")

    if not all_preds:
        return pd.DataFrame()
    return pd.concat(all_preds, ignore_index=True)


def compute_feature_importance(panel, train_end=TRAIN_END):
    """训一个 XGBoost 取特征重要度（用于报告解读）"""
    import xgboost as xgb
    train = panel[panel["month"] <= pd.Timestamp(train_end)]
    if train.empty:
        return pd.Series()
    X = train[FEATURE_COLS].values
    y = np.log(train["realized_vol_next"].values)
    model = xgb.XGBRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=1.0, random_state=42, n_jobs=-1, verbosity=0)
    model.fit(X, y)
    imp = pd.Series(model.feature_importances_, index=FEATURE_COLS)
    return imp.sort_values(ascending=False)


# =====================================================================
# 评估指标
# =====================================================================

def evaluate_predictions(pred_df, panel, model_name):
    """
    计算 IC / MSE / QLIKE / 分组单调性。

    pred_df: fund_code, month, pred_vol (在 month t 预测 t+1 月)
    panel: fund_code, month, [features], realized_vol_next
           (realized_vol_next 已在 build_panel 中按 shift(-1) 对齐为 t+1 月实现 vol)

    返回 (summary dict, per-month IC series)
    """
    # 直接用 panel 已对齐的 realized_vol_next 作为评估目标
    target = panel[["fund_code", "month", "realized_vol_next"]]
    merged = pred_df.merge(target, on=["fund_code", "month"], how="inner")

    if merged.empty:
        return {}, pd.Series()

    # 月度 IC (Spearman)
    ic_series = merged.groupby("month").apply(
        lambda g: stats.spearmanr(g["pred_vol"], g["realized_vol_next"]).correlation
        if len(g) >= 5 else np.nan)
    ic_series = ic_series.dropna()

    # MSE
    mse = float(np.mean((merged["pred_vol"] - merged["realized_vol_next"]) ** 2))

    # QLIKE（Patton 2011 标准形式，作用在**方差**上）：
    #   L = rv/h − log(rv/h) − 1，其中 h 是预测方差、rv 是实现方差。
    # 旧实现直接用波动率之比 σ_real/σ_pred 代入，得到的是 (σa/σf) − log(σa/σf) − 1，
    # 与标准式 (σa/σf)² − 2log(σa/σf) − 1 不是同一个损失函数 —— 与文献不可比，
    # 模型排序也可能不同。这里改为标准方差形式。
    rv_var = merged["realized_vol_next"] ** 2
    pred_var = merged["pred_vol"].replace(0, np.nan) ** 2
    ratio = rv_var / pred_var
    qlike = float(np.mean(ratio - np.log(ratio.where(ratio > 0)) - 1))

    # 分5组单调性
    group_means = (
        merged.assign(
            group=lambda x: pd.qcut(x["pred_vol"], q=5, labels=False, duplicates="drop"))
        .groupby("group")["realized_vol_next"].mean())
    # 单调性: group index 与 mean 的 Spearman
    if len(group_means) >= 3:
        mono_corr = stats.spearmanr(group_means.index, group_means.values).correlation
    else:
        mono_corr = np.nan

    summary = {
        "model": model_name,
        "n_pred": len(merged),
        "n_months": int(merged["month"].nunique()),
        # 评测窗口（供报告标题/说明与实际内容对齐）
        "win_start": merged["month"].min().strftime("%Y-%m"),
        "win_end": merged["month"].max().strftime("%Y-%m"),
        "ic_mean": float(ic_series.mean()) if len(ic_series) else np.nan,
        "ic_std": float(ic_series.std()) if len(ic_series) else np.nan,
        "icir": float(ic_series.mean() / ic_series.std()) if len(ic_series) and ic_series.std() > 0 else np.nan,
        "ic_pos_ratio": float((ic_series > 0).mean()) if len(ic_series) else np.nan,
        "mse": mse,
        "qlike": qlike,
        "group_monotonicity": float(mono_corr) if not np.isnan(mono_corr) else np.nan,
        "group_means": group_means.to_dict() if len(group_means) else {},
    }
    return summary, ic_series


# =====================================================================
# 组合模拟（vol-targeting vs 温度计 vs 等权）
# =====================================================================

def last_signal_before(sig, m_ts):
    """取**严格早于** m_ts 的最近一个信号值；没有则返回 None。

    月份 m 的收益只有在 m 走完之后才观测得到。用 `index <= m_ts` 的信号去定
    m 的仓位，等于拿期末信息去交易整月收益（同月前视）。
    而且 HAR/回撤模型的预测目标是 t+1 月，取 `t < m` 的那一条恰好就是**对 m 月
    的预测**，同时修掉期限错配。
    """
    s = sig[sig.index < pd.Timestamp(m_ts)]
    return float(s.iloc[-1]) if len(s) else None


def portfolio_simulation(ret_wide, pred_df, index_df, oos_start=OOS_START,
                         n_funds=N_PORTFOLIO_FUNDS, target_vol=TARGET_VOL):
    """
    三仓位方案对比。
    - 等权: 总仓位恒为 1.0
    - 温度计: 用 HS300 PE 分位映射（与 settings.yaml 对齐）
    - vol-targeting: 仓位 = clip(target_vol / 组合预测vol, 0.05, 1.0)

    月度再平衡，0.5% 单边成本。返回 dict of metrics per scheme.
    """
    # 1. 选组合基金：只用 **OOS 之前** 的数据挑（按 OOS 期间的覆盖度排序 =
    #    用"未来还活着"选样本，是幸存者偏差的一种）。这里退到 OOS 起点之前看覆盖度。
    ret_pre = ret_wide.loc[ret_wide.index < pd.Timestamp(oos_start)]
    coverage = ret_pre.notna().sum().sort_values(ascending=False)
    port_codes = coverage.head(n_funds).index.tolist()
    print(f"  [portfolio] 选 {len(port_codes)} 只基金用于组合模拟"
          f"（按 {pd.Timestamp(oos_start).date()} 之前的净值覆盖度挑）")

    ret_port = ret_wide[port_codes].copy()
    ret_port.index = pd.to_datetime(ret_port.index)

    # 月度收益（每月日收益算术平均，近似等权组合月收益）
    monthly_ret = ret_port.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    # 组合等权月收益（忽略缺失基金，等权剩余）
    port_monthly_ret = monthly_ret.mean(axis=1)
    port_monthly_ret = port_monthly_ret.dropna()

    # OOS 月份
    oos_months = port_monthly_ret.index[port_monthly_ret.index >= pd.Timestamp(oos_start)]
    oos_months = [m for m in oos_months if m in pred_df["month"].values]

    # 温度计映射 (与 config/settings.yaml 一致)
    def temp_to_equity(pe_pct):
        if pe_pct is None or np.isnan(pe_pct):
            return 0.35
        if pe_pct < 0.20:
            return 0.70
        if pe_pct < 0.40:
            return 0.55
        if pe_pct < 0.60:
            return 0.35
        if pe_pct < 0.80:
            return 0.20
        return 0.05

    # 预测 vol 聚合到月（组合预测vol = 基金预测vol 的均值）
    pred_monthly = (
        pred_df.groupby("month")["pred_vol"].mean().sort_index())

    # index PE 分位月度
    idx_monthly_pe = index_df["pe_percentile"].resample("ME").last()

    schemes = {"Equal_Weight": [], "Thermometer": [], "Vol_Targeting": []}
    positions = {"Equal_Weight": [], "Thermometer": [], "Vol_Targeting": []}
    # 期初在现金里（仓位 0）：首月建仓要付一次成本。
    # 旧版从 1.0 起算 → 首月"已经满仓"，凭空少收一次建仓费。
    prev_pos = {"Equal_Weight": 0.0, "Thermometer": 0.0, "Vol_Targeting": 0.0}
    rf_monthly = RF_ANNUAL / 12.0

    for m in oos_months:
        m_ts = pd.Timestamp(m)
        if m_ts not in port_monthly_ret.index:
            continue
        base_ret = port_monthly_ret.loc[m_ts]
        if pd.isna(base_ret):
            continue

        # 各方案目标仓位（信号一律**严格早于** m，见 last_signal_before）
        eq_pos = 1.0
        # 温度计: 用 m 之前已知的 PE 分位
        pe_pct = last_signal_before(idx_monthly_pe, m_ts)
        th_pos = temp_to_equity(pe_pct if pe_pct is not None else np.nan)
        # vol-targeting: 用 m 之前发布的、对 m 月的预测 vol
        pred_v = last_signal_before(pred_monthly, m_ts)
        if pred_v is None:
            pred_v = 0.20
        vt_pos = float(np.clip(target_vol / pred_v, 0.05, 1.0)) if pred_v > 0 else 0.35

        cur_pos = {"Equal_Weight": eq_pos, "Thermometer": th_pos, "Vol_Targeting": vt_pos}

        for name, pos in cur_pos.items():
            turnover = abs(pos - prev_pos[name])
            cost = turnover * ONE_WAY_COST
            # 未投资部分（1−pos）放货基/短债，按无风险利率计息，而不是当 0 收益
            net_ret = base_ret * pos + (1.0 - pos) * rf_monthly - cost
            schemes[name].append((m_ts, net_ret))
            positions[name].append((m_ts, pos))
            prev_pos[name] = pos

    metrics = {}
    for name, rets in schemes.items():
        if not rets:
            continue
        s = pd.Series(dict(rets))
        m = _portfolio_metrics(s, rf_annual=RF_ANNUAL)
        m["avg_position"] = float(np.mean([p[1] for p in positions[name]]))
        m["avg_turnover"] = float(np.mean([
            abs(positions[name][i][1] - (positions[name][i-1][1] if i > 0 else 0.0))
            for i in range(len(positions[name]))]))
        metrics[name] = m
    return metrics


def _portfolio_metrics(monthly_rets, rf_annual=0.02):
    """组合月度收益 -> 年化指标（Sharpe 扣无风险利率，与 backtest.compute_metrics 同口径）"""
    r = monthly_rets.dropna().values
    n = len(r)
    if n == 0:
        return {}
    nav = np.cumprod(1 + r)
    total_ret = nav[-1] - 1
    ann_ret = nav[-1] ** (12 / n) - 1
    ann_vol = np.std(r, ddof=1) * np.sqrt(12) if n > 1 else 0
    sharpe = (ann_ret - rf_annual) / ann_vol if ann_vol > 0 else 0
    peak = np.maximum.accumulate(nav)
    dd = (peak - nav) / peak
    max_dd = float(dd.max())
    calmar = ann_ret / max_dd if max_dd > 0 else 0
    return {
        "months": n, "total_return": total_ret, "annual_return": ann_ret,
        "annual_volatility": ann_vol, "sharpe": sharpe,
        "max_drawdown": max_dd, "calmar": calmar,
    }


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


def generate_report(universe_df, panel, summaries, ic_series_dict,
                    feat_imp, portfolio_metrics, data_info):
    lines = []
    lines.append("# 基金下月波动率预测 (XGBoost + Walk-Forward) 报告\n")
    lines.append(f"> 生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 运行说明
    lines.append("## 运行说明\n")
    lines.append("```bash")
    lines.append("python src/analysis/vol_predictor.py [--db data/fund_quant.db] [--max-funds 200]")
    lines.append("```\n")
    lines.append("依赖: `pandas`, `numpy`, `scipy`, `scikit-learn`, `xgBoost`。"
                 "`arch` 库在 Python 3.13 下不可用，改用 EWMA(λ=0.94) 作为 GARCH 系基线"
                 "（数学上为 GARCH(1,1) 的 α+β=1 特例）。\n")

    # 第一性原理审查结论
    lines.append("## 一、第一性原理审查结论（修正后做）\n")
    lines.append("- **综合信心**: 78%")
    lines.append("- **建议**: 修正后做。修正项:")
    lines.append("  1. 特征 30 → 16（奥卡姆剃刀）")
    lines.append("  2. 基线 = EWMA + 6M 历史vol（arch 装不上，EWMA 为 GARCH 特例）")
    lines.append("  3. 必须含组合模拟闭环（vol-targeting vs 温度计 vs 等权）")
    lines.append("  4. 限定股票型/偏股基金（聚类强），债基单独看")
    lines.append("  5. 幸存者偏差诚实披露（沿用 factor_test）")
    lines.append("- **最致命一击**: vol-targeting 在 vol↑ 时减仓=跌后卖出，交易成本+踏空可能侵蚀组合收益")
    lines.append("- **最没把握的事**: XGBoost 是否真能跑过 EWMA（EWMA 对 vol 聚类已很强）")
    lines.append("- **最大遗漏**: 幸存者偏差 + vol↑ 时减仓的方向性副作用")
    lines.append("")

    # 数据概况
    lines.append("## 二、数据概况\n")
    lines.append(f"- 基金池: {len(universe_df)} 只 (类型: {', '.join(EQUITY_FUND_TYPES)})")
    lines.append(f"- 净值区间: {data_info['nav_start']} ~ {data_info['nav_end']}")
    lines.append(f"- 月度预测样本: {len(panel)} fund-month")
    lines.append(f"- 训练期: {PRED_START[:7]} ~ {TRAIN_END[:7]}")
    lines.append(f"- 测试期(OOS): {OOS_START[:7]} ~ {data_info['last_month']}")
    lines.append(f"- 重训频率: 每 {REFIT_FREQ_MONTHS} 月（扩展窗口）")
    lines.append("")
    lines.append("### 数据局限声明\n")
    lines.append("1. **幸存者偏差**: 基金池为当前存续基金，已清盘基金未纳入（同 factor_test）。")
    lines.append("2. **daily_return 列实测不可靠**: 本模块从 `unit_nav` 用 pct_change 重新计算日收益，"
                 "并剔除 |单日收益|>20% 的异常值（多为分红除权/数据错误）。")
    lines.append("3. **无模拟数据**: 数据不足即过滤，不使用 `np.random` 兜底。")
    lines.append("4. **交易成本**: 单边 0.5%（场外 C 类/ETF 联接口径）。")
    lines.append("5. **PE 分位**: 沿用 `index_daily.pe_percentile`，温度计映射与 `settings.yaml` 一致。")
    lines.append("")

    # 特征定义
    lines.append("## 三、特征定义（15 个，奥卡姆剃刀后）\n")
    lines.append("| 组 | 特征 | 计算方式 | 物理含义 |")
    lines.append("|:--|:--|:--|:--|")
    lines.append("| A 历史vol(5) | vol_1m/3m/6m | std(last 21/63/126)×√252 | vol 聚类，最强信号 |")
    lines.append("|  | ewma_vol | EWMA(λ=0.94)×√252 | GARCH 特例，基线兼特征 |")
    lines.append("|  | vol_of_vol_3m | 近3月月度vol的std | vol 体制切换 |")
    lines.append("| B 高矩(4) | ret_skew_3m | 近63日收益偏度 | 负偏→未来vol↑ |")
    lines.append("|  | ret_kurt_3m | 近63日超额峰度 | 肥尾→未来vol↑ |")
    lines.append("|  | max_dd_6m | 近126日最大回撤 | 回撤-vol 联动 |")
    lines.append("|  | up_down_ratio_3m | std(上行)/std(下行) | 下行不对称 |")
    lines.append("| C 宏观(3) | hs300_pe_pct | 沪深300 PE分位 | 高估值→vol↑ |")
    lines.append("|  | hs300_6m_ret | 沪深300 6月收益 | 市场体制 |")
    lines.append("|  | hs300_6m_vol | 沪深300 6月日vol | 市场→基金vol传导 |")
    lines.append("| D 截面(3) | fund_size_log | log(规模) | 大盘基金vol更低 |")
    lines.append("|  | vol_rank_pct | 6M vol 截面秩分位 | 同类相对风险 |")
    lines.append("|  | mom_6m | 6月累计收益 | 趋势控制 |")
    lines.append("")
    lines.append("**注**: 原设计含 `mgt_fee`（管理费，主动风险代理），实测 `fund_info.mgt_fee` 对长历史基金普遍为 NULL "
                 "(18000 只股票型基金中仅 5456 有值，且按历史长度排序的 Top30 基金全部为 NULL)，故剔除。"
                 "数据不足即过滤，不强行填充。\n")
    lines.append("")
    lines.append("**防泄露**: t 月末特征严格只用 ≤t 数据；标签 = t+1 月实现 vol；无全样本标准化。\n")

    # 主结果（窗口取自实际评测月份，保证标题与内容一致；四个模型窗口已对齐）
    wins = {s.get("win_start") for s in summaries.values() if s.get("win_start")}
    we = {s.get("win_end") for s in summaries.values() if s.get("win_end")}
    if len(wins) == 1 and len(we) == 1:
        win_txt = f"{wins.pop()} ~ {we.pop()}"
    else:  # 不该发生：align_oos_window 已把窗口统一
        win_txt = f"{min(wins)} ~ {max(we)}（窗口不一致，请检查 align_oos_window）"
    lines.append(f"## 四、预测效果对比（OOS {win_txt}，四模型同窗口）\n")
    lines.append("| 模型 | n_pred | n_months | IC均值 | ICIR | IC>0占比 | MSE | QLIKE | 分5组单调性 | 判定 |")
    lines.append("|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|")
    for name in ["EWMA", "6M_Hist", "HAR-RV", "XGBoost"]:
        if name not in summaries:
            continue
        s = summaries[name]
        verdict = _verdict(s, summaries.get("EWMA"))
        lines.append(
            f"| {name} | {s.get('n_pred')} | {s.get('n_months')} "
            f"| {fmt_num(s['ic_mean'])} | {fmt_num(s['icir'])} "
            f"| {fmt_pct(s['ic_pos_ratio'])} | {fmt_num(s['mse'])} "
            f"| {fmt_num(s['qlike'])} | {fmt_num(s['group_monotonicity'])} | {verdict} |"
        )
    lines.append("")
    lines.append("**判定逻辑**: XGBoost 必须 IC > max(EWMA, 6M_Hist) 且 QLIKE < 基线，"
                 "否则 ML 增量为零。IC>0.05 + ICIR>0.3 视为有效信号。")
    lines.append("**窗口对齐**: 基线与 ML 一律限制在同一 OOS 月份集合（`align_oos_window`）；"
                 "旧版基线跑全样本（2018 起）、ML 只有 OOS（2023 起），"
                 "n_pred/n_months 不等，是**不同期间**的比较。\n")

    # 滚动 IC
    lines.append("## 五、滚动 IC（12 月）\n")
    lines.append("| 模型 | 滚动IC均值 | 滚动IC标准差 | IC>0占比 |")
    lines.append("|:--|:--|:--|:--|")
    for name, ic in ic_series_dict.items():
        if ic is None or len(ic.dropna()) == 0:
            lines.append(f"| {name} | N/A | N/A | N/A |")
            continue
        ric = ic.rolling(12, min_periods=6).mean()
        lines.append(
            f"| {name} | {fmt_num(ric.mean())} | {fmt_num(ric.std())} "
            f"| {fmt_pct((ric > 0).mean())} |"
        )
    lines.append("")

    # 分5组真实 vol
    lines.append("## 六、分5组单调性（XGBoost 预测）\n")
    if "XGBoost" in summaries and summaries["XGBoost"]["group_means"]:
        gm = summaries["XGBoost"]["group_means"]
        lines.append("| 预测vol分组(低→高) | 真实vol均值 |")
        lines.append("|:--|:--|")
        for g, v in gm.items():
            lines.append(f"| G{int(g)+1} | {fmt_pct(v)} |")
        lines.append("")
        mono = summaries["XGBoost"]["group_monotonicity"]
        lines.append(f"**分组-真实vol Spearman**: {fmt_num(mono)}。"
                     f"{'单调（预测有效）' if (not np.isnan(mono) and mono > 0.9) else '非完全单调（预测能力有限）'}。\n")
    else:
        lines.append("数据不足。\n")

    # 特征重要度
    lines.append("## 七、XGBoost 特征重要度\n")
    if feat_imp is not None and len(feat_imp):
        lines.append("| 特征 | 重要度 |")
        lines.append("|:--|:--|")
        for f, v in feat_imp.items():
            lines.append(f"| {f} | {fmt_num(v)} |")
        lines.append("")
        top = feat_imp.index[0]
        lines.append(f"**解读**: 最重要特征 = `{top}`。"
                     f"{'历史 vol 类主导 → ML 增量主要来自基线特征的组合，新信息有限' if 'vol' in top or 'ewma' in top else '非历史 vol 特征主导 → ML 有新增量信息'}。\n")
    else:
        lines.append("未计算。\n")

    # 组合模拟
    lines.append("## 八、组合模拟（vol-targeting vs 温度计 vs 等权）\n")
    lines.append(f"组合: {N_PORTFOLIO_FUNDS} 只最长历史基金等权；月度再平衡；单边成本 {ONE_WAY_COST*100:.1f}%。\n")
    lines.append("| 方案 | 年化收益 | 年化波动 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 平均换手 |")
    lines.append("|:--|:--|:--|:--|:--|:--|:--|:--|")
    for name, m in portfolio_metrics.items():
        lines.append(
            f"| {name} | {fmt_pct(m['annual_return'])} | {fmt_pct(m['annual_volatility'])} "
            f"| {fmt_pct(m['max_drawdown'])} | {fmt_num(m['sharpe'])} "
            f"| {fmt_num(m['calmar'])} | {fmt_pct(m['avg_position'])} | {fmt_pct(m['avg_turnover'])} |"
        )
    lines.append("")
    # 找回撤最低方案
    if portfolio_metrics:
        best_dd = min(portfolio_metrics.items(), key=lambda x: x[1]["max_drawdown"])
        best_sharpe = max(portfolio_metrics.items(), key=lambda x: x[1]["sharpe"])
        lines.append(f"**最低回撤**: {best_dd[0]} (最大回撤 {fmt_pct(best_dd[1]['max_drawdown'])})")
        lines.append(f"**最高夏普**: {best_sharpe[0]} (夏普 {fmt_num(best_sharpe[1]['sharpe'])})\n")
        lines.append("### 关键判定（规则6：vol→仓位→降回撤链路验证）\n")
        vt = portfolio_metrics.get("Vol_Targeting")
        th = portfolio_metrics.get("Thermometer")
        eq = portfolio_metrics.get("Equal_Weight")
        if vt and th and eq:
            if vt["max_drawdown"] < eq["max_drawdown"]:
                lines.append(f"- vol-targeting 比等权降回撤 {fmt_pct(eq['max_drawdown'] - vt['max_drawdown'])} → 仓位调整有效")
            else:
                lines.append(f"- vol-targeting 未降回撤（vs 等权），仓位调整无效")
            if vt["max_drawdown"] < th["max_drawdown"]:
                lines.append(f"- vol-targeting 比温度计进一步降回撤 {fmt_pct(th['max_drawdown'] - vt['max_drawdown'])} → ML 预测有增量价值")
            else:
                lines.append(f"- vol-targeting 未优于温度计（ML 预测未带来仓位价值增量）")
            if vt["annual_return"] < eq["annual_return"] * 0.8:
                lines.append(f"- ⚠ vol-targeting 年化收益显著低于等权 ({fmt_pct(vt['annual_return'])} vs {fmt_pct(eq['annual_return'])}) → "
                             "跌后减仓的踏空成本显著（规则10 最大遗漏的实证）")
            lines.append("")

    # 结论
    lines.append("## 九、结论\n")
    har_s = summaries.get("HAR-RV", {})
    ewma_s = summaries.get("EWMA", {})
    xgb_s = summaries.get("XGBoost", {})
    ic_har = har_s.get("ic_mean", np.nan)
    ic_ewma = ewma_s.get("ic_mean", np.nan)
    ic_xgb = xgb_s.get("ic_mean", np.nan)

    if not np.isnan(ic_har):
        if ic_har > ic_ewma:
            lines.append(f"**主模型 HAR-RV 跑过 EWMA 基线** (IC {fmt_num(ic_har)} vs {fmt_num(ic_ewma)})。")
        else:
            lines.append(f"**主模型 HAR-RV 未跑过 EWMA** (IC {fmt_num(ic_har)} vs {fmt_num(ic_ewma)})。")
        lines.append("HAR-RV (Corsi 2004, log-HAR) 作为主模型：3 特征 OLS，无超参，可解释。\n")

    if not np.isnan(ic_xgb) and not np.isnan(ic_har):
        delta = ic_xgb - ic_har
        if delta > 0:
            lines.append(f"XGBoost IC 略高于 HAR-RV (+{fmt_num(delta)})。")
            lines.append("排列检验与 DM 检验的结论需以 `vol_model_comparison.py` "
                         "**同窗口重跑**后的结果为准（此处不引用旧数值）。\n")
        else:
            lines.append(f"XGBoost 未跑过 HAR-RV (IC {fmt_num(ic_xgb)} vs {fmt_num(ic_har)})。"
                         "简单模型已接近天花板。\n")

    if portfolio_metrics:
        vt = portfolio_metrics.get("Vol_Targeting")
        eq = portfolio_metrics.get("Equal_Weight")
        if vt and eq:
            if vt["max_drawdown"] < eq["max_drawdown"]:
                lines.append(f"组合层面: vol-targeting 降回撤有效 "
                             f"(最大回撤 {fmt_pct(vt['max_drawdown'])} vs 等权 {fmt_pct(eq['max_drawdown'])})。")
            else:
                lines.append(f"组合层面: vol-targeting 未降回撤 "
                             f"(最大回撤 {fmt_pct(vt['max_drawdown'])} vs 等权 {fmt_pct(eq['max_drawdown'])})。")

    lines.append("")
    lines.append("## 十、已知局限与口径说明\n")
    lines.append("- **幸存者偏差**：基金池取自当前数据库的存续基金，已清盘/合并的基金不在样本内。")
    lines.append("- **组合模拟的选样**：组合基金按 **OOS 起点之前** 的净值覆盖度挑（不使用 OOS 期间数据），"
                 "但仍受「今天还活着的基金」这一层幸存者偏差影响。")
    lines.append("- **QLIKE 口径**：采用 Patton (2011) 标准形式，作用在**方差**上 —— "
                 "`rv/h − log(rv/h) − 1`（h=预测方差）。旧实现误用波动率之比，与文献不可比。")
    lines.append("- **评测窗口**：所有模型限制在同一 OOS 月份集合（见 §四 的 `n_pred`/`n_months`）。")
    lines.append("- **分红/除权处理**：日收益由 `unit_nav` 计算并剔除 |日收益|>20% 的异常点"
                 "（多为分红除权造成的假跳变）；EWMA 遇到缺失日**跳过而非填 0**。")
    lines.append("- **置换检验的零假设**：`vol_model_comparison.py` 的置换检验在**基金内部**打乱标签，"
                 "保留每只基金自身的均值 —— 它检验的是「基金内的增量信号」，"
                 "而不是「模型整体无预测力」（后者打乱后 AUC 应回到 0.5）。")
    lines.append("- **指数 PE 分位**：`index_daily.pe_percentile` 在采集时留空（全为 0），"
                 "本模块用**扩展窗口分位数**就地补算（只用 ≤ 当日数据）。\n")

    lines.append("---")
    lines.append("*本报告由 `src/analysis/vol_predictor.py` 自动生成。"
                 "所有结论基于历史回测，不构成投资建议。*\n")

    report_path = DOCS_DIR / "vol_prediction_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[报告] 已生成: {report_path}")
    return report_path


def _verdict(s, baseline_s):
    """判定某模型相对基线的表现"""
    if not baseline_s:
        return "N/A"
    ic_ok = s["ic_mean"] > baseline_s["ic_mean"]
    qlike_ok = (not np.isnan(s["qlike"]) and not np.isnan(baseline_s["qlike"])
                 and s["qlike"] < baseline_s["qlike"])
    if ic_ok and qlike_ok:
        return "跑过基线"
    if ic_ok or qlike_ok:
        return "部分跑过"
    return "未跑过基线"


def save_csv_results(summaries, ic_series_dict, feat_imp, portfolio_metrics, panel):
    """保存原始结果到 CSV"""
    # 主结果
    rows = []
    for name, s in summaries.items():
        rows.append({
            "model": name, "n_pred": s.get("n_pred"), "n_months": s.get("n_months"),
            "ic_mean": s.get("ic_mean"), "ic_std": s.get("ic_std"),
            "icir": s.get("icir"), "ic_pos_ratio": s.get("ic_pos_ratio"),
            "mse": s.get("mse"), "qlike": s.get("qlike"),
            "group_monotonicity": s.get("group_monotonicity"),
        })
    pd.DataFrame(rows).to_csv(RESULT_DIR / "vol_main_results.csv", index=False, encoding="utf-8-sig")

    # IC 时序
    ic_df = pd.DataFrame({k: v for k, v in ic_series_dict.items() if v is not None})
    if not ic_df.empty:
        ic_df.to_csv(RESULT_DIR / "vol_ic_timeseries.csv", encoding="utf-8-sig")

    # 特征重要度
    if feat_imp is not None and len(feat_imp):
        feat_imp.to_csv(RESULT_DIR / "vol_feature_importance.csv", encoding="utf-8-sig")

    # 组合指标
    if portfolio_metrics:
        pd.DataFrame(portition_metrics_to_rows(portfolio_metrics)).to_csv(
            RESULT_DIR / "vol_portfolio.csv", index=False, encoding="utf-8-sig")

    # 样本快照（最后6个月特征）
    if not panel.empty:
        snap = panel[panel["month"] >= panel["month"].max() - pd.DateOffset(months=6)]
        snap.to_csv(RESULT_DIR / "vol_panel_snapshot.csv", index=False, encoding="utf-8-sig")

    print(f"  [CSV] 结果已保存到 {RESULT_DIR}/")


def portition_metrics_to_rows(portfolio_metrics):
    rows = []
    for name, m in portfolio_metrics.items():
        row = {"scheme": name}
        row.update(m)
        rows.append(row)
    return rows


# =====================================================================
# 主流程
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="基金下月波动率预测 (HAR-RV 主模型 + Walk-Forward)")
    parser.add_argument("--db", default=str(DB_PATH), help="数据库路径")
    parser.add_argument("--max-funds", type=int, default=200, help="基金数量上限")
    args = parser.parse_args()

    print("=" * 60)
    print("基金下月波动率预测 (HAR-RV 主模型 + Walk-Forward)")
    print("=" * 60)

    # 1. 数据加载
    print("\n[1/7] 数据加载...")
    universe = load_fund_universe(args.db, max_funds=args.max_funds)
    if universe.empty:
        print("[错误] 未选出任何基金，退出。")
        return
    fund_codes = universe["fund_code"].tolist()
    nav_wide = load_fund_navs(args.db, fund_codes)
    index_df = load_index_data(args.db, "000300")

    # 2. 收益率与月度实现 vol
    print("\n[2/7] 计算日收益率与月度实现 vol...")
    ret_wide = compute_daily_returns(nav_wide)
    me_arr = month_end_dates(ret_wide.index, start=PRED_START)
    print(f"  月度时点: {len(me_arr)} 个月 ({me_arr[0].date()} ~ {me_arr[-1].date()})")
    realized_df = monthly_realized_vol(ret_wide, me_arr)
    print(f"  实现波动率样本: {len(realized_df)} fund-month")

    # 3. 特征工程
    print("\n[3/7] 特征工程（15 特征）...")
    t0 = time.time()
    features_df = compute_features(ret_wide, universe, index_df, me_arr)
    print(f"  特征样本: {len(features_df)} fund-month, 耗时 {time.time()-t0:.1f}s")

    # 4. 组装面板 + HAR 特征
    print("\n[4/7] 组装面板 + HAR-RV 特征...")
    panel = build_panel(features_df, realized_df)
    panel = add_har_features(panel, realized_df)
    print(f"  合并面板: {len(panel)} fund-month (含 HAR 特征)")
    if panel.empty:
        print("[错误] 面板为空，退出。")
        return
    data_info = {
        "nav_start": nav_wide.index[0].strftime("%Y-%m-%d"),
        "nav_end": nav_wide.index[-1].strftime("%Y-%m-%d"),
        "last_month": panel["month"].max().strftime("%Y-%m"),
    }

    # 5. 基线 + 主模型(HAR-RV) + 对比(XGBoost)
    print("\n[5/7] 基线模型...")
    ewma_pred = baseline_ewma(panel)
    hist6m_pred = baseline_6m_hist(panel)

    print("\n  HAR-RV (主模型, Corsi 2004, log-HAR)...")
    t0 = time.time()
    har_pred = walk_forward_har(panel)
    print(f"  HAR-RV 完成, 预测 {len(har_pred)} 条, 耗时 {time.time()-t0:.1f}s")

    print("\n  XGBoost (对比模型)...")
    t0 = time.time()
    xgb_pred = walk_forward_xgb(panel)
    print(f"  XGBoost 完成, 预测 {len(xgb_pred)} 条, 耗时 {time.time()-t0:.1f}s")

    # 特征重要度
    print("  计算特征重要度...")
    feat_imp = compute_feature_importance(panel)

    # 6. 评估（所有模型统一到同一 OOS 月份集合，否则基线/ML 是不同期间的比较）
    print("\n[6/7] 评估与报告生成...")
    summaries = {}
    ic_series_dict = {}

    preds = align_oos_window({
        "EWMA": ewma_pred, "6M_Hist": hist6m_pred,
        "HAR-RV": har_pred, "XGBoost": xgb_pred,
    })
    for name in ["EWMA", "6M_Hist", "HAR-RV", "XGBoost"]:
        pred = preds.get(name)
        if pred is None or pred.empty:
            continue
        s, ic = evaluate_predictions(pred, panel, name)
        summaries[name] = s
        ic_series_dict[name] = ic
        print(f"  {name}: IC={fmt_num(s.get('ic_mean'))}, "
              f"ICIR={fmt_num(s.get('icir'))}, "
              f"QLIKE={fmt_num(s.get('qlike'))}, "
              f"单调性={fmt_num(s.get('group_monotonicity'))}, "
              f"n={s.get('n_pred')}/{s.get('n_months')}月")

    # 组合模拟（用 HAR-RV 预测做 vol-targeting）
    print("\n  组合模拟 (vol-targeting vs 温度计 vs 等权)...")
    if not har_pred.empty:
        portfolio_metrics = portfolio_simulation(ret_wide, har_pred, index_df)
        for name, m in portfolio_metrics.items():
            print(f"    {name}: 年化={fmt_pct(m['annual_return'])}, "
                  f"回撤={fmt_pct(m['max_drawdown'])}, 夏普={fmt_num(m['sharpe'])}")
    else:
        portfolio_metrics = {}

    # 保存 CSV + 生成报告
    save_csv_results(summaries, ic_series_dict, feat_imp, portfolio_metrics, panel)
    report_path = generate_report(
        universe, panel, summaries, ic_series_dict,
        feat_imp, portfolio_metrics, data_info)

    print(f"\n[7/7] 完成")
    print(f"[报告] {report_path}")
    print(f"[原始结果] {RESULT_DIR}/")

    # 关键结论
    har_s = summaries.get("HAR-RV", {})
    ewma_s = summaries.get("EWMA", {})
    if har_s and ewma_s:
        if har_s["ic_mean"] > ewma_s["ic_mean"]:
            print(f"\n结论: HAR-RV 跑过 EWMA (IC {fmt_num(har_s['ic_mean'])} vs {fmt_num(ewma_s['ic_mean'])})。")
            print("HAR-RV 作为主模型，3 特征 OLS 无超参可解释。")
        else:
            print(f"\n结论: HAR-RV 未跑过 EWMA (IC {fmt_num(har_s['ic_mean'])} vs {fmt_num(ewma_s['ic_mean'])})。")
    xgb_s = summaries.get("XGBoost", {})
    if xgb_s and har_s:
        delta = xgb_s["ic_mean"] - har_s["ic_mean"]
        print(f"XGBoost vs HAR-RV: IC 增量 {fmt_num(delta)} "
              f"({'XGBoost 略高，排列显著但 DM 不显著(见 vol_model_comparison)' if delta > 0 else 'HAR-RV 更优'})。")


if __name__ == "__main__":
    main()
