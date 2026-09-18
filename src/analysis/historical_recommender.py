"""
基于历史回测的基金推荐引擎

核心问题: "过去三年里, 如果每月按评分模型选基金, 哪些基金持续被选中且真的赚了钱?"

方法:
1. 从3年前开始, 每月底对全量基金打分(Top 30)
2. 记录选中的基金 + 它们的实际未来1月/3月/6月收益
3. 统计: 哪些基金被反复选中? 选中后的实际收益如何?
4. 输出: "持续被选中且收益验证过的基金" ← 有数据支撑的推荐

与 FundScreener 的区别:
- Screener: 当前的质量筛选(排除有坑的)
- 本模块: 历史模拟验证(哪些筛选结果真的赚过钱)
"""

import numpy as np
import pandas as pd
import random
from datetime import timedelta
from typing import Optional
from collections import defaultdict

from ..data.database import Database
from .nav_series import VALUATION_NAV_SQL, valuation_nav
from .risk_free import RISK_FREE_ANNUAL
from .fund_scorer import type_bucket


class HistoricalRecommender:
    """基于历史回测的基金推荐"""

    def __init__(self, db: Database):
        self.db = db

    # =================================================================
    # 主接口
    # =================================================================

    def _run_monthly_backtest(self, nav_cache: dict, dates: list, top_n: int) -> tuple:
        """逐月回测：对每月打分选 TopN，统计每只基金被选中次数与后续收益。

        批次 4.4：**按类型桶分组各取 TopN**（权益一组、非权益一组）。
        原实现债基与权益同池排序 —— 打分里 30% 权重的回撤项让债基拿
        "低回撤免费分"，系统性挤掉权益；本项目是**权益仓位择时**系统。
        分组后月度 picks = 权益 TopN + 非权益 TopN，两个类型都有代表。
        """
        print(f"    回测 ({len(dates)}个月)...")
        monthly_picks = {}
        fund_stats = defaultdict(lambda: {
            "times_picked": 0, "total_score": 0,
            "returns_1m": [], "returns_3m": [], "returns_6m": [],
            "first_pick": None, "last_pick": None,
        })

        for i, date in enumerate(dates):
            scores = []
            for code, navs in nav_cache.items():
                if len(navs) < 60:
                    continue
                s = self._score_from_tuples(navs, date)
                if s is not None:
                    scores.append((code, s))

            picks = []
            for bucket in ("equity", "bond"):
                group = [(c, s) for c, s in scores if self._type_bucket(c) == bucket]
                group.sort(key=lambda x: x[1], reverse=True)
                picks.extend(group[:top_n])

            monthly_picks[date] = []

            for code, score in picks:
                navs = nav_cache[code]
                a1m = self._fwd_return_from_tuples(navs, date, 21)
                a3m = self._fwd_return_from_tuples(navs, date, 63)
                a6m = self._fwd_return_from_tuples(navs, date, 126)

                monthly_picks[date].append({
                    "code": code, "score": round(score, 1),
                    "actual_1m": round(a1m, 1) if a1m is not None else None,
                    "actual_3m": round(a3m, 1) if a3m is not None else None,
                    "actual_6m": round(a6m, 1) if a6m is not None else None,
                })
                st = fund_stats[code]
                st["times_picked"] += 1
                st["total_score"] += score
                if st["first_pick"] is None:
                    st["first_pick"] = date
                st["last_pick"] = date
                if a1m is not None:
                    st["returns_1m"].append(a1m)
                if a3m is not None:
                    st["returns_3m"].append(a3m)
                if a6m is not None:
                    st["returns_6m"].append(a6m)

            scores.clear()

            if (i + 1) % 4 == 0:
                print(f"      进度: {i+1}/{len(dates)}个月")

        return monthly_picks, fund_stats

    def _compile_proven_winners(self, fund_stats: dict, dates: list) -> list:
        """统计被频繁选中且后续收益好的基金，按综合分排序"""
        proven = []
        for code, st in fund_stats.items():
            if st["times_picked"] < max(3, len(dates) * 0.1):  # 至少被选中3次或10%的月份
                continue

            avg_score = st["total_score"] / st["times_picked"]
            avg_1m = np.mean(st["returns_1m"]) if st["returns_1m"] else 0
            avg_3m = np.mean(st["returns_3m"]) if st["returns_3m"] else 0
            avg_6m = np.mean(st["returns_6m"]) if st["returns_6m"] else 0
            win_1m = (np.array(st["returns_1m"]) > 0).sum() / max(len(st["returns_1m"]), 1) * 100
            win_3m = (np.array(st["returns_3m"]) > 0).sum() / max(len(st["returns_3m"]), 1) * 100

            # 综合评分: 选中频率(30%) + 平均收益(30%) + 胜率(20%) + 模型评分(20%)
            freq_score = min(100, st["times_picked"] / len(dates) * 100)
            ret_score = max(0, min(100, (avg_3m + 10) * 3))  # -10%→0分, +20%→90分
            win_score = win_3m * 0.7 + win_1m * 0.3
            score_weight = min(100, avg_score * 1.2)

            composite = (
                freq_score * 0.30 + ret_score * 0.30 +
                win_score * 0.20 + score_weight * 0.20
            )

            info = self._get_fund_info(code)
            proven.append({
                "code": code,
                "name": info.get("fund_name", "") if info else "",
                "type": info.get("fund_type", "") if info else "",
                "bucket": self._type_bucket(code),
                "composite_score": round(composite, 1),
                "times_picked": st["times_picked"],
                "pick_rate": round(st["times_picked"] / len(dates) * 100, 1),
                "avg_score": round(avg_score, 1),
                "avg_return_1m": round(avg_1m, 1),
                "avg_return_3m": round(avg_3m, 1),
                "avg_return_6m": round(avg_6m, 1),
                "win_rate_1m": round(win_1m, 0),
                "win_rate_3m": round(win_3m, 0),
                "first_pick": st["first_pick"],
                "last_pick": st["last_pick"],
            })

        proven.sort(key=lambda x: x["composite_score"], reverse=True)
        return proven

    def _build_current_picks(self, monthly_picks: dict, fund_stats: dict, latest_date: str) -> list:
        """当前推荐：取最新一期 picks，附带历史表现"""
        current = []
        if latest_date in monthly_picks:
            for pick in monthly_picks[latest_date][:20]:
                info = self._get_fund_info(pick["code"])
                st = fund_stats[pick["code"]]
                rets = st["returns_3m"]
                current.append({
                    "code": pick["code"],
                    "name": info.get("fund_name", "") if info else "",
                    "type": info.get("fund_type", "") if info else "",
                    "bucket": self._type_bucket(pick["code"]),
                    "score": pick["score"],
                    "times_picked": st["times_picked"],
                    "hist_avg_3m": round(np.mean(rets), 1) if rets else None,
                    "hist_win_3m": round((np.array(rets) > 0).sum() / max(len(rets), 1) * 100, 0) if rets else None,
                })
        return current

    def recommend(self, lookback_years: int = 3, top_n: int = 30) -> dict:
        """
        主入口: 历史回测 + 统计排名 → 推荐清单。

        流程: 加载净值 → 逐月回测 → 综合表现 → 当前推荐 → 统计摘要。
        各阶段逻辑见 _run_monthly_backtest / _compile_proven_winners / _build_current_picks。

        Returns:
            dict with proven_winners / current_picks / stats
        """
        # 1. 获取所有候选基金的净值
        all_codes = self._get_candidates()
        if len(all_codes) < 20:
            return {"error": f"净值数据不足, 仅有 {len(all_codes)} 只有效基金"}

        # 2. 确定回测日期点 (每月底)
        dates = self._get_monthly_dates(lookback_years)
        if len(dates) < 3:
            return {"error": "数据时间范围不足"}

        # 3. 预加载所有基金的净值数据为 tuple 列表 (纯Python, 无pandas, 无C扩展)
        #    只加载回测窗口(+预热段)内历史，避免固定 LIMIT 截断造成早期月份“无历史”
        since = (pd.to_datetime(dates[0]) - timedelta(days=150)).strftime("%Y-%m-%d")
        print(f"    加载 {len(all_codes)} 只基金净值({since} 起)...")
        nav_cache = {}
        for i, code in enumerate(all_codes):
            nav_cache[code] = self._load_nav_tuples(code, since)
            if (i + 1) % 30 == 0:
                print(f"      加载: {i+1}/{len(all_codes)}")

        # 4. 逐月回测
        monthly_picks, fund_stats = self._run_monthly_backtest(nav_cache, dates, top_n)

        # 5. 综合表现 → 历史验证最强
        proven = self._compile_proven_winners(fund_stats, dates)

        # 6. 当前推荐（附历史表现）
        current = self._build_current_picks(monthly_picks, fund_stats, dates[-1])

        # 7. 统计摘要
        n_good = sum(1 for p in proven if p["avg_return_3m"] > 0)
        n_bad = sum(1 for p in proven if p["avg_return_3m"] <= 0)
        top10_avg_3m = np.mean([p["avg_return_3m"] for p in proven[:10]]) if proven else 0

        # 类型分布（批次 4.4 验收：证明不再债基一边倒）
        def _dist(items):
            d = {"equity": 0, "bond": 0}
            for p in items:
                d[p.get("bucket") or type_bucket(p.get("type"))] += 1
            return d

        eq_n = sum(1 for c in all_codes if self._type_bucket(c) == "equity")

        return {
            "proven_winners": proven[:20],
            "current_picks": current,
            "stats": {
                "total_months": len(dates),
                "total_candidates": len(all_codes),
                "candidates_equity": eq_n,
                "candidates_bond": len(all_codes) - eq_n,
                "funds_ever_picked": len(fund_stats),
                "funds_with_proven_record": len(proven),
                "proven_good": n_good,
                "proven_bad": n_bad,
                "top10_avg_3m_return": round(top10_avg_3m, 1),
                "date_range": f"{dates[0]} ~ {dates[-1]}",
                "proven_type_dist": _dist(proven[:20]),
                "current_type_dist": _dist(current[:20]),
                # 批次 4.3：本引擎用**已实现的前向收益**定义"赢家"，属样本内
                # 同义反复（用结果选结果），**不是样本外能力证据**。
                # 上层（CLI/API/前端）必须把这个标注展示给用户，不得隐去。
                "methodology": "in-sample",
            },
        }

    # =================================================================
    # 工具
    # =================================================================

    def _get_candidates(self, per_group: int = 60, seed: int = 42) -> list:
        """候选池：按类型分层（权益 / 非权益），每组**固定种子随机抽样**。

        批次 4.5 修复：原实现 `bond[:60] + equity[:60]` 取的是 SQL 返回顺序
        （实测恰好等于代码升序）—— 即「成立最早的一批」，带系统性久期偏差
        与幸存者偏差，注释却写「按类型分层」名不副实；且 `bond[:60]` 实际
        只有 47 只，静默少于声称的 60。

        现在的口径：
        - 先 `sorted()` 掐断对 SQL 返回顺序的依赖；
        - 固定种子（默认 42）随机抽样 —— **可复现**，且乱序插入同一批代码
          抽出的集合不变（验收测试断言这一点）；
        - 每组数量显式传入，不足 per_group 时全保留（不再静默缺额，
          实际数量通过 recommend() 的 stats.candidates_* 上报）。
        """
        cur = self.db.conn.cursor()
        # 只取有足够历史的基金(>252个交易日≈1年)
        cur.execute("""
            SELECT fund_code FROM fund_nav
            GROUP BY fund_code HAVING COUNT(*) >= 252
        """)
        codes = sorted({r[0] for r in cur.fetchall()})
        equity = [c for c in codes if self._type_bucket(c) == "equity"]
        bond = [c for c in codes if self._type_bucket(c) == "bond"]

        rng = random.Random(seed)
        def _sample(group: list) -> list:
            return group if len(group) <= per_group else rng.sample(group, per_group)

        return _sample(bond) + _sample(equity)

    def _get_monthly_dates(self, lookback_years: int) -> list:
        """回测采样点：**自然月末**（批次 4.6）。

        原实现 `d -= timedelta(days=60)` 是"每 2 个月一个点"的伪月度采样，
        而前瞻收益按 21 个交易日（≈1 个月）评估 → 相邻样本重叠 50%，
        样本独立性失真（与已修的 backtest B4 同一 bug 模式）。
        现在与 B4 一致改用 `freq="ME"`，采样间隔 = 前瞻窗口 = 1 个月。
        """
        max_date = self.db.get_latest_nav_date()
        if not max_date:
            return []

        end = pd.to_datetime(max_date)
        # 用月数而非年数：调用方允许 1.5 这类非整数年（pd.DateOffset(years=1.5) 会抛
        # "Non-integer years ... not supported"），18 个月 = 1.5 年的准确表达
        start = end - pd.DateOffset(months=int(lookback_years * 12))
        months = pd.date_range(start=start, end=end, freq="ME")
        return [d.strftime("%Y-%m-%d") for d in months]

    # -------- 纯Python净值操作 (避开pandas C扩展Windows bug) --------

    def _load_nav_tuples(self, code: str, since: str = None) -> list:
        """加载净值数据: [(date_str, nav_float), ...], 按日期升序。
        可用 since(YYYY-MM-DD) 只加载回测窗口内及更早一小段(供动量/夏普预热)的历史，
        避免固定 LIMIT 300 把多年回测的早期月份“截断成无历史”。"""
        cur = self.db.conn.cursor()
        # 取累计净值：动量/夏普是"这只基金赚了多少"，单位净值会把分红算成下跌
        sql = ("SELECT nav_date, " + VALUATION_NAV_SQL + " AS nav FROM fund_nav "
               "WHERE fund_code = ?" + (" AND nav_date >= ?" if since else "") +
               " ORDER BY nav_date ASC LIMIT 8000")
        params = [code] + ([since] if since else [])
        cur.execute(sql, params)
        rows = cur.fetchall()
        if not rows or len(rows) < 60:
            return []
        return [(str(r[0]), float(r[1])) for r in rows]

    @staticmethod
    def _score_from_tuples(nav_tuples: list, date: str) -> Optional[float]:
        """纯Python打分, 无pandas。

        批次 4.7/4.10 口径修正：
        - 无风险利率用单一真源 `RISK_FREE_ANNUAL`（原硬编码 0.03，与其他模块不一致）；
        - 回撤只看**近 1 年（252 个交易日）**，与其他模块的 `max_drawdown_1y`
          口径对齐（原实现从全历史第一个点起算，老基金被远古回撤惩罚）；
        - 子分范围 [0, 100]（原 max(5,...) 保底让实测分数挤在 [5,95]）。
        """
        # 取 date 之前的所有点
        vals = [n[1] for n in nav_tuples if n[0] <= date]
        if len(vals) < 60:
            return None

        # 动量 (最近63天)
        n_vals = len(vals)
        idx_63 = max(0, n_vals - 63)
        mom = (vals[-1] / vals[idx_63] - 1) * 100 if vals[idx_63] > 0 else 0
        mom_score = max(0, min(100, (mom + 30) / 80 * 100)) * 0.4

        # 夏普 (日收益的均值/标准差)
        daily = [(vals[i] / vals[i-1] - 1) for i in range(1, n_vals)]
        if len(daily) >= 20:
            avg_d = sum(daily) / len(daily)
            var_d = sum((d - avg_d) ** 2 for d in daily) / (len(daily) - 1)
            std_d = var_d ** 0.5
            ann_ret = avg_d * 252
            ann_vol = std_d * (252 ** 0.5)
            sv = (ann_ret - RISK_FREE_ANNUAL) / ann_vol if ann_vol > 0 else 0
            sharpe_score = max(0, min(100, (sv + 1) / 3.5 * 100)) * 0.3
        else:
            sharpe_score = 15          # [0,100] 的中性贡献（50 × 0.3）

        # 回撤（近1年，与其他模块同口径）
        recent = vals[-252:]
        peak = recent[0]; max_dd = 0
        for p in recent:
            if p > peak: peak = p
            dd = (peak - p) / peak * 100
            if dd > max_dd: max_dd = dd
        dd_score = max(0, min(100, (50 - max_dd) / 50 * 100)) * 0.3

        return mom_score + sharpe_score + dd_score

    @staticmethod
    def _fwd_return_from_tuples(nav_tuples: list, date: str, days: int) -> Optional[float]:
        """纯Python前瞻收益"""
        if not nav_tuples:
            return None
        # 找 date 对应位置
        for i in range(len(nav_tuples) - 1, -1, -1):
            if nav_tuples[i][0] <= date:
                start_idx = i
                end_idx = min(i + days, len(nav_tuples) - 1)
                if end_idx <= start_idx:
                    return None
                s = nav_tuples[start_idx][1]
                e = nav_tuples[end_idx][1]
                return (e / s - 1) * 100 if s > 0 else None
        return None

    # legacy compat
    def _quick_score(self, code: str, nav_records: list, date: str) -> Optional[float]:
        navs = [(str(r.get('nav_date','')),
                 valuation_nav(r.get('unit_nav'), r.get('acc_nav'))) for r in nav_records]
        navs.sort(key=lambda x: x[0])
        return self._score_from_tuples(navs, date)

    def _fund_types(self) -> dict:
        """懒加载一次 fund_info -> {fund_code: row} 映射，避免逐基金全表扫描 O(F²)"""
        if not hasattr(self, "_ftmap"):
            self._ftmap = {f["fund_code"]: f for f in self.db.get_all_funds()}
        return self._ftmap

    def _type_bucket(self, code: str) -> str:
        """粗类型桶（权益/非权益）—— 4.4 分组评分的依据。

        与 `fund_scorer.type_bucket` 同一口径（单一实现，避免两处漂移）。
        """
        info = self._fund_types().get(code)
        ftype = info.get("fund_type", "") if info else ""
        return type_bucket(ftype)

    # 向后兼容：旧调用名
    def _is_equity(self, code: str) -> bool:
        return self._type_bucket(code) == "equity"

    def _get_fund_info(self, code: str) -> Optional[dict]:
        return self._fund_types().get(code, {})
