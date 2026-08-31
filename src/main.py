"""
🚀 量化基金系统 v3.0

用法:
    python src/main.py              # 每周报告(温度+仓位建议)
    python src/main.py temp         # 市场温度详情
    python src/main.py score        # 基金质量筛选(🟢🟡🔴)
    python src/main.py sector       # 31行业板块排名+推荐
    python src/main.py recommend    # 历史验证基金推荐
    python src/main.py rebalance    # 持仓调仓建议
    python src/main.py sentiment    # 消息面监控
    python src/main.py portfolio    # 持仓盈亏
    python src/main.py buy/sell     # 买入/卖出记录
    python src/main.py plan         # 投资计划+进度
    python src/main.py web          # 启动Web仪表盘
    python src/main.py schedule     # 定时调度(每周日自动生成周报)
    python src/main.py init           # 一键初始化(首次使用)
    python src/main.py collect/nav/enrich  # 分步数据采集
"""

import sys
import os

# 修复 Windows GBK 编码问题：强制 stdout/stderr 使用 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import Database
from src.data.collector import DataCollector, quick_test
from src.data.hithink_collector import HiThinkCollector, quick_test as hithink_quick_test
from src.analysis.fund_scorer import FundScorer, FundScreener
from src.analysis.thermometer import MarketThermometer
from src.analysis.portfolio import PortfolioTracker
from src.analysis.strategy_engine import StrategyEngine
from src.analysis.rebalance_advisor import RebalanceAdvisor
from src.analysis.sentiment_monitor import SentimentMonitor, quick_scan as sentiment_quick_scan
from src.analysis.sector_analyzer import SectorAnalyzer
from src.analysis.historical_recommender import HistoricalRecommender
from src.analysis.investment_plan import get_plan, get_progress
from src.output.reporter import WeeklyReporter

