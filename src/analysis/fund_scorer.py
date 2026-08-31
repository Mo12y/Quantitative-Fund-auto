"""
基金质量筛选器 v3.0: 从"选冠军"改为"排除不合格的"。

v2→v3 核心变化（基于第一性原理审查结论）:
- 不再输出"Top10排名"——假装能预测未来表现是不诚实的
- 改为输出"质量筛选池"——只告诉你哪些基金没有明显的坑
- 每个维度不是"打分"而是"通过/警告/不通过"三态
- 加入风险标签: 🟢稳健 / 🟡注意 / 🔴高风险
- 动量过高不吹捧、反而警告——因为高动量=追涨风险

设计原则:
  不预测"哪只最好"（不可预测），
  只排除"哪些有坑"（可判断），
  剩下的你自己选。
"""

import pandas as pd
import numpy as np
from typing import Optional
from ..data.database import Database


class FundScreener:
    """基金质量筛选器 v3.0"""

    # 质量门槛
    THRESHOLDS = {
        "min_age_months": 12,       # 成立至少1年
        "min_size_yi": 0.5,         # 规模至少5千万
        "max_size_yi": 200,         # 规模不超过200亿（太大不灵活）
        "max_total_fee": 2.0,       # 总费率(管理+托管)不超过2.0%
        "warn_total_fee": 1.5,      # 总费率超过1.5%提示注意
        "max_drawdown_1y": 35,      # 近1年最大回撤不超过35%
        "min_manager_years": 2,     # 基金经理从业至少2年
        "momentum_warning": 40,     # 近3月涨幅>40%→追涨警告
    }

    def __init__(self, db: Database, risk_free_rate: float = 0.02):
        self.db = db
        self.risk_free_rate = risk_free_rate

    # =================================================================
    # 主接口
    # =================================================================

    def screen_funds(
        self,
        fund_types: list = None,
        max_results: int = 30,
    ) -> pd.DataFrame:
        """
        质量筛选: 排除不合格基金，对剩余基金贴风险标签。

        Args:
            fund_types: 要筛选的基金类型
            max_results: 最多返回多少只

        Returns:
            DataFrame with columns:
            fund_code, fund_name, fund_type, mgt_fee, fund_size,
            risk_label (🟢稳健/🟡注意/🔴高风险),
            risk_reasons (list of warning reasons),
            quality_checks (dict of pass/warn/fail per dimension),
            metrics (dict of actual values)
        """
        if fund_types is None:
            fund_types = ["股票型", "混合型", "指数型"]

        all_funds = self.db.get_all_funds()
        fund_info_map = {f["fund_code"]: f for f in all_funds}
        nav_funds = self._get_funds_with_nav()

        results = []
        for code in nav_funds:
            info = fund_info_map.get(code)
            if info is None:
                continue
            ftype = info.get("fund_type", "")
            if not any(ft in ftype for ft in fund_types):
                continue

            result = self._screen_single_fund(code, info)
            if result is None:
                continue

            # 排除标签为"不合格"的基金
            if result["risk_label"] == "不合格":
                continue

            results.append({
                "fund_code": code,
                "fund_name": info.get("fund_name", ""),
                "fund_type": ftype,
                "mgt_fee": info.get("mgt_fee", 0) or 0,
                "fund_size": info.get("fund_size", 0) or 0,
                "risk_label": result["risk_label"],
                "risk_reasons": result["risk_reasons"],
                "quality_checks": result["quality_checks"],
                "metrics": result["metrics"],
            })

        df = pd.DataFrame(results)
        if df.empty:
            return df

        # 按风险等级排序：稳健 > 注意 > 高风险
        risk_order = {"🟢 稳健": 0, "🟡 注意": 1, "🔴 高风险": 2}
        df["_risk_order"] = df["risk_label"].map(risk_order).fillna(3)
        df = df.sort_values(["_risk_order", "mgt_fee"]).drop(columns=["_risk_order"])
        df = df.reset_index(drop=True)

        return df.head(max_results)

    def _screen_single_fund(self, fund_code: str, fund_info: dict) -> Optional[dict]:
        """
        对单只基金进行质量检查。

        Returns:
            dict with risk_label, risk_reasons, quality_checks, metrics
            或 None（净值数据不足）
        """
        nav_records = self.db.get_fund_nav(fund_code)
        if len(nav_records) < 60:
            return None

        df_nav = pd.DataFrame(nav_records)
        df_nav["nav_date"] = pd.to_datetime(df_nav["nav_date"])
        df_nav = df_nav.sort_values("nav_date")

        # 计算指标
        nav_series = df_nav.set_index("nav_date")["unit_nav"]
        metrics = {}
        checks = {}
        warnings = []

        # ---- 检查1: 成立时间 ----
        est = fund_info.get("establish_date", "")
        age_ok = True
        if est:
            try:
                e = pd.to_datetime(est)
                months = (pd.Timestamp.now() - e).days / 30
                metrics["age_months"] = round(months, 0)
                age_ok = months >= self.THRESHOLDS["min_age_months"]
                checks["成立时间"] = "✅ 通过" if age_ok else f"❌ 仅{months:.0f}个月"
            except Exception:
                checks["成立时间"] = "⚠️ 未知"
        else:
            checks["成立时间"] = "⚠️ 无数据"

        # ---- 检查2: 规模 ----
        size = float(fund_info.get("fund_size") or 0)
        metrics["fund_size_yi"] = size
        if size == 0:
            checks["基金规模"] = "✅ 无数据(跳过检查)"
        elif size < self.THRESHOLDS["min_size_yi"]:
            checks["基金规模"] = f"❌ 仅{size:.2f}亿(清盘风险)"
            warnings.append(f"规模仅{size:.2f}亿，有清盘风险")
        elif size > self.THRESHOLDS["max_size_yi"]:
            checks["基金规模"] = f"⚠️ {size:.1f}亿(偏大)"
        else:
            checks["基金规模"] = f"✅ {size:.1f}亿"

        # ---- 检查3: 费率 ----
        mgt_fee = float(fund_info.get("mgt_fee") or 0)
        cust_fee = float(fund_info.get("custodian_fee") or 0)
        total_fee = mgt_fee + cust_fee
        metrics["total_fee"] = total_fee
        # 如果数据库没有费率数据（=0），不妄加判断
        if mgt_fee == 0 and cust_fee == 0:
            checks["费率"] = "⊘ 无数据"
        elif total_fee > self.THRESHOLDS["max_total_fee"]:
            checks["费率"] = f"❌ {total_fee:.2f}%(过高)"
            warnings.append(f"总费率{total_fee:.2f}%过高，严重侵蚀长期收益")
        elif total_fee > self.THRESHOLDS["warn_total_fee"]:
            checks["费率"] = f"⚠️ {total_fee:.2f}%(偏高)"
        else:
            checks["费率"] = f"✅ {total_fee:.2f}%"

        # ---- 检查4: 回撤 ----
        if len(nav_series) >= 60:
            recent = nav_series.iloc[-252:] if len(nav_series) >= 252 else nav_series
            peak = recent.iloc[0]
            max_dd = 0
            for p in recent.values:
                if p > peak:
                    peak = p
                dd = (peak - p) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            metrics["max_drawdown_1y"] = round(max_dd, 1)
            if max_dd > self.THRESHOLDS["max_drawdown_1y"]:
                checks["回撤控制"] = f"❌ {max_dd:.0f}%(过大)"
                warnings.append(f"近1年最大回撤{max_dd:.0f}%，超过{self.THRESHOLDS['max_drawdown_1y']}%阈值")
            elif max_dd > 25:
                checks["回撤控制"] = f"⚠️ {max_dd:.0f}%(偏高)"
            else:
                checks["回撤控制"] = f"✅ {max_dd:.0f}%"
        else:
            checks["回撤控制"] = "⚠️ 数据不足"

        # ---- 检查5: 动量(追涨风险) ----
        if len(nav_series) >= 63:
            mom_3m = (nav_series.iloc[-1] / nav_series.iloc[-63] - 1) * 100
            metrics["momentum_3m"] = round(mom_3m, 1)
            if mom_3m > self.THRESHOLDS["momentum_warning"]:
                checks["追涨风险"] = f"🔴 近3月涨{mom_3m:.0f}%(追涨!)"
                warnings.append(f"近3月涨幅{mom_3m:.0f}%过高，此时买入有追涨风险")
            elif mom_3m > 25:
                checks["追涨风险"] = f"⚠️ 近3月涨{mom_3m:.0f}%"
            elif mom_3m < -20:
                checks["追涨风险"] = f"💡 近3月跌{abs(mom_3m):.0f}%(可能超跌)"
            else:
                checks["追涨风险"] = f"✅ 近3月{mom_3m:+.0f}%"
        else:
            checks["追涨风险"] = "⚠️ 数据不足"

        # ---- 检查6: 夏普比率 ----
        if len(nav_records) >= 60:
            daily_returns = nav_series.pct_change().dropna().values
            if len(daily_returns) >= 20:
                ann_ret = np.mean(daily_returns) * 252
                ann_vol = np.std(daily_returns, ddof=1) * np.sqrt(252)
                sharpe = (ann_ret - self.risk_free_rate) / (ann_vol) if ann_vol > 0 else 0
                metrics["sharpe"] = round(sharpe, 2)
                metrics["ann_vol"] = round(ann_vol * 100, 1)
                if sharpe < 0:
                    checks["风险调整收益"] = "❌ 夏普为负"
                    warnings.append("夏普比率为负，承担风险但没有获得相应回报")
                elif sharpe < 0.3:
                    checks["风险调整收益"] = "⚠️ 夏普偏低"
                else:
                    checks["风险调整收益"] = f"✅ {sharpe:.2f}"
            else:
                checks["风险调整收益"] = "⚠️ 数据不足"

        # ---- 判定风险标签 ----
        # "⊘ 无数据" 和 "✅ 无数据(跳过检查)" 不计入警告
        fail_count = sum(1 for v in checks.values() if v.startswith("❌"))
        warn_count = sum(1 for v in checks.values()
                        if (v.startswith("⚠️") or v.startswith("🔴") or v.startswith("💡"))
                        and not v.startswith("⚠️ 无数据"))

        if fail_count >= 2:
            risk_label = "不合格"
        elif fail_count >= 1:
            risk_label = "🔴 高风险"
        elif warn_count >= 2:
            risk_label = "🟡 注意"
        elif warn_count >= 1:
            risk_label = "🟡 注意"
        else:
            risk_label = "🟢 稳健"

        return {
            "risk_label": risk_label,
            "risk_reasons": warnings,
            "quality_checks": checks,
            "metrics": metrics,
        }

    def _get_funds_with_nav(self) -> set:
        """获取有净值数据的基金代码集合"""
        return self.db.get_all_fund_codes()

    def get_pool_summary(self, df: pd.DataFrame) -> dict:
        """获取筛选池的统计摘要"""
        if df.empty:
            return {"total": 0, "by_risk": {}, "avg_fee": 0}

        by_risk = df["risk_label"].value_counts().to_dict()
        avg_fee = df["mgt_fee"].mean()

        return {
            "total": len(df),
            "by_risk": by_risk,
            "avg_fee": round(avg_fee, 2),
        }


# ---- 向后兼容别名 ----
FundScorer = FundScreener
