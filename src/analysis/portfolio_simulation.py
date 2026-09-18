"""
组合模拟: Vol-Targeting + 回撤预警 减仓 vs 纯 Vol-Targeting

运行方式:
    python src/analysis/portfolio_simulation.py [--max-funds 200] [--dd-threshold 8]

说明:
    --max-funds     基金数量上限，默认 200
    --dd-threshold  回撤预警阈值(%)，默认 8（敏感性分析最优配置）

依赖: pandas, numpy, scipy, scikit-learn, xgboost

本模块独立于现有核心模块，不修改数据库与核心功能。
- 原始结果 -> data/portfolio_results/
- 报告     -> docs/portfolio_simulation_report.md

业务问题:
    vol-targeting 能否降回撤？在它之上叠加回撤预警（XGBoost），
    能否进一步降低回撤、代价是多少收益？这是验证回撤预警业务增量的关键测试。

口径（本轮修正）:
    - 仓位信号一律取**严格早于当月**的最近一期预测（旧版含当月月末 → 同月前视）
    - 未投资部分按无风险利率计息；期初从现金出发，首月如实收建仓成本
    - 回撤标签在**复权净值**（累计净值）上计算，避免分红污染

策略:
    - Equal_Weight: 等权基准 (pos=1.0)
    - Vol_Targeting: HAR-RV 预测 vol → 仓位=clip(15%/pred_vol, 0.05, 1.0)
    - Vol_DD_Combined: vol-targeting × 回撤预警减仓
        (当组合内 >50% 基金触发预警时，仓位减半)

信号组合逻辑:
    pred_vol_portfolio = mean(基金 HAR-RV 预测 vol)
    dd_warning_frac = fraction(基金 XGBoost 预警 proba > 0.5)
    vol_target_pos = clip(target_vol / pred_vol_portfolio, 0.05, 1.0)
    dd_warning_pos = 0.5 if dd_warning_frac > 0.5 else 1.0
    combined_pos = vol_target_pos × dd_warning_pos

验证:
    - 同口径回测: 30 基金组合, 月度再平衡, 0.5% 单边成本, 2023-2026 OOS
    - 指标: 年化收益/波动/最大回撤/夏普/Calmar
    - 重点看: Vol_DD_Combined vs Vol_Targeting 的回撤降低 vs 收益损失

诚实声明:
    - 基金池为当前存续基金，存在幸存者偏差（沿用 factor_test 披露）
    - 回撤预警阈值是构造的（规则5），8% 经敏感性分析确认为最优
    - 不使用模拟/随机数据；数据不足即过滤
    - 负结果（叠加预警无效或更差）也如实报告
"""

import argparse
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

# 复用 vol_predictor 的数据加载、HAR-RV、特征工程、组合回测骨架
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.analysis.vol_predictor import (
    load_fund_universe, load_fund_navs, load_index_data,
    compute_daily_returns, month_end_dates, compute_features,
    monthly_realized_vol, add_har_features, walk_forward_har,
    build_panel, _portfolio_metrics, last_signal_before,
    FALLBACK_PRED_VOL, FALLBACK_PE_EQUITY, FALLBACK_DD_FRAC,
    FEATURE_COLS, HAR_FEATURES, TRAIN_END, OOS_START, REFIT_FREQ_MONTHS,
    TRADING_DAYS_YEAR, TARGET_VOL, ONE_WAY_COST, N_PORTFOLIO_FUNDS, RF_ANNUAL,
    DB_PATH, DOCS_DIR, RESULT_DIR,
)
# 复用 drawdown_warning 的月度回撤 + XGBoost 分类
from src.analysis.drawdown_warning import (
    monthly_max_drawdown, build_drawdown_labels, walk_forward_clf,
    make_xgboost_clf, PURGING_GAP_MONTHS,
)

warnings.filterwarnings("ignore")

PORTFOLIO_RESULT_DIR = Path(DB_PATH).parent / "portfolio_results"
PORTFOLIO_RESULT_DIR.mkdir(parents=True, exist_ok=True)