# 读取环境变量中的 API Key
def _load_api_key():
    try:
        with open(".env") as f:
            for line in f:
                if line.startswith("HITHINK_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""

HITHINK_KEY = _load_api_key()


def cmd_test():
    """Phase 0: 测试数据接口"""
    quick_test()


def cmd_init():
    """一键初始化: 测试接口 → 采集数据 → 采集净值 → 补充详情（首次使用执行一次）"""
    print("=" * 60)
    print("🚀 一键初始化（首次使用执行一次，约 5-10 分钟）")
    print("=" * 60)
    print()

    print("步骤 1/4: 测试数据接口...")
    cmd_test()
    print()

    print("步骤 2/4: 采集基金列表与指数估值...")
    cmd_collect()
    print()

    print("步骤 3/4: 采集基金净值历史（最耗时，请耐心等待）...")
    cmd_nav()
    print()

    print("步骤 4/4: 补充基金详情...")
    cmd_enrich()
    print()

    print("=" * 60)
    print("✅ 初始化完成！现在运行 python src/main.py 查看周报")
    print("=" * 60)


def cmd_schedule():
    """定时调度: 每周日 20:00 自动生成周报（依赖 schedule 库）"""
    try:
        import schedule
        import time
    except ImportError:
        print("❌ 未安装 schedule 库，请先执行: pip install schedule")
        return

    print("⏰ 定时调度已启动")
    print("   每周日 20:00 自动生成周报")
    print("   按 Ctrl+C 停止")
    print()

    schedule.every().sunday.at("20:00").do(cmd_report)

    while True:
        schedule.run_pending()
        time.sleep(60)


def cmd_index():
    """采集指数PE/PB估值历史（独立运行，不依赖基金数据）"""
    import time
    db = Database("data/fund_quant.db")
    collector = DataCollector(db)

    print("📡 采集指数PE/PB估值...")
    print()

    indices = {
        "000300": {"name": "沪深300", "pe_sym": "沪深300", "pb_sym": "沪深300"},
        "000905": {"name": "中证500", "pe_sym": "中证500", "pb_sym": "中证500"},
        "000016": {"name": "上证50",  "pe_sym": "上证50",  "pb_sym": "上证50"},
    }

    for code, info in indices.items():
        print(f"  [{info['name']}] 采集PE/PB ...")
        try:
            pe_df = ak.stock_index_pe_lg(symbol=info["pe_sym"])
            print(f"      PE: {len(pe_df)} 条 ({str(pe_df['日期'].iloc[0])[:10]} ~ {str(pe_df['日期'].iloc[-1])[:10]})")
            time.sleep(0.5)

            pb_df = ak.stock_index_pb_lg(symbol=info["pb_sym"])
            print(f"      PB: {len(pb_df)} 条 ({str(pb_df['日期'].iloc[0])[:10]} ~ {str(pb_df['日期'].iloc[-1])[:10]})")
            time.sleep(0.5)

            collector.save_index_val_to_db(code, pe_df, pb_df)
            print(f"      ✅ 已入库")
        except Exception as e:
            # 重试一次
            print(f"      ⚠️ 首次失败: {e}，等5秒重试...")
            time.sleep(5)
            try:
                pe_df = ak.stock_index_pe_lg(symbol=info["pe_sym"])
                pb_df = ak.stock_index_pb_lg(symbol=info["pb_sym"])
                collector.save_index_val_to_db(code, pe_df, pb_df)
                print(f"      ✅ 重试成功")
            except Exception as e2:
                print(f"      ❌ 仍失败: {e2}")

    db.close()
    print()
    print("=" * 60)
    print("✅ 指数估值采集完成！")
    print("💡 下一步: python src/main.py temp → 查看市场温度")
    print("=" * 60)


def cmd_collect():
    """采集数据（基金列表+指数估值）"""
    db = Database("data/fund_quant.db")
    collector = DataCollector(db)

    print("📡 正在采集数据...")

    # 步骤1: 采集基金名称+类型列表
    print("  [1/4] 采集基金名称列表...")
    try:
        name_df = ak.fund_name_em()
        print(f"        ✅ 获取 {len(name_df)} 只基金")
    except Exception as e:
        print(f"        ❌ 失败: {e}")
        db.close()
        return

    # 步骤2: 采集基金每日净值和费率
    print("  [2/4] 采集基金每日数据...")
    try:
        daily_df = ak.fund_open_fund_daily_em()
        print(f"        ✅ 获取 {len(daily_df)} 条数据")
    except Exception as e:
        print(f"        ❌ 失败: {e}")
        db.close()
        return

    # 合并入库
    print("  [3/4] 合并数据并入库...")
    try:
        collector.save_fund_list_to_db(name_df, daily_df)
        fund_count = len(name_df)
        print(f"        ✅ 已入库 {fund_count} 只基金基本信息")
    except Exception as e:
        print(f"        ❌ 失败: {e}")
        import traceback
        traceback.print_exc()

    # 步骤4: 采集指数估值
    print("  [4/4] 采集指数PE/PB估值...")
    try:
        index_results = collector.collect_all_index_valuations()
        for code, data in index_results.items():
            if "pe" in data and "pb" in data:
                collector.save_index_val_to_db(code, data["pe"], data["pb"])
                print(f"        ✅ {code} 指数估值已保存")
            else:
                print(f"        ⚠️ {code} PE/PB数据不完整，跳过")
    except Exception as e:
        print(f"        ❌ 失败: {e}")

    db.close()
    print()
    print("=" * 60)
    print("✅ 数据采集完成！")
    print(f"   基金总数: {len(name_df)} 只")
    print(f"   指数估值: 已更新")
    print()
    print("💡 下一步:")
    print("   python src/main.py nav        → 采集Top基金的净值历史（评分需要）")
    print("   python src/main.py score      → 查看基金评分排名")
    print("   python src/main.py            → 生成完整周报")
    print("=" * 60)


def cmd_nav():
    """
    采集净值历史+基金详情: 从各类型基金中分层采样。
    同时获取基金详细信息(规模/经理/成立日)以支撑质量筛选。
    """
    import sqlite3
    db = Database("data/fund_quant.db")
    collector = DataCollector(db)
    cur = db.conn.cursor()

    samples = [
        ("偏股型", "WHERE fund_type LIKE '%偏股%' AND (purchase_status = '' OR purchase_status LIKE '%开放%') ORDER BY mgt_fee ASC LIMIT 200"),
        ("混合灵活型", "WHERE fund_type LIKE '%混合型-灵活%' AND (purchase_status = '' OR purchase_status LIKE '%开放%') ORDER BY mgt_fee ASC LIMIT 150"),
        ("股票型", "WHERE fund_type = '股票型' AND (purchase_status = '' OR purchase_status LIKE '%开放%') ORDER BY mgt_fee ASC LIMIT 100"),
        ("指数型", "WHERE fund_type LIKE '%指数型-股票%' AND (purchase_status = '' OR purchase_status LIKE '%开放%') ORDER BY mgt_fee ASC LIMIT 100"),
        ("债券型", "WHERE fund_type LIKE '%债券型%' AND (purchase_status = '' OR purchase_status LIKE '%开放%') ORDER BY mgt_fee ASC LIMIT 50"),
    ]

    all_candidates = []
    for label, where_clause in samples:
        cur.execute(f"SELECT fund_code, fund_name, fund_type, mgt_fee FROM fund_info {where_clause}")
        candidates = cur.fetchall()
        all_candidates.extend(candidates)
        print(f"  {label}: 筛选出 {len(candidates)} 只")

    seen = set()
    unique_candidates = []
    for c in all_candidates:
        if c[0] not in seen:
            seen.add(c[0])
            unique_candidates.append(c)

    total = len(unique_candidates)
    print(f"\n📊 去重后共 {total} 只候选基金")
    print(f"   步骤1: 采集净值历史 + 基金详情...")
    print()

    success_nav = 0
    success_detail = 0
    fail_count = 0
    for i, (code, name, ftype, fee) in enumerate(unique_candidates):
        # 采集净值
        try:
            df = collector.collect_fund_nav(code)
            collector.save_fund_nav_batch(code, df)
            success_nav += 1
        except Exception:
            fail_count += 1

        # 采集基金详情(规模/经理/成立日) — 关键数据,支撑质量筛选
        try:
            detail = collector.collect_fund_detail(code)
            if detail:
                collector.save_fund_detail_to_db(code, detail)
                success_detail += 1
        except Exception:
            pass

        if (i + 1) % 50 == 0:
            print(f"   进度: {i+1}/{total} (净值{success_nav} ok, 详情{success_detail} ok, {fail_count} fail)")

    db.close()
    print()
    print("=" * 60)
    print(f"✅ 采集完成！净值: {success_nav} 只, 基金详情: {success_detail} 只, 失败: {fail_count} 只")
    print(f"   质量筛选现在可以使用真实的规模/经理/成立日数据了")
    print()
    print("💡 下一步:")
    print("   python src/main.py score  → 查看基金质量筛选")
    print("=" * 60)


def cmd_enrich():
    """
    快速补充: 为已有净值的基金补充详细信息(规模/经理/成立日)。
    只采集profile不重新采集净值，速度快。
    """
    import sqlite3
    db = Database("data/fund_quant.db")
    collector = DataCollector(db)
    cur = db.conn.cursor()

    cur.execute("SELECT DISTINCT fund_code FROM fund_nav")
    existing_codes = [r[0] for r in cur.fetchall()]
    print(f"📊 已有 {len(existing_codes)} 只有净值数据的基金")
    print(f"   为缺少详细信息的基金补充数据...")
    print()

    success = 0
    skip = 0
    for i, code in enumerate(existing_codes):
        # 检查是否已有详细信息
        cur.execute("SELECT fund_size, manager_name, establish_date FROM fund_info WHERE fund_code = ?", (code,))
        info = cur.fetchone()
        if info and (info[0] or 0) > 0 and info[1]:
            skip += 1
            continue

        try:
            detail = collector.collect_fund_detail(code)
            if detail:
                collector.save_fund_detail_to_db(code, detail)
                success += 1
        except Exception:
            pass

        if (i + 1) % 50 == 0:
            print(f"   进度: {i+1}/{len(existing_codes)} (新增{success}, 已有{skip})")

    db.close()
    print()
    print("=" * 60)
    print(f"✅ 补充完成！新增: {success} 只, 已有: {skip} 只")
    print(f"   现在质量筛选可以区分🟢稳健和🟡注意了")
    print("=" * 60)


def cmd_score():
    """基金质量筛选（v3.0: 替代评分排名）"""
    db = Database("data/fund_quant.db")
    screener = FundScreener(db)
    thermometer = MarketThermometer(db)

    print("🔍 基金质量筛选 v3.0")
    print()

    temp = thermometer.get_temperature()
    print(f"🌡️ 当前市场温度: {temp['temperature']}°C — {temp['level_desc']}")
    print()

    df = screener.screen_funds()
    if df.empty:
        print("❌ 暂无通过质量筛选的基金。")
    else:
        summary = screener.get_pool_summary(df)
        print(f"📊 筛选结果: {summary['total']}只通过质量筛选")
        for label, count in summary.get("by_risk", {}).items():
            print(f"   {label}: {count}只")
        print()
        print("─" * 70)

        # 分组展示
        for risk_label in ["🟢 稳健", "🟡 注意", "🔴 高风险"]:
            subset = df[df["risk_label"] == risk_label]
            if subset.empty:
                continue
            print(f"\n{risk_label}:")
            for _, row in subset.iterrows():
                checks = row.get("quality_checks", {})
                metrics = row.get("metrics", {})
                mom = metrics.get("momentum_3m", 0) or 0
                dd = metrics.get("max_drawdown_1y", 0) or 0

                print(f"  {row['fund_code']} {str(row['fund_name'])[:28]:<30} 费率{row['mgt_fee']:.2f}%  近3月{mom:+.0f}%  回撤{dd:.0f}%")

                reasons = row.get("risk_reasons", [])
                for r in reasons:
                    print(f"    ↳ {r}")

    db.close()


def cmd_temp():
    """查看市场温度 (v2.0: 含真实成交量+风格判断+分歧检测)"""
    db = Database("data/fund_quant.db")
    thermometer = MarketThermometer(db)

    temp = thermometer.get_temperature()

    # 温度条
    t = temp['temperature']
    bar = "█" * int(t / 5) + "░" * (20 - int(t / 5))
    comp = temp["components"]

    print("🌡️ 市场温度计 v2.0")
    print("=" * 50)
    print(f"  [{bar}] {t}°C")
    print(f"  状态: {temp['level_desc']}")
    print(f"  建议: {temp['action']}")
    print(f"  建议权益仓位: {temp['target_equity_pct']}%")
    print()
    print("  📊 五维分解:")
    print(f"    PE估值分位数:  {comp['pe_score']:.0f}°  (越高越贵)")
    print(f"    PB估值分位数:  {comp['pb_score']:.0f}°  (越高越贵)")
    print(f"    股债性价比:    {comp['erp_score']:.0f}°  (越高股票越贵)")
    print(f"    成交量热度:    {comp['volume_score']:.0f}°  (天量=高温)")
    print(f"    市场情绪:      {comp['sentiment_score']:.0f}°  (贪婪=高温)")

    # 估值分歧
    div = temp.get("divergence", {})
    print()
    print(f"  🔍 估值分歧度: {div.get('level', '未知')}")
    print(f"     {div.get('message', '')}")

    # 市场风格
    style = temp.get("market_style", {})
    if style.get("dominant") != "unknown":
        print()
        print(f"  🎨 市场风格: {style.get('dominant', '')}")
        print(f"     {style.get('detail', '')}")
        returns = style.get("returns", {})
        if returns:
            for name, ret in returns.items():
                arrow = "📈" if ret > 0 else "📉"
                print(f"     {arrow} {name}: {ret:+.1f}% (20日)")

    db.close()


def cmd_backtest():
    """
    简单回测: 用历史3年数据验证评分模型。

    策略: 每周按评分买Top3基金，持有1周后换仓。
    对比: 我们的策略 vs 等权持有沪深300
    """
    import pandas as pd
    import numpy as np
    from datetime import timedelta

    db = Database("data/fund_quant.db")

    print("📊 回测验证: 多因子评分策略 vs 沪深300基准")
    print("=" * 60)

    # 1. 获取有净值数据的基金
    cur = db.conn.cursor()
    cur.execute("SELECT DISTINCT fund_code FROM fund_nav")
    all_codes = [r[0] for r in cur.fetchall()]
    print(f"   候选基金池: {len(all_codes)} 只")

    if len(all_codes) < 10:
        print("   ❌ 样本太少，无法回测。请先运行: python src/main.py nav")
        db.close()
        return

    # 2. 确定回测时间范围
    cur.execute("SELECT MIN(nav_date), MAX(nav_date) FROM fund_nav")
    min_date, max_date = cur.fetchone()
    print(f"   数据时间范围: {min_date} ~ {max_date}")

    weeks = []
    d = pd.to_datetime(max_date)
    start = d - timedelta(days=365 * 3)
    while d > start:
        weeks.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=7)
    weeks = sorted(weeks)
    print(f"   回测周期: {weeks[0]} ~ {weeks[-1]} ({len(weeks)} 周)")

    # 3. 获取沪深300基准
    try:
        import akshare as ak
        hs300 = ak.stock_zh_index_daily(symbol="sh000300")
        hs300["date"] = pd.to_datetime(hs300["date"])
        hs300 = hs300.set_index("date")["close"]
    except Exception:
        hs300 = None

    strategy_returns = []
    benchmark_returns = []
    print()
    print("   正在回测...")

    for i, week_date in enumerate(weeks[:-1]):
        next_week = weeks[i + 1]
        try:
            # 对每只基金做简化评分
            scores = []
            for code in all_codes:
                navs = db.get_fund_nav(code, end_date=week_date)
                if len(navs) < 60:
                    continue
                df_nav = pd.DataFrame(navs).sort_values("nav_date")
                df_nav["daily_return"] = df_nav["unit_nav"].pct_change() * 100
                returns_arr = df_nav["daily_return"].dropna().values
                if len(returns_arr) < 20:
                    continue
                try:
                    nav = df_nav.set_index("nav_date")["unit_nav"]
                    mom = (nav.iloc[-1] / nav.iloc[-min(63, len(nav))] - 1) * 100 if len(nav) >= 21 else 0
                    mom_score = max(5, min(95, (mom + 30) / 80 * 100))

                    ann_ret = np.mean(returns_arr) * 252
                    ann_vol = np.std(returns_arr, ddof=1) * np.sqrt(252)
                    sv = (ann_ret / 100 - 0.02) / (ann_vol / 100) if ann_vol > 0 else 0
                    sharpe_score = max(5, min(95, (sv + 1) / 3.5 * 100))

                    peak = df_nav["unit_nav"].iloc[0]
                    max_dd = 0
                    for p in df_nav["unit_nav"].values:
                        if p > peak:
                            peak = p
                        dd = (peak - p) / peak * 100
                        if dd > max_dd:
                            max_dd = dd
                    dd_score = max(5, min(95, (50 - max_dd) / 50 * 100))

                    total = mom_score * 0.4 + sharpe_score * 0.3 + dd_score * 0.3
                    scores.append((code, total))
                except Exception:
                    continue

            scores.sort(key=lambda x: x[1], reverse=True)
            top3 = [s[0] for s in scores[:3] if s[1] > 0]
            if not top3:
                continue

            weekly_returns = []
            for code in top3:
                nb = db.get_fund_nav(code, end_date=week_date)
                na = db.get_fund_nav(code, end_date=next_week)
                if nb and na:
                    try:
                        b_val = float(nb[-1]["unit_nav"])
                        a_val = float(na[-1]["unit_nav"])
                        if b_val > 0:
                            weekly_returns.append((a_val / b_val - 1) * 100)
                    except Exception:
                        pass

            if weekly_returns:
                strategy_returns.append(np.mean(weekly_returns))

            if hs300 is not None:
                try:
                    b_idx = hs300.loc[hs300.index <= pd.to_datetime(week_date)]
                    a_idx = hs300.loc[hs300.index <= pd.to_datetime(next_week)]
                    if len(b_idx) > 0 and len(a_idx) > 0:
                        b_hs = b_idx.iloc[-1]
                        a_hs = a_idx.iloc[-1]
                        benchmark_returns.append((a_hs / b_hs - 1) * 100)
                except Exception:
                    pass

            if (i + 1) % 50 == 0:
                print(f"   进度: {i+1}/{len(weeks)-1} 周已完成")

        except Exception:
            continue

    db.close()

    if not strategy_returns:
        print("\n❌ 回测数据不足，无法生成结果。")
        return

    sr = np.array(strategy_returns)
    br = np.array(benchmark_returns) if benchmark_returns else None

    total_ret = np.prod(1 + sr / 100) - 1
    ann_ret = (1 + total_ret) ** (52 / len(sr)) - 1
    ann_vol = np.std(sr, ddof=1) * np.sqrt(52)
    sv = (ann_ret - 0.02) / (ann_vol / 100) if ann_vol > 0 else 0
    cum = np.cumprod(1 + sr / 100)
    peak = cum[0]
    max_dd = 0
    for c in cum:
        if c > peak:
            peak = c
        dd = (peak - c) / peak * 100
        if dd > max_dd:
            max_dd = dd
    win_rate = (sr > 0).sum() / len(sr) * 100

    print()
    print("=" * 60)
    print("📊 回测结果 (近3年)")
    print("=" * 60)
    print(f"\n🎯 多因子选基策略:")
    print(f"   总收益率:    {total_ret*100:+.1f}%")
    print(f"   年化收益率:  {ann_ret*100:+.1f}%")
    print(f"   年化波动率:  {ann_vol:.1f}%")
    print(f"   夏普比率:    {sv:.2f}")
    print(f"   最大回撤:    {max_dd:.1f}%")
    print(f"   周胜率:      {win_rate:.0f}%")
    print(f"   回测周数:    {len(sr)}")

    if br is not None and len(br) > 0:
        b_total = np.prod(1 + br / 100) - 1
        b_ann = (1 + b_total) ** (52 / len(br)) - 1
        b_vol = np.std(br, ddof=1) * np.sqrt(52)
        b_sv = (b_ann - 0.02) / (b_vol / 100) if b_vol > 0 else 0
        b_cum = np.cumprod(1 + br / 100)
        b_peak = b_cum[0]
        b_max_dd = 0
        for c in b_cum:
            if c > b_peak:
                b_peak = c
            dd = (b_peak - c) / b_peak * 100
            if dd > b_max_dd:
                b_max_dd = dd

        print(f"\n📉 沪深300基准:")
        print(f"   总收益率:    {b_total*100:+.1f}%")
        print(f"   年化收益率:  {b_ann*100:+.1f}%")
        print(f"   年化波动率:  {b_vol:.1f}%")
        print(f"   夏普比率:    {b_sv:.2f}")
        print(f"   最大回撤:    {b_max_dd:.1f}%")

        alpha = ann_ret - b_ann
        print(f"\n⚖️ 超额收益 (Alpha): {alpha*100:+.1f}%/年")
        if alpha > 0:
            print(f"   ✅ 策略跑赢基准 {alpha*100:.1f}% 每年")
        else:
            print(f"   ⚠️ 策略跑输基准 {abs(alpha)*100:.1f}% 每年")

    print()
    print("⚠️ 回测局限性:")
    print("   1. 未考虑申购/赎回费 (场外约0.5-1.5%)")
    print("   2. 未考虑T+1确认延迟")
    print("   3. 过去表现不代表未来收益")
    print("   4. 只覆盖了有净值数据的基金子集")
    print("=" * 60)


