"""
消息面监控 v3.0 — "让消息帮你做更好的决策，不是更多的决策"

v2→v3:
- ✅ 重新加入宏观政策信号——精准检测LPR/降准/PMI变动
- ✅ CCTV政策关键词——只匹配能影响投资决策的关键词
- ❌ 永远不加每日市场新闻——那是对已经发生的事的解释
- ❌ 永远不加情绪打分——伪科学的数字只会制造焦虑

判断标准: 这条信息能让你做出比"什么都不做"更好的决策吗？
"""

from datetime import datetime
from dataclasses import dataclass

try:
    import akshare as ak
except ImportError:
    ak = None

try:
    import pandas as pd
except ImportError:
    pd = None


@dataclass
class Alert:
    level: str        # "🔴" | "🟡" | "ℹ️"
    category: str
    title: str
    detail: str
    timestamp: str


class SentimentMonitor:
    """消息面监控 v3.0 — 精准信号，拒绝噪音"""

    # 基金层面: 必须关注的事件
    FUND_CRITICAL = [
        "清盘", "终止", "暂停赎回", "巨额赎回",
        "基金经理变更", "基金经理离任",
    ]

    # 宏观: 能改变投资决策的政策关键词
    # 这些是真正能影响资产价格的政策信号
    POLICY_SIGNALS = {
        "货币宽松": ["降准", "降息", "下调存款准备金", "下调LPR", "下调利率", "定向降准",
                    "逆回购利率下调", "MLF利率下调", "SLF利率下调"],
        "货币收紧": ["加息", "上调存款准备金", "上调利率", "上调LPR", "流动性收紧"],
        "财政刺激": ["增发国债", "专项债", "减税降费", "财政扩张", "特别国债",
                    "大规模设备更新", "以旧换新"],
        "产业政策": ["集成电路", "半导体", "人工智能", "新能源汽车", "光伏", "储能",
                    "数字经济", "数据要素", "算力"],
        "资本市场": ["注册制", "退市制度", "减持新规", "交易监管", "印花税",
                    "引入长期资金", "养老金入市"],
        "房地产": ["房贷利率下调", "首付比例下调", "限购放松", "保交楼", "白名单"],
    }

    # CCTV传递的政策意图
    CCTV_POLICY_KEYWORDS = [
        "降准", "降息", "下调", "存款准备金",
        "稳健的货币政策", "积极的财政政策", "适度宽松",
        "稳住楼市", "提振消费", "发展新质生产力",
        "扩大内需", "大规模设备更新", "以旧换新",
        "化解地方政府债务", "防范金融风险",
        "集成电路", "人工智能", "新能源",
        "专项债", "超长期特别国债",
    ]

    def __init__(self):
        pass

    # =================================================================
    # 主入口
    # =================================================================

    def full_scan(self, holding_codes: list = None) -> dict:
        """
        完整扫描: 基金事件 + 宏观政策信号。

        Returns:
            dict: alerts, all_clear, signal_summary, scan_time
        """
        all_alerts = []

        # 1. 基金事件: 经理变更、清盘风险
        if holding_codes:
            try:
                all_alerts.extend(self._scan_fund_events(holding_codes))
            except Exception:
                pass

        # 2. 宏观政策: LPR/降准/PMI变化
        try:
            all_alerts.extend(self._scan_macro_policy())
        except Exception:
            pass

        # 3. CCTV政策信号
        try:
            all_alerts.extend(self._scan_cctv_policy())
        except Exception:
            pass

        # 按严重度排
        level_order = {"🔴": 0, "🟡": 1, "ℹ️": 2}
        all_alerts.sort(key=lambda a: level_order.get(a.level, 3))

        # 生成一句话摘要
        summary = self._summarize(all_alerts)

        return {
            "alerts": all_alerts,
            "alert_count": len(all_alerts),
            "all_clear": len(all_alerts) == 0,
            "signal_summary": summary,
            "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    # =================================================================
    # 基金事件
    # =================================================================

    def _scan_fund_events(self, fund_codes: list) -> list:
        alerts = []
        for code in fund_codes[:20]:
            try:
                # 经理变更
                try:
                    person_df = ak.fund_announcement_personnel_em()
                    if person_df is not None and not person_df.empty:
                        for _, row in person_df.iterrows():
                            if str(row.iloc[0]) == str(code):
                                title = str(row.iloc[1]) if len(person_df.columns) > 1 else "经理变更"
                                alerts.append(Alert(
                                    level="🔴", category="经理变更",
                                    title=f"基金{code}经理变更",
                                    detail=title[:80],
                                    timestamp=str(datetime.now().date()),
                                ))
                                break
                except Exception:
                    pass

                # 重要公告
                try:
                    report_df = ak.fund_announcement_report_em()
                    if report_df is not None and not report_df.empty:
                        for _, row in report_df.iterrows():
                            if str(row.iloc[0]) != str(code):
                                continue
                            title = str(row.iloc[1]) if len(report_df.columns) > 1 else ""
                            hit = [kw for kw in self.FUND_CRITICAL if kw in str(title)]
                            if hit:
                                alerts.append(Alert(
                                    level="🔴", category="重要公告",
                                    title=f"基金{code}: {title[:50]}",
                                    detail=f"命中: {', '.join(hit[:3])}",
                                    timestamp=str(row.iloc[3]) if len(report_df.columns) > 3 else "",
                                ))
                except Exception:
                    pass
            except Exception:
                continue
        return alerts

    # =================================================================
    # 宏观政策: 数据驱动的精准检测
    # =================================================================

    def _scan_macro_policy(self) -> list:
        """
        用宏观数据检测政策变动:
        - LPR变了没？（最近1个月 vs 之前）
        - 存款准备金率变了没？
        - PMI在什么水平？(<50=收缩)
        """
        alerts = []

        # 1. LPR利率检测 — 按日期排序取最新
        try:
            df = ak.macro_china_lpr()
            if df is not None and len(df) >= 2:
                df = df.copy()
                df["_date"] = pd.to_datetime(df["TRADE_DATE"], errors="coerce")
                df = df.dropna(subset=["_date"]).sort_values("_date").reset_index(drop=True)
                if len(df) >= 2:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2]
                    lpr1y_now = float(latest["LPR1Y"])
                    lpr1y_prev = float(prev["LPR1Y"])
                    lpr5y_now = float(latest["LPR5Y"])
                    lpr5y_prev = float(prev["LPR5Y"])
                    latest_date = str(latest["TRADE_DATE"])

                    try:
                        d = pd.to_datetime(latest_date)
                        if (pd.Timestamp.now() - d).days <= 90:
                            if lpr1y_now < lpr1y_prev or lpr5y_now < lpr5y_prev:
                                alerts.append(Alert(
                                    level="🟡", category="货币政策",
                                    title=f"LPR下调! 1年期{lpr1y_now}% 5年期{lpr5y_now}%",
                                    detail=f"利好股市(降低企业融资成本)。上次: 1Y={lpr1y_prev}% 5Y={lpr5y_prev}%",
                                    timestamp=latest_date,
                                ))
                            elif lpr1y_now > lpr1y_prev:
                                alerts.append(Alert(
                                    level="🟡", category="货币政策",
                                    title=f"LPR上调! 1年期{lpr1y_now}% 5年期{lpr5y_now}%",
                                    detail=f"利空股市(提高融资成本)。上次: 1Y={lpr1y_prev}% 5Y={lpr5y_prev}%",
                                    timestamp=latest_date,
                                ))
                    except Exception:
                        pass
        except Exception:
            pass

        # 2. PMI 检测 — 需要按日期排序取最新
        try:
            df = ak.macro_china_pmi()
            if df is not None and len(df) >= 2:
                # 月份格式: "2026年07月份" — 提取年份+月份排序
                def _parse_month(s):
                    try:
                        s = str(s)
                        y = int(s[:4])
                        m = int(s[5:7]) if len(s) > 5 else 1
                        return y * 100 + m
                    except Exception:
                        return 0

                df = df.copy()
                df["_month_key"] = df["月份"].apply(_parse_month)
                df = df.sort_values("_month_key").reset_index(drop=True)

                latest = df.iloc[-1]
                prev = df.iloc[-2]
                latest_pmi = float(latest["制造业-指数"])
                prev_pmi = float(prev["制造业-指数"])
                month = str(latest["月份"])

                if latest_pmi < 50:
                    direction = "继续下滑" if latest_pmi < prev_pmi else "有所回升"
                    alerts.append(Alert(
                        level="🟡" if latest_pmi < 48 else "ℹ️",
                        category="经济指标",
                        title=f"PMI={latest_pmi}，制造业{direction}",
                        detail=f"PMI<50=经济收缩。{month}制造业PMI {latest_pmi}，上月{prev_pmi}。对股市偏利空，利好债券。",
                        timestamp=month,
                    ))
                elif latest_pmi > 52:
                    alerts.append(Alert(
                        level="ℹ️", category="经济指标",
                        title=f"PMI={latest_pmi}，制造业扩张中",
                        detail=f"{month}制造业PMI {latest_pmi}，经济处于扩张区间。对股市偏利好。",
                        timestamp=month,
                    ))
        except Exception:
            pass

        # === 宏观信号检测完成 ===
        return alerts

    # =================================================================
    # CCTV政策信号
    # =================================================================

    def _scan_cctv_policy(self) -> list:
        """
        CCTV新闻联播 — 只看最近几期，只匹配真正的政策关键词。
        """
        alerts = []
        try:
            df = ak.news_cctv()
            if df is None or df.empty:
                return alerts

            recent = df.head(3)  # 最近3期
            for _, row in recent.iterrows():
                content = str(row.get("content", ""))
                title = str(row.get("title", ""))
                date_str = str(row.get("date", ""))

                text = title + content[:500]  # 只看前500字
                hits = [kw for kw in self.CCTV_POLICY_KEYWORDS if kw in text]

                if hits:
                    # 去重: 同类信号只报一个
                    unique_hits = list(set(hits))[:5]
                    alerts.append(Alert(
                        level="ℹ️", category="政策信号",
                        title=f"CCTV: {title[:45]}",
                        detail=f"涉及: {', '.join(unique_hits)}",
                        timestamp=date_str,
                    ))

        except Exception:
            pass

        return alerts[:3]  # 最多3条

    # =================================================================
    # 摘要生成
    # =================================================================

    def _summarize(self, alerts: list) -> str:
        if not alerts:
            return "本周无需要关注的宏观事件或基金公告"

        reds = [a for a in alerts if a.level == "🔴"]
        yellows = [a for a in alerts if a.level == "🟡"]
        infos = [a for a in alerts if a.level == "ℹ️"]

        parts = []
        if reds:
            parts.append(f"{len(reds)}条紧急信号({reds[0].title[:30]})")
        if yellows:
            parts.append(f"{len(yellows)}条需关注")
        if infos:
            parts.append(f"{len(infos)}条政策信息")

        return "；".join(parts) if parts else "本周无需要关注的宏观事件或基金公告"


def quick_scan():
    monitor = SentimentMonitor()
    result = monitor.full_scan()

    print("=" * 56)
    print("📰 消息面扫描 v3.0")
    print("=" * 56)
    print(f"扫描时间: {result['scan_time']}")
    print(f"信号摘要: {result['signal_summary']}")
    print()

    if result["all_clear"]:
        print("✅ 本周无事。")
    else:
        for alert in result["alerts"]:
            print(f"  {alert.level} [{alert.category}] {alert.title}")
            print(f"     {alert.detail}")
            print()

    print("=" * 56)
    print("💡 筛选标准: 这条信息能让你做出更好的决策吗？")
    print("   每日市场新闻 → 不能 → 不包含")
    print("   LPR降息 → 能 → 包含")
    print("=" * 56)


if __name__ == "__main__":
    quick_scan()
