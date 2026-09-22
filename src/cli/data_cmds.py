"""
数据采集命令：test / init / index / collect / nav / enrich / hithink。
"""

import os
import sys
import time

import akshare as ak

from src.data.collector import DataCollector, parse_daily_snapshot, quick_test
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


def _invalidate_web_cache(db) -> None:
    """采集类命令写完数据后，失效 Web 的 analysis_snapshot 快照。

    为什么必须有（黑箱验收审计 F-08）：Web 的 `/api/funds` 等端点会把结果
    落进 `analysis_snapshot` 并给 6 小时 TTL；**写持仓**会触发失效，但
    **CLI 采集不会** —— 于是"跑完 init 回来打开页面，数字一个都没变"
    （界面还端着采集前的空结果）。采集完成后显式清一次即可。
    """
    try:
        n = db.clear_analysis_snapshots()
        if n:
            print(f"   ♻️ 已失效 Web 缓存快照 {n} 项（下次打开页面会自动重算）")
    except Exception:
        pass


def cmd_test():
    """Phase 0: 测试数据接口"""
    quick_test()


def cmd_init():
    """一键初始化: 测试接口 → 采集数据 → 交易日历 → 采集净值 → 补充详情（首次使用执行一次）"""
    print("=" * 60)
    print("🚀 一键初始化（首次使用执行一次，约 15-25 分钟；净值采集最耗时）")
    print("=" * 60)
    print()

    print("步骤 1/5: 测试数据接口...")
    cmd_test()
    print()

    print("步骤 2/5: 采集基金列表与指数估值...")
    cmd_collect()
    print()

    # 交易日历是 T+1 确认 / 定投跳节假日的基础（黑箱验收审计 F-06：
    # 旧版 init 不含这一步，新用户跑完"一键初始化"后 trade_calendar 仍为空，
    # 只能回退"跳过周末"，法定节假日会被当成交易日）。
    print("步骤 3/5: 刷新交易日历（T+1 确认 / 定投跳过节假日）...")
    cmd_calendar()
    print()

    print("步骤 4/5: 采集基金净值历史（最耗时，请耐心等待）...")
    cmd_nav()
    print()

    print("步骤 5/5: 补充基金详情...")
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

    _invalidate_web_cache(db)
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

    _invalidate_web_cache(db)
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
    nav_rows = 0
    empty_nav = 0          # 接口没抛异常、但**一行都没入库**（源头无数据）
    success_detail = 0
    fail_count = 0
    for i, (code, name, ftype, fee) in enumerate(unique_candidates):
        # 采集净值
        try:
            df = collector.collect_fund_nav(code)
            n_rows = collector.save_fund_nav_batch(code, df) or 0
            # F-07：按**实际入库行数**计成功，而不是"接口没抛异常"
            if n_rows > 0:
                success_nav += 1
                nav_rows += n_rows
            else:
                empty_nav += 1
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
            print(f"   进度: {i+1}/{total} (净值{success_nav} ok/{empty_nav} 空, 详情{success_detail} ok, {fail_count} fail)")

    _invalidate_web_cache(db)
    db.close()
    print()
    print("=" * 60)
    print(f"✅ 采集完成！净值: {success_nav} 只（入库 {nav_rows} 行）, 基金详情: {success_detail} 只, 失败: {fail_count} 只")
    if empty_nav:
        # 必须显式声明：旧版把这些也算成"ok"，造成"600 只 ok / 库里只 305 只"的假成功
        print(f"   ⚠️ 另有 {empty_nav} 只**接口返回空、未入库任何行**（源头无净值数据）"
              f" —— 它们不计入「净值成功」")
    print(f"   质量筛选现在可以使用真实的规模/经理/成立日数据了")
    print()
    print("💡 下一步:")
    print("   python src/main.py score  → 查看基金质量筛选")
    print("=" * 60)