# 回撤预警减仓幅度：触发时仓位乘以此系数
DD_REDUCTION_FACTOR = 0.5
# 触发预警的基金比例阈值（组合级信号）
DD_TRIGGER_FRAC = 0.5
# 预警概率阈值（单基金级）
DD_PROBA_THRESHOLD = 0.5


# =====================================================================
# 回撤预警预测（复用 drawdown_warning 模块）
# =====================================================================

def get_drawdown_predictions(nav_wide, ret_wide, me_arr, universe, index_df,
                            threshold_pct=8.0, acc_wide=None):
    """
    跑 XGBoost 回撤预警 walk-forward，返回 DataFrame:
        fund_code, month, pred_proba, pred_label
    """
    th_frac = threshold_pct / 100.0
    print(f"  [DD] 计算月度回撤 (阈值={threshold_pct}%)...")
    dd_df = monthly_max_drawdown(nav_wide, me_arr, acc_wide=acc_wide)
    labels = build_drawdown_labels(dd_df, th_frac)

    print(f"  [DD] 特征工程（复用 vol_predictor 15 特征）...")
    features_df = compute_features(ret_wide, universe, index_df, me_arr)
    panel = features_df.merge(
        labels[["fund_code", "month", "label"]], on=["fund_code", "month"], how="inner")
    panel = panel.dropna(subset=FEATURE_COLS + ["label"])

    pos = int((panel["label"] == 1).sum())
    print(f"  [DD] 样本 {len(panel)}, 正类 {pos} ({pos/len(panel)*100:.1f}%)")

    print(f"  [DD] XGBoost walk-forward...")
    t0 = time.time()
    xgb_pred = walk_forward_clf(panel, make_xgboost_clf, FEATURE_COLS, "XGBoost")
    print(f"  [DD] 完成, 预测 {len(xgb_pred)} 条, 耗时 {time.time()-t0:.1f}s")
    return xgb_pred


# =====================================================================
# 组合模拟：三策略对比
# =====================================================================