def cmd_backtest2():
    """
    回测 v2: 月频评估 + 温度阈值触发 + 交易成本模拟。

    与 v1(每周换仓)的核心区别:
    - 每月评估一次（而非每周）
    - 温度变化<15°不调仓（减少摩擦）
    - 模拟申购/赎回费用
    """
    db = Database("data/fund_quant.db")
    engine = StrategyEngine(db)

    print("📊 回测 v2: 月频 + 温度阈值 + 交易成本")
    print("=" * 60)
    print("   策略规则:")
    print("   - 每月评估一次市场温度")
    print("   - 温度变化 ≥ 15° 才调仓")
    print("   - 冷市重动量选股，热市重回撤防御")
    print("   - 模拟申购费0.15% + 赎回费0.5%")
    print()

    result = engine.backtest_v2(lookback_years=3, trading_cost_enabled=True)

    if "error" in result:
        print(f"   ❌ {result['error']}")
        db.close()
        return

    s = result["strategy"]
    print("=" * 60)
    print("📊 回测结果 (近3年, 月频)")
    print("=" * 60)
    print(f"\n🎯 v2策略 (月频+阈值触发):")
    print(f"   总收益率:    {s['total_return']:+.1f}%")
    print(f"   年化收益率:  {s['annual_return']:+.1f}%")
    print(f"   年化波动率:  {s['annual_volatility']:.1f}%")
    print(f"   夏普比率:    {s['sharpe']:.2f}")
    print(f"   最大回撤:    {s['max_drawdown']:.1f}%")
    print(f"   月胜率:      {s['win_rate']:.0f}%")
    print(f"   回测月数:    {s['months']}")
    print(f"   温度触发次数:{s.get('temp_changes', 'N/A')} 次")

    if "benchmark" in result:
        b = result["benchmark"]
        alpha = result.get("alpha", 0)
        print(f"\n📉 沪深300基准:")
        print(f"   总收益率:    {b['total_return']:+.1f}%")
        print(f"   年化收益率:  {b['annual_return']:+.1f}%")
        print(f"\n⚖️ 超额收益 (Alpha): {alpha:+.1f}%/年")
        if alpha > 0:
            print(f"   ✅ 策略跑赢基准 {alpha:.1f}% 每年")
        else:
            print(f"   ⚠️ 策略跑输基准 {abs(alpha):.1f}% 每年")

    print()
    print("⚠️ 回测局限性: 过去表现不代表未来 / 仅覆盖有净值数据的基金")
    print("=" * 60)
    db.close()


