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


    # ------------------------------------------------------------------
    # 单项质量检查（提取自 _screen_single_fund，每项返回 (check_text, warning, *metrics)）
    # ------------------------------------------------------------------

    def _check_age(self, fund_info: dict) -> tuple:
        """成立时间检查 → (check_text, warning)"""
        est = fund_info.get("establish_date", "")
        if not est:
            return "⚠️ 无数据", None
        try:
            e = pd.to_datetime(est)
            months = (pd.Timestamp.now() - e).days / 30
            if months >= self.THRESHOLDS["min_age_months"]:
                return "✅ 通过", None
            return f"❌ 仅{months:.0f}个月", f"成立仅{months:.0f}个月，不足{self.THRESHOLDS['min_age_months']}个月"
        except Exception:
            return "⚠️ 未知", None

    def _check_size(self, fund_info: dict) -> tuple:
        """规模检查 → (check_text, warning, size_yi)"""
        size = float(fund_info.get("fund_size") or 0)
        if size == 0:
            return "✅ 无数据(跳过检查)", None, size
        if size < self.THRESHOLDS["min_size_yi"]:
            return f"❌ 仅{size:.2f}亿(清盘风险)", f"规模仅{size:.2f}亿，有清盘风险", size
        if size > self.THRESHOLDS["max_size_yi"]:
            return f"⚠️ {size:.1f}亿(偏大)", None, size
        return f"✅ {size:.1f}亿", None, size

    def _check_fee(self, fund_info: dict) -> tuple:
        """费率检查 → (check_text, warning, total_fee)"""
        mgt = float(fund_info.get("mgt_fee") or 0)
        cust = float(fund_info.get("custodian_fee") or 0)
        total = mgt + cust
        if mgt == 0 and cust == 0:
            return "⊘ 无数据", None, total
        if total > self.THRESHOLDS["max_total_fee"]:
            return f"❌ {total:.2f}%(过高)", f"总费率{total:.2f}%过高，严重侵蚀长期收益", total
        if total > self.THRESHOLDS["warn_total_fee"]:
            return f"⚠️ {total:.2f}%(偏高)", None, total
        return f"✅ {total:.2f}%", None, total

    def _check_drawdown(self, nav_series) -> tuple:
        """近1年最大回撤检查 → (check_text, warning, max_dd)"""
        if len(nav_series) < 60:
            return "⚠️ 数据不足", None, None
        recent = nav_series.iloc[-252:] if len(nav_series) >= 252 else nav_series
        peak = recent.iloc[0]
        max_dd = 0
        for p in recent.values:
            if p > peak:
                peak = p
            dd = (peak - p) / peak * 100
            if dd > max_dd:
                max_dd = dd
        if max_dd > self.THRESHOLDS["max_drawdown_1y"]:
            return f"❌ {max_dd:.0f}%(过大)", f"近1年最大回撤{max_dd:.0f}%，超过{self.THRESHOLDS['max_drawdown_1y']}%阈值", round(max_dd, 1)
        if max_dd > 25:
            return f"⚠️ {max_dd:.0f}%(偏高)", None, round(max_dd, 1)
        return f"✅ {max_dd:.0f}%", None, round(max_dd, 1)

    def _check_momentum(self, nav_series) -> tuple:
        """追涨风险检查 → (check_text, warning, mom_3m)"""
        if len(nav_series) < 63:
            return "⚠️ 数据不足", None, None
        mom = (nav_series.iloc[-1] / nav_series.iloc[-63] - 1) * 100
        if mom > self.THRESHOLDS["momentum_warning"]:
            return f"🔴 近3月涨{mom:.0f}%(追涨!)", f"近3月涨幅{mom:.0f}%过高，此时买入有追涨风险", round(mom, 1)
        if mom > 25:
            return f"⚠️ 近3月涨{mom:.0f}%", None, round(mom, 1)
        if mom < -20:
            return f"💡 近3月跌{abs(mom):.0f}%(可能超跌)", None, round(mom, 1)
        return f"✅ 近3月{mom:+.0f}%", None, round(mom, 1)

    def _check_sharpe(self, nav_series) -> tuple:
        """风险调整收益检查 → (check_text, warning, sharpe, ann_vol)"""
        if len(nav_series) < 60:
            return "⚠️ 数据不足", None, None, None
        daily = nav_series.pct_change().dropna().values
        if len(daily) < 20:
            return "⚠️ 数据不足", None, None, None
        ann_ret = np.mean(daily) * 252
        ann_vol = np.std(daily, ddof=1) * np.sqrt(252)
        sharpe = (ann_ret - self.risk_free_rate) / ann_vol if ann_vol > 0 else 0
        if sharpe < 0:
            return "❌ 夏普为负", "夏普比率为负，承担风险但没有获得相应回报", round(sharpe, 2), round(ann_vol * 100, 1)
        if sharpe < 0.3:
            return "⚠️ 夏普偏低", None, round(sharpe, 2), round(ann_vol * 100, 1)
        return f"✅ {sharpe:.2f}", None, round(sharpe, 2), round(ann_vol * 100, 1)

    def _screen_single_fund(self, fund_code: str, fund_info: dict) -> Optional[dict]:
        """
        对单只基金进行质量检查（6 个维度，各维度逻辑见 _check_* 方法）。

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
        nav_series = df_nav.set_index("nav_date")["unit_nav"]

        metrics = {}
        checks = {}
        warnings = []

        # ---- 检查1: 成立时间 ----
        checks["成立时间"], w = self._check_age(fund_info)
        if w:
            warnings.append(w)

        # ---- 检查2: 规模 ----
        checks["基金规模"], w, size = self._check_size(fund_info)
        if w:
            warnings.append(w)
        metrics["fund_size_yi"] = size

        # ---- 检查3: 费率 ----
        checks["费率"], w, total_fee = self._check_fee(fund_info)
        if w:
            warnings.append(w)
        metrics["total_fee"] = total_fee

        # ---- 检查4: 回撤 ----
        checks["回撤控制"], w, max_dd = self._check_drawdown(nav_series)
        if w:
            warnings.append(w)
        if max_dd is not None:
            metrics["max_drawdown_1y"] = max_dd

        # ---- 检查5: 动量(追涨风险) ----
        checks["追涨风险"], w, mom = self._check_momentum(nav_series)
        if w:
            warnings.append(w)
        if mom is not None:
            metrics["momentum_3m"] = mom

        # ---- 检查6: 夏普比率 ----
        checks["风险调整收益"], w, sharpe, ann_vol = self._check_sharpe(nav_series)
        if w:
            warnings.append(w)
        if sharpe is not None:
            metrics["sharpe"] = sharpe
            metrics["ann_vol"] = ann_vol

        # ---- 判定风险标签 ----
        fail_count = sum(1 for v in checks.values() if v.startswith("❌"))
        warn_count = sum(
            1 for v in checks.values()
            if (v.startswith("⚠️") or v.startswith("🔴") or v.startswith("💡"))
            and not v.startswith("⚠️ 无数据")
        )

        if fail_count >= 2:
            risk_label = "不合格"
        elif fail_count >= 1:
            risk_label = "🔴 高风险"
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
