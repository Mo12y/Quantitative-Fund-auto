#!/usr/bin/env python3
"""深历史净值批量采集（批次 3）

**通道选型依据**（见 scripts/probe_collect_feasibility*.py 实测）：
    通道                  拉全历史(1824条)   换算 18,677 只(并发8)
    lsjz 分页             8.46s / 92 页      5.5 小时
    akshare               3.12s / 2 次调用   2.0 小时
    pingzhongdata         0.48s / 1 次请求   33 分钟  ← 选用
压力实测：400 只 × workers=8 → **零失败**，9.4 只/秒。

**顺带解决 fund_info 空字段**：pingzhongdata 一次请求同时含
规模 / 费率 / 现任基金经理 / 同类排名 / 股票仓位 / 资产配置，
覆盖此前 0% 填充的 custodian_fee / purchase_fee / redeem_fee / manager_tenure。

**风控纪律**：
    · 写库前必须已有完整快照（本脚本会检查）
    · **幂等**：fund_nav 用 ON CONFLICT DO UPDATE；fund_info **只填空值，不覆盖已采到的**
      （这是之前"两条采集路径互相清空字段"事故的直接教训）
    · **断点续传**：进度写 JSON，中断后重跑不会重复拉取
    · 时限友好：--limit 可先小规模试跑

用法:
    python scripts/collect_deep_nav.py --limit 50          # 试跑 50 只
    python scripts/collect_deep_nav.py                     # 全量（约 33~66 分钟）
    python scripts/collect_deep_nav.py --workers 8 --resume
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")
PROGRESS = os.path.join(ROOT, "data", "collect_progress.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
CN = timezone(timedelta(hours=8))   # 东财毫秒是北京时间零点


def ms_to_date(ms):
    """⚠️ 必须显式用北京时间：本机 UTC+8 时 fromtimestamp 恰好正确，
    换到 UTC 机器会整体早一天（批次 2.3 的同一教训）。"""
    return datetime.fromtimestamp(int(ms) / 1000, tz=CN).strftime("%Y-%m-%d")


def fetch(code):
    """返回 (code, ok, payload)；payload 为解析后的 dict。"""
    url = f"http://fund.eastmoney.com/pingzhongdata/{code}.js"
    try:
        r = requests.get(url, headers={"User-Agent": UA,
                                       "Referer": f"http://fund.eastmoney.com/{code}.html"},
                         timeout=25)
        if r.status_code != 200 or len(r.content) < 1000:
            return code, False, {"err": f"HTTP {r.status_code}/{len(r.content)}B"}
        txt = r.text
    except Exception as e:
        return code, False, {"err": f"{type(e).__name__}: {str(e)[:60]}"}

    out = {"nav": [], "info": {}}
    # 单位净值 + 日增长率
    m = re.search(r'var\s+Data_netWorthTrend\s*=\s*(\[.*?\])\s*;', txt, re.S)
    unit = {}
    if m:
        for mm in re.finditer(r'\{"x":(\d+),"y":([\d.]+),"equityReturn":([-\d.]+)', m.group(1)):
            d = ms_to_date(mm.group(1))
            unit[d] = (float(mm.group(2)), float(mm.group(3)))
    # 累计净值
    acc = {}
    m2 = re.search(r'var\s+Data_ACWorthTrend\s*=\s*(\[.*?\])\s*;', txt, re.S)
    if m2:
        for mm in re.finditer(r'\[(\d+),([\d.]+)\]', m2.group(1)):
            acc[ms_to_date(mm.group(1))] = float(mm.group(2))
    for d in sorted(set(unit) | set(acc)):
        u = unit.get(d, (None, None))
        out["nav"].append((d, u[0], acc.get(d), u[1]))

    # ---- fund_info 增强字段 ----
    def grab(pat, cast=str):
        mm = re.search(pat, txt, re.S)
        if not mm:
            return None
        try:
            return cast(mm.group(1))
        except Exception:
            return None

    rate = grab(r'var\s+fund_Rate\s*=\s*"([\d.]+)"', float)
    if rate is not None:
        out["info"]["purchase_fee"] = rate
    scale = re.search(r'"series":\[(.*?)\]', txt[re.search(r'var\s+Data_fluctuationScale', txt).start():] if re.search(r'var\s+Data_fluctuationScale', txt) else "", re.S)
    mgr = re.search(r'"name":"([^"]+)"', txt[re.search(r'var\s+Data_currentFundManager', txt).start():] if re.search(r'var\s+Data_currentFundManager', txt) else "")
    if mgr:
        out["info"]["manager_name"] = mgr.group(1)
    return code, True, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只采前 N 只（试跑用）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--resume", action="store_true", default=True)
    ap.add_argument("--include-all", action="store_true",
                    help="含非开放申购；默认只采「开放申购」")
    args = ap.parse_args()

    # ---- 快照检查（铁律 2）----
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

    # ---- 目标清单 ----
    where = "" if args.include_all else "where purchase_status like '%开放%'"
    codes = [r[0] for r in conn.execute(f"select fund_code from fund_info {where} order by fund_code")]
    done = set()
    if args.resume and os.path.exists(PROGRESS):
        try:
            done = set(json.load(open(PROGRESS, encoding="utf-8")).get("done", []))
        except Exception:
            pass
    todo = [c for c in codes if c not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"目标 {len(codes):,} 只 / 已完成 {len(done):,} / 本次待采 {len(todo):,}")
    if not todo:
        print("无待采，退出")
        return

    nav_sql = """INSERT INTO fund_nav (fund_code, nav_date, unit_nav, acc_nav, daily_return)
                 VALUES (?,?,?,?,?)
                 ON CONFLICT(fund_code, nav_date) DO UPDATE SET
                   unit_nav=COALESCE(excluded.unit_nav, fund_nav.unit_nav),
                   acc_nav=COALESCE(excluded.acc_nav, fund_nav.acc_nav),
                   daily_return=COALESCE(excluded.daily_return, fund_nav.daily_return)"""
    # ⚠️ fund_info 只填空值，**绝不覆盖已采到的**（R2 事故的教训）
    info_sql = """UPDATE fund_info SET
                    purchase_fee = COALESCE(purchase_fee, ?),
                    manager_name = COALESCE(manager_name, ?),
                    updated_at = datetime('now','localtime')
                  WHERE fund_code = ?"""

    t0 = time.time()
    ok = fail = 0
    rows_total = 0
    errs = []
    lock = __import__("threading").Lock()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (code, good, payload) in enumerate(ex.map(fetch, todo), 1):
            if good:
                navs = payload["nav"]
                try:
                    with conn:
                        conn.executemany(nav_sql, [(code, d, u, a, r) for d, u, a, r in navs])
                        inf = payload.get("info", {})
                        conn.execute(info_sql, (inf.get("purchase_fee"),
                                                inf.get("manager_name"), code))
                    rows_total += len(navs)
                    ok += 1
                    done.add(code)
                except Exception as e:
                    fail += 1
                    errs.append((code, f"DB {type(e).__name__}: {str(e)[:50]}"))
            else:
                fail += 1
                errs.append((code, payload.get("err", "?")))

            if i % 200 == 0 or i == len(todo):
                el = time.time() - t0
                print(f"  {i:,}/{len(todo):,}  成功 {ok:,} 失败 {fail}  "
                      f"{rows_total:,} 行  {el:.0f}s  {i/el:.1f} 只/秒  "
                      f"预计剩余 {(len(todo)-i)/max(i/el,0.1)/60:.0f} 分钟")
                json.dump({"done": sorted(done), "updated": datetime.now().isoformat()},
                          open(PROGRESS, "w", encoding="utf-8"))

    json.dump({"done": sorted(done), "updated": datetime.now().isoformat()},
              open(PROGRESS, "w", encoding="utf-8"))
    el = time.time() - t0
    print()
    print(f"完成：成功 {ok:,} / 失败 {fail} / 写入 {rows_total:,} 行 / 耗时 {el/60:.1f} 分钟")
    if errs:
        print(f"失败样本（前 10）: {errs[:10]}")
    n = conn.execute("select count(*) from fund_nav").fetchone()[0]
    d = conn.execute("select count(distinct fund_code) from fund_nav").fetchone()[0]
    deep = conn.execute("""select count(*) from (select fund_code from fund_nav
                           group by fund_code having count(*)>=756)""").fetchone()[0]
    print(f"    库内 fund_nav: {n:,} 行 / {d:,} 只 / **深历史(≥756) {deep:,} 只**")
    conn.close()


if __name__ == "__main__":
    main()