def cmd_snapshot():
    """
    全市场当日净值快照：1 次请求覆盖全市场（日常增量主路径，约 10 秒）。

    - 写 fund_nav：快照自带的最近 2 个交易日的单位/累计净值
      （INSERT OR IGNORE 只增不改，重跑/连跑安全）；
    - 写 fund_info：申购状态/费率走 upsert 局部更新（只覆盖真正采到值的字段）；
    - 日线缺口检测：fund_nav 现有最新日期与快照首日之间若隔了交易日，
      说明中间漏跑了，打印缺口清单——快照只含最近 2 个交易日，
      缺口不会靠重跑 snapshot 自动补齐，需走历史采集路径回填。
    """
    db = Database("data/fund_quant.db")

    print("📡 全市场当日快照（1 次请求，约 10 秒）...")
    try:
        daily_df = ak.fund_open_fund_daily_em()
    except Exception as e:
        print(f"❌ 获取失败: {e}")
        db.close()
        return

    latest_before = db.get_latest_nav_date()
    nav_records, info_rows, snap_dates = parse_daily_snapshot(daily_df)
    if not snap_dates:
        print("❌ 快照中未识别到带日期的净值列（接口结构可能已变化），未写入任何数据")
        db.close()
        return

    before_cnt = db.conn.execute("SELECT COUNT(*) FROM fund_nav").fetchone()[0]
    db.insert_nav_batch(nav_records)
    after_cnt = db.conn.execute("SELECT COUNT(*) FROM fund_nav").fetchone()[0]

    # 申购状态/费率：两万行包成一个事务（逐行 commit 会被 fsync 拖死）
    with db.immediate():
        for f in info_rows:
            db.upsert_fund_info(f, commit=False)

    new_rows = after_cnt - before_cnt
    print(f"✅ 快照日期: {'、'.join(snap_dates)}（{len(daily_df)} 只基金）")
    print(f"   fund_nav: 解析 {len(nav_records)} 行，新增 {new_rows} 行（已有日期自动忽略）")
    print(f"   fund_info: 申购状态/费率局部更新 {len(info_rows)} 只（空值不覆盖旧值）")

    # 日线缺口检测：快照只给最近 2 个交易日，漏跑的日子需要显式提示
    if latest_before and latest_before < snap_dates[0]:
        trade_days = [d for d in db.get_trade_dates(start=latest_before, end=snap_dates[-1])
                      if latest_before < d < snap_dates[0]]
        if trade_days:
            print(f"⚠️ 日线缺口: {latest_before} ~ {snap_dates[0]} 之间缺 "
                  f"{len(trade_days)} 个交易日: {'、'.join(trade_days[:10])}")
            print("   重跑 snapshot 补不回这些日子，需用历史采集路径回填（nav / 定向 collect_fund_nav）。")
        elif db.count_trade_dates() == 0:
            print(f"ℹ️ 净值日期 {latest_before} → {snap_dates[-1]}；"
                  f"交易日历为空，无法判定是否有缺口（可先跑 python src/main.py calendar）。")

    db.log_data_collection("fund_nav_snapshot", "success", new_rows)
    _invalidate_web_cache(db)
    db.close()
    print()
    print("💡 snapshot 是日常增量主路径；历史回填仍需 nav（分层采样）。")


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

    _invalidate_web_cache(db)
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
    _invalidate_web_cache(db)
    db.close()


def cmd_hithink():
    """测试 HiThink 同花顺官方 API 连接"""
    if not HITHINK_KEY:
        print("❌ 未找到 HITHINK_API_KEY，请在 .env 文件中设置")
        return
    hithink_quick_test(HITHINK_KEY)



