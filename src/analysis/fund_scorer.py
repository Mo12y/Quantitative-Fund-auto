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
from .nav_series import valuation_nav_series
from .risk_free import RISK_FREE_ANNUAL
from . import peer_percentile
from . import fund_fee
from . import nav_metrics

# 申购状态分类（D2）——
#   `暂停申购` / `封闭期`：**买不进去**，直接排除出推荐池；
#   `限大额`：限额度 ≠ 不能买（每天 ¥10 定投完全合规）→ 保留，但标注单日上限；
#   空串 / NULL / 无法归类的文字：**视为未知**，沿用 `_check_size` 的“无数据(跳过检查)”
#   做法，**不得静默当作可申购**。
_BLOCKED_PURCHASE_MARKERS = ("暂停申购", "封闭期")
_LIMITED_PURCHASE_MARKERS = ("限大额", "限购")


def type_bucket(fund_type: str) -> str:
    """粗分类型桶：权益（equity） / 非权益（bond）。

    批次 4.1/4.4 的归一化口径：债基与权益的回撤、动量区间天然不可比，
    综合评分的百分位归一化必须**在同一个桶内**进行，否则债基的"低回撤"
    会变成挤掉权益的免费分。historical_recommender 复用同一函数。
    """
    t = str(fund_type or "")
    return "equity" if any(kw in t for kw in ("股票", "混合", "指数", "QDII")) else "bond"