def combined_portfolio_simulation(ret_wide, vol_pred, dd_pred, index_df,
                                  oos_start=OOS_START,
                                  n_funds=N_PORTFOLIO_FUNDS,
                                  target_vol=TARGET_VOL):
    """
    三仓位方案对比。
    - Equal_Weight: pos=1.0
    - Vol_Targeting: pos=clip(target_vol / pred_vol_portfolio, 0.05, 1.0)
    - Vol_DD_Combined: vol_target_pos × dd_warning_pos

    月度再平衡，0.5% 单边成本。返回 dict of metrics per scheme.
    """
    # 1. 选组合基金：只用 **OOS 之前** 的净值覆盖度挑（按 OOS 期间覆盖度排序 =
    #    用"未来还活着"选样本，是幸存者偏差的一种）
    ret_pre = ret_wide.loc[ret_wide.index < pd.Timestamp(oos_start)]
    coverage = ret_pre.notna().sum().sort_values(ascending=False)
    port_codes = coverage.head(n_funds).index.tolist()
    print(f"  [portfolio] 选 {len(port_codes)} 只基金用于组合模拟"
          f"（按 {pd.Timestamp(oos_start).date()} 之前的净值覆盖度挑）")

    ret_port = ret_wide[port_codes].copy()
    ret_port.index = pd.to_datetime(ret_port.index)

    # 月度组合收益（等权）
    monthly_ret = ret_port.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    port_monthly_ret = monthly_ret.mean(axis=1).dropna()

    # OOS 月份
    oos_months = port_monthly_ret.index[port_monthly_ret.index >= pd.Timestamp(oos_start)]

    # 预测 vol 聚合到月（组合预测vol = 基金预测vol 的均值）
    vol_pred_df = vol_pred[vol_pred["fund_code"].isin(port_codes)].copy()
    vol_pred_df["month"] = pd.to_datetime(vol_pred_df["month"])
    # 月末对齐：把 vol 预测对齐到月末（取 <= 月末的最近预测）
    vol_pred_monthly = vol_pred_df.groupby("month")["pred_vol"].mean().sort_index()

    # 回撤预警聚合到月：组合内触发预警的基金比例
    dd_pred_df = dd_pred[dd_pred["fund_code"].isin(port_codes)].copy()
    dd_pred_df["month"] = pd.to_datetime(dd_pred_df["month"])
    # 组合级预警信号：当月触发预警的基金比例
    dd_monthly = dd_pred_df.groupby("month").apply(
        lambda g: (g["pred_proba"] >= DD_PROBA_THRESHOLD).sum() / len(g)
        if len(g) > 0 else 0).sort_index()

    schemes = {"Equal_Weight": [], "Vol_Targeting": [], "Vol_DD_Combined": []}
    positions = {"Equal_Weight": [], "Vol_Targeting": [], "Vol_DD_Combined": []}
    signals = {"Vol_Targeting": [], "Vol_DD_Combined": []}
    # 兜底命中计数：缺失信号时用了中性值，必须能被上层看见（不静默填）
    fallback_counts = {"vol_signal": 0, "dd_signal": 0}
    # 期初在现金里（仓位 0）：首月建仓要付一次成本（旧版从 1.0 起算，少收首月费用）
    prev_pos = {"Equal_Weight": 0.0, "Vol_Targeting": 0.0, "Vol_DD_Combined": 0.0}
    rf_monthly = RF_ANNUAL / 12.0

    for m in oos_months:
        m_ts = pd.Timestamp(m)
        # 对齐到月末（resample('ME') 产生月末日期）
        if m_ts not in port_monthly_ret.index:
            continue
        base_ret = port_monthly_ret.loc[m_ts]
        if pd.isna(base_ret):
            continue

        # 信号一律**严格早于** m：月份 m 的收益只有走完才知道，
        # 用 index<=m（含月末 m）的信号定 m 的仓位是同月前视。
        # HAR/回撤预测的目标本就是 t+1 月，取 t<m 恰好对齐到"对 m 月的预测"。
        pred_v = last_signal_before(vol_pred_monthly, m_ts)
        if pred_v is None:
            pred_v = FALLBACK_PRED_VOL
            fallback_counts["vol_signal"] += 1
        dd_frac = last_signal_before(dd_monthly, m_ts)
        if dd_frac is None:
            dd_frac = FALLBACK_DD_FRAC
            fallback_counts["dd_signal"] += 1

        # 各方案仓位
        eq_pos = 1.0
        vt_pos = float(np.clip(target_vol / pred_v, 0.05, 1.0)) if pred_v > 0 else FALLBACK_PE_EQUITY
        dd_pos = DD_REDUCTION_FACTOR if dd_frac > DD_TRIGGER_FRAC else 1.0
        combined_pos = vt_pos * dd_pos

        cur_pos = {
            "Equal_Weight": eq_pos,
            "Vol_Targeting": vt_pos,
            "Vol_DD_Combined": combined_pos,
        }

        for name, pos in cur_pos.items():
            turnover = abs(pos - prev_pos[name])
            cost = turnover * ONE_WAY_COST
            # 未投资部分（1−pos）放货基/短债，按无风险利率计息
            net_ret = base_ret * pos + (1.0 - pos) * rf_monthly - cost
            schemes[name].append((m_ts, net_ret))
            positions[name].append((m_ts, pos))
            prev_pos[name] = pos

        signals["Vol_Targeting"].append((m_ts, pred_v, dd_frac))
        signals["Vol_DD_Combined"].append((m_ts, pred_v, dd_frac, combined_pos))

    metrics = {}
    for name, rets in schemes.items():
        if not rets:
            continue
        s = pd.Series(dict(rets))
        m = _portfolio_metrics(s, rf_annual=RF_ANNUAL)
        # 声明兜底命中次数：这几个月的仓位/减仓判断不是信号算出来的，是缺失时的中性兜底
        m["fallback_vol_signal_months"] = int(fallback_counts["vol_signal"])
        m["fallback_dd_signal_months"] = int(fallback_counts["dd_signal"])
        m["avg_position"] = float(np.mean([p[1] for p in positions[name]]))
        m["avg_turnover"] = float(np.mean([
            abs(positions[name][i][1] - (positions[name][i-1][1] if i > 0 else 0.0))
            for i in range(len(positions[name]))]))
        metrics[name] = m

    return metrics, signals, port_codes


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


