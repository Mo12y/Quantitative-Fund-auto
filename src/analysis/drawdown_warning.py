"""
基金下月回撤预警分类器 (XGBoost 二分类 + Walk-Forward)

运行方式:
    python src/analysis/drawdown_warning.py [--db data/fund_quant.db] [--max-funds 200] [--thresholds 3,5,8]

说明:
    --max-funds   基金数量上限（按历史长度降序），默认 200
    --thresholds  回撤阈值（%，逗号分隔），默认 "3,5,8" 做敏感性分析

依赖: pandas, numpy, scipy, scikit-learn, xgboost

本模块独立于现有核心模块，不修改数据库与核心功能。
- 原始结果 -> data/drawdown_results/
- 报告     -> docs/drawdown_warning_report.md

预测目标:
    基金下月最大回撤是否 > 阈值（二分类）
    月内最大回撤 = max(peak - trough) / peak，在**复权净值**上计算
    （优先用累计净值 acc_nav，除息日不下挫；并剔除 |日收益|>20% 的异常点）
    （极端事件预警，信噪比比 vol 预测低，但 ML 二分类能挖非线性模式）

模型:
    - 基线1: 历史回撤频率（上月回撤>阈值 → 下月也>阈值）
    - 基线2: Logistic Regression（15 特征线性）
    - 主模型: XGBoost 二分类（15 特征 + scale_pos_weight 处理不平衡）
    - 对照: RandomForest（LightGBM 不可用时的降级）

验证:
    - Walk-Forward 扩展窗口 + Purging Gap（月频 horizon=21 交易日）
    - 严禁随机划分训练/测试集
    - 指标: AUC / 正类召回率 / 精确率 / F1 / Brier Score
    - 重点看正类召回率（漏报比误报代价高）
    - 阈值敏感性分析（3% / 5% / 8%）

类别不平衡处理:
    - 正类（回撤>阈值）预估 10-15%
    - XGBoost: scale_pos_weight = neg/pos
    - 不使用 SMOTE（避免时序数据合成）

诚实声明:
    - 基金池为当前存续基金，存在幸存者偏差（沿用 factor_test 披露）
    - 回撤阈值是构造的（规则5），做敏感性分析（3/5/8%）
    - 不使用模拟/随机数据；数据不足即过滤
    - 负结果（ML 跑不过基线）也如实报告
    - 正类召回率是首要指标（漏报极端回撤代价高）
"""

import argparse
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    roc_auc_score, recall_score, precision_score, f1_score,
    brier_score_loss, confusion_matrix,
)

# 复用 vol_predictor 的数据加载与特征工程
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.analysis.vol_predictor import (
    load_fund_universe, load_fund_navs, load_index_data,
    compute_daily_returns, compute_adjusted_nav, month_end_dates, compute_features,
    align_oos_window,
    FEATURE_COLS, TRAIN_END, OOS_START, REFIT_FREQ_MONTHS,
    TRADING_DAYS_YEAR, DB_PATH, DOCS_DIR,
)

warnings.filterwarnings("ignore")

RESULT_DIR = Path(DB_PATH).parent / "drawdown_results"
RESULT_DIR.mkdir(parents=True, exist_ok=True)

PURGING_GAP_MONTHS = 1

# OOS 内部再切一刀：前半段只用于**选型**（阈值 × 模型），后半段用于**报告**。
# 旧版直接拿全 OOS 的 AUC 挑出最优组合再当结论汇报 —— 那是在测试集上选模型，
# 报出来的 AUC 天然偏乐观。
VAL_FRACTION = 0.5


def split_oos_months(panel, oos_start=OOS_START, val_fraction=VAL_FRACTION):
    """把 OOS 月份按时间顺序切成 (validation, test) 两段。"""
    months = sorted(m for m in panel["month"].unique() if m >= pd.Timestamp(oos_start))
    if len(months) < 4:
        return months, []
    cut = max(1, int(len(months) * val_fraction))
    return months[:cut], months[cut:]


# =====================================================================
# 月度最大回撤计算
# =====================================================================