class FundScreener:
    """基金质量筛选器 v3.0"""

    # 质量门槛
    #
    # ⚠️⚠️ 改任何阈值之前先读这一段（本批次 A1 确立的设计原则）⚠️⚠️
    #   同一个数字在不同组里的含义可以差 100 倍，所以先分类：
    #
    #   ① 「绝对概念」→ 保留**绝对限值**，不随同侪分布浮动。
    #      典型：费率上限、回撤容忍度。"回撤超过 35% 就不推荐"是一句
    #      **风险偏好声明**，不是相对排名 —— 牛市里全市场回撤都小时，
    #      相对化会变成"矮子里拔将军"，把该拦的也放进来。
    #
    #   ② 「相对概念」→ 改用**组内分位**。
    #      典型：追涨（momentum_warning）。"涨得多不多"本质是相对同侪的。
    #
    #   依据（上一批次实测）：常数阈值选出的集合是 P75 的**严格超集**（重合 100%）
    #   → 换分位**不改变选出谁**；但同一个 35 在 A 组淘汰 19%、在 E 组只淘汰 0.2%
    #   —— **跨组强度完全不一致**。这才是换分位的真正价值（跨组一致性），不是"选得更准"。
    THRESHOLDS = {
        "min_age_months": 12,       # 成立至少1年
        "min_size_yi": 0.5,         # 规模至少5千万
        "max_size_yi": 200,         # 规模不超过200亿（太大不灵活）
        "max_total_fee": 2.0,       # 总费率(管理+托管)不超过2.0%
        "warn_total_fee": 1.5,      # 总费率超过1.5%提示注意
        # 近1年最大回撤不超过35% —— **绝对门槛**（见上①，不参与分位化）。
        # 但它的**实测跨组位置**极不均匀，必须知道（上一批次实测）：
        #     A 偏股混合 P81（淘汰 19%） | B 灵活配置 P86 | C 主动股票 P82
        #     D 指数股票 P94 | E 债券 P100（只淘汰 0.2%） | F QDII/其他 P96
        # → **在 D/E/F 组它几乎不设限**（E 组等于没有回撤门槛）。
        #   未来若要动它，请按组分别论证，不要一刀切。
        "max_drawdown_1y": 35,
        # 基金经理从业至少2年 —— ⚠️ 注意：本阈值**从未被任何代码使用**（死阈值）。
        # `manager_tenure` 数据已具备（67.0%），但**没有对应的检查方法**。
        # 见 docs/参照系接入执行报告.md §2（A4 实测）。
        "min_manager_years": 2,
        # 近3月涨幅>40%→追涨警告 —— 实测**已失效**（各组通过率 99.3%~100%）。
        # 本批次改为组内 P90，见 _check_momentum 与 `momentum_warning_pct`。
        "momentum_warning": 40,
    }
    # 分位化的阈值（与上面的常数不同：这些按**组内分位**取，随参照系走）
    #   momentum_warning_pct = 90 → 追涨线 = 该组 momentum_3m 的 P90
    # （本项目自定阈值，依据是**实测分布**而非文献 —— 上一批次实测常数 40 在各组
    #   位于 P99~P100，通过率 99.3%~100%，等于没有这条检查。）
    MOMENTUM_WARNING_PCT = 90

    def __init__(self, db: Database, risk_free_rate: float = RISK_FREE_ANNUAL):
        self.db = db
        # 无风险利率：单一真源（批次 4.7），全项目统一 0.02，不得各自硬编码
        self.risk_free_rate = risk_free_rate
        # 参照系分位缓存：**懒加载**（进程内只读一次盘）。
        # 没有缓存时不做任何猜测 —— 由 _momentum_threshold 返回 None，
        # 调用方如实声明"参照系未构建"（铁律 5）。
        self._peer_cache = None
        self._peer_loaded = False

    def _peer_dist(self) -> dict | None:
        if not self._peer_loaded:
            self._peer_cache = peer_percentile.load_cache()
            self._peer_loaded = True
        return self._peer_cache

    def _momentum_threshold(self, fund_type: str):
        """返回 (该组 momentum_3m 的 P90, 组名)；不可得时 (None, 组名或 None)。

        为什么不是常数：上一批次实测 `momentum_warning = 40` 在各组位于 P99~P100
        （通过率 99.3%~100%），等于没有这条检查；而各组 P90 实测是
        A 3.93 / B 2.85 / C 9.42 / D 4.29 / E 0.87 / F 0.52 —— 相差 18 倍。
        同一个 "40" 对不同组毫无意义，"追涨"必须按组内相对位置判定。
        """
        g = peer_percentile.group_of(fund_type)
        if not g:
            return None, None
        thr = peer_percentile.threshold(self._peer_dist(), g, "momentum_3m",
                                        self.MOMENTUM_WARNING_PCT)
        return thr, g

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

        # ① 先用 fund_info 过滤基金类型（完全不碰净值表）
        picked = []
        for code in nav_funds:
            info = fund_info_map.get(code)
            if info is None:
                continue
            ftype = info.get("fund_type", "") or ""
            if not any(ft in ftype for ft in fund_types):
                continue
            picked.append((code, ftype))

        # ② 一次性批量读回候选基金的净值序列（替代逐基金 get_fund_nav）
        series_map = self._load_nav_series([c for c, _ in picked])

        results = []
        for code, ftype in picked:
            pair = series_map.get(code)
            if pair is None:
                continue
            vals, dates = pair
            if vals is None or len(vals) < 60:
                continue
            info = fund_info_map[code]
            result = self._score_series(vals, info, dates)

            # 排除标签为"不合格"的基金，以及**买不进去**的（暂停申购/封闭期）
            if result["risk_label"] == "不合格" or result.get("purchase_blocked"):
                continue

            # mgt_fee 缺失保持 None（批次 4.1）：费率字段的 0 绝大多数是
            # 「没采到」而非真零费率，转成 0 会让"缺失"在排序里排在最前。
            raw_fee = info.get("mgt_fee")
            mgt_fee = float(raw_fee) if raw_fee not in (None, "", 0) else None

            results.append({
                "fund_code": code,
                "fund_name": info.get("fund_name", ""),
                "fund_type": ftype,
                "mgt_fee": mgt_fee,
                "fund_size": info.get("fund_size", 0) or 0,
                "purchase_status": result["metrics"].get("purchase_status", ""),
                "risk_label": result["risk_label"],
                "risk_reasons": result["risk_reasons"],
                "quality_checks": result["quality_checks"],
                "check_levels": result["check_levels"],
                "metrics": result["metrics"],
            })

        df = pd.DataFrame(results)
        if df.empty:
            return df

        df["mgt_fee"] = pd.to_numeric(df["mgt_fee"], errors="coerce")   # None → NaN
        # 综合评分（批次 4.1）：类型桶内归一化，让排序在同一风险等级内不再任意
        df = self._attach_quality_score(df)

        # 排序（批次 4.1 修复）：旧键 ["_risk_order", "mgt_fee"] 在真实数据里
        # 两列几乎全是常数（同标签 + 费率全缺）→ 排序退化为 SQLite 返回顺序。
        # 现在：1) 风险等级（稳健 > 注意 > 高风险）
        #       2) 综合评分 quality_score 降序（类型桶内归一化百分位）
        #       3) 管理费升序，缺失（NaN）排最后，不得当 0
        risk_order = {"🟢 稳健": 0, "🟡 注意": 1, "🔴 高风险": 2}
        df["_risk_order"] = df["risk_label"].map(risk_order).fillna(3)
        # ⚠️ 末尾必须带 **deterministic tiebreak `fund_code`**（2026-09-25 实测发现）：
        # ① 上游 `_get_funds_with_nav()` 返回的是 **set**（`get_all_fund_codes` 用集合推导），
        #    迭代顺序随 `PYTHONHASHSEED` 变化 —— 实测同一份数据、不同进程给出的前 10 名尾部完全不同
        #    （seed=1: …020215,006961,006962 ／ seed=12345: …006961,020215,009324）；
        # ② `sort_values` 默认 quicksort **不稳定**，平局的相对顺序本就不保证。
        # 后果是"调仓顾问建议买哪只"会随运行变 —— 违反本项目「结论可复现」。加上 fund_code 后
        # 排序成为**全序**，与输入顺序无关，结果稳定。
        df = df.sort_values(["_risk_order", "quality_score", "mgt_fee", "fund_code"],
                            ascending=[True, False, True, True], na_position="last",
                            kind="mergesort")
        df = df.drop(columns=["_risk_order"]).reset_index(drop=True)

        return df.head(max_results)

    def _attach_quality_score(self, df: pd.DataFrame) -> pd.DataFrame:
        """给筛选池挂**综合评分**（0-100，批次 4.1）。

        三个子项按**类型桶内百分位**归一化（批次 4.4 的口径）：
          - 风险调整收益（夏普）45%：桶内百分位，越高越好
          - 回撤控制 30%：近1年最大回撤取负后的桶内百分位（回撤越小越好）
          - 费率 25%：管理费桶内百分位（越低越好）
        任一指标缺失按 0.5 中性处理 —— **缺数据不奖励也不惩罚**（批次 2.2 约定）。
        评分数值本身只用于同风险等级内部的排序，不代表绝对质量。
        """
        if df.empty:
            df["quality_score"] = pd.Series(dtype=float)
            return df
        df = df.copy()
        buckets = df["fund_type"].map(type_bucket)
        metrics = df["metrics"].apply(lambda m: m or {})
        sharpe = pd.to_numeric(metrics.map(lambda m: m.get("sharpe")), errors="coerce")
        dd = pd.to_numeric(metrics.map(lambda m: m.get("max_drawdown_1y")), errors="coerce")
        fee = pd.to_numeric(df["mgt_fee"], errors="coerce")

        def _pct(s, sign=1.0):
            # 桶内百分位；NaN（缺失）→ 0.5 中性
            return (sign * s).groupby(buckets).rank(pct=True).fillna(0.5)

        df["quality_score"] = (
            100 * (0.45 * _pct(sharpe) + 0.30 * _pct(dd, sign=-1.0) + 0.25 * _pct(fee, sign=-1.0))
        ).round(1)
        return df


    # ------------------------------------------------------------------
    # 单项质量检查（提取自 _screen_single_fund）
    #
    # 返回值约定（批次 4.8 结构化）：`(level, check_text, warning, *metrics)`，
    #   level ∈ "pass" | "warn" | "fail" | "info" | "unknown"。
    # 业务判定**只看 level**，不看 emoji 前缀 —— 展示层的符号改了不能
    # 静默改变风险分级。`check_text` 只负责给人看。
    # ------------------------------------------------------------------

    def _check_age(self, fund_info: dict) -> tuple:
        """成立时间检查 → (level, check_text, warning)"""
        est = fund_info.get("establish_date", "")
        if not est:
            return "unknown", "⚠️ 无数据", None
        try:
            e = pd.to_datetime(est)
            months = (pd.Timestamp.now() - e).days / 30
            if months >= self.THRESHOLDS["min_age_months"]:
                return "pass", "✅ 通过", None
            return "fail", f"❌ 仅{months:.0f}个月", f"成立仅{months:.0f}个月，不足{self.THRESHOLDS['min_age_months']}个月"
        except Exception:
            return "unknown", "⚠️ 未知", None

    def _check_size(self, fund_info: dict) -> tuple:
        """规模检查 → (level, check_text, warning, size_yi)"""
        size = float(fund_info.get("fund_size") or 0)
        if size == 0:
            return "unknown", "✅ 无数据(跳过检查)", None, size
        if size < self.THRESHOLDS["min_size_yi"]:
            return "fail", f"❌ 仅{size:.2f}亿(清盘风险)", f"规模仅{size:.2f}亿，有清盘风险", size
        if size > self.THRESHOLDS["max_size_yi"]:
            return "warn", f"⚠️ {size:.1f}亿(偏大)", None, size
        return "pass", f"✅ {size:.1f}亿", None, size

    def _check_fee(self, fund_info: dict) -> tuple:
        """运作费率检查（**TER**）→ (level, check_text, warning, ter)

        口径（2026-09-22 改，批次 A2）：
        · 指标 = **TER = 管理费 + 托管费 + 销售服务费**（晨星 Total Expense Ratio）。
          文献依据：Morningstar 2016（Russel Kinnel）在测过的**所有变量里，费率对后续
          业绩的预测力最强**；2025 复现（20 年）呈"最便宜→最贵"近乎完美阶梯。**晨星中国
          明确批评**"只看管理费+托管费"的披露方式 → 所以方向是**更全**，不是"仅管理费"。
        · 判定 = **组内分位**（同类比较）。管理费本身按类型分层（权益 1.2% / 指数 0.15% /
          货币 0.30%），单一绝对阈值必然"要么杀光权益、要么形同虚设"（执行报告 §0.2 实测）。

        ⚠️ 历史坑（已修，别再踩）：
        · 旧 `max_total_fee=2.0`（管理费+托管费）实测**近似失效**（>2.0 仅 10 只）；
        · 更要命的是：当时 `mgt_fee` 里装的其实是**申购手续费（打折后）**，不是管理费
          （四重证据确证，见 scripts/migrate_mgt_fee_to_purchase_fee.py）。申购费是**交易费用**、
          受平台折扣影响，**不得混进 TER**。

        数据缺失**显式声明**（铁律 5）：TER 不可算 → unknown + 说明缺哪项，**绝不写 0 顶替**
        （0 会把 TER 系统性算低 —— 正是"托管费恒为 0"踩过的坑）。
        """
        ter, missing = fund_fee.compute_ter(fund_info)
        if ter is None:
            return ("unknown",
                    "⊘ TER 不可算（缺 %s）" % fund_fee.missing_text(missing),
                    None, None)
        g = peer_percentile.group_of(fund_info.get("fund_type"))
        pct = peer_percentile.percentile(self._peer_dist(), g, "ter", ter) if g else None
        if pct is None:
            # 参照系还没建到 TER（或该组样本不足）→ 如实声明，不硬判
            return "unknown", "⊘ TER %.2f%%（同类参照系未就绪，暂不判定）" % ter, None, ter
        cheap = 100.0 - pct
        if pct >= 90:
            return ("warn",
                    "⚠️ TER %.2f%%（同类最贵 10%%）" % ter,
                    ("TER %.2f%% 处于同组最贵的 10%%。费率是预测后续净回报最有效的单变量之一"
                     "（晨星 2016/2025 研究），长期复利下差距会被放大。") % ter,
                    ter)
        return "pass", "✅ TER %.2f%%（同类最便宜 %.0f%%）" % (ter, cheap), None, ter

    def _check_purchasable(self, fund_info: dict) -> tuple:
        """可申购性检查 → (level, check_text, warning, status_raw)

        **`warning` 一律返回 None**（除非真的买不进去）—— 申购状态与"基金好不好"
        是两个轴：`限大额` 是申购限制，不是质量瑕疵。把它算进 `warn_count`
        会把一只 🟢 稳健基金压成 🟡 注意，等于变相降级（D2 要的是"保留但标注"）。
        所以限大额只写进 `quality_checks["申购状态"]` 与行上的 `purchase_status`
        字段，由前端单独挂一个标签，**不改风险等级**（level="info"，不计 warn）。

        `status_raw` 原样返回，供调用方决定是否真的排除。
        **未知不等于可申购**：空串和无法归类的文字都走“⊘ 未知(跳过检查)”，
        既不判失败也不判通过（沿用 `_check_size` 的既有做法）。
        """
        raw = (fund_info.get("purchase_status") or "").strip()
        if not raw:
            return "unknown", "⊘ 申购状态未知(跳过检查)", None, raw
        if any(m in raw for m in _BLOCKED_PURCHASE_MARKERS):
            return "fail", f"❌ {raw}", f"当前**{raw}**，买不进去，不应出现在推荐池里", raw
        if any(m in raw for m in _LIMITED_PURCHASE_MARKERS):
            return "info", "🔸 限大额·注意单日申购上限", None, raw
        if "开放" in raw:
            return "pass", "✅ 可申购", None, raw
        return "unknown", f"⊘ 申购状态未知({raw})", None, raw

    def _check_drawdown(self, vals, dates=None) -> tuple:
        """近1年最大回撤检查 → (level, check_text, warning, max_dd)

        入参是**升序的单位净值序列**（numpy array）。原来用 pandas 逐点循环，
        540 只基金要跑十几万次 Python 迭代；改成 maximum.accumulate 后整批只需几十毫秒。
        """
        # 2026-09-25：改走 nav_metrics —— 近1年回撤用**日期窗口**（原为 252 **点数**，
        # 序列有缺口时会跨过 1 年；口径文档早已指出必须按日期）。
        m = nav_metrics.compute(vals, dates)
        if m is None:
            return "unknown", "⚠️ 数据不足", None, None
        max_dd = float(m["max_drawdown_1y"]) if np.isfinite(m["max_drawdown_1y"]) else 0.0
        if max_dd > self.THRESHOLDS["max_drawdown_1y"]:
            return "fail", f"❌ {max_dd:.0f}%(过大)", f"近1年最大回撤{max_dd:.0f}%，超过{self.THRESHOLDS['max_drawdown_1y']}%阈值", round(max_dd, 1)
        if max_dd > 25:
            return "warn", f"⚠️ {max_dd:.0f}%(偏高)", None, round(max_dd, 1)
        return "pass", f"✅ {max_dd:.0f}%", None, round(max_dd, 1)

    def _check_momentum(self, vals, fund_type: str = None, dates=None) -> tuple:
        """追涨风险检查 → (level, check_text, warning, mom_3m)

        **阈值 = 组内 P90**（A1 原则：追涨是相对概念，不能跨组用同一常数）。

        为什么必须改：上一批次实测常数 `40` 在各组位于 P99~P100（通过率 99.3%~100%），
        而各组 P90 是 A 3.93 / B 2.85 / C 9.42 / D 4.29 / E 0.87 / F 0.52 —— 相差 18 倍。

        ⚠️ 取不到参照系时**不退回常数 40**（那等于恢复一条已证失效的检查，
        违反"数据缺失必须声明"）。改为如实声明"跳过"，由上层展示。
        """
        # 2026-09-25：近3月动量改走 nav_metrics 的**日期窗口**（原为 63 **点数**）。
        m = nav_metrics.compute(vals, dates)
        if m is None:
            return "unknown", "⚠️ 数据不足", None, None
        mom = float(m["momentum_3m"]) if np.isfinite(m["momentum_3m"]) else 0.0
        thr, group = self._momentum_threshold(fund_type)
        if thr is None:
            # 显式声明缺什么，不静默放行、也不用假值顶替
            why = ("类型未映射到参照系组" if group is None
                   else "参照系未构建（缺 data/peer_distributions.json）")
            return "unknown", f"⊘ {why}，跳过追涨检查", None, round(mom, 1)
        if mom > thr:
            return ("warn",
                    f"🔴 近3月涨{mom:.1f}%（超同类P{self.MOMENTUM_WARNING_PCT}线 {thr:.1f}%）",
                    f"近3月涨幅{mom:.1f}% 高于同组（{group}）的 P{self.MOMENTUM_WARNING_PCT} "
                    f"参考线 {thr:.1f}%，处于追涨区",
                    round(mom, 1))
        if mom > 25:
            return "warn", f"⚠️ 近3月涨{mom:.0f}%", None, round(mom, 1)
        if mom < -20:
            return "warn", f"💡 近3月跌{abs(mom):.0f}%(可能超跌)", None, round(mom, 1)
        return "pass", f"✅ 近3月{mom:+.1f}%(P{self.MOMENTUM_WARNING_PCT}线 {thr:.1f}%)", None, round(mom, 1)

    def _check_sharpe(self, vals, dates=None) -> tuple:
        """风险调整收益检查 → (level, check_text, warning, sharpe, ann_vol)

        口径（2026-09-25 统一，实现见 `src/analysis/nav_metrics.py`）：
        · **固定 3 年窗口**，不再用"基金全历史"。依据：晨星（全球基金评级标准）要求满
          **36 个月**才予评级、按 **3/5/10 年**固定窗口算；天天基金「特色数据」也只给
          **近1/2/3 年**固定窗口 —— **没有一家用全历史**，因为只有固定窗口才让不同
          年龄的基金**可比**。（本项目 3 年同时对晨星最小窗 + 天天基金最长展示窗 + 既有 756 天门槛。）
        · 年化波动用 `TRADING_DAYS`(244)，**不是 252** —— 项目早已实测"252 会把年化波动
          高估 3.07%"，但这里漏改了（2026-09-25 修）。
        · 年化收益用**几何**（按日期跨度），与参照系 `metrics()` 同源 —— 此前本处用
          "算术均值×252"，导致**同一只基金在两处算出不同夏普**。
        · 窗口按**日期**切（`_load_nav_series` 已带日期）；序列有缺口，按点数当年数会
          把年化严重高估（实测最坏约 2.3 倍）。
        """
        m = nav_metrics.compute(vals, dates, risk_free=self.risk_free_rate)
        if m is None:
            return "unknown", "⚠️ 数据不足", None, None, None
        sharpe, ann_vol = float(m["sharpe"]), float(m["ann_vol"])   # ann_vol 已是 %
        if not np.isfinite(sharpe) or not np.isfinite(ann_vol):
            return "unknown", "⚠️ 数据不足", None, None, None
        if sharpe < 0:
            return "fail", "❌ 夏普为负", "夏普比率为负，承担风险但没有获得相应回报", round(sharpe, 2), round(ann_vol, 1)
        if sharpe < 0.3:
            return "warn", "⚠️ 夏普偏低", None, round(sharpe, 2), round(ann_vol, 1)
        return "pass", f"✅ {sharpe:.2f}", None, round(sharpe, 2), round(ann_vol, 1)

    # ------------------------------------------------------------------
    # 净值序列装载
    # ------------------------------------------------------------------

    def _nav_values(self, fund_code: str) -> np.ndarray:
        """单只基金的**估值净值**序列（按日期升序的 numpy 数组）。

        用累计净值而非单位净值：分红除息日单位净值下挫会被下面几个检查
        误判成"真实下跌"（假回撤 / 假低动量），见 `nav_series` 模块说明。
        """
        df = pd.read_sql_query(
            "SELECT unit_nav, acc_nav FROM fund_nav WHERE fund_code = ? AND unit_nav IS NOT NULL "
            "ORDER BY nav_date ASC", self.db.conn, params=[fund_code])
        return valuation_nav_series(df).to_numpy(dtype=float)

    def _load_nav_series(self, codes: list) -> dict:
        """一次性读出多只基金的**估值净值**序列 → {code: (np.ndarray(升序), dates)}。

        原来每只基金走一次 `get_fund_nav()`（list[dict] → DataFrame），
        540 只基金要构造 100 多万个 dict；这里改成按批 SQL 直读 + groupby，
        实测 /api/funds 的冷计算从 ~7.8s 降到 ~2.8s。

        取累计净值（见 `nav_series`）：单位净值会把分红除息当成下跌。
        """
        out = {}
        if not codes:
            return out
        CHUNK = 500                                   # 控制在 SQLite 变量上限内
        for i in range(0, len(codes), CHUNK):
            part = codes[i:i + CHUNK]
            q = ("SELECT fund_code, nav_date, unit_nav, acc_nav FROM fund_nav "
                 "WHERE unit_nav IS NOT NULL AND fund_code IN (%s) "
                 "ORDER BY fund_code, nav_date" % ",".join("?" * len(part)))
            df = pd.read_sql_query(q, self.db.conn, params=part)
            for code, sub in df.groupby("fund_code", sort=False):
                # 2026-09-25：**同时返回日期** —— 指标窗口必须按日期切（序列有缺口，
                # 按点数当年数会把年化严重高估，实测最坏约 2.3 倍）。
                out[code] = (valuation_nav_series(sub).to_numpy(dtype=float),
                             sub["nav_date"].astype(str).tolist())
        return out

    def _screen_single_fund(self, fund_code: str, fund_info: dict) -> Optional[dict]:
        """
        对单只基金进行质量检查（6 个维度，各维度逻辑见 _check_* 方法）。

        Returns:
            dict with risk_label, risk_reasons, quality_checks, metrics
            或 None（净值数据不足）
        """
        vals = self._nav_values(fund_code)
        if len(vals) < 60:
            return None
        return self._score_series(vals, fund_info)

    def _score_series(self, vals, fund_info: dict, dates=None) -> dict:
        """对一段已排好序的净值序列做 6 维检查并给风险标签。

        批次 4.8：业务判定（fail/warn 计数）只读**结构化 level**
        （`check_levels`），`quality_checks` 里的 emoji 文本只用于展示 ——
        改一次展示符号不再会静默改变风险分级。
        """
        metrics = {}
        checks = {}
        levels = {}
        warnings = []

        # ---- 检查1: 成立时间 ----
        levels["成立时间"], checks["成立时间"], w = self._check_age(fund_info)
        if w:
            warnings.append(w)

        # ---- 检查2: 规模 ----
        levels["基金规模"], checks["基金规模"], w, size = self._check_size(fund_info)
        if w:
            warnings.append(w)
        metrics["fund_size_yi"] = size

        # ---- 检查3: 费率 ----
        levels["费率"], checks["费率"], w, total_fee = self._check_fee(fund_info)
        if w:
            warnings.append(w)
        metrics["total_fee"] = total_fee

        # ---- 检查4: 回撤 ----
        levels["回撤控制"], checks["回撤控制"], w, max_dd = self._check_drawdown(vals, dates)
        if w:
            warnings.append(w)
        if max_dd is not None:
            metrics["max_drawdown_1y"] = max_dd

        # ---- 检查5: 动量(追涨风险) ----
        # 需要 fund_type 才能定位"同组"（阈值 = 该组 P90），故把类型传进去。
        levels["追涨风险"], checks["追涨风险"], w, mom = self._check_momentum(
            vals, fund_info.get("fund_type"), dates)
        if w:
            warnings.append(w)
        if mom is not None:
            metrics["momentum_3m"] = mom

        # ---- 检查6: 夏普比率 ----
        levels["风险调整收益"], checks["风险调整收益"], w, sharpe, ann_vol = self._check_sharpe(vals, dates)
        if w:
            warnings.append(w)
        if sharpe is not None:
            metrics["sharpe"] = sharpe
            metrics["ann_vol"] = ann_vol

        # ---- 检查7: 可申购性（暂停申购/封闭期买不进去）----
        levels["申购状态"], checks["申购状态"], w, purchase_status = self._check_purchasable(fund_info)
        if w:
            warnings.append(w)
        metrics["purchase_status"] = purchase_status
        purchase_blocked = any(m in purchase_status for m in _BLOCKED_PURCHASE_MARKERS)

        # ---- 判定风险标签（只看结构化 level，不看 emoji）----
        fail_count = sum(1 for lv in levels.values() if lv == "fail")
        warn_count = sum(1 for lv in levels.values() if lv == "warn")

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
            "check_levels": levels,
            "metrics": metrics,
            "purchase_blocked": purchase_blocked,
        }

    def _get_funds_with_nav(self) -> set:
        """获取有净值数据的基金代码集合"""
        return self.db.get_all_fund_codes()

    def get_pool_summary(self, df: pd.DataFrame) -> dict:
        """获取筛选池的统计摘要

        `avg_fee` 只在**有费率数据**的基金上求平均，并回报样本数 ——
        旧版把缺失当 0 一起平均，会把平均费率显著拉低。
        """
        if df.empty:
            return {"total": 0, "by_risk": {}, "avg_fee": 0, "fee_n": 0,
                    "limited_n": 0, "status_unknown_n": 0}

        by_risk = df["risk_label"].value_counts().to_dict()
        fees = pd.to_numeric(df["mgt_fee"], errors="coerce")
        fees = fees[fees > 0]
        avg_fee = float(fees.mean()) if len(fees) else 0.0

        ps = df["purchase_status"].fillna("").astype(str) if "purchase_status" in df.columns \
            else pd.Series([""] * len(df))
        unknown = int((ps.str.strip() == "").sum())

        return {
            "total": len(df),
            "by_risk": by_risk,
            "avg_fee": round(avg_fee, 2),
            "fee_n": int(len(fees)),
            # 池内限大额（保留但标注）与申购状态未知的数量 —— 静默当作可申购会骗人
            "limited_n": int(ps.str.contains("限大额").sum()),
            "status_unknown_n": unknown,
        }


# ---- 向后兼容别名 ----
FundScorer = FundScreener


def format_fee(mgt_fee) -> str:
    """费率显示文本：缺失或为 0 时显示「无数据」，而不是 0.00%。

    长历史基金的 `mgt_fee` 大量缺失；直接打印成 0.00% 会被读成"零费率"。
    """
    try:
        v = float(mgt_fee or 0)
    except (TypeError, ValueError):
        return "无数据"
    return f"{v:.2f}%" if v > 0 else "无数据"