def cmd_strategy():
    """v2策略建议: 月频评估，判断是否应调仓"""
    db = Database("data/fund_quant.db")
    engine = StrategyEngine(db)

    suggestion = engine.get_weekly_suggestion()
    temp = suggestion["temp_data"]

    print("🎯 策略引擎 v2.0 (月频+温度阈值)")
    print("=" * 50)

    # 温度
    t = temp["temperature"]
    bar = "█" * int(t / 5) + "░" * (20 - int(t / 5))
    print(f"\n🌡️ 当前温度: [{bar}] {t}°C — {temp['level_desc']}")
    print(f"   建议权益仓位: {suggestion['target_equity_pct']}%")

    # 调仓判断
    print(f"\n📋 调仓判断:")
    if suggestion["should_rebalance"]:
        print(f"   🔄 {suggestion['reason']}")
    else:
        print(f"   ✋ {suggestion['reason']}")

    # 交易成本
    cost = suggestion.get("cost_estimate")
    if cost:
        print(f"\n💰 预估交易成本:")
        print(f"   总成本约: ¥{cost['total_cost']:.2f}")
        for bd in cost.get("breakdown", []):
            print(f"   - {bd['fund_name'][:20]}: 持{bd['days_held']}天, 赎回费{bd['fee_rate']} ≈ ¥{bd['sell_fee']:.2f}")

    # 推荐基金
    df = suggestion.get("recommended_funds")
    if df is not None and not df.empty:
        print(f"\n📈 当前市场状态推荐:")
        for _, row in df.head(5).iterrows():
            print(f"   {row['rank']:>2}. {row['fund_code']} {str(row['fund_name'])[:25]:<27} 总分:{row['total_score']:.0f}")

    db.close()