def generate_report(metrics, signals, port_codes, data_info, dd_threshold):
    lines = []
    lines.append("# 组合模拟 — Vol-Targeting + 回撤预警 减仓\n")
    lines.append(f"> 生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    lines.append("## 一、运行说明\n")
    lines.append("```bash")
    lines.append(f"python src/analysis/portfolio_simulation.py --dd-threshold {dd_threshold}")
    lines.append("```\n")
    lines.append("**业务问题**: vol-targeting 能否降回撤？在它之上叠加回撤预警，"
                 "能否进一步降低回撤（代价是多少收益）？\n")
    lines.append("### 策略定义\n")
    lines.append("| 策略 | 仓位公式 | 说明 |")
    lines.append("|:--|:--|:--|")
    lines.append("| Equal_Weight | 1.0 | 等权基准 |")
    lines.append(f"| Vol_Targeting | clip(15% / pred_vol, 0.05, 1.0) | HAR-RV 预测 vol |")
    lines.append(f"| Vol_DD_Combined | Vol_Targeting × (0.5 if DD预警 else 1.0) | "
                 f"组合内>{DD_TRIGGER_FRAC*100:.0f}% 基金触发预警时减半 |")
    lines.append("")
    lines.append(f"**回撤预警**: XGBoost @ {dd_threshold}% 阈值"
                 f"（AUC/p 值见 `drawdown_warning_report.md`，此处不引用旧数值）\n")

    # 数据
    lines.append("## 二、数据概况\n")
    lines.append(f"- 组合基金: {len(port_codes)} 只（按 **OOS 起点之前** 的净值覆盖度挑选）")
    lines.append(f"- 净值区间: {data_info['nav_start']} ~ {data_info['nav_end']}")
    lines.append(f"- OOS: {OOS_START[:7]} ~ {data_info['last_month']}")
    lines.append(f"- 月度再平衡, 单边成本 {ONE_WAY_COST*100:.1f}%, "
                 f"未投资部分按无风险利率 {RF_ANNUAL*100:.1f}% 计息")
    lines.append("- 仓位信号一律取**严格早于当月**的最近一期（避免同月前视）\n")

    # 组合指标
    lines.append(f"## 三、组合指标对比（OOS {OOS_START[:7]} ~ {data_info['last_month']}）\n")
    lines.append("| 策略 | 年化收益 | 年化波动 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 换手 |")
    lines.append("|:--|:--|:--|:--|:--|:--|:--|:--|")
    for name in ["Equal_Weight", "Vol_Targeting", "Vol_DD_Combined"]:
        if name not in metrics:
            continue
        m = metrics[name]
        lines.append(
            f"| {name} | {fmt_pct(m.get('annual_return'))} | {fmt_pct(m.get('annual_volatility'))} "
            f"| {fmt_pct(m.get('max_drawdown'))} | {fmt_num(m.get('sharpe'))} "
            f"| {fmt_num(m.get('calmar'))} | {fmt_pct(m.get('avg_position'))} "
            f"| {fmt_pct(m.get('avg_turnover'))} |"
        )
    lines.append("")

    # 增量分析
    lines.append("## 四、增量分析（规则6 没被挑战过的东西）\n")
    eq = metrics.get("Equal_Weight", {})
    vt = metrics.get("Vol_Targeting", {})
    cb = metrics.get("Vol_DD_Combined", {})

    if vt and cb:
        dd_delta = (cb.get("max_drawdown", 0) - vt.get("max_drawdown", 0)) * 100
        ret_delta = (cb.get("annual_return", 0) - vt.get("annual_return", 0)) * 100
        sharpe_delta = cb.get("sharpe", 0) - vt.get("sharpe", 0)
        lines.append("### Vol_DD_Combined vs Vol_Targeting\n")
        lines.append(f"- 回撤变化: {dd_delta:+.2f}pp ({fmt_pct(vt.get('max_drawdown'))} → {fmt_pct(cb.get('max_drawdown'))})")
        lines.append(f"- 收益变化: {ret_delta:+.2f}pp ({fmt_pct(vt.get('annual_return'))} → {fmt_pct(cb.get('annual_return'))})")
        lines.append(f"- 夏普变化: {sharpe_delta:+.4f} ({fmt_num(vt.get('sharpe'))} → {fmt_num(cb.get('sharpe'))})")
        lines.append("")
        if dd_delta < -1e-9 and ret_delta >= 0:
            lines.append("**判定**: 回撤预警叠加有效——降回撤且不损收益。\n")
        elif dd_delta < -1e-9 and ret_delta < 0:
            lines.append("**判定**: 回撤预警叠加降回撤但牺牲收益——需权衡回撤降低 vs 收益损失。\n")
        elif abs(dd_delta) <= 1e-9:
            lines.append("**判定**: 回撤**无变化**（两方案最大回撤相同）——"
                         "该信号在此期间没有改变回撤，"
                         f"但收益变化 {ret_delta:+.2f}pp、夏普变化 {sharpe_delta:+.4f}，"
                         "不能据此说预警有效或有害。\n")
        else:
            lines.append("**判定**: 回撤预警叠加无效——回撤反而上升。\n")

    # 信号触发统计
    lines.append("## 五、信号触发统计\n")
    sig_combined = signals.get("Vol_DD_Combined", [])
    if sig_combined:
        n_months = len(sig_combined)
        n_triggered = sum(1 for s in sig_combined if s[2] > DD_TRIGGER_FRAC)
        trigger_rate = n_triggered / n_months if n_months > 0 else 0
        avg_dd_frac = np.mean([s[2] for s in sig_combined])
        avg_pred_vol = np.mean([s[1] for s in sig_combined])
        lines.append(f"- OOS 月数: {n_months}")
        lines.append(f"- 预警触发月数: {n_triggered} ({fmt_pct(trigger_rate)})")
        lines.append(f"- 平均预警基金比例: {fmt_pct(avg_dd_frac)}")
        lines.append(f"- 平均组合预测 vol: {fmt_pct(avg_pred_vol)}")
        lines.append("")

    # 月度仓位序列
    lines.append("## 六、月度仓位对比\n")
    lines.append("| 月份 | 预测vol | 预警基金比例 | Vol_Target 仓位 | Combined 仓位 |")
    lines.append("|:--|:--|:--|:--|:--|")
    for s in sig_combined:
        m_ts, pred_v, dd_frac, comb_pos = s
        vt_pos = float(np.clip(TARGET_VOL / pred_v, 0.05, 1.0)) if pred_v > 0 else FALLBACK_PE_EQUITY
        trigger = "是" if dd_frac > DD_TRIGGER_FRAC else "否"
        lines.append(
            f"| {m_ts.strftime('%Y-%m')} | {fmt_pct(pred_v)} | {fmt_pct(dd_frac)} {trigger} "
            f"| {fmt_pct(vt_pos)} | {fmt_pct(comb_pos)} |"
        )
    lines.append("")

    # 结论
    lines.append("## 七、结论\n")
    if vt and cb:
        dd_improvement = (vt.get("max_drawdown", 0) - cb.get("max_drawdown", 0)) * 100
        ret_loss = (vt.get("annual_return", 0) - cb.get("annual_return", 0)) * 100
        lines.append(f"1. **回撤降低**: {dd_improvement:.2f}pp "
                     f"({fmt_pct(vt.get('max_drawdown'))} → {fmt_pct(cb.get('max_drawdown'))})")
        lines.append(f"2. **收益变化**（= Vol_Targeting − Combined，负值表示叠加后收益更高）: "
                     f"{ret_loss:+.2f}pp "
                     f"({fmt_pct(vt.get('annual_return'))} → {fmt_pct(cb.get('annual_return'))})")
        lines.append(f"3. **夏普变化**: {cb.get('sharpe', 0) - vt.get('sharpe', 0):+.4f}")
        if dd_improvement > 1e-9 and ret_loss < dd_improvement:
            lines.append(f"4. **业务判定**: 回撤预警有正增量——回撤降 {dd_improvement:.2f}pp > 收益损 {ret_loss:.2f}pp")
        elif dd_improvement > 1e-9:
            lines.append(f"4. **业务判定**: 回撤降 {dd_improvement:.2f}pp 但收益损 {ret_loss:.2f}pp，需风险偏好判断")
        elif abs(dd_improvement) <= 1e-9:
            lines.append("4. **业务判定**: 两方案**最大回撤完全相同**——"
                         "回撤预警在此期间没有改变回撤幅度，"
                         f"收益变化 {ret_loss:+.2f}pp、夏普变化 "
                         f"{cb.get('sharpe', 0) - vt.get('sharpe', 0):+.4f}。"
                         "结论只能限于「未见降回撤效果」，不能说有害。")
        else:
            lines.append(f"4. **业务判定**: 回撤预警无增量——回撤反升 {abs(dd_improvement):.2f}pp")
    lines.append("")

    lines.append("## 八、已知局限与口径说明\n")
    lines.append("- **同月前视已修**：仓位信号改为取**严格早于当月**的最近一期预测。"
                 "旧版用 `index <= 月末`（含当月月末）的信号去定当月仓位，"
                 "而 HAR/回撤预测的目标本就是下一个月 —— 等于用期末信息交易整月收益。")
    lines.append("- **期限错配已修**：取 t < m 的那一期，恰好就是「对 m 月的预测」。")
    lines.append("- **现金计息**：未投资部分按无风险利率计息；期初从现金（仓位 0）出发，"
                 "首月如实收取建仓成本。")
    lines.append("- **选样**：组合基金按 OOS 起点之前的覆盖度挑，但仍受"
                 "「当前存续基金」这一层幸存者偏差影响。")
    lines.append("- **回撤标签**：月内最大回撤在**复权净值**（累计净值，除息日不下挫）上计算；"
                 "旧版直接用单位净值，分红会污染标签。")
    lines.append("- **阈值的选择**：回撤阈值来自对 OOS 的敏感性搜索，属于样本内选型 —— "
                 "本报告的绝对 AUC 因此偏乐观。")
    # 兜底声明：这几个月的仓位不是信号算出来的
    _fb_vol = _fb_dd = _n_months = 0
    if metrics:
        _any = next(iter(metrics.values()))
        _fb_vol = int(_any.get("fallback_vol_signal_months", 0))
        _fb_dd = int(_any.get("fallback_dd_signal_months", 0))
        _n_months = int(_any.get("months", 0))
    lines.append(f"- **缺失信号的兜底（不静默）**：OOS 共 {_n_months} 个月，其中"
                 f"**波动率信号缺失 {_fb_vol} 个月**（按 {FALLBACK_PRED_VOL*100:.0f}% 年化兜底）、"
                 f"**回撤信号缺失 {_fb_dd} 个月**（按「无触发」兜底）。"
                 "这几个月的仓位不是模型算出来的，报告结论不应覆盖它们以外的月份。\n")

    lines.append("---")
    lines.append("*本报告由 `src/analysis/portfolio_simulation.py` 自动生成。"
                 "所有结论基于历史回测，不构成投资建议。*\n")

    report_path = DOCS_DIR / "portfolio_simulation_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[报告] 已生成: {report_path}")
    return report_path


