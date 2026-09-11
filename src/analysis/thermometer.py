"""
市场温度计 v2.0: 综合判断当前市场估值水平。

升级内容 (v1→v2):
- ✅ 成交量维度: 使用真实沪深300成交量分位数；**无数据时剔除该维度并按剩余权重重新归一化**，
  不再静默注入 50.0（缺失维度会在 degraded_dimensions 里声明）
- ✅ 市场情绪维度: 使用价格波动率+涨跌幅+成交量趋势合成；量能缺失时按剩余子项重新归一化
- ✅ 多指数综合: 从只看沪深300 → 沪深300+中证500+上证50 三指数综合
  （按**实际存在的指数**权重之和归一化，缺指数不再把结果整体减半）
- ✅ 估值分化检测: 新增"分歧度"指标，PE/PB信号不一致时发出警告
- ✅ 市场风格判断: 判断当前是 价值/成长、大盘/小盘 哪种风格占优

核心理念不变: 不预测短期涨跌，只判断"当前是便宜还是贵"。
"""

import pandas as pd
import numpy as np
import os
from typing import Optional
from ..data.database import Database


def _live_default() -> bool:
    """是否允许联网取实时市场数据。默认联网；设 QFA_MARKET_LIVE=0 则全部走本地 DB 快照。
    Web 仪表盘应离线（读 DB），以换取首屏速度与稳定。"""
    return os.environ.get("QFA_MARKET_LIVE", "1") not in ("0", "false", "False", "no")