def cmd_report():
    """生成完整周报 (v3.0 质量筛选 + 消息面)"""
    db = Database("data/fund_quant.db")
    screener = FundScreener(db)
    thermometer = MarketThermometer(db)
    portfolio = PortfolioTracker(db)
    reporter = WeeklyReporter(db)

    # 消息面扫描
    holdings = portfolio.get_portfolio_summary()
    holding_codes = [h["fund_code"] for h in holdings.get("holdings_detail", [])]

    sentiment_data = None
    try:
        monitor = SentimentMonitor()
        sentiment_data = monitor.full_scan(holding_codes=holding_codes)
    except Exception:
        pass

    reporter.generate_full_report(screener, thermometer, portfolio, sentiment_data)
    db.close()


def cmd_hithink():
    """测试 HiThink 同花顺官方 API 连接"""
    if not HITHINK_KEY:
        print("❌ 未找到 HITHINK_API_KEY，请在 .env 文件中设置")
        return
    hithink_quick_test(HITHINK_KEY)


def cmd_sentiment():
    """消息面扫描: 市场新闻 + 基金公告 + 情绪打分"""
    sentiment_quick_scan()


def cmd_portfolio():
    """查看持仓"""
    db = Database("data/fund_quant.db")
    tracker = PortfolioTracker(db)

    summary = tracker.get_portfolio_summary()

    if not summary.get("has_holdings"):
        print("📋 暂无持仓记录。")
        print("   添加买入记录: python src/main.py buy")
    else:
        print("📋 当前持仓")
        print("=" * 60)
        print(f"  总投入: ¥{summary['total_invested']:,.2f}")
        print(f"  总市值: ¥{summary['total_market_value']:,.2f}")
        print(f"  浮动盈亏: ¥{summary['total_pnl']:+,.2f} ({summary['total_return_pct']:+.2f}%)")
        print()
        for d in summary["holdings_detail"]:
            print(
                f"  {d['fund_name'][:25]:<25} | "
                f"买入: {d['buy_date']} | "
                f"投入¥{d['buy_amount']:,.0f} | "
                f"市值¥{d['current_value']:,.0f} | "
                f"盈亏¥{d['pnl']:+,.0f} ({d['pnl_pct']:+.1f}%) | "
                f"持有{d['days_held']}天"
            )
        print()
        print("  资产配置:")
        for ftype, pct in summary["asset_allocation"].items():
            print(f"    {ftype}: {pct}%")

    db.close()