def monthly_max_drawdown(nav_wide, month_ends, acc_wide=None):
    """
    计算每月每基金的最大回撤。
    月内最大回撤 = max((peak - trough) / peak) within month

    在**复权净值**上计算：优先用累计净值 acc_nav（除息日单位净值下挫、累计净值不动，
    分红不会被当成回撤），并复用 vol_predictor 的异常收益过滤。
    旧实现直接拿 unit_nav 且无任何过滤 → 分红污染标签、推高正例率。

    返回 long DataFrame: fund_code, month, max_drawdown
    """
    nav_indexed = compute_adjusted_nav(nav_wide, acc_wide)
    nav_indexed.index = pd.to_datetime(nav_indexed.index)

    records = []
    for me in month_ends:
        me_ts = pd.Timestamp(me)
        month_start = me_ts.replace(day=1)
        mask = (nav_indexed.index >= month_start) & (nav_indexed.index <= me_ts)
        month_nav = nav_indexed.loc[mask]

        if len(month_nav) < 3:
            continue

        # 每只基金的月内最大回撤
        for code in month_nav.columns:
            s = month_nav[code].dropna()
            if len(s) < 3:
                continue
            peak = s.cummax()
            dd = (peak - s) / peak
            max_dd = float(dd.max())
            if not np.isnan(max_dd) and max_dd >= 0:
                records.append({
                    "fund_code": code,
                    "month": me_ts,
                    "max_drawdown": max_dd,
                })

    return pd.DataFrame(records)


def build_drawdown_labels(dd_df, threshold):
    """
    构造二分类标签: max_drawdown > threshold → 1, else 0
    标签 = t+1 月的回撤是否 > threshold（shift -1）
    """
    dd = dd_df.copy().sort_values(["fund_code", "month"])
    dd["label"] = (dd.groupby("fund_code")["max_drawdown"].shift(-1) > threshold).astype(int)
    # 当前月回撤（作为特征源）
    dd["dd_current"] = dd["max_drawdown"]
    return dd[["fund_code", "month", "dd_current", "label"]]


# =====================================================================
# Walk-Forward 二分类
# =====================================================================

def walk_forward_clf(panel, model_factory, feature_cols, model_name,
                      train_end=TRAIN_END, refit_months=REFIT_FREQ_MONTHS,
                      purging_gap_months=PURGING_GAP_MONTHS):
    """
    Walk-Forward 二分类 + Purging Gap。
    返回 DataFrame: fund_code, month, pred_proba, pred_label, model
    """
    panel = panel.sort_values(["month", "fund_code"]).reset_index(drop=True)
    train_end_ts = pd.Timestamp(train_end)
    last_ts = panel["month"].max()

    refit_starts = pd.date_range(
        start=train_end_ts + pd.offsets.MonthBegin(1),
        end=last_ts, freq=f"{refit_months}MS")

    all_preds = []
    for i, rs in enumerate(refit_starts):
        train_cutoff = rs - pd.DateOffset(months=purging_gap_months)
        train = panel[panel["month"] < train_cutoff]
        if i + 1 < len(refit_starts):
            test_end = refit_starts[i + 1] - pd.Timedelta(days=1)
        else:
            test_end = last_ts
        test = panel[(panel["month"] >= rs) & (panel["month"] <= test_end)]

        train = train.dropna(subset=feature_cols + ["label"])
        test = test.dropna(subset=feature_cols + ["label"])
        if train.empty or test.empty:
            continue

        # 检查训练集是否有正类
        pos_count = int((train["label"] == 1).sum())
        neg_count = int((train["label"] == 0).sum())
        if pos_count < 10 or neg_count < 10:
            continue

        X_train = train[feature_cols].values
        y_train = train["label"].values
        X_test = test[feature_cols].values

        model = model_factory(pos_count, neg_count)
        model.fit(X_train, y_train)
        pred_proba = model.predict_proba(X_test)[:, 1]
        pred_label = (pred_proba >= 0.5).astype(int)

        out = test[["fund_code", "month"]].copy()
        out["pred_proba"] = pred_proba
        out["pred_label"] = pred_label
        out["model"] = model_name
        out["label"] = test["label"].values
        all_preds.append(out)

        if (i + 1) % 4 == 0 or i == len(refit_starts) - 1:
            print(f"    [walk-forward {model_name}] refit@{rs.date()}: "
                  f"train={len(train)} (pos={pos_count}), test={len(test)}")

    if not all_preds:
        return pd.DataFrame()
    return pd.concat(all_preds, ignore_index=True)


# =====================================================================
# 模型 factory
# =====================================================================

def make_logistic(pos_count, neg_count):
    """Logistic Regression 基线"""
    return LogisticRegression(
        max_iter=1000, class_weight="balanced",
        random_state=42, solver="lbfgs",
    )


