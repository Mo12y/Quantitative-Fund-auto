"""
板块轮动分析器: 对31个申万一级行业进行多维度排名，输出值得关注的方向。

方法:
1. 动量排名: 近1月/3月/6月收益率排序
2. 趋势强度: 价格在均线上的位置 + 均线方向
3. 超跌反弹候选: 跌幅最大但趋势未破的板块
4. 相对估值: 配合市场风格(价值vs成长)给出方向建议

不预测"哪个板块会涨"——只告诉你"当前哪些板块表现强势/哪些严重超跌"。
"""

import time
import numpy as np
import pandas as pd
from datetime import datetime

try:
    import akshare as ak
except ImportError:
    ak = None


class SectorAnalyzer:
    """申万一级行业板块分析器"""

    # 申万31个一级行业代码
    SW_SECTORS = {
        "801010": "农林牧渔", "801020": "采掘", "801030": "化工",
        "801040": "钢铁", "801050": "有色金属", "801080": "电子",
        "801110": "家用电器", "801120": "食品饮料", "801130": "纺织服装",
        "801140": "轻工制造", "801150": "医药生物", "801160": "公用事业",
        "801170": "交通运输", "801180": "房地产", "801200": "商贸零售",
        "801210": "社会服务", "801230": "综合", "801710": "建筑材料",
        "801720": "建筑装饰", "801730": "电力设备", "801740": "国防军工",
        "801750": "计算机", "801760": "通信", "801770": "银行",
        "801780": "非银金融", "801790": "汽车", "801880": "传媒",
        "801890": "机械设备", "801950": "煤炭", "801960": "石油石化",
        "801970": "环保",
    }

    def __init__(self):
        if ak is None:
            raise ImportError("需要 akshare")
        self._cache = {}  # 缓存行业数据

    # =================================================================
    # 主接口
    # =================================================================

    def analyze(self) -> dict:
        """
        对全部31个行业进行多维度分析。

        Returns:
            dict with:
            - rankings: 综合排名
            - strongest: 动量最强的5个 (趋势跟踪)
            - weakest: 最弱的5个 (超跌候选)
            - value_candidates: 超跌+趋势未破 (逆向)
            - momentum_leaders: 强势+趋势向上 (顺势)
            - sector_details: 每个行业的完整数据
        """
        print("  📊 采集31个行业数据...")
        data = self._collect_all_sectors()

        if not data:
            return {"error": "数据采集失败"}

        # 综合评分
        scored = self._score_all(data)

        # 分类推荐
        strongest = scored[:5]
        weakest = scored[-5:]

        # 超跌反弹候选: 近1月跌超8% 但 近6月>0 且 站上120日线
        value_candidates = self._pick_value_candidates(scored)

        # 动量领涨: 近3月>0 且 价格在60日线上方
        momentum_leaders = []
        for s in scored:
            if s["ret_3m"] > 0 and s["ma_ratio"] > 1.0:
                momentum_leaders.append(s)
        momentum_leaders.sort(key=lambda x: x["ret_3m"], reverse=True)

        return {
            "rankings": scored[:15],
            "strongest": strongest,
            "weakest": weakest,
            "value_candidates": value_candidates[:5],
            "momentum_leaders": momentum_leaders[:5],
            "all_sectors": scored,
            "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    # =================================================================
    # 候选筛选（纯逻辑，可单测）
    # =================================================================

    @staticmethod
    def _pick_value_candidates(scored: list) -> list:
        """超跌反弹候选：近1月跌超8% **且** 近6月收益>0 **且** 站上120日均线。

        三个条件缺一不可。旧实现只看"短期跌 + 价格高于 MA60 的 85%"，
        会把**持续单边下跌**的板块当成反弹候选（接飞刀）——近6月>0 保证长期
        趋势未坏，站上120日线确保未破位。
        """
        out = [
            s for s in scored
            if s.get("ret_1m", 0) < -8
            and s.get("ret_6m", 0) > 0
            and s.get("ma120_ratio", 1.0) > 1.0
        ]
        out.sort(key=lambda x: x["ret_1m"])
        return out

    # =================================================================
    # 数据采集
    # =================================================================

    def _collect_all_sectors(self) -> list:
        """采集31个行业的历史行情"""
        results = []
        total = len(self.SW_SECTORS)
        for i, (code, name) in enumerate(self.SW_SECTORS.items()):
            try:
                if code in self._cache:
                    df = self._cache[code]
                else:
                    df = ak.index_hist_sw(symbol=code, period="day")
                    self._cache[code] = df
                    time.sleep(0.08)  # 礼貌延迟

                close = pd.to_numeric(df["收盘"], errors="coerce").dropna()
                if len(close) < 120:
                    continue

                n = len(close)
                ret_1m = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if n >= 21 else 0
                ret_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100 if n >= 63 else 0
                ret_6m = (close.iloc[-1] / close.iloc[-126] - 1) * 100 if n >= 126 else 0

                # 均线位置 (价格 / MA60)
                ma60 = close.iloc[-60:].mean() if n >= 60 else close.mean()
                ma120 = close.iloc[-min(120,n):].mean()
                ma_ratio = close.iloc[-1] / ma60 if ma60 > 0 else 1.0
                ma120_ratio = close.iloc[-1] / ma120 if ma120 > 0 else 1.0

                # 波动率
                vol = close.pct_change().iloc[-126:].std() * np.sqrt(252) * 100 if n >= 126 else 0

                # 最大回撤
                recent = close.iloc[-126:] if n >= 126 else close
                peak = recent.iloc[0]
                max_dd = 0
                for p in recent.values:
                    if p > peak:
                        peak = p
                    dd = (peak - p) / peak * 100
                    if dd > max_dd:
                        max_dd = dd

                # 均线方向 (MA20斜率)
                ma20_now = close.iloc[-20:].mean() if n >= 20 else close.mean()
                ma20_prev = close.iloc[-40:-20].mean() if n >= 40 else close.mean()
                ma_slope = (ma20_now / ma20_prev - 1) * 100 if ma20_prev > 0 else 0

                results.append({
                    "code": code,
                    "name": name,
                    "ret_1m": round(ret_1m, 1),
                    "ret_3m": round(ret_3m, 1),
                    "ret_6m": round(ret_6m, 1),
                    "ma_ratio": round(ma_ratio, 3),
                    "ma120_ratio": round(ma120_ratio, 3),
                    "volatility": round(vol, 1),
                    "max_dd_6m": round(max_dd, 1),
                    "ma_slope": round(ma_slope, 1),
                    "data_days": n,
                    "last_date": str(df["日期"].iloc[-1]),
                })

                if (i + 1) % 10 == 0:
                    print(f"    进度: {i+1}/{total}")

            except Exception as e:
                pass

        return results

    # =================================================================
    # 综合评分
    # =================================================================

    def _score_all(self, data: list) -> list:
        """
        综合评分 = 动量(40%) + 趋势强度(30%) + 风险调整(30%)

        动量: 近1月30% + 近3月40% + 近6月30%
        趋势: MA位置50% + MA斜率50%
        风险: 波动率越低越好50% + 回撤越小越好50%
        """
        if not data:
            return []

        df = pd.DataFrame(data)
        # 标准化函数
        def zscore(s):
            return (s - s.mean()) / s.std() if s.std() > 0 else 0

        # 动量分
        mom = (
            zscore(df["ret_1m"]) * 0.30 +
            zscore(df["ret_3m"]) * 0.40 +
            zscore(df["ret_6m"]) * 0.30
        )

        # 趋势分
        trend = (
            zscore(df["ma_ratio"]) * 0.5 +
            zscore(df["ma_slope"]) * 0.5
        )

        # 风险分 (低波动+小回撤=高分)
        risk = (
            zscore(-df["volatility"]) * 0.5 +
            zscore(-df["max_dd_6m"]) * 0.5
        )

        # 综合
        total = mom * 0.40 + trend * 0.30 + risk * 0.30
        df["score"] = total
        df["mom_score"] = mom
        df["trend_score"] = trend
        df["risk_score"] = risk

        df = df.sort_values("score", ascending=False).reset_index(drop=True)
        df["rank"] = range(1, len(df) + 1)

        return df.to_dict(orient="records")

    # =================================================================
    # 针对用户持仓的建议
    # =================================================================

    def recommend_for_portfolio(self, current_sectors: list) -> dict:
        """
        根据用户当前持仓的行业暴露，推荐：
        - 应该减配的（已过度集中）
        - 应该增加的方向（互补/对冲风险）

        Args:
            current_sectors: 用户当前持有的行业列表, e.g. ["电子", "电力设备"]
        """
        analysis = self.analyze()
        if "error" in analysis:
            return {"error": analysis["error"]}

        all_sectors = analysis.get("all_sectors", [])
        momentum = analysis.get("momentum_leaders", [])
        value = analysis.get("value_candidates", [])

        # 用户已有的行业
        held_names = set(current_sectors)

        # 找互补行业: 与持有行业低相关、表现不差
        # 简化: 推荐食品饮料、银行、公用事业、医药等防御型
        defensive = ["食品饮料", "银行", "公用事业", "医药生物", "交通运输", "家用电器"]
        complements = [s for s in all_sectors
                       if s["name"] in defensive
                       and s["name"] not in held_names]
        complements.sort(key=lambda x: x["score"], reverse=True)

        # 在动量领先中排除已持有的（避免继续集中）
        fresh_momentum = [s for s in momentum
                          if s["name"] not in held_names]

        # 在超跌中找非科技方向
        non_tech_value = [s for s in value
                          if s["name"] not in held_names
                          and s["name"] not in ["电子", "计算机", "通信", "传媒"]]

        return {
            "held_sectors": list(held_names),
            "overexposed": [s for s in all_sectors if s["name"] in held_names and s["ret_1m"] < -5],
            "complements": complements[:3],
            "fresh_momentum": fresh_momentum[:3],
            "value_non_tech": non_tech_value[:3],
            "temperature_note": "温度>60°C偏热，建议少配权益多配固收。以下推荐是在权益范围内的相对选择，不代表应该满仓。",
        }