def cmd_buy():
    """交互式添加买入记录"""
    print("📝 添加买入记录")
    print("-" * 40)

    fund_code = input("基金代码 (如 000011): ").strip()
    fund_name = input("基金名称 (如 华夏大盘精选): ").strip()
    buy_date = input("买入日期 (YYYY-MM-DD, 默认今天): ").strip()
    if not buy_date:
        from datetime import date
        buy_date = date.today().isoformat()

    try:
        amount = float(input("买入金额 (元): ").strip())
    except ValueError:
        print("❌ 金额格式错误")
        return

    notes = input("备注 (可选): ").strip()

    db = Database("data/fund_quant.db")
    tracker = PortfolioTracker(db)
    tracker.add_buy_transaction(fund_code, fund_name, buy_date, amount, notes)
    db.close()

    print(f"\n✅ 已记录: {buy_date} 买入 {fund_name}({fund_code}) ¥{amount:,.2f}")


def cmd_web():
    """启动本地仪表盘 (http://localhost:5020)"""
    import subprocess
    import webbrowser
    print("🚀 启动仪表盘...")
    print("   http://localhost:5020")
    print("   按 Ctrl+C 停止")
    print()
    # Open browser after a short delay
    import threading
    def _open():
        import time
        time.sleep(1.5)
        webbrowser.open("http://localhost:5020")
    threading.Thread(target=_open, daemon=True).start()
    # 预计算板块数据（后台线程，不阻塞启动）
    from src.web.app import app, _precompute_sectors
    threading.Thread(target=_precompute_sectors, daemon=True).start()
    # Run Flask
    app.run(host="0.0.0.0", port=5020, debug=False)


def cmd_rebalance():
    """辅助调仓: 对比实际持仓 vs 目标仓位, 输出具体买卖指令"""
    db = Database("data/fund_quant.db")
    advisor = RebalanceAdvisor(db)

    # 计算总资金(持仓+估计的现金)
    holdings = db.get_current_holdings()
    total_invested = sum(h["buy_amount"] for h in holdings)

    if not holdings:
        print("📋 暂无持仓。先录入:")
        print("   python src/main.py buy")
        db.close()
        return

    # 假设总资金 = 已投入 + 10%现金缓冲, 或提示用户输入
    total_cap = total_invested * 1.1  # 留10%现金缓冲

    result = advisor.analyze(total_capital=total_cap)

    # === 输出 ===
    temp = result["temperature"]
    summary = result["summary"]
    instructions = result["instructions"]

    print()
    print("=" * 55)
    print("🔄 辅助调仓建议")
    print("=" * 55)

    # 状态
    t = temp["temperature"]
    bar = "█" * int(t / 5) + "░" * (20 - int(t / 5))
    print(f"\n🌡️ 温度: [{bar}] {t}°C — {temp['level_desc']}")
    print(f"📊 当前权益: {result['current_equity_pct']:.0f}% → 目标: {result['target_equity_pct']}%")
    print(f"💰 总资产约: ¥{result['total_capital']:.0f} (含现金)")

    # 调仓建议
    print(f"\n📋 {summary['verdict']}")
    print(f"   {summary['detail']}")

    if instructions:
        print()
        for inst in instructions:
            icon = {"卖出": "🔴", "买入": "🟢", "持有": "⚪"}
            print(f"  {icon.get(inst['action'], '•')} [{inst['action']}] {inst['fund_code']} {inst['fund_name'][:28]:<30}")
            print(f"     金额: ¥{inst['amount']:.0f} | 仓位: {inst['current_pct']:.1f}%→{inst['target_pct']:.1f}%")
            print(f"     原因: {inst['reason']}")
            print()

    # 提醒
    print("⚠️ 操作提醒:")
    print("   1. 以上为系统建议, 最终决策由你做出")
    print("   2. 场外基金持有<7天赎回费1.5%, 请确认持有天数")
    print("   3. 在支付宝手动操作后, 用 python src/main.py buy/sell 记录")
    print("=" * 55)

    db.close()