# =====================================================================
# 主流程
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="组合模拟: Vol-Targeting + 回撤预警")
    parser.add_argument("--db", default=str(DB_PATH), help="数据库路径")
    parser.add_argument("--max-funds", type=int, default=200, help="基金数量上限")
    parser.add_argument("--dd-threshold", type=float, default=8, help="回撤预警阈值(%)")
    args = parser.parse_args()

    print("=" * 70)
    print(f"组合模拟: Vol-Targeting + 回撤预警 (阈值={args.dd_threshold}%)")
    print("=" * 70)

    # 1. 数据加载
    print("\n[1/4] 数据加载...")
    universe = load_fund_universe(args.db, max_funds=args.max_funds)
    if universe.empty:
        print("[错误] 未选出任何基金")
        return
    fund_codes = universe["fund_code"].tolist()
    nav_wide = load_fund_navs(args.db, fund_codes)
    acc_wide = load_fund_navs(args.db, fund_codes, column="acc_nav")
    index_df = load_index_data(args.db, "000300")

    # 2. 收益率 + 月度 + 特征
    print("\n[2/4] 收益率 + 特征工程...")
    ret_wide = compute_daily_returns(nav_wide)
    me_arr = month_end_dates(ret_wide.index)
    print(f"  月度时点: {len(me_arr)} 个月 ({me_arr[0].date()} ~ {me_arr[-1].date()})")

    # 3. HAR-RV 预测 + 回撤预警预测
    print("\n[3/4] 模型预测...")
    print("  [3a] HAR-RV 波动率预测...")
    t0 = time.time()
    rv = monthly_realized_vol(ret_wide, me_arr)
    features_df = compute_features(ret_wide, universe, index_df, me_arr)
    panel = build_panel(features_df, rv)
    panel = add_har_features(panel, rv)
    vol_pred = walk_forward_har(panel)
    print(f"  HAR-RV 完成, 预测 {len(vol_pred)} 条, 耗时 {time.time()-t0:.1f}s")

    print("  [3b] XGBoost 回撤预警...")
    t0 = time.time()
    dd_pred = get_drawdown_predictions(
        nav_wide, ret_wide, me_arr, universe, index_df, args.dd_threshold,
        acc_wide=acc_wide)
    print(f"  回撤预警完成, 预测 {len(dd_pred)} 条, 耗时 {time.time()-t0:.1f}s")

    # 4. 组合模拟
    print("\n[4/4] 组合模拟...")
    data_info = {
        "nav_start": nav_wide.index[0].strftime("%Y-%m-%d"),
        "nav_end": nav_wide.index[-1].strftime("%Y-%m-%d"),
        "last_month": max(me_arr).strftime("%Y-%m"),
    }
    metrics, signals, port_codes = combined_portfolio_simulation(
        ret_wide, vol_pred, dd_pred, index_df)

    # 打印结果
    print("\n" + "=" * 70)
    print(f"组合指标对比 (OOS {OOS_START[:7]} ~ {data_info['last_month']}):")
    print(f"{'策略':<20} {'年化收益':>10} {'年化波动':>10} {'最大回撤':>10} {'夏普':>8} {'Calmar':>8}")
    for name in ["Equal_Weight", "Vol_Targeting", "Vol_DD_Combined"]:
        if name not in metrics:
            continue
        m = metrics[name]
        print(f"{name:<20} {fmt_pct(m.get('annual_return')):>10} "
              f"{fmt_pct(m.get('annual_volatility')):>10} "
              f"{fmt_pct(m.get('max_drawdown')):>10} "
              f"{fmt_num(m.get('sharpe')):>8} {fmt_num(m.get('calmar')):>8}")
    print("=" * 70)

    # 5. 报告
    print("\n[报告] 生成...")
    report_path = generate_report(metrics, signals, port_codes, data_info, args.dd_threshold)

    # 保存 CSV
    rows = []
    for name, m in metrics.items():
        row = {"scheme": name}
        row.update(m)
        rows.append(row)
    if rows:
        pd.DataFrame(rows).to_csv(
            PORTFOLIO_RESULT_DIR / "portfolio_metrics.csv",
            index=False, encoding="utf-8-sig")
        print(f"  [CSV] {PORTFOLIO_RESULT_DIR}/portfolio_metrics.csv")

    # 月度信号 CSV
    sig_rows = []
    for s in signals.get("Vol_DD_Combined", []):
        m_ts, pred_v, dd_frac, comb_pos = s
        vt_pos = float(np.clip(TARGET_VOL / pred_v, 0.05, 1.0)) if pred_v > 0 else FALLBACK_PE_EQUITY
        sig_rows.append({
            "month": m_ts, "pred_vol": pred_v, "dd_warning_frac": dd_frac,
            "vol_target_pos": vt_pos, "combined_pos": comb_pos,
            "dd_triggered": int(dd_frac > DD_TRIGGER_FRAC),
        })
    if sig_rows:
        pd.DataFrame(sig_rows).to_csv(
            PORTFOLIO_RESULT_DIR / "monthly_signals.csv",
            index=False, encoding="utf-8-sig")
        print(f"  [CSV] {PORTFOLIO_RESULT_DIR}/monthly_signals.csv")

    print(f"\n[完成] 报告: {report_path}")


if __name__ == "__main__":
    main()
