"""批次 3.1c：pingzhongdata 压力验证 + 字段解析

它是三条通道里最快的（0.48s vs akshare 3.12s vs lsjz 8.46s），
**但此前实测过它会被限流**（首次 291KB 成功、之后稳定 0 字节）。
所以必须验证：① 持续压力下成功率 ② 能否解析出单位净值+累计净值 ③ 限流恢复时间
"""
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

c = sqlite3.connect("file:data/fund_quant.db?mode=ro", uri=True)
codes = [r[0] for r in c.execute(
    "select fund_code from fund_info where purchase_status like '%开放%' limit 400")]
c.close()


def get_pz(code):
    try:
        r = requests.get(f"http://fund.eastmoney.com/pingzhongdata/{code}.js",
                         headers={"User-Agent": UA,
                                  "Referer": f"http://fund.eastmoney.com/{code}.html"},
                         timeout=20)
        return code, r.status_code, len(r.content), r.text
    except Exception as e:
        return code, type(e).__name__, 0, ""


print("=" * 88)
print("① 单只解析：能拿到哪些变量？")
print("=" * 88)
code, st, size, txt = get_pz("007029")
print(f"  HTTP {st}  {size:,} B")
vars_found = re.findall(r"var\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", txt)
print(f"  JS 变量 {len(vars_found)} 个：{vars_found[:24]}")
KEY = {
    "Data_netWorthTrend": "单位净值走势（含日增长率）",
    "Data_ACWorthTrend": "累计净值走势",
    "Data_grandTotal": "累计收益率",
    "Data_rateInSimilarType": "同类排名",
    "Data_fluctuationScale": "规模变动",
    "Data_currentFundManager": "现任基金经理",
    "Data_holderStructure": "持有人结构",
    "Data_assetAllocation": "资产配置",
    "Data_performanceEvaluation": "业绩评价",
    "fund_Rate": "费率",
    "Data_fundSharesPositions": "股票仓位",
}
print()
for k, desc in KEY.items():
    has = f"var {k}" in txt
    print(f"  {'✅' if has else '❌'} {k:32} {desc}")

m = re.search(r"var Data_netWorthTrend\s*=\s*(\[.*?\]);", txt, re.S)
if m:
    arr = m.group(1)
    n = arr.count("x:")
    print(f"\n  ★ Data_netWorthTrend 解析出 {n} 个点")
    print(f"     首条样例: {arr[:130]}")
m2 = re.search(r"var Data_ACWorthTrend\s*=\s*(\[.*?\]);", txt, re.S)
if m2:
    arr2 = m2.group(1)
    n2 = arr2.count("[")
    print(f"  ★ Data_ACWorthTrend 解析出 {n2} 个点")
    print(f"     首条样例: {arr2[:110]}")

print()
print("=" * 88)
print("② 持续压力：400 只 × workers=8（这是此前失败过的通道）")
print("=" * 88)
t0 = time.time()
ok = fail = 0
small = []
with ThreadPoolExecutor(max_workers=8) as ex:
    for i, (cd, st, sz, _t) in enumerate(ex.map(get_pz, codes)):
        if st == 200 and sz > 5000:
            ok += 1
        else:
            fail += 1
            if len(small) < 8:
                small.append((cd, st, sz))
        if (i + 1) % 100 == 0:
            el = time.time() - t0
            print(f"    进度 {i+1}/400  成功 {ok} 失败 {fail}  {el:.1f}s  {ok/el:.1f} 只/秒")
el = time.time() - t0
print(f"  结果：成功 {ok}/400  失败 {fail}  耗时 {el:.1f}s  ≈{ok/el:.1f} 只/秒")
if small:
    print(f"  异常样本: {small}")

print()
print("=" * 88)
print("③ 若受限流，等待后能否恢复")
print("=" * 88)
if fail > 40:
    for wait in [10, 30]:
        time.sleep(wait)
        _c, _s, _z, _t = get_pz("007029")
        print(f"  等待 {wait}s 后重试: HTTP {_s}  {_z:,} B  {'✅ 已恢复' if _z > 5000 else '❌ 仍限流'}")
else:
    print("  未触发限流，无需测试恢复")

print()
print("=" * 88)
print("④ 工期估算（按实测速率）")
print("=" * 88)
rate = ok / el if el > 0 else 0
if rate > 0:
    print(f"  实测 {rate:.1f} 只/秒 → 18,677 只约 {18677/rate/60:.1f} 分钟")
    print(f"  保守按一半速率 {rate/2:.1f} 只/秒 → {18677/(rate/2)/60:.1f} 分钟")