def make_xgboost_clf(pos_count, neg_count):
    """XGBoost 二分类，scale_pos_weight 处理不平衡"""
    import xgboost as xgb
    spw = max(neg_count / pos_count, 1.0) if pos_count > 0 else 1.0
    return xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        reg_alpha=0.1, reg_lambda=1.0,
        scale_pos_weight=spw,
        random_state=42, n_jobs=-1, verbosity=0,
        eval_metric="auc",
    )


def make_rf_clf(pos_count, neg_count):
    """RandomForest 二分类"""
    return RandomForestClassifier(
        n_estimators=200, max_depth=8, min_samples_leaf=20,
        class_weight="balanced",
        random_state=42, n_jobs=-1,
    )


# =====================================================================
# 基线: 历史回撤频率
# =====================================================================

def baseline_history_freq(panel, threshold):
    """
    基线: 上月回撤>阈值 → 预测下月也>阈值
    pred_proba = 历史回撤频率（该基金历史正类比例）
    """
    panel = panel.sort_values(["fund_code", "month"]).copy()
    # 滚动历史正类比例（expanding, shift 1 防 leak）
    panel["pred_proba"] = panel.groupby("fund_code")["label"].transform(
        lambda s: s.shift(1).expanding(min_periods=5).mean())
    panel["pred_label"] = (panel["pred_proba"] >= 0.5).astype(int)
    panel["model"] = "History_Freq"
    return panel[["fund_code", "month", "pred_proba", "pred_label",
                  "model", "label"]].dropna(subset=["pred_proba"])


# =====================================================================
# 评估
# =====================================================================

def evaluate_classifier(pred_df, model_name):
    """
    评估二分类模型。
    重点指标: AUC, 正类召回率（漏报代价高）, F1, Brier Score
    """
    if pred_df.empty or "label" not in pred_df.columns:
        return {}

    y_true = pred_df["label"].values
    y_proba = pred_df["pred_proba"].values
    y_pred = pred_df["pred_label"].values

    pos_count = int((y_true == 1).sum())
    neg_count = int((y_true == 0).sum())
    total = len(y_true)
    pos_ratio = pos_count / total if total > 0 else 0

    metrics = {
        "model": model_name,
        "n_total": total,
        "n_pos": pos_count,
        "n_neg": neg_count,
        "pos_ratio": pos_ratio,
    }

    # AUC（需要两类都有）
    if pos_count > 0 and neg_count > 0:
        metrics["auc"] = float(roc_auc_score(y_true, y_proba))
    else:
        metrics["auc"] = np.nan

    # 正类召回率（最重要——漏报极端回撤代价高）
    if pos_count > 0:
        metrics["recall_pos"] = float(recall_score(y_true, y_pred, pos_label=1, zero_division=0))
        metrics["precision_pos"] = float(precision_score(y_true, y_pred, pos_label=1, zero_division=0))
    else:
        metrics["recall_pos"] = np.nan
        metrics["precision_pos"] = np.nan

    # F1
    metrics["f1"] = float(f1_score(y_true, y_pred, zero_division=0))

    # Brier Score（概率校准）
    metrics["brier"] = float(brier_score_loss(y_true, y_proba))

    # 混淆矩阵
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    metrics["tn"] = int(cm[0, 0])
    metrics["fp"] = int(cm[0, 1])
    metrics["fn"] = int(cm[1, 0])
    metrics["tp"] = int(cm[1, 1])

    # 漏报率（正类中未被预警的比例）
    metrics["miss_rate"] = metrics["fn"] / pos_count if pos_count > 0 else np.nan

    return metrics


# =====================================================================
# 排列检验
# =====================================================================