class MarketThermometer:
    """市场温度计 v2.0"""

    DEFAULT_WEIGHTS = {
        "pe_percentile": 0.25,
        "pb_percentile": 0.15,
        "erp": 0.25,
        "volume": 0.20,
        "sentiment": 0.15,
    }

    # 沪深300 在本地 index_daily 中的代码（000300 == akshare 的 sh000300）
    HS300_CODE = "000300"

    TEMP_LEVELS = {
        "cold":   (0,  20, "🧊 极冷", "极度恐慌，可大胆加仓", 0.70),
        "cool":   (20, 40, "💧 偏冷", "市场低迷，适度加仓",   0.55),
        "normal": (40, 60, "🌤️ 适中", "正常水平，保持定投",   0.35),
        "warm":   (60, 80, "🔥 偏热", "情绪高涨，减少买入",   0.20),
        "hot":    (80, 101,"☀️ 过热", "极度贪婪，考虑减仓",   0.05),
    }

    # 申万风格指数 (数据到2026-08-07, 可靠)
    STYLE_INDICES = {
        "801811": {"name": "申万大盘",     "style": "blend", "cap": "large"},
        "801812": {"name": "申万中盘",     "style": "blend", "cap": "mid"},
        "801813": {"name": "申万小盘",     "style": "blend", "cap": "small"},
        "801821": {"name": "申万高PE(成长)", "style": "growth", "cap": "all"},
        "801822": {"name": "申万低PE(价值)", "style": "value",  "cap": "all"},
    }

    def __init__(self, db: Database, weights: dict = None, live: Optional[bool] = None):
        self.db = db
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.live = _live_default() if live is None else live
        self._lpr_cache = None  # 缓存LPR利率

    # =================================================================
    # 主接口
    # =================================================================

    def get_temperature(self) -> dict:
        """
        计算当前市场温度及完整诊断信息。

        缺失维度（无数据/样本不足）会被**剔除，并按剩余维度的权重重新归一化**，
        而不是静默注入 50.0；缺失项在 degraded_dimensions 中声明，便于上层提示。

        Returns:
            dict: 温度、等级、各维度分解、风格判断、仓位建议
        """
        pe_score, pb_score, erp_score = self._calc_valuation_scores()
        volume_score = self._calc_volume_score()
        sentiment_score = self._calc_sentiment_score()

        dims = [
            ("pe_percentile", "pe_score", pe_score),
            ("pb_percentile", "pb_score", pb_score),
            ("erp", "erp_score", erp_score),
            ("volume", "volume_score", volume_score),
            ("sentiment", "sentiment_score", sentiment_score),
        ]
        used = [(w, v) for w, _, v in dims if v is not None]
        degraded = [w for w, _, v in dims if v is None]

        total_w = sum(self.weights[w] for w, _ in used)
        if total_w > 0:
            temperature = sum(self.weights[w] * v for w, v in used) / total_w
        else:
            temperature = 50.0
            degraded = [w for w, _, _ in dims]

        level, level_desc, action, _ = self._classify_temperature(temperature)

        # 估值分化检测（只看实际可用的估值维度）
        divergence = self._detect_divergence(pe_score, pb_score, erp_score)

        # 市场风格
        style = self._detect_market_style()

        # 仓位: 温度映射 + 分歧修正 (借鉴 ai-hedge-fund: 分歧→减仓)
        base_equity = self._calc_target_equity(temperature) / 100.0
        if divergence["level"] == "显著分歧":
            base_equity *= 0.85
        elif divergence["level"] == "轻微分歧":
            base_equity *= 0.95

        def _r(v):
            return float(round(v, 1)) if v is not None else None

        return {
            "temperature": float(round(temperature, 1)),
            "level": level,
            "level_desc": level_desc,
            "action": action,
            "components": {
                "pe_score": _r(pe_score),
                "pb_score": _r(pb_score),
                "erp_score": _r(erp_score),
                "volume_score": _r(volume_score),
                "sentiment_score": _r(sentiment_score),
            },
            "degraded_dimensions": degraded,
            "divergence": divergence,
            "market_style": style,
            "target_equity_pct": round(base_equity * 100, 1),
        }


    # =================================================================
    # 估值维度（从数据库读取）
    # =================================================================

    def _calc_valuation_scores(self) -> tuple:
        """从数据库读取PE/PB/ERP分位数，计算三个估值维度的温度。

        PE/PB 按**实际存在的指数**权重之和归一化（缺指数不再把结果整体减半）；
        完全无数据时返回 None，由 get_temperature 剔除该维度。
        """
        index_val = self.db.get_index_valuation()

        hs300 = self._find_index(index_val, "000300")
        zz500 = self._find_index(index_val, "000905")
        sz50  = self._find_index(index_val, "000016")

        # PE/PB分位数: 三指数加权（沪深300 50% + 中证500 30% + 上证50 20%）
        pe_num = pe_w = 0.0
        pb_num = pb_w = 0.0
        for idx, weight in [(hs300, 0.5), (zz500, 0.3), (sz50, 0.2)]:
            if not idx:
                continue
            pe = idx.get("pe_percentile")
            pb = idx.get("pb_percentile")
            if pe is not None:
                pe_num += float(pe) * weight
                pe_w += weight
            if pb is not None:
                pb_num += float(pb) * weight
                pb_w += weight

        pe_score = pe_num / pe_w if pe_w > 0 else None
        pb_score = pb_num / pb_w if pb_w > 0 else None

        # ERP: 使用沪深300 PE + 实时LPR(1年期)作为无风险利率
        erp_score = None
        if hs300:
            pe = hs300.get("pe", 0)
            if pe and pe > 0:
                earnings_yield = (1.0 / float(pe)) * 100
                bond_yield = self._get_risk_free_rate()
                erp = earnings_yield - bond_yield
                erp_temp = 100 - (erp - 2.0) / (6.5 - 2.0) * 100
                erp_score = max(5.0, min(95.0, erp_temp))

        return pe_score, pb_score, erp_score

    def _get_risk_free_rate(self) -> float:
        """获取无风险利率: 1年期LPR。联网模式实时拉取；离线模式回退 3.0%"""
        if not self.live:
            return 3.0
        if self._lpr_cache is not None:
            return self._lpr_cache
        try:
            import akshare as ak
            import pandas as pd
            df = ak.macro_china_lpr()
            df["_d"] = pd.to_datetime(df["TRADE_DATE"], errors="coerce")
            df = df.dropna(subset=["_d"]).sort_values("_d")
            rate = float(df.iloc[-1]["LPR1Y"])
            self._lpr_cache = rate
            return rate
        except Exception:
            return 3.0  # fallback

    def _hs300_df(self) -> Optional[pd.DataFrame]:
        """沪深300 日线(close/volume)。联网取 akshare；离线读本地 index_daily 快照。
        均返回与 akshare 兼容的 DataFrame（含 volume/close），失败返回 None。"""
        if self.live:
            try:
                import akshare as ak
                return ak.stock_zh_index_daily(symbol="sh000300")
            except Exception:
                return None
        try:
            rows = self.db.get_index_daily(self.HS300_CODE)
            if not rows:
                return None
            return pd.DataFrame(rows)  # close, volume, trade_date, ...
        except Exception:
            return None

    # =================================================================
    # 成交量维度 v2: 使用真实数据
    # =================================================================

    def _calc_volume_score(self) -> Optional[float]:
        """
        成交量温度: 使用沪深300真实成交量历史分位数。

        从 index_daily 表读取成交量数据，计算最近20日均量在历史中的位置。
        天量→市场过热(高温)，缩量→市场冷清(低温)。

        无数据/样本不足时返回 **None**（由调用方剔除该维度并重新归一化），
        绝不返回 50.0 —— 那会让"数据缺失"伪装成一个中性的真实读数。

        同时结合"量价关系"判断:
        - 价跌量缩: 恐慌出清 → 可能底部
        - 价跌量增: 恐慌抛售 → 不确定
        - 价涨量增: 健康上涨 → 中性
        - 价涨量缩: 犹豫上涨 → 可能顶部
        """
        try:
            df = self._hs300_df()
            if df is None or df.empty or "volume" not in df.columns:
                return None
            volumes = pd.to_numeric(df["volume"], errors="coerce").dropna()

            if len(volumes) < 100:
                return None

            # 1) 成交量分位数 (天量=高温)
            vol_20d = volumes.iloc[-20:].mean()
            vol_pct = (volumes < vol_20d).sum() / len(volumes) * 100
            vol_temp = vol_pct  # 0-100, 越热=温度越高

            # 2) 量价关系修正 (±15°)
            close = pd.to_numeric(df["close"], errors="coerce").dropna()
            price_change_20d = (close.iloc[-1] / close.iloc[-20] - 1) * 100
            vol_change_20d = (vol_20d / volumes.iloc[-40:-20].mean() - 1) * 100

            adjustment = 0
            if price_change_20d < -3 and vol_change_20d < -20:
                adjustment = -10  # 价跌量缩: 底部信号，降温
            elif price_change_20d > 5 and vol_change_20d < -10:
                adjustment = +10  # 价涨量缩: 顶部信号，升温
            elif price_change_20d < -5 and vol_change_20d > 20:
                adjustment = +5   # 价跌量增: 恐慌，升温

            return max(5.0, min(95.0, vol_temp + adjustment))

        except Exception:
            return None

    # =================================================================
    # 情绪维度 v2: 合成指标替代 crashed fund_new_found API
    # =================================================================

    def _calc_sentiment_score(self) -> Optional[float]:
        """
        市场情绪温度: 使用波动率+涨跌幅+量价关系合成。

        fund_new_found_em/ths 在 Windows 上 CFFI segfault，改用合成指标:
        - 近期波动率分位数 (30%): 高波动=恐慌或亢奋=偏热
        - 近期涨跌幅 (30%): 暴涨=贪婪=偏热，暴跌=恐慌=中性偏冷
        - 成交量趋势 (40%): 持续放量=情绪升温

        量能缺失时**剔除该子项，按剩余子项重新归一化**；完全没有可用数据时返回 None。
        """
        try:
            df = self._hs300_df()
            if df is None or df.empty or "close" not in df.columns:
                return None
            close = pd.to_numeric(df["close"], errors="coerce").dropna()
            volumes = (pd.to_numeric(df["volume"], errors="coerce").dropna()
                       if "volume" in df.columns else pd.Series(dtype=float))

            if len(close) < 100:
                return None

            parts = []  # (子项温度, 权重)

            # 1) 波动率分位数 (30%)
            returns = close.pct_change().dropna()
            vol_20d = returns.iloc[-20:].std() * np.sqrt(252) * 100  # 年化波动率
            all_vols = returns.rolling(20).std().dropna() * np.sqrt(252) * 100
            if len(all_vols) > 0:
                vol_pct = (all_vols < vol_20d).sum() / len(all_vols) * 100  # 高波动=热
                parts.append((vol_pct, 0.30))

            # 2) 近期涨跌幅 (30%)（暴涨=贪婪，赋予高温）
            ret_20d = (close.iloc[-1] / close.iloc[-20] - 1) * 100
            ret_60d = (close.iloc[-1] / close.iloc[-60] - 1) * 100 if len(close) >= 60 else ret_20d
            # 短期大涨+中长期温和=正常；短期暴涨+中长期也暴涨=过热
            if ret_20d > 10 and ret_60d > 30:
                ret_temp = 90   # 暴涨模式，过热
            elif ret_20d > 5:
                ret_temp = 65   # 偏热
            elif ret_20d < -10:
                ret_temp = 20   # 暴跌后，恐慌出清=偏冷
            elif ret_20d < -5:
                ret_temp = 35   # 下跌中
            else:
                ret_temp = 50   # 正常
            parts.append((ret_temp, 0.30))

            # 3) 成交量趋势 (40%) —— 无量能数据则剔除该子项
            if len(volumes) >= 65:
                vol_5d = volumes.iloc[-5:].mean()
                vol_60d = volumes.iloc[-60:-5].mean()
                if vol_60d > 0:
                    vol_ratio = vol_5d / vol_60d
                    if vol_ratio > 2.0:
                        vol_trend_temp = 85   # 异常放量
                    elif vol_ratio > 1.5:
                        vol_trend_temp = 65
                    elif vol_ratio > 1.2:
                        vol_trend_temp = 55
                    elif vol_ratio < 0.6:
                        vol_trend_temp = 20   # 极度缩量
                    elif vol_ratio < 0.8:
                        vol_trend_temp = 35
                    else:
                        vol_trend_temp = 50
                    parts.append((vol_trend_temp, 0.40))

            if not parts:
                return None

            w_sum = sum(w for _, w in parts)
            sentiment = sum(v * w for v, w in parts) / w_sum
            return max(5.0, min(95.0, sentiment))

        except Exception:
            return None

    # =================================================================
    # 估值分化检测 v2 新增
    # =================================================================

    def _detect_divergence(self, pe_score: float, pb_score: float, erp_score: float) -> dict:
        """
        检测PE/PB/ERP之间的分歧程度。

        当PE分位高但PB分位低时（意味着利润下滑但资产还值钱），
        这是典型的"盈利下行期"，估值信号不可靠。
        """
        signals = [s for s in (pe_score, pb_score, erp_score) if s is not None]
        if len(signals) < 2:
            return {"level": "未知", "message": "估值维度数据不足，无法判断分歧", "max_diff": 0.0}
        max_diff = max(signals) - min(signals)

        if max_diff < 15:
            level = "一致"
            msg = "PE/PB/ERP 三个维度信号一致，判断可靠度较高"
        elif max_diff < 30:
            level = "轻微分歧"
            msg = "估值信号有分歧，建议结合其他信息判断"
        else:
            # 检查具体是哪对分歧
            if pe_score - pb_score > 20:
                msg = "PE偏高但PB偏低：可能处于盈利下行期，企业利润减少但资产价值还在。估值信号不完全可靠，建议谨慎。"
            elif pb_score - pe_score > 20:
                msg = "PB偏高但PE偏低：可能处于盈利上行期，资产重估中。可适度积极。"
            else:
                msg = "估值信号明显分歧，建议降低仓位等待信号一致"
            level = "显著分歧"

        return {"level": level, "message": msg, "max_diff": round(max_diff, 1)}

    # =================================================================
    # 市场风格判断 v2 新增
    # =================================================================

    def _detect_market_style(self) -> dict:
        """
        判断当前市场的主导风格：大盘/小盘 + 价值/成长。

        使用申万风格指数(index_hist_sw)，数据到2026-08-07。
        801811 大盘 | 801812 中盘 | 801813 小盘
        801821 高PE(成长) | 801822 低PE(价值)
        """
        try:
            if not self.live:
                # 本地 DB 无申万风格指数日线 -> 离线不联网，返回 unknown
                return {"dominant": "unknown", "detail": "离线模式未提供申万风格指数数据"}
            import akshare as ak

            style_returns = {}
            for code, info in self.STYLE_INDICES.items():
                try:
                    df = ak.index_hist_sw(symbol=code, period="day")
                    close = pd.to_numeric(df["收盘"], errors="coerce").dropna()
                    if len(close) >= 20:
                        ret_20d = (close.iloc[-1] / close.iloc[-20] - 1) * 100
                        style_returns[code] = {
                            "name": info["name"],
                            "return_20d": round(ret_20d, 2),
                            "style": info["style"],
                            "cap": info["cap"],
                        }
                except Exception:
                    pass

            if not style_returns:
                return {"dominant": "unknown", "detail": "无法获取风格指数数据"}

            # 价值(801822 低PE) vs 成长(801821 高PE)
            value_ret = style_returns.get("801822", {}).get("return_20d", 0)
            growth_ret = style_returns.get("801821", {}).get("return_20d", 0)

            if value_ret > growth_ret + 1:
                style_dom = "价值"
                style_detail = f"价值(低PE)跑赢成长(高PE) {value_ret - growth_ret:.1f}%"
            elif growth_ret > value_ret + 1:
                style_dom = "成长"
                style_detail = f"成长(高PE)跑赢价值(低PE) {growth_ret - value_ret:.1f}%"
            else:
                style_dom = "均衡"
                style_detail = "价值与成长表现接近"

            # 大盘(801811) vs 小盘(801813)
            large_ret = style_returns.get("801811", {}).get("return_20d", 0)
            small_ret = style_returns.get("801813", {}).get("return_20d", 0)

            if large_ret > small_ret + 1:
                cap_dom = "大盘"
                cap_detail = f"大盘跑赢小盘 {large_ret - small_ret:.1f}%"
            elif small_ret > large_ret + 1:
                cap_dom = "小盘"
                cap_detail = f"小盘跑赢大盘 {small_ret - large_ret:.1f}%"
            else:
                cap_dom = "均衡"
                cap_detail = "大小盘表现接近"

            return {
                "dominant": f"{cap_dom}{style_dom}",
                "style": style_dom,
                "cap": cap_dom,
                "detail": f"{cap_detail} | {style_detail}",
                "returns": {v["name"]: v["return_20d"] for v in style_returns.values()},
                "data_date": str(df["日期"].iloc[-1]) if style_returns else "unknown",
            }

        except Exception:
            return {"dominant": "unknown", "detail": "风格判断数据获取失败"}

    # =================================================================
    # 工具方法
    # =================================================================

    def _find_index(self, index_val: list, code: str) -> Optional[dict]:
        """按 index_code **精确匹配**查找指数。

        旧实现用子串匹配（如 "50" 也能命中 "中证500"），命中结果取决于列表顺序；
        改为精确匹配，杜绝这类误配。
        """
        for val in index_val:
            if str(val.get("index_code", "") or "") == str(code):
                return val
        return None

    # 温度→权益仓位的锚点（取各档位中点）。锚点之间线性插值 → 仓位随温度连续变化，
    # 避免"39.9°→55%、40.0°→35%"这类跨档 20 个百分点的跳变。
    EQUITY_ANCHORS = ((10.0, 0.70), (30.0, 0.55), (50.0, 0.35), (70.0, 0.20), (90.0, 0.05))

    def _classify_temperature(self, temp: float) -> tuple:
        """根据温度数值返回 (level, desc, action, target_equity)

        target_equity 为**档位语义值**（用于文案/展示）；数值化的仓位建议走
        _calc_target_equity 的插值，不要直接用这里的档位值。
        """
        for level, (low, high, desc, action, equity) in self.TEMP_LEVELS.items():
            if low <= temp < high:
                return level, desc, action, equity
        return "normal", "🌤️ 适中", "正常水平，保持定投", 0.35

    def _calc_target_equity(self, temp: float) -> float:
        """温度→权益仓位映射（百分比）。在 EQUITY_ANCHORS 锚点之间线性插值，单调不增。"""
        anchors = self.EQUITY_ANCHORS
        if temp <= anchors[0][0]:
            eq = anchors[0][1]
        elif temp >= anchors[-1][0]:
            eq = anchors[-1][1]
        else:
            eq = anchors[-1][1]
            for (t0, e0), (t1, e1) in zip(anchors, anchors[1:]):
                if t0 <= temp <= t1:
                    eq = e0 + (e1 - e0) * (float(temp) - t0) / (t1 - t0)
                    break
        # float() 必要：temp 常是 numpy 标量，np.float64 会顺着算出 np.bool_
        # 之类的非 JSON 可序列化类型（Web 端 jsonify 会直接 500）。
        return float(round(eq * 100, 1))

    def get_volume_history(self, days: int = 60) -> pd.DataFrame:
        """获取近期成交量数据，用于报告展示"""
        try:
            if not self.live:
                df = self._hs300_df()
                if df is None or df.empty:
                    return pd.DataFrame()
                df = df.tail(days).copy()
                df["date"] = pd.to_datetime(df["trade_date"], errors="coerce")
                return df[["date", "close", "volume"]]
            import akshare as ak
            df = ak.stock_zh_index_daily(symbol="sh000300")
            df = df.tail(days).copy()
            df["date"] = pd.to_datetime(df["date"])
            return df[["date", "close", "volume"]]
        except Exception:
            return pd.DataFrame()
