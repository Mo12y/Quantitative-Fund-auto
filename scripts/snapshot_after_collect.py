"""采集完成后的数据保存（只做这一件，不跑验证清单）"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\DSH\projects\Quantitative-Fund-auto"
os.chdir(ROOT)
DB = "data/fund_quant.db"

print("=" * 90)
print("[1] 采集后完整性检查")
print("=" * 90)
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
qc = c.execute("PRAGMA quick_check").fetchone()[0]
print(f"  PRAGMA quick_check : {qc}")
n, d = c.execute("select count(*), count(distinct fund_code) from fund_nav").fetchone()
deep = c.execute("""select count(*) from (select fund_code from fund_nav
                    group by fund_code having count(*)>=756)""").fetchone()[0]
mid = c.execute("""select count(*) from (select fund_code from fund_nav
                   group by fund_code having count(*)>=252)""").fetchone()[0]
acc = c.execute("select count(*) from fund_nav where acc_nav is not null").fetchone()[0]
print(f"  fund_nav           : {n:,} 行 / {d:,} 只")
print(f"  深历史 (>=756天)    : {deep:,} 只   (采集前 576)")
print(f"  >=252天            : {mid:,} 只   (采集前 589)")
print(f"  acc_nav 非空        : {acc:,} ({acc/n:.1%})")
print()
print("  深历史按类型：")
for t, k in c.execute("""
    with dp as (select fund_code from fund_nav group by fund_code having count(*)>=756)
    select coalesce(f.fund_type,'(未知)'), count(*)
    from dp join fund_info f on f.fund_code=dp.fund_code
    group by 1 order by 2 desc limit 10"""):
    print(f"    {t:24} {k:>6,}")
print()
print("  fund_info 填充率：")
for col in ["establish_date", "fund_size", "manager_name", "custodian_fee",
            "purchase_fee", "redeem_fee", "manager_tenure", "benchmark"]:
    k = c.execute(f'select count(*) from fund_info where "{col}" is not null and "{col}" != \'\'').fetchone()[0]
    print(f"    {col:16} {k:>7,} / 27,864  ({k/27864:>6.2%})")
# 可买池覆盖
opened = c.execute("select count(*) from fund_info where purchase_status like '%开放%'").fetchone()[0]
both = c.execute("""select count(*) from fund_info f
    join (select fund_code from fund_nav group by fund_code having count(*)>=756) d
      on d.fund_code=f.fund_code where f.purchase_status like '%开放%'""").fetchone()[0]
print()
print(f"  可买池 {opened:,} / 其中深历史 {both:,} = **覆盖率 {both/opened:.1%}**  (采集前 2.99%)")
c.close()

print()
print("=" * 90)
print("[2] 快照（保存采集完的数据）")
print("=" * 90)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
snap = f"data/_snapshot_{ts}_post_collect.db"
sz = os.path.getsize(DB)
print(f"  源库: {sz:,} bytes ({sz/1e9:.2f} GB)")
shutil.copy2(DB, snap)
ssz = os.path.getsize(snap)
print(f"  快照: {snap}")
print(f"        {ssz:,} bytes   一致={os.path.getsize(DB) == ssz}")

print()
print("  sha256 计算中（2GB，请稍候）...")
def sha(p, chunk=1 << 22):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

h1 = sha(DB)
h2 = sha(snap)
print(f"  源库 sha256: {h1}")
print(f"  快照 sha256: {h2}")
print(f"  逐字节一致  : {h1 == h2}")

# 写一份清单
manifest = {
    "snapshot": snap,
    "created_at": ts,
    "source_db": DB,
    "bytes": ssz,
    "sha256": h2,
    "quick_check": qc,
    "stats": {"fund_nav_rows": n, "funds_with_nav": d,
              "deep_history_756": deep, "ge_252": mid,
              "opened_pool": opened, "opened_with_deep": both,
              "coverage": round(both / opened, 4)},
    "note": "全量采集完成后的原始快照；验证清单尚未执行（用户要求中午再做）",
}
mf = "data/_snapshot_post_collect_manifest.json"
json.dump(manifest, open(mf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n  清单: {mf}")