def _finalize_fees():
    """采集完成后**自动走完剩下两步**（用户 2026-09-22 要求）：

    ① 重建参照系缓存（peer_distributions.json，含 TER 分位网格）
    ② 审计 + 回归哨兵（audit_fee_ter.py 看 TER 分布；validate_vs_source.py 确认
       momentum 口径没被 TER 改动带坏，ρ 应 ≥0.85）

    幂等：可单独重跑（`python src/main.py fees --finalize-only`）。
    """
    import subprocess
    print()
    print("=" * 60)
    print("🎉 采集完成 → 自动走完剩下两步")
    print("=" * 60)

    # ① 重建参照系缓存
    print("\n[1/2] 重建参照系缓存（含 TER 分位）...")
    try:
        from src.analysis import peer_percentile as pp
        dist = pp.build_distributions()
        p = pp.save_cache(dist)
        print("   ✓ 已写入", p)
    except Exception as e:
        print("   ✗ 重建失败:", e)

    # ② 审计 + 回归哨兵
    print("\n[2/2] 审计 + 回归哨兵 ...")
    for script, desc in (("audit_fee_ter.py", "TER 分布审计"),
                         ("validate_vs_source.py", "momentum 回归哨兵（ρ ≥ 0.85）")):
        print("   ── %s ──" % desc)
        try:
            r = subprocess.run(
                [sys.executable, os.path.join("scripts", script)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            out = (r.stdout or "").strip()
            print("   " + "\n   ".join(out.splitlines()[-22:]))
            if r.returncode != 0:
                print("   ⚠️ 返回码 %d" % r.returncode)
        except Exception as e:
            print("   ✗ %s 失败: %s" % (desc, e))
    print()
    print("=" * 60)
    print("✅ 全部完成。可打开 Web 查看：基金池的费率检查已按 TER 组内分位生效。")
    print("=" * 60)


def cmd_fees():
    """补采真·运作费率（管理费 / 托管费 / 销售服务费）—— **可断点续采**。

    为什么需要（见 docs/参照系接入执行报告 §8）：
    `fund_info.mgt_fee` 历史上装的是**手续费（申购费，打折后）**，四重证据确证
    （akshare 官方文档×3 / A类0.15%·C类0.00% / 我们库 A 类 94.1% 有值·C 类 99.6% 为空 /
    反证：若为管理费则 1.2、1.5 应占多数，实测仅 233 只）。已由
    scripts/migrate_mgt_fee_to_purchase_fee.py 正名到 purchase_fee。

    真费率只能逐只取：akshare `fund_fee_em(symbol,'运作费用')`（源=天天基金），
    实测 0.60 秒/只、8/8 成功、类型一致性正确。产出用于
    **TER = 管理费 + 托管费 + 销售服务费**（晨星口径，也是"费率预测业绩"研究所用指标）。

    用法:
        python src/main.py fees            # 续采（已有 mgt_fee 的自动跳过）
        python src/main.py fees 500        # 只采 500 只（试跑 / 分批）
        python src/main.py fees 500 --all  # 强制重采（含已有值的）
    """
    args = [a for a in sys.argv[2:]]
    if "--finalize-only" in args:
        _finalize_fees()
        return
    force = "--all" in args
    nums = [a for a in args if a.isdigit()]
    limit = int(nums[0]) if nums else None

    db = Database("data/fund_quant.db")
    if not force:
        todo = [r[0] for r in db.conn.execute(
            "SELECT fund_code FROM fund_info WHERE mgt_fee IS NULL OR mgt_fee = 0")]
    else:
        todo = [r[0] for r in db.conn.execute("SELECT fund_code FROM fund_info")]
    # 优先采"筛选器真正会用到的"（有净值的），让最有价值的数据先落地
    with_nav = {r[0] for r in db.conn.execute("SELECT DISTINCT fund_code FROM fund_nav")}
    todo.sort(key=lambda c: (0 if c in with_nav else 1, c))
    if limit:
        todo = todo[:limit]

    total = len(todo)
    if not total:
        print("✅ 所有基金都已有管理费数据（如需重采加 --all）")
        db.close()
        _finalize_fees()          # 续采到"没有剩余"=采集完成 → 自动走完剩下两步
        return
    print(f"待采 {total} 只（有净值的优先）；预计 {total * 0.6 / 60:.0f} 分钟，可随时 Ctrl+C 中断后续采")

    collector = DataCollector(db)
    ok = empty = fail = 0
    t0 = time.time()
    for i, code in enumerate(todo):
        r = collector.collect_fund_fee(code)
        if r is None:
            fail += 1
        elif r.get("mgt_fee") is None and r.get("custodian_fee") is None:
            empty += 1
        else:
            db.upsert_fund_info({"fund_code": code, **r})
            ok += 1
        if (i + 1) % 50 == 0:
            el = time.time() - t0
            eta = (total - i - 1) * el / (i + 1) / 60
            print(f"   进度: {i+1}/{total} (成功{ok} 空{empty} 失败{fail}) 已用 {el/60:.1f} 分，预计还需 {eta:.0f} 分")
    _invalidate_web_cache(db)
    print()
    print("=" * 60)
    print(f"✅ 费率补采完成！成功 {ok} 只, 空 {empty} 只, 失败 {fail} 只, 耗时 {(time.time()-t0)/60:.1f} 分")
    q = lambda s: db.conn.execute(s).fetchone()[0]
    print("   当前覆盖: 管理费 %d | 托管费 %d | 销售服务费 %d" % (
        q("SELECT COUNT(*) FROM fund_info WHERE mgt_fee>0"),
        q("SELECT COUNT(*) FROM fund_info WHERE custodian_fee>0"),
        q("SELECT COUNT(*) FROM fund_info WHERE sales_service_fee>0")))
    remaining = q("SELECT COUNT(*) FROM fund_info WHERE mgt_fee IS NULL OR mgt_fee = 0")
    db.close()
    if remaining > 0:
        print("   ⚠️ 还有 %d 只待采（本次用了 --limit 或有失败）。再跑 `python src/main.py fees` 续采；"
              "采完会自动走完剩下两步。" % remaining)
        return
    _finalize_fees()          # 采集**真正完成**（无剩余）→ 自动重建缓存 + 审计 + 回归哨兵
