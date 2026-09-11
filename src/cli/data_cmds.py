"""
数据采集命令：test / init / index / collect / nav / enrich / hithink。
"""

import os
import time

import akshare as ak

from src.data.collector import DataCollector, quick_test
from src.data.database import Database
from src.data.hithink_collector import quick_test as hithink_quick_test


# 同花顺 API Key（.env 或环境变量 HITHINK_API_KEY 皆可）
def _load_api_key():
    """读取 HITHINK_API_KEY：优先环境变量，其次 .env"""
    env_key = os.environ.get("HITHINK_API_KEY", "").strip()
    if env_key:
        return env_key
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


def cmd_index():
    """采集指数PE/PB估值历史（独立运行，不依赖基金数据）"""
    import time
    db = Database("data/fund_quant.db")
    collector = DataCollector(db)

    print("📡 采集指数PE/PB估值...")
    print()

    indices = {
        "000300": {"name": "沪深300", "pe_sym": "沪深300", "pb_sym": "沪深300", "daily_sym": "sh000300"},
        "000905": {"name": "中证500", "pe_sym": "中证500", "pb_sym": "中证500", "daily_sym": "sh000905"},
        "000016": {"name": "上证50",  "pe_sym": "上证50",  "pb_sym": "上证50",  "daily_sym": "sh000016"},
    }

    def _fetch_daily(sym):
        """指数日线（含 volume）——失败不阻断 PE/PB 入库，只让量能维度走降级路径"""
        try:
            return ak.stock_zh_index_daily(symbol=sym)
        except Exception:
            return None

    for code, info in indices.items():
        print(f"  [{info['name']}] 采集PE/PB ...")
        try:
            pe_df = ak.stock_index_pe_lg(symbol=info["pe_sym"])
            print(f"      PE: {len(pe_df)} 条 ({str(pe_df['日期'].iloc[0])[:10]} ~ {str(pe_df['日期'].iloc[-1])[:10]})")
            time.sleep(0.5)

            pb_df = ak.stock_index_pb_lg(symbol=info["pb_sym"])
            print(f"      PB: {len(pb_df)} 条 ({str(pb_df['日期'].iloc[0])[:10]} ~ {str(pb_df['日期'].iloc[-1])[:10]})")
            time.sleep(0.5)

            daily_df = _fetch_daily(info["daily_sym"])
            print(f"      日线(成交量): {len(daily_df) if daily_df is not None else 0} 条")

            collector.save_index_val_to_db(code, pe_df, pb_df, daily_df)
            print(f"      ✅ 已入库")
        except Exception as e:
            # 重试一次
            print(f"      ⚠️ 首次失败: {e}，等5秒重试...")
            time.sleep(5)
            try:
                pe_df = ak.stock_index_pe_lg(symbol=info["pe_sym"])
                pb_df = ak.stock_index_pb_lg(symbol=info["pb_sym"])
                daily_df = _fetch_daily(info["daily_sym"])
                collector.save_index_val_to_db(code, pe_df, pb_df, daily_df)
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
                collector.save_index_val_to_db(code, data["pe"], data["pb"], data.get("daily"))
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
    db = Database("data/fund_quant.db")
    collector = DataCollector(db)
    cur = db.conn.cursor()

    existing_codes = sorted(db.get_all_fund_codes())
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


def cmd_calendar():
    """刷新交易日历（T+1 确认 / 定投跳过节假日用）"""
    db = Database("data/fund_quant.db")
    dates, src = [], None
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        dates = [str(x)[:10] for x in df[col].tolist()]
        src = "akshare"
    except Exception as e:
        print(f"⚠️ akshare 交易日历获取失败: {str(e)[:80]}")

    if not dates:
        path = os.path.join("data", "trade_calendar.csv")
        if os.path.exists(path):
            try:
                import csv
                with open(path, newline="", encoding="utf-8") as f:
                    rows = list(csv.reader(f))
                dates = [r[0].strip()[:10] for r in rows[1:] if r and r[0].strip()]
                src = path
            except Exception as e:
                print(f"⚠️ 读取 {path} 失败: {str(e)[:80]}")

    if dates:
        db.upsert_trade_dates(dates)
        print(f"✅ 交易日历已更新: {len(dates)} 个交易日（来源: {src}）")
        print(f"   覆盖: {min(dates)} ~ {max(dates)}")
    else:
        print("ℹ️ 未获取到交易日历；将回退为“跳过周末”规则（T+1 仍可用，只是不识别法定节假日）。")
        print("   可放置 data/trade_calendar.csv（首行表头 trade_date）后重跑本命令。")
    db.close()


def cmd_hithink():
    """测试 HiThink 同花顺官方 API 连接"""
    if not HITHINK_KEY:
        print("❌ 未找到 HITHINK_API_KEY，请在 .env 文件中设置")
        return
    hithink_quick_test(HITHINK_KEY)