def cmd_sector():
    """板块分析: 31个行业动量排名 + 超跌候选 + 针对你持仓的建议"""
    db = Database("data/fund_quant.db")

    # 获取用户持仓的行业暴露
    holdings = db.get_current_holdings()
    held_sectors = set()
    if holdings:
        for h in holdings:
            info = None
            for f in db.get_all_funds():
                if f["fund_code"] == h["fund_code"]:
                    info = f; break
            if info:
                ftype = info.get("fund_type", "")
                name = info.get("fund_name", "")
                # 映射基金名→行业
                for kw, sector in [("半导体","电子"),("芯片","电子"),("科创","电子"),
                                     ("电力","电力设备"),("电网","电力设备"),("新能源","电力设备"),
                                     ("计算机","计算机"),("AI","计算机"),("纳斯达克","QDII"),
                                     ("通信","通信"),("机器人","机械设备"),
                                     ("医药","医药生物"),("消费","食品饮料"),("食品","食品饮料"),
                                     ("银行","银行"),("红利","银行")]:
                    if kw in name or kw in ftype:
                        held_sectors.add(sector)

    print("📊 31个申万一级行业分析中...")
    analyzer = SectorAnalyzer()
    result = analyzer.analyze()

    if "error" in result:
        print(f"  ❌ {result['error']}")
        db.close()
        return

    print()
    print("=" * 60)
    print("📊 申万31行业综合排名 Top 15")
    print("=" * 60)
    print(f"  {'排名':<4} {'行业':<10} {'综合分':>6} {'近1月':>7} {'近3月':>7} {'波动':>6}")
    print("  " + "-" * 42)
    for s in result["rankings"]:
        marker = " ← 你已持有" if s["name"] in held_sectors else ""
        print(f"  {s['rank']:<4} {s['name']:<10} {s['score']:>6.2f} {s['ret_1m']:>+6.1f}% {s['ret_3m']:>+6.1f}% {s['volatility']:>5.0f}%{marker}")

    print()
    print("=" * 60)
    print("🔥 动量领涨 (顺势)        💧 超跌候选 (逆向)")
    print("=" * 60)
    ml = result.get("momentum_leaders", [])
    vc = result.get("value_candidates", [])
    for i in range(max(len(ml), len(vc))):
        left = f"  {ml[i]['name']:<10} 近1月{ml[i]['ret_1m']:+.1f}%" if i < len(ml) else " " * 30
        right = f"  {vc[i]['name']:<10} 近1月{vc[i]['ret_1m']:+.1f}%" if i < len(vc) else ""
        print(f"{left}    {right}")

    if held_sectors:
        print()
        print("=" * 60)
        print("🎯 针对你的持仓")
        print("=" * 60)
        rec = analyzer.recommend_for_portfolio(list(held_sectors))
        print(f"  你已持有: {', '.join(held_sectors)}")
        print()
        if rec.get("complements"):
            print("  互补方向 (与你持仓低相关, 分散风险):")
            for s in rec["complements"]:
                print(f"    🟢 {s['name']}: 近1月{s['ret_1m']:+.1f}%  近3月{s['ret_3m']:+.1f}%")
        if rec.get("value_non_tech"):
            print()
            print("  超跌非科技 (值得关注):")
            for s in rec["value_non_tech"]:
                print(f"    💧 {s['name']}: 近1月{s['ret_1m']:+.1f}%")
        print()
        print(f"  ⚠️ {rec.get('temperature_note','')}")

    db.close()


def cmd_recommend():
    """历史验证推荐: 基于3年回测, 找出持续被选中且真的赚了钱的基金"""
    db = Database("data/fund_quant.db")
    hr = HistoricalRecommender(db)

    print("🔄 历史回测推荐引擎运行中...")
    print("   (80只基金 · 每2月打分 · 约1.5年数据)")
    print()
    result = hr.recommend(lookback_years=1.5)

    if "error" in result:
        print(f"❌ {result['error']}")
        db.close()
        return

    stats = result["stats"]
    print("=" * 60)
    print("📊 历史回测统计")
    print("=" * 60)
    print(f"  回测周期: {stats['date_range']} ({stats['total_months']}个月)")
    print(f"  候选基金: {stats['total_candidates']}只")
    print(f"  被选中过的: {stats['funds_ever_picked']}只")
    print(f"  历史验证: {stats['funds_with_proven_record']}只有效数据")
    print(f"    正收益{stats['proven_good']}只 / 负收益{stats['proven_bad']}只")
    print(f"  Top10平均3月后收益: {stats['top10_avg_3m_return']:+.1f}%")

    print()
    print("=" * 60)
    print("🏆 历史验证最强 (高选中率 + 实际正收益)")
    print("=" * 60)
    print(f"  {'基金':<10} {'名称':<28} {'综合':>5} {'选中率':>6} {'均1月':>6} {'均3月':>6} {'胜率':>5}")
    print("  " + "-" * 68)
    for p in result["proven_winners"][:15]:
        print(f"  {p['code']:<10} {str(p['name'])[:26]:<28} {p['composite_score']:>5.0f} {p['pick_rate']:>5.0f}% {p['avg_return_1m']:>+5.1f}% {p['avg_return_3m']:>+5.1f}% {int(p['win_rate_3m']):>4}%")

    print()
    print("=" * 60)
    print("🎯 当前推荐 (最新评分 + 历史验证)")
    print("=" * 60)
    for p in result["current_picks"][:10]:
        hist = ""
        if p.get("hist_avg_3m") is not None:
            hist = f" | 历史选中{p['times_picked']}次, 均3月+{p['hist_avg_3m']:.1f}%"
        print(f"  {p['code']} {str(p['name'])[:30]:<32} 评分{p['score']:.0f}{hist}")

    db.close()


