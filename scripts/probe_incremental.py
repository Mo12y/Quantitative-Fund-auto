"""增量采集可行性：每日更新用哪条通道、多快、多少流量？

关键问题：pingzhongdata 是**全量接口**（每只 291KB），
用它做"日常增量"在请求数和流量上**和全量一样贵**。
→ 需要一条真正的增量通道。
"""
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
HDR = {"User-Agent": UA, "Referer": "http://fundf10.eastmoney.com/"}

c = sqlite3.connect("file:data/fund_quant.db?mode=ro", uri=True)
codes = [r[0] for r in c.execute(
    "select fund_code from fund_info where purchase_status like '%开放%' limit 400")]
c.close()

print("=" * 90)
print("① lsjz 首页 = 最近 N 天？（增量通道的前提）")
print("=" * 90)
r = requests.get("http://api.fund.eastmoney.com/f10/lsjz", headers=HDR,
                 params={"fundCode": "007029", "pageIndex": 1, "pageSize": 20}, timeout=15)
d = r.json()
lst = (d.get("Data") or {}).get("LSJZList") or []
print(f"  返回 {len(lst)} 条，日期范围: {lst[-1].get('FSRQ')} ~ {lst[0].get('FSRQ')}")
print(f"  ★ 最新在前（倒序）: {lst[0].get('FSRQ')} 是第一条 → {'是 ✅' if lst[0].get('FSRQ') > lst[-1].get('FSRQ') else '否 ❌'}")
print(f"  单次流量: {len(r.content):,} bytes")

print()
print("=" * 90)
print("② 增量请求的实测速率（400 只 lsjz 首页）")
print("=" * 90)


def one(code):
    t0 = time.time()
    try:
        rr = requests.get("http://api.fund.eastmoney.com/f10/lsjz", headers=HDR,
                          params={"fundCode": code, "pageIndex": 1, "pageSize": 20}, timeout=15)
        ok = rr.status_code == 200 and len(rr.content) > 300
        return ok, len(rr.content), time.time() - t0
    except Exception:
        return False, 0, time.time() - t0


for w in [8, 16]:
    t0 = time.time()
    ok = bad = 0
    bytes_total = 0
    with ThreadPoolExecutor(max_workers=w) as ex:
        for good, sz, _ in ex.map(one, codes):
            if good:
                ok += 1
                bytes_total += sz
            else:
                bad += 1
    el = time.time() - t0
    rate = ok / el if el else 0
    print(f"  workers={w:>2}  成功 {ok}/400 失败 {bad}  {el:.1f}s  "
          f"{rate:.0f} 只/秒  均 {bytes_total/max(ok,1):,.0f} B/只")
    if w == 8:
        print(f"     → 18,677 只约 {18677/rate/60:.1f} 分钟，流量 {18677*bytes_total/max(ok,1)/1e6:.0f} MB")

print()
print("=" * 90)
print("③ 全量 vs 增量的成本对照")
print("=" * 90)
print(f"  {'':12} {'通道':16} {'每只':>9} {'18,677只耗时':>14} {'流量':>10}")
print(f"  {'首次全量':12} {'pingzhongdata':16} {'291 KB':>9} {'2.7 小时':>14} {'5.4 GB':>10}")
print(f"  {'每日增量':12} {'lsjz 首页':16} {'4.5 KB':>9} {'见上':>14} {'见上':>10}")
