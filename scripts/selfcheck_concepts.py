"""批次 2：概念性自查（只读）

2.1 幸存者偏差的**量化**：参照系池（净值≥3年）与「今天可买池」差多少？
2.2 `acc_nav` 隐含「红利再投」假设 —— 若用户收现金分红，偏差多大？
"""
import os
import sqlite3
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = r"D:\DSH\projects\Quantitative-Fund-auto\data\fund_quant.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def q(sql, *p):
    return conn.execute(sql, p).fetchall()


print("=" * 96)
print("2.1 幸存者偏差量化：参照系池 vs 今天可买池")
print("=" * 96)
tot = q("select count(*) from fund_info")[0][0]
opened = q("select count(*) from fund_info where purchase_status like '%开放%'")[0][0]
deep = q("""select count(*) from (select fund_code from fund_nav
            group by fund_code having count(*)>=756)""")[0][0]
both = q("""select count(*) from fund_info f
            join (select fund_code from fund_nav group by fund_code having count(*)>=756) d
              on d.fund_code=f.fund_code
            where f.purchase_status like '%开放%'""")[0][0]
print(f"  全市场 fund_info        : {tot:>7,}")
print(f"  今天可申购（开放申购）    : {opened:>7,}")
print(f"  参照系池（净值 ≥3 年）    : {deep:>7,}   = 全市场的 {deep/tot:.2%}")
print(f"  两者交集（可买 且 深历史）: {both:>7,}")
print()
print(f"  ★ 参照系池覆盖「可买池」的 **{both/opened:.2%}**")
print(f"    即今天能买到的基金里，**{1-both/opened:.1%} 不在参照系内** —— 无法给出同类百分位")
print()
print("  按类型看这个缺口（可买池里有多少能进参照系）：")
rows = q("""
    select f.fund_type,
           count(distinct f.fund_code) as 可买,
           count(distinct case when n.c>=756 then f.fund_code end) as 有深历史
    from fund_info f
    left join (select fund_code, count(*) c from fund_nav group by fund_code) n
      on n.fund_code = f.fund_code
    where f.purchase_status like '%开放%' and f.fund_type is not null
    group by f.fund_type having 可买 >= 100
    order by 可买 desc limit 14
""")
print(f"    {'类型':22} {'可买':>7} {'有深历史':>8} {'覆盖':>8}")
for t, a, b in rows:
    print(f"    {str(t)[:20]:22} {a:>7,} {b:>8,} {b/a:>7.1%}")

print()
print("=" * 96)
print("2.2 acc_nav 的「红利再投」假设 —— 对现金分红用户的偏差")
print("=" * 96)
# 哪些基金 acc_nav 与 unit_nav 有实质差异（= 分过红）
r = q("""select count(distinct fund_code) from fund_nav
         where acc_nav is not null and unit_nav is not null
           and abs(acc_nav-unit_nav) > 0.001""")[0][0]
tot_nav = q("select count(distinct fund_code) from fund_nav")[0][0]
print(f"  有分红痕迹的基金（acc≠unit）: {r:,} / {tot_nav:,} = {r/tot_nav:.2%}")

print()
print("  深历史池（≥3年）里各类型的分红比例与累计分红幅度：")
rows = q("""
    with deep as (select fund_code from fund_nav group by fund_code having count(*)>=756)
    select f.fund_type,
           count(distinct f.fund_code) as n,
           count(distinct case when abs(n2.acc_nav-n2.unit_nav)>0.001 then f.fund_code end) as 有分红,
           max(abs(n2.acc_nav-n2.unit_nav)) as 最大差,
           max(case when n2.unit_nav>0 then abs(n2.acc_nav-n2.unit_nav)/n2.unit_nav else 0 end) as 最大相对差
    from fund_info f
    join deep d on d.fund_code=f.fund_code
    left join fund_nav n2 on n2.fund_code=f.fund_code
    group by f.fund_type having n >= 10 order by n desc
""")
print(f"    {'类型':22} {'深历史':>7} {'有分红':>7} {'占比':>7} {'最大相对差':>10}")
for t, n, d, md, rel in rows:
    print(f"    {str(t)[:20]:22} {n:>7} {d:>7} {d/n:>6.0%} {rel:>10.2%}")
print()
print("  ⚠️ 含义：acc_nav 假设**红利全部再投资**。若用户实际选现金分红，")
print("     实际收益会低于 acc_nav 口径算出的收益，差额≈累计分红率。")
print("     用户 D4 决策：「默认红利再投，保留可切换」→ 对默认用户口径正确，")
print("     但 UI 需声明「本曲线按红利再投口径」，否则选现金分红的用户会误读。")
