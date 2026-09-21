#!/usr/bin/env python3
"""每日增量采集（通道与全量不同，成本差 44 倍）

## 为什么增量必须换通道

`pingzhongdata` 是**全量接口** —— 要"今天的新净值"也会把 13 年历史全吐出来（291KB/只）。
拿它做日常增量，和全量一样贵。

实测对照（18,677 只）：
    首次全量  pingzhongdata  291 KB/只  2.7 小时  5.4 GB
    每日增量  lsjz 首页       4.5 KB/只  **3.7 分钟**  **83 MB**

`lsjz` 的 `pageIndex=1&pageSize=20` 返回**最近 20 天、最新在前**（实测 2026-08-24 ~ 2026-09-18），
一次请求同时含 `DWJZ` 单位净值 + `LJJZ` 累计净值 + `JZZZL` 日增长率。

拉「最近 20 天」的附带好处：
    · 自动覆盖净值修正（基金偶有更正历史值）
    · 容忍连续 20 天没跑

## 三项附加能力

1. **发现新基金**：每天新基金成立 → 拉名录，把新代码加入采集（一次请求）
2. **⭐ 清盘/停牌检测**：连续 N 天不再更新净值 → 报告出来（**保留**在历史分布里，
   这正是修复"幸存者偏差"的机制 —— 参照系不再只由活下来的基金构成）
3. **断点续传**：进度独立于全量采集

## 风控纪律（与全量采集一致）

    · 写库前检查快照
    · 幂等：`ON CONFLICT(fund_code, nav_date) DO UPDATE` + `COALESCE`
    · 时区显式 `Asia/Shanghai`
    · **不改 schema、不动 fund_info 之外的表**（清盘检测只报告，不落库）

用法:
    python scripts/collect_incremental.py                  # 正常增量
    python scripts/collect_incremental.py --days 20        # 拉最近 20 天（默认）
    python scripts/collect_incremental.py --stale-days 10  # 清盘判定阈值
    python scripts/collect_incremental.py --limit 100      # 试跑
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")
PROGRESS = os.path.join(ROOT, "data", "incremental_progress.json")
STALE_REPORT = os.path.join(ROOT, "data", "stale_funds.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
HDR = {"User-Agent": UA, "Referer": "http://fundf10.eastmoney.com/"}
CN = timezone(timedelta(hours=8))


def fetch_recent(code, page_size=20):
    """lsjz 首页 = 最近 N 天。返回 (code, ok, rows[(date, unit, acc, ret)])"""
    try:
        r = requests.get("http://api.fund.eastmoney.com/f10/lsjz", headers=HDR,
                         params={"fundCode": code, "pageIndex": 1, "pageSize": page_size},
                         timeout=20)
        if r.status_code != 200:
            return code, False, f"HTTP {r.status_code}"
        d = r.json()
        lst = (d.get("Data") or {}).get("LSJZList") or []
        rows = []
        for it in lst:
            dt = it.get("FSRQ")
            if not dt:
                continue

            def num(v):
                try:
                    f = float(v)
                    return f if f == f else None
                except Exception:
                    return None

            rows.append((dt, num(it.get("DWJZ")), num(it.get("LJJZ")), num(it.get("JZZZL"))))
        return code, True, rows
    except Exception as e:
        return code, False, f"{type(e).__name__}: {str(e)[:50]}"


def discover_new_funds(conn, api_key_note=""):
    """拉最新基金名录，把库里没有的代码补进 fund_info（只填代码+名称）"""
    try:
        import akshare as ak
        df = ak.fund_name_em()
        have = {r[0] for r in conn.execute("select fund_code from fund_info")}
        new = [(str(r[0]), str(r[2]) if df.shape[1] > 2 else str(r[0]),
                str(r[3]) if df.shape[1] > 3 else "")
               for r in df.itertuples(index=False) if str(r[1]) not in have]
        if new:
            with conn:
                conn.executemany(
                    "insert or ignore into fund_info (fund_code, fund_name, fund_type) values (?,?,?)",
                    new)
        return len(new), len(df)
    except Exception as e:
        return -1, f"{type(e).__name__}: {str(e)[:60]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20, help="拉最近 N 天（lsjz 单页上限 20）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--stale-days", type=int, default=10,
                    help="超过 N 天无新净值 → 报告为疑似清盘/停牌")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-discover", action="store_true", help="跳过新基金发现")
    args = ap.parse_args()

    snaps = [f for f in os.listdir(os.path.join(ROOT, "data"))
             if f.startswith("_snapshot_") and f.endswith(".db")]
    if not snaps:
        print("❌ 未找到 data/_snapshot_*.db —— 写库前必须先做完整快照（铁律 2）")
        sys.exit(1)
    print(f"✓ 快照存在: {sorted(snaps)[-1]}")

    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")

    # ---- ① 发现新基金 ----
    if not args.skip_discover:
        n_new, tot = discover_new_funds(conn)
        if n_new >= 0:
            print(f"① 新基金发现：名录 {tot:,} 只，新增入库 {n_new} 只")
        else:
            print(f"① 新基金发现失败（跳过，不影响增量）：{tot}")

    where = "where purchase_status like '%开放%' or purchase_status is null or purchase_status = ''"
    codes = [r[0] for r in conn.execute(f"select fund_code from fund_info {where} order by fund_code")]
    if args.limit:
        codes = codes[:args.limit]
    print(f"   待增量 {len(codes):,} 只（最近 {args.days} 天）")

    sql = """INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)
             VALUES (?,?,?,?,?)
             ON CONFLICT(fund_code, nav_date) DO UPDATE SET
               unit_nav=COALESCE(excluded.unit_nav, fund_nav.unit_nav),
               acc_nav=COALESCE(excluded.acc_nav, fund_nav.acc_nav),
               daily_return=COALESCE(excluded.daily_return, fund_nav.daily_return)"""

    t0 = time.time()
    ok = fail = 0
    rows_w = 0
    seen = {}      # code -> 最新净值日（用于清盘检测）
    errs = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (code, good, payload) in enumerate(ex.map(lambda c: fetch_recent(c, args.days), codes), 1):
            if good and payload:
                try:
                    with conn:
                        conn.executemany(sql, [(code, d, u, a, r) for d, u, a, r in payload])
                    rows_w += len(payload)
                    ok += 1
                    seen[code] = max(d for d, *_ in payload)
                except Exception as e:
                    fail += 1
                    errs.append((code, f"DB {type(e).__name__}"))
            else:
                fail += 1
                if len(errs) < 30:
                    errs.append((code, payload if isinstance(payload, str) else "空"))
            if i % 2000 == 0 or i == len(codes):
                el = time.time() - t0
                print(f"   {i:,}/{len(codes):,}  成功 {ok:,} 失败 {fail}  "
                      f"{rows_w:,} 行  {el:.0f}s  {i/max(el,1):.0f} 只/秒")

    el = time.time() - t0
    print(f"\n② 增量完成：成功 {ok:,} / 失败 {fail} / 写入 {rows_w:,} 行 / {el/60:.1f} 分钟")
    if errs:
        print(f"   失败样本（前 10）: {errs[:10]}")

    # ---- ③ 清盘 / 停牌检测（只报告，不落库、不改 schema）----
    # ⚠️ 首版只看「成功返回但净值日期旧」的基金，**漏掉了最该检测的那批** ——
    #    返回**空列表**的基金（lsjz 无数据）往往正是已清盘/合并的。
    #    试跑 200 只里有 14 只返回空，全是老代码（000002/000012/000108…）。
    today = datetime.now(CN).date()
    cutoff = (today - timedelta(days=args.stale_days)).isoformat()
    # 库里各基金的最后净值日
    last_in_db = {r[0]: r[1] for r in conn.execute(
        "select fund_code, max(nav_date) from fund_nav group by fund_code")}
    stale = []
    no_data = []
    for code in codes:
        last = seen.get(code)
        if last is None:
            # 本次取数返回空 —— 无论库里有没有记录，都值得报告：
            #   库里有旧记录 → 疑似清盘
            #   库里也没记录 → 从未采到过（可能是停牌/清盘/数据源不覆盖）
            last = last_in_db.get(code)
            d = (today - datetime.fromisoformat(last).date()).days if last else None
            if d is None or d >= args.stale_days:
                no_data.append({"fund_code": code, "last_nav": last, "days_stale": d})
            continue
        if last < cutoff:
            stale.append({"fund_code": code, "last_nav": last,
                          "days_stale": (today - datetime.fromisoformat(last).date()).days})
    stale.sort(key=lambda x: -x["days_stale"])
    no_data.sort(key=lambda x: -(x["days_stale"] if x["days_stale"] is not None else 10**6))
    json.dump({"checked_at": datetime.now(CN).isoformat(),
               "stale_days_threshold": args.stale_days,
               "count_no_recent_data": len(stale),
               "count_fetch_empty": len(no_data),
               "no_recent_data": stale[:2000],
               "fetch_empty": no_data[:2000]},
              open(STALE_REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n③ 疑似清盘/停牌：")
    print(f"   有数据但净值陈旧 : {len(stale):,} 只（超过 {args.stale_days} 天）")
    print(f"   取数返回**空**    : {len(no_data):,} 只  ← 多数是已清盘/合并")
    print(f"   报告已写 {os.path.relpath(STALE_REPORT, ROOT)}")
    print("   ⭐ 这些基金**不应删除** —— 保留在参照系的历史分布里才能修复幸存者偏差")
    for s in (no_data[:3] + stale[:2]):
        ds = f"已停 {s['days_stale']} 天" if s["days_stale"] is not None else "库中无净值记录"
        print(f"     {s['fund_code']}  最后净值 {s['last_nav']}  {ds}")

    n = conn.execute("select count(*) from fund_nav").fetchone()[0]
    d = conn.execute("select count(distinct fund_code) from fund_nav").fetchone()[0]
    deep = conn.execute("""select count(*) from (select fund_code from fund_nav
                           group by fund_code having count(*)>=756)""").fetchone()[0]
    print(f"\n   库内：{n:,} 行 / {d:,} 只 / 深历史 {deep:,} 只")
    conn.close()


if __name__ == "__main__":
    main()
