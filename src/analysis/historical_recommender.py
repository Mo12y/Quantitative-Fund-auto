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
from datetime import timedelta
from typing import Optional
from collections import defaultdict

from ..data.database import Database


class HistoricalRecommender:
    """基于历史回测的基金推荐"""

    def __init__(self, db: Database):
        self.db = db

    # =================================================================
    # 主接口
    # =================================================================

    def recommend(self, lookback_years: int = 3, top_n: int = 30) -> dict:
        """
        主入口: 历史回测 + 统计排名 → 推荐清单。

        Returns:
            dict with:
            - proven_winners: 持续被选中且收益好的基金
            - current_picks: 当前模型选出的基金(含历史表现)
            - stats: 回测统计摘要
        """
        # 1. 获取所有候选基金的净值
        all_codes = self._get_candidates()
        if len(all_codes) < 20:
            return {"error": f"净值数据不足, 仅有 {len(all_codes)} 只有效基金"}

        # 2. 确定回测日期点 (每月底)
        dates = self._get_monthly_dates(lookback_years)
        if len(dates) < 3:
            return {"error": "数据时间范围不足"}

        # 3. 预加载所有基金的净值数据为tuple列表 (纯Python, 无pandas, 无C扩展)
        print(f"    加载 {len(all_codes)} 只基金净值...")
        nav_cache = {}
        for i, code in enumerate(all_codes):
            nav_cache[code] = self._load_nav_tuples(code)
            if (i+1) % 30 == 0:
                print(f"      加载: {i+1}/{len(all_codes)}")

        # 4. 逐月打分
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

            scores.sort(key=lambda x: x[1], reverse=True)
            picks = scores[:top_n]
            monthly_picks[date] = []

            for code, score in picks:
                navs = nav_cache[code]
                a1m = self._fwd_return_from_tuples(navs, date, 21)
                a3m = self._fwd_return_from_tuples(navs, date, 63)
                a6m = self._fwd_return_from_tuples(navs, date, 126)

                monthly_picks[date].append({
                    "code": code, "score": round(score, 1),
                    "actual_1m": round(a1m,1) if a1m is not None else None,
                    "actual_3m": round(a3m,1) if a3m is not None else None,
                    "actual_6m": round(a6m,1) if a6m is not None else None,
                })
                st = fund_stats[code]
                st["times_picked"] += 1
                st["total_score"] += score
                if st["first_pick"] is None: st["first_pick"] = date
                st["last_pick"] = date
                if a1m is not None: st["returns_1m"].append(a1m)
                if a3m is not None: st["returns_3m"].append(a3m)
                if a6m is not None: st["returns_6m"].append(a6m)

            scores.clear()

            if (i+1) % 4 == 0:
                print(f"      进度: {i+1}/{len(dates)}个月")

        # 4. 计算每个基金的综合表现
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
            score_weight = min(100, avg_score * 1.2)  # 模型评分归一化

            composite = (
                freq_score * 0.30 +
                ret_score * 0.30 +
                win_score * 0.20 +
                score_weight * 0.20
            )

            # 获取基金基本信息
            info = self._get_fund_info(code)
            name = info.get("fund_name", "") if info else ""
            ftype = info.get("fund_type", "") if info else ""

            proven.append({
                "code": code,
                "name": name,
                "type": ftype,
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

        # 5. 当前推荐: 取最新一期的 picks, 附带历史表现
        latest_date = dates[-1]
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
                    "score": pick["score"],
                    "times_picked": st["times_picked"],
                    "hist_avg_3m": round(np.mean(rets), 1) if rets else None,
                    "hist_win_3m": round((np.array(rets) > 0).sum() / max(len(rets), 1) * 100, 0) if rets else None,
                })

        # 统计摘要
        n_good = sum(1 for p in proven if p["avg_return_3m"] > 0)
        n_bad = sum(1 for p in proven if p["avg_return_3m"] <= 0)
        top10_avg_3m = np.mean([p["avg_return_3m"] for p in proven[:10]]) if proven else 0

        return {
            "proven_winners": proven[:20],
            "current_picks": current,
            "stats": {
                "total_months": len(dates),
                "total_candidates": len(all_codes),
                "funds_ever_picked": len(fund_stats),
                "funds_with_proven_record": len(proven),
                "proven_good": n_good,
                "proven_bad": n_bad,
                "top10_avg_3m_return": round(top10_avg_3m, 1),
                "date_range": f"{dates[0]} ~ {dates[-1]}",
            },
        }

    # =================================================================
    # 工具
    # =================================================================

    def _get_candidates(self) -> list:
        cur = self.db.conn.cursor()
        # 只取有足够历史的基金(>252个交易日≈1年)
        cur.execute("""
            SELECT fund_code FROM fund_nav
            GROUP BY fund_code HAVING COUNT(*) >= 252
        """)
        codes = [r[0] for r in cur.fetchall()]
        # 按类型分层: 债基60只 + 权益60只 = 120只
        # 权益型: 股票型/混合型/指数型
        equity = [c for c in codes if self._is_equity(c)]
        bond = [c for c in codes if not self._is_equity(c)]
        # 各取60只
        return bond[:60] + equity[:60]

    def _get_monthly_dates(self, lookback_years: int) -> list:
        cur = self.db.conn.cursor()
        cur.execute("SELECT MAX(nav_date) FROM fund_nav")
        max_date = cur.fetchone()[0]
        if not max_date:
            return []

        end = pd.to_datetime(max_date)
        start = end - timedelta(days=365 * lookback_years)
        months = []
        d = end
        while d >= start:
            months.append(d.strftime("%Y-%m-%d"))
            d -= timedelta(days=60)  # 每2个月一个采样点, 减少计算量
        return sorted(months)

    # -------- 纯Python净值操作 (避开pandas C扩展Windows bug) --------

    def _load_nav_tuples(self, code: str) -> list:
        """加载净值数据: [(date_str, nav_float), ...], 最多300条, 按日期升序"""
        cur = self.db.conn.cursor()
        cur.execute("""
            SELECT nav_date, unit_nav FROM fund_nav
            WHERE fund_code = ? ORDER BY nav_date DESC LIMIT 300
        """, (code,))
        rows = cur.fetchall()
        if not rows or len(rows) < 60:
            return []
        # reverse to ascending
        rows = list(rows)
        rows.reverse()
        return [(str(r[0]), float(r[1])) for r in rows]

    @staticmethod
    def _score_from_tuples(nav_tuples: list, date: str) -> Optional[float]:
        """纯Python打分, 无pandas"""
        # 取 date 之前的所有点
        vals = [n[1] for n in nav_tuples if n[0] <= date]
        if len(vals) < 60:
            return None

        # 动量 (最近63天)
        n_vals = len(vals)
        idx_63 = max(0, n_vals - 63)
        mom = (vals[-1] / vals[idx_63] - 1) * 100 if vals[idx_63] > 0 else 0
        mom_score = max(5, min(95, (mom + 30) / 80 * 100)) * 0.4

        # 夏普 (日收益的均值/标准差)
        daily = [(vals[i] / vals[i-1] - 1) for i in range(1, n_vals)]
        if len(daily) >= 20:
            avg_d = sum(daily) / len(daily)
            var_d = sum((d - avg_d) ** 2 for d in daily) / (len(daily) - 1)
            std_d = var_d ** 0.5
            ann_ret = avg_d * 252
            ann_vol = std_d * (252 ** 0.5)
            sv = (ann_ret - 0.03) / ann_vol if ann_vol > 0 else 0
            sharpe_score = max(5, min(95, (sv + 1) / 3.5 * 100)) * 0.3
        else:
            sharpe_score = 15

        # 回撤
        peak = vals[0]; max_dd = 0
        for p in vals:
            if p > peak: peak = p
            dd = (peak - p) / peak * 100
            if dd > max_dd: max_dd = dd
        dd_score = max(5, min(95, (50 - max_dd) / 50 * 100)) * 0.3

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
        navs = [(str(r.get('nav_date','')), float(r.get('unit_nav',0))) for r in nav_records]
        navs.sort(key=lambda x: x[0])
        return self._score_from_tuples(navs, date)

    def _is_equity(self, code: str) -> bool:
        """判断是否为权益类基金(通过fund_info表)"""
        info = self._get_fund_info(code)
        ftype = info.get("fund_type", "") if info else ""
        return any(kw in ftype for kw in ["股票", "混合", "指数", "QDII"])

    def _get_fund_info(self, code: str) -> Optional[dict]:
        funds = self.db.get_all_funds()
        for f in funds:
            if f["fund_code"] == code:
                return f
        return {}
