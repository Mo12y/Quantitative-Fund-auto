"""决定性验证：可买池里那些「<3月」的基金，是真年轻还是采集没跑到？

直接对样本调 pingzhongdata，看服务端返回多长历史。
若返回只有近期 → 是真年轻（结构性问题，采集无解）
若返回数年     → 是采集未覆盖（补采集可解）
"""
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir(r"D:\DSH\projects\Quantitative-Fund-auto")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
CN = timezone(timedelta(hours=8))

c = sqlite3.connect("file:data/fund_quant.db?mode=ro", uri=True)
# 取「可买 且 当前净值点数 < 60」的基金，跨不同代码段抽样（避免只测老代码段）
rows = c.execute("""
    select f.fund_code from fund_info f
    join (select fund_code, count(*) k from fund_nav group by fund_code) n
      on n.fund_code = f.fund_code
    where f.purchase_status like '%开放%' and n.k < 60
    order by random() limit 40
""").fetchall()
c.close()
codes = [r[0] for r in rows]
print(f"抽样 {len(codes)} 只「可买 且 当前<60点」的基金，直接问服务端要全历史\n")

import collections
buckets = collections.Counter()
detail = []
for i, code in enumerate(codes, 1):
    try:
        r = requests.get(f"http://fund.eastmoney.com/pingzhongdata/{code}.js",
                         headers={"User-Agent": UA,
                                  "Referer": f"http://fund.eastmoney.com/{code}.html"},
                         timeout=20)
        if r.status_code != 200 or len(r.content) < 1000:
            buckets["拉取失败"] += 1
            continue
        txt = r.text
        m = re.search(r'var\s+Data_netWorthTrend\s*=\s*(\[.*?\])\s*;', txt, re.S)
        if not m:
            buckets["无净值数据"] += 1
            continue
        xs = re.findall(r'\{"x":(\d+),"y":', m.group(1))
        n = len(xs)
        if n == 0:
            buckets["无净值数据"] += 1
            continue
        d0 = datetime.fromtimestamp(int(xs[0]) / 1000, tz=CN).strftime("%Y-%m-%d")
        if n >= 756:
            buckets[">=3年"] += 1
        elif n >= 252:
            buckets["1-3年"] += 1
        elif n >= 60:
            buckets["3月-1年"] += 1
        else:
            buckets["<3月"] += 1
        if len(detail) < 12:
            detail.append((code, n, d0))
    except Exception as e:
        buckets[f"异常 {type(e).__name__}"] += 1
    time.sleep(0.15)

print("服务端返回的真实历史长度：")
for k, v in buckets.most_common():
    print(f"  {k:16} {v:>3} 只  ({v/len(codes):.0%})")
print()
print("样本明细（代码 / 点数 / 起点）：")
for code, n, d0 in detail:
    print(f"  {code}  {n:>5} 点  起点 {d0}")
print()
deep = sum(v for k, v in buckets.items() if k in (">=3年", "1-3年"))
print(f"★ 其中 {deep}/{len(codes)} 只服务端有 ≥1 年历史 → **采集未覆盖，补采集可解**")
short = buckets.get("<3月", 0) + buckets.get("3月-1年", 0)
print(f"★ 其中 {short}/{len(codes)} 只服务端也只有短期 → **真年轻，采集无解**")