def cmd_sell():
    """交互式添加卖出记录"""
    db = Database("data/fund_quant.db")
    tracker = PortfolioTracker(db)

    # 先显示当前持仓
    summary = tracker.get_portfolio_summary()
    if not summary.get("has_holdings"):
        print("📋 暂无持仓记录，无需卖出。")
        db.close()
        return

    print("📋 当前持仓:")
    for d in summary["holdings_detail"]:
        print(f"  ID:{d['holding_id']} | {d['fund_name'][:25]} | 市值¥{d['current_value']:,.0f}")

    print()
    try:
        holding_id = int(input("要卖出的持仓ID: ").strip())
    except ValueError:
        print("❌ ID格式错误")
        db.close()
        return

    sell_date = input("卖出日期 (YYYY-MM-DD, 默认今天): ").strip()
    if not sell_date:
        from datetime import date
        sell_date = date.today().isoformat()

    try:
        sell_amount = float(input("卖出金额 (元): ").strip())
    except ValueError:
        print("❌ 金额格式错误")
        db.close()
        return

    tracker.record_sell(holding_id, sell_date, sell_amount)
    db.close()

    print(f"\n✅ 已记录: {sell_date} 卖出 持仓ID={holding_id}, ¥{sell_amount:,.2f}")


def cmd_plan():
    """查看投资计划 + 当前进度"""
    db = Database("data/fund_quant.db")
    plan = get_plan()
    progress = get_progress(db)

    print()
    print("=" * 60)
    print(f"📋 {plan['name']}")
    print(f"   起始日期: {plan['start_date']} · 总资金: ¥{plan['total_capital']:.0f}")
    print("=" * 60)

    # 计划总览表
    print(f"\n{'基金':<22}{'角色':<10}{'目标':>8}{'已投':>8}{'还差':>8}{'进度':>8}")
    print("-" * 60)
    for f in progress["funds"]:
        bar_len = int(f["progress_pct"] / 100 * 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)
        print(
            f"{f['name'][:20]:<22}{f['role']:<10}"
            f"{f['target']:>6.0f}¥{f['invested']:>7.0f}¥{f['remaining']:>7.0f}¥"
            f"{bar:>11} {f['progress_pct']:.0f}%"
        )

    # 现金 + 汇总
    equity_target = plan['total_capital'] - plan['cash_reserve']
    print(f"\n💰 现金底仓(弹药): ¥{plan['cash_reserve']:.0f}")
    print(f"📊 已投权益: ¥{progress['total_invested']:.0f} / 目标 ¥{equity_target:.0f}")
    if equity_target > 0:
        print(f"   总进度: {progress['total_invested'] / equity_target * 100:.0f}%")

    # 下一笔建议
    print("\n" + "-" * 60)
    print("🎯 下一笔该买什么:")
    has_next = False
    for f in progress["funds"]:
        n = f["next"]
        if n and n["amount"] > 0:
            has_next = True
            if n.get("note"):
                print(f"   {f['name'][:20]:<22} {n['note']}")
            else:
                print(f"   {f['name'][:20]:<22} 买 ¥{n['amount']:.0f}  (建议 {n['date']})")
    if not has_next:
        print("   ✅ 所有基金已买满目标，等待温度信号再加仓")

    # 现金信号
    print(f"\n💡 现金 ¥{plan['cash_reserve']:.0f} 的使用时机:")
    for level, rule in plan["temp_rules"].items():
        print(f"   {rule}")

    print()
    print("⚠️ 操作提醒:")
    print("   1. 每次买入后: python src/main.py buy 录入")
    print("   2. 查看进度: python src/main.py plan")
    print("   3. 不满7天别赎回(1.5%惩罚费)")
    print("=" * 60)

    db.close()


def print_help():
    """打印帮助信息"""
    print(__doc__)


def main():
    """主入口: 根据命令行参数分发到不同子命令"""
    if len(sys.argv) < 2:
        # 无参数: 默认生成报告
        cmd_report()
        return

    cmd = sys.argv[1].lower()

    commands = {
        "test": cmd_test,
        "init": cmd_init,
        "schedule": cmd_schedule,
        "collect": cmd_collect,
        "index": cmd_index,
        "nav": cmd_nav,
        "enrich": cmd_enrich,
        "hithink": cmd_hithink,
        "score": cmd_score,
        "temp": cmd_temp,
        "sentiment": cmd_sentiment,
        "backtest": cmd_backtest,
        "backtest2": cmd_backtest2,
        "strategy": cmd_strategy,
        "report": cmd_report,
        "portfolio": cmd_portfolio,
        "buy": cmd_buy,
        "sell": cmd_sell,
        "rebalance": cmd_rebalance,
        "recommend": cmd_recommend,
        "sector": cmd_sector,
        "plan": cmd_plan,
        "web": cmd_web,
        "help": print_help,
    }

    if cmd in commands:
        commands[cmd]()
    else:
        print(f"❌ 未知命令: {cmd}")
        print_help()


if __name__ == "__main__":
    # 延迟导入 akshare（只有 collect 命令才需要）
    import akshare as ak
    main()