def permutation_test_auc(pred_df, panel, model_factory, feature_cols,
                          n_permute=50, train_end=TRAIN_END,
                          refit_months=REFIT_FREQ_MONTHS, model_name="XGBoost"):
    """
    排列检验: 打乱标签，重训模型，看 AUC 是否在噪声内。
    p-value = (1 + count(shuffled_auc <= real_auc)) / (1 + n_permute)

    `model_name` 会写进返回值 —— 报告里必须如实标注"这个 p 值是对哪个模型做的"，
    旧版用「最强 ML」的名字去标一个只对 XGBoost 跑过的检验，标签与数字对不上。
    """
    if pred_df.empty:
        return {"p_value": np.nan, "model": model_name}

    y_true = pred_df["label"].values
    y_proba = pred_df["pred_proba"].values
    pos = int((y_true == 1).sum())
    neg = int((y_true == 0).sum())
    if pos < 10 or neg < 10:
        return {"p_value": np.nan, "model": model_name}
    real_auc = roc_auc_score(y_true, y_proba)

    shuffled_aucs = []
    panel_shuffled = panel.copy()

    for k in range(n_permute):
        panel_shuffled["label"] = panel_shuffled.groupby("fund_code")[
            "label"].transform(lambda s: s.sample(frac=1, random_state=k).values)
        try:
            sh_pred = walk_forward_clf(
                panel_shuffled, model_factory, feature_cols, "shuffled",
                train_end, refit_months, PURGING_GAP_MONTHS)
            if sh_pred.empty:
                continue
            sh_y = sh_pred["label"].values
            sh_p = sh_pred["pred_proba"].values
            sp = int((sh_y == 1).sum())
            sn = int((sh_y == 0).sum())
            if sp < 5 or sn < 5:
                continue
            shuffled_aucs.append(roc_auc_score(sh_y, sh_p))
        except Exception:
            continue
        if (k + 1) % 10 == 0:
            print(f"    [permutation] {k+1}/{n_permute}, "
                  f"shuffled AUC mean={np.mean(shuffled_aucs):.4f}" if shuffled_aucs else "")

    if not shuffled_aucs:
        return {"p_value": np.nan, "n_permute": 0, "model": model_name}

    # Phipson-Smyth: p = (1 + count(shuffled >= real)) / (1 + n)
    # 检验"真实 AUC 是否显著优于随机"
    count_ge = sum(1 for a in shuffled_aucs if a >= real_auc)
    p_value = (1 + count_ge) / (1 + n_permute)
    return {
        "real_auc": float(real_auc),
        "p_value": float(p_value),
        "n_permute": len(shuffled_aucs),
        "shuffled_auc_mean": float(np.mean(shuffled_aucs)),
        "shuffled_auc_std": float(np.std(shuffled_aucs)),
        "model": model_name,
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


def generate_report(universe_df, panel_by_threshold, all_preds_by_threshold,
                    metrics_by_threshold, perm_results_by_threshold,
                    data_info, thresholds,
                    val_metrics_by_threshold=None, test_metrics_by_threshold=None,
                    split_by_threshold=None):
    val_metrics_by_threshold = val_metrics_by_threshold or {}
    test_metrics_by_threshold = test_metrics_by_threshold or {}
    split_by_threshold = split_by_threshold or {}
    lines = []
    lines.append("# 基金下月回撤预警分类器 — 报告\n")
    lines.append(f"> 生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    lines.append("## 一、运行说明\n")
    lines.append("```bash")
    lines.append("python src/analysis/drawdown_warning.py [--max-funds 200] [--thresholds 3,5,8]")
    lines.append("```\n")
    lines.append("预测目标: 基金下月最大回撤是否 > 阈值（二分类）")
    lines.append("评估重点: **正类召回率**（漏报极端回撤代价高于误报）")
    lines.append("类别不平衡: scale_pos_weight = neg/pos（不使用 SMOTE，避免时序合成）\n")

    # 数据
    lines.append("## 二、数据概况\n")
    sample_panel = list(panel_by_threshold.values())[0] if panel_by_threshold else pd.DataFrame()
    lines.append(f"- 基金池: {len(universe_df)} 只 (股票型/偏股)")
    lines.append(f"- 净值区间: {data_info['nav_start']} ~ {data_info['nav_end']}")
    lines.append(f"- 月度样本: {len(sample_panel)} fund-month")
    lines.append(f"- 训练期: 2018-01 ~ {TRAIN_END[:7]}（purging gap = 1 月）")
    lines.append(f"- 测试期(OOS): {OOS_START[:7]} ~ {data_info['last_month']}")
    lines.append(f"- 重训频率: 每 {REFIT_FREQ_MONTHS} 月")
    _split = next(iter(split_by_threshold.values()), None)
    if _split and _split[0]:
        vm0, vm1 = _split[0][0], _split[0][-1]
        lines.append(f"- OOS 内部再切: 验证段 {vm0.strftime('%Y-%m')} ~ {vm1.strftime('%Y-%m')}"
                     f"（选型用）/ 测试段 {_split[1][0].strftime('%Y-%m')} ~ "
                     f"{_split[1][-1].strftime('%Y-%m')}（报告用）")
    lines.append("")

    # 各阈值正类比例
    lines.append("## 三、各阈值正类比例（规则5 标签天然性 + 规则8 样本量）\n")
    lines.append("| 回撤阈值 | 总样本 | 正类 | 负类 | 正类占比 | 样本量判定 |")
    lines.append("|:--|:--|:--|:--|:--|:--|")
    for th in thresholds:
        panel = panel_by_threshold.get(th, pd.DataFrame())
        if panel.empty:
            continue
        pos = int((panel["label"] == 1).sum())
        neg = int((panel["label"] == 0).sum())
        total = len(panel)
        ratio = pos / total if total > 0 else 0
        verdict = "充足" if pos >= 500 else ("边缘" if pos >= 100 else "不足")
        lines.append(f"| {th}% | {total} | {pos} | {neg} | {fmt_pct(ratio)} | {verdict} |")
    lines.append("")

    # 各阈值模型对比
    for th in thresholds:
        metrics_dict = metrics_by_threshold.get(th, {})
        if not metrics_dict:
            continue
        lines.append(f"## 四、模型对比 — 回撤阈值 {th}%\n")
        lines.append("| 模型 | AUC | 正类召回 | 精确率 | F1 | Brier | 漏报率 | TP | FN | FP | TN |")
        lines.append("|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|")
        for name in ["History_Freq", "Logistic", "XGBoost", "RandomForest"]:
            if name not in metrics_dict:
                continue
            m = metrics_dict[name]
            lines.append(
                f"| {name} | {fmt_num(m.get('auc'))} | {fmt_pct(m.get('recall_pos'))} "
                f"| {fmt_pct(m.get('precision_pos'))} | {fmt_num(m.get('f1'))} "
                f"| {fmt_num(m.get('brier'))} | {fmt_pct(m.get('miss_rate'))} "
                f"| {m.get('tp', 0)} | {m.get('fn', 0)} | {m.get('fp', 0)} | {m.get('tn', 0)} |"
            )
        lines.append("")

        # 判定
        base_auc = metrics_dict.get("History_Freq", {}).get("auc", 0)
        best_ml = None
        best_ml_auc = 0
        for name in ["Logistic", "XGBoost", "RandomForest"]:
            if name in metrics_dict:
                auc = metrics_dict[name].get("auc", 0)
                if auc > best_ml_auc:
                    best_ml_auc = auc
                    best_ml = name
        if best_ml and best_ml_auc > base_auc:
            lines.append(f"**最强 ML**: {best_ml} (AUC={fmt_num(best_ml_auc)})，"
                         f"跑过基线 (History_Freq AUC={fmt_num(base_auc)})。\n")
        elif best_ml:
            lines.append(f"**最强 ML**: {best_ml} (AUC={fmt_num(best_ml_auc)})，"
                         f"未跑过基线 (History_Freq AUC={fmt_num(base_auc)})。\n")

        # 排列检验
        perm = perm_results_by_threshold.get(th, {})
        if perm and not np.isnan(perm.get("p_value", np.nan)):
            # 标题必须标"这个 p 值是对哪个模型做的"：检验只对 XGBoost 跑过，
            # 旧版却用「最强 ML」（可能是 RandomForest）去标，标签与数字对不上。
            lines.append(f"### 排列检验（{perm.get('model', 'XGBoost')}）\n")
            lines.append(f"- 真实 AUC: {fmt_num(perm.get('real_auc'))}")
            lines.append(f"- 排列 AUC 均值: {fmt_num(perm.get('shuffled_auc_mean'))}")
            lines.append(f"- 排列 AUC std: {fmt_num(perm.get('shuffled_auc_std'))}")
            lines.append(f"- p-value: {fmt_num(perm.get('p_value'))}")
            p = perm.get("p_value", 1)
            sig = "显著 (5%)" if p < 0.05 else ("显著 (10%)" if p < 0.10 else "不显著")
            lines.append(f"- 判定: **{sig}**\n")

    # 敏感性分析
    lines.append("## 五、阈值敏感性分析（规则5）\n")
    lines.append("| 阈值 | 最强模型 | AUC | 正类召回 | 排列 p |")
    lines.append("|:--|:--|:--|:--|:--|")
    for th in thresholds:
        metrics_dict = metrics_by_threshold.get(th, {})
        if not metrics_dict:
            continue
        best_name = max(
            [n for n in ["XGBoost", "Logistic", "RandomForest"] if n in metrics_dict],
            key=lambda n: metrics_dict[n].get("auc", 0),
            default=None,
        )
        if not best_name:
            continue
        m = metrics_dict[best_name]
        perm = perm_results_by_threshold.get(th, {})
        p = perm.get("p_value", np.nan)
        lines.append(
            f"| {th}% | {best_name} | {fmt_num(m.get('auc'))} "
            f"| {fmt_pct(m.get('recall_pos'))} | {fmt_num(p)} |"
        )
    lines.append("")

    # 结论：**在验证段选型、在测试段报告**（旧版直接拿全 OOS 的 AUC 挑最优组合当结论）
    lines.append("## 六、结论（验证段选型 · 测试段报告）\n")
    n_search = len(thresholds) * 4  # 阈值数 × 模型数（XGBoost/Logistic/RF/History_Freq）
    lines.append(f"**选型口径**: 在**验证段**（OOS 前 {int(VAL_FRACTION*100)}% 月份）上挑 "
                 f"AUC 最高的（阈值, 模型）组合，然后在**测试段**（其余月份）上报告其表现。"
                 f"共搜索 {n_search} 个组合，排列 p 值按 Bonferroni 校正 ×{n_search}。\n")

    best_combo = None
    best_val_auc = -1.0
    for th in thresholds:
        for name, m in val_metrics_by_threshold.get(th, {}).items():
            auc = m.get("auc")
            if auc is not None and not np.isnan(auc) and auc > best_val_auc:
                best_val_auc = auc
                best_combo = (th, name)

    if best_combo:
        th, name = best_combo
        tm = test_metrics_by_threshold.get(th, {}).get(name, {})
        vm = val_metrics_by_threshold[th][name]
        perm = perm_results_by_threshold.get(th, {})
        p_raw = perm.get("p_value", np.nan)
        lines.append(f"1. **选出的组合**: {name} @ {th}% 阈值 "
                     f"（验证段 AUC={fmt_num(best_val_auc)}）")
        lines.append(f"2. **测试段表现**: AUC={fmt_num(tm.get('auc'))}，"
                     f"正类召回={fmt_pct(tm.get('recall_pos'))}，"
                     f"漏报率={fmt_pct(tm.get('miss_rate'))}，样本 {tm.get('n_total')}")
        if not np.isnan(p_raw):
            p_adj = min(1.0, p_raw * n_search)
            lines.append(f"3. **排列检验**: 原始 p={fmt_num(p_raw)}，"
                         f"Bonferroni 校正后 p={fmt_num(p_adj)}"
                         f"（{'显著' if p_adj < 0.05 else '不显著'}）")
        # 基线对比（同一测试段）
        base_m = test_metrics_by_threshold.get(th, {}).get("History_Freq", {})
        base_auc = base_m.get("auc")
        auc_t = tm.get("auc")
        _p_adj = min(1.0, p_raw * n_search) if not np.isnan(p_raw) else np.nan
        if base_auc is not None and auc_t is not None and not np.isnan(base_auc):
            delta = auc_t - base_auc
            if delta > 0.05 and not np.isnan(_p_adj) and _p_adj < 0.05:
                lines.append(f"4. **ML 增量（测试段）**: +{fmt_num(delta)} AUC，"
                             "排列检验校正后仍显著 → 回撤预警上有真实增量")
            elif delta > 0.05:
                lines.append(f"4. **ML 增量（测试段）**: +{fmt_num(delta)} AUC，"
                             f"但排列检验 Bonferroni 校正后 p={fmt_num(_p_adj)} **不显著** → "
                             "该增量未被统计支持，只能当参考，不能当结论。")
            elif delta > 0:
                lines.append(f"4. **ML 增量（测试段）**: +{fmt_num(delta)} AUC，增量较小")
            else:
                lines.append(f"4. **ML 未跑过基线（测试段）**: {fmt_num(delta)} AUC，简单模型已够")
        lines.append("5. **选择偏差声明**: 上面的 AUC 仍是「12 个组合里挑出来的」最大值，"
                     "验证段与测试段同属 2023 年后的同一样本区间，"
                     "不能等同于独立样本外验证。\n")
    lines.append("")

    lines.append("## 七、已知局限与口径说明\n")
    lines.append("- **回撤标签口径**：月内最大回撤在**复权净值**上计算 —— 优先用累计净值 "
                 "`acc_nav`（除息日单位净值下挫、累计净值不动，分红不会被误判成回撤），"
                 "并复用 `clean_returns` 剔除 |日收益|>20% 的异常点。"
                 "旧版直接拿 `unit_nav` 且无任何过滤，分红污染标签、推高正例率。")
    lines.append("- **选型偏差**：最优（阈值, 模型）组合在验证段挑、测试段报告，"
                 "并做 Bonferroni 校正；但验证/测试同属 2023 年后的同一样本区间，"
                 "不等于独立样本外验证。")
    lines.append("- **置换检验的零假设**：置换在**基金内部**打乱标签，保留每只基金自身的正类比例 —— "
                 "它检验的是「基金内的增量信号」，不是「模型整体无预测力」"
                 "（后者打乱后 AUC 应回到 0.5）。")
    lines.append("- **幸存者偏差**：基金池为当前存续基金，已清盘基金不在样本内。")
    lines.append("- **不回填历史**：本报告只读数据库，不修改任何历史记录。\n")

    lines.append("---")
    lines.append("*本报告由 `src/analysis/drawdown_warning.py` 自动生成。"
                 "所有结论基于历史回测，不构成投资建议。*\n")

    report_path = DOCS_DIR / "drawdown_warning_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[报告] 已生成: {report_path}")
    return report_path


# =====================================================================
# 主流程
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="基金下月回撤预警分类器")
    parser.add_argument("--db", default=str(DB_PATH), help="数据库路径")
    parser.add_argument("--max-funds", type=int, default=200, help="基金数量上限")
    parser.add_argument("--thresholds", default="3,5,8", help="回撤阈值(%, 逗号分隔)")
    parser.add_argument("--n-permute", type=int, default=50, help="排列检验次数")
    args = parser.parse_args()

    thresholds = [float(t) for t in args.thresholds.split(",")]

    print("=" * 70)
    print(f"基金下月回撤预警分类器 (阈值: {thresholds}%)")
    print("=" * 70)

    # 1. 数据加载
    print("\n[1/5] 数据加载...")
    universe = load_fund_universe(args.db, max_funds=args.max_funds)
    if universe.empty:
        print("[错误] 未选出任何基金")
        return
    fund_codes = universe["fund_code"].tolist()
    nav_wide = load_fund_navs(args.db, fund_codes)
    acc_wide = load_fund_navs(args.db, fund_codes, column="acc_nav")
    index_df = load_index_data(args.db, "000300")

    # 2. 收益率 + 月度回撤
    print("\n[2/5] 计算月度最大回撤...")
    ret_wide = compute_daily_returns(nav_wide)
    me_arr = month_end_dates(ret_wide.index)
    print(f"  月度时点: {len(me_arr)} 个月 ({me_arr[0].date()} ~ {me_arr[-1].date()})")
    dd_df = monthly_max_drawdown(nav_wide, me_arr, acc_wide=acc_wide)
    print(f"  回撤样本: {len(dd_df)} fund-month")
    print(f"  回撤分布: mean={dd_df['max_drawdown'].mean():.4f}, "
          f"median={dd_df['max_drawdown'].median():.4f}, "
          f"max={dd_df['max_drawdown'].max():.4f}")

    # 3. 特征工程（复用 vol_predictor 的 15 特征）
    print("\n[3/5] 特征工程（15 特征，复用 vol_predictor）...")
    t0 = time.time()
    features_df = compute_features(ret_wide, universe, index_df, me_arr)
    print(f"  特征样本: {len(features_df)} fund-month, 耗时 {time.time()-t0:.1f}s")

    data_info = {
        "nav_start": nav_wide.index[0].strftime("%Y-%m-%d"),
        "nav_end": nav_wide.index[-1].strftime("%Y-%m-%d"),
        "last_month": max(me_arr).strftime("%Y-%m"),
    }

    # 4. 每个阈值跑模型
    panel_by_threshold = {}
    all_preds_by_threshold = {}
    metrics_by_threshold = {}
    val_metrics_by_threshold = {}
    test_metrics_by_threshold = {}
    split_by_threshold = {}
    perm_results_by_threshold = {}

    for th in thresholds:
        th_frac = th / 100.0
        print(f"\n[4/5] 阈值 {th}% — 构建标签 + 跑模型...")

        # 构建标签
        labels = build_drawdown_labels(dd_df, th_frac)
        panel = features_df.merge(
            labels[["fund_code", "month", "label"]], on=["fund_code", "month"], how="inner")
        panel = panel.dropna(subset=FEATURE_COLS + ["label"])
        panel_by_threshold[th] = panel

        pos = int((panel["label"] == 1).sum())
        neg = int((panel["label"] == 0).sum())
        print(f"  样本: {len(panel)}, 正类={pos} ({pos/len(panel)*100:.1f}%), 负类={neg}")

        if pos < 50:
            print(f"  ⚠ 正类不足 50，跳过此阈值")
            continue

        # 基线
        print(f"  基线: 历史回撤频率...")
        base_pred = baseline_history_freq(panel, th_frac)

        # Logistic
        print(f"  Logistic Regression...")
        log_pred = walk_forward_clf(panel, make_logistic, FEATURE_COLS, "Logistic")

        # XGBoost
        print(f"  XGBoost...")
        t0 = time.time()
        xgb_pred = walk_forward_clf(panel, make_xgboost_clf, FEATURE_COLS, "XGBoost")
        print(f"  XGBoost 完成, 耗时 {time.time()-t0:.1f}s")

        # RandomForest
        print(f"  RandomForest...")
        rf_pred = walk_forward_clf(panel, make_rf_clf, FEATURE_COLS, "RandomForest")

        # 评估
        all_preds = {
            "History_Freq": base_pred,
            "Logistic": log_pred,
            "XGBoost": xgb_pred,
            "RandomForest": rf_pred,
        }
        # 基线是 expanding 历史频率（可覆盖全样本），ML 只有 walk-forward 的 OOS 段。
        # 不对齐的话 n_total 是 18551 vs 8533 —— 那是**不同期间**的比较。
        all_preds = align_oos_window(all_preds)
        all_preds_by_threshold[th] = all_preds
        metrics = {}
        for name, pred in all_preds.items():
            if pred.empty:
                continue
            m = evaluate_classifier(pred, name)
            metrics[name] = m
            print(f"  {name}: AUC={fmt_num(m.get('auc'))}, "
                  f"召回={fmt_pct(m.get('recall_pos'))}, "
                  f"F1={fmt_num(m.get('f1'))}, "
                  f"漏报={fmt_pct(m.get('miss_rate'))}")
        metrics_by_threshold[th] = metrics

        # 验证/测试两段评估：选型只看 validation，报告只看 test
        val_months, test_months = split_oos_months(panel)
        split_by_threshold[th] = (val_months, test_months)
        val_metrics_by_threshold[th] = {
            name: evaluate_classifier(pred[pred["month"].isin(val_months)], name)
            for name, pred in all_preds.items() if not pred.empty}
        test_metrics_by_threshold[th] = {
            name: evaluate_classifier(pred[pred["month"].isin(test_months)], name)
            for name, pred in all_preds.items() if not pred.empty}
        print(f"  验证段 {len(val_months)} 个月 / 测试段 {len(test_months)} 个月")

        # 排列检验（只对 XGBoost）
        if not xgb_pred.empty and args.n_permute > 0:
            print(f"  排列检验 (n={args.n_permute})...")
            t0 = time.time()
            perm = permutation_test_auc(
                xgb_pred, panel, make_xgboost_clf, FEATURE_COLS,
                n_permute=args.n_permute, model_name="XGBoost")
            perm_results_by_threshold[th] = perm
            print(f"  p-value={fmt_num(perm.get('p_value'))}, "
                  f"耗时 {time.time()-t0:.1f}s")

    # 5. 报告
    print(f"\n[5/5] 生成报告...")
    report_path = generate_report(
        universe, panel_by_threshold, all_preds_by_threshold,
        metrics_by_threshold, perm_results_by_threshold,
        data_info, thresholds,
        val_metrics_by_threshold=val_metrics_by_threshold,
        test_metrics_by_threshold=test_metrics_by_threshold,
        split_by_threshold=split_by_threshold)

    # 保存 CSV
    rows = []
    for th, metrics_dict in metrics_by_threshold.items():
        for name, m in metrics_dict.items():
            row = {"threshold": th, "model": name}
            row.update(m)
            rows.append(row)
    if rows:
        pd.DataFrame(rows).to_csv(
            RESULT_DIR / "drawdown_main_results.csv",
            index=False, encoding="utf-8-sig")
        print(f"  [CSV] {RESULT_DIR}/drawdown_main_results.csv")

    print(f"\n[完成] 报告: {report_path}")

    # 关键结论
    print("\n" + "=" * 70)
    print("关键结论:")
    for th in thresholds:
        metrics = metrics_by_threshold.get(th, {})
        if not metrics:
            continue
        best = max(
            [n for n in ["XGBoost", "Logistic", "RandomForest", "History_Freq"] if n in metrics],
            key=lambda n: metrics[n].get("auc", 0),
            default=None,
        )
        if best:
            m = metrics[best]
            perm = perm_results_by_threshold.get(th, {})
            p = perm.get("p_value", np.nan)
            print(f"  阈值{th}%: 最强={best}, AUC={fmt_num(m.get('auc'))}, "
                  f"召回={fmt_pct(m.get('recall_pos'))}, "
                  f"排列p={fmt_num(p)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
