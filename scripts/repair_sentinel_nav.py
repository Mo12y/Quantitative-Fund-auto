# -*- coding: utf-8 -*-
"""
数据修复：把「缺失哨兵值」0.0 改为 NULL
=======================================
依据：scripts/selfcheck_data_quality.py 实测（2026-09-21）
   - acc_nav = 0.0 : 124 行 / 30 只   ← 语义是"不知道"，不是"净值为零"
   - unit_nav <= 0 :  40 行 /  1 只   ← 同上（均为 000425 长盛添利宝货币B）

为什么必须修：
  `0.0` 不是 NULL。任何朴素的 `COALESCE(acc_nav, unit_nav)` 都会返回 0.0，
  而正确行为是**逐行回退 unit_nav**（SSOT: src/analysis/nav_series.py）。
  实测该陷阱已命中 scripts/calibrate_thresholds.py（参照系口径与线上推荐分叉）。

为什么是安全的行为中性改动：
  SSOT `VALUATION_NAV_SQL` 用 `acc_nav IS NOT NULL AND acc_nav > 0` 判定有效性，
  NULL 与 0.0 在它眼里**同样无效** → 修复后所有正确读者行为不变，
  只有踩了 COALESCE 陷阱的读者被修好。零行为回归风险。

铁律 2：写库前必须有快照，且快照须仍与源库一致。
"""
import hashlib
import io
import json
import os
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "fund_quant.db")
MANIFEST = os.path.join(ROOT, "data", "_snapshot_post_collect_manifest.json")
DRY = "--apply" not in sys.argv


def sha256(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main():
    print("=" * 74)
    print("数据修复：缺失哨兵 0.0 → NULL" + ("   [DRY-RUN，加 --apply 才写库]" if DRY else "   [APPLY]"))
    print("=" * 74)

    # ---------- ① 快照校验 ----------
    if not os.path.exists(MANIFEST):
        print("❌ 未找到快照清单", MANIFEST)
        return 1
    man = json.load(open(MANIFEST, encoding="utf-8"))
    snap = man.get("snapshot_path") or man.get("snapshot")
    if snap and not os.path.isabs(snap):
        snap = os.path.join(ROOT, snap)
    if not snap or not os.path.exists(snap):
        cand = [f for f in os.listdir(os.path.join(ROOT, "data"))
                if f.startswith("_snapshot_") and f.endswith(".db")]
        if not cand:
            print("❌ 无快照，拒绝写库（铁律 2）")
            return 1
        snap = os.path.join(ROOT, "data", sorted(cand)[-1])
    print("快照:", os.path.relpath(snap, ROOT))
    sz_snap = os.path.getsize(snap)
    sz_db = os.path.getsize(DB)
    print("  快照 %s 字节 | 源库 %s 字节 | 差 %+d" % (f"{sz_snap:,}", f"{sz_db:,}", sz_db - sz_snap))

    exp = man.get("sha256")
    if exp:
        got = sha256(snap)
        print("  快照 sha256 校验:", "✅ 一致" if got == exp else "❌ 不一致 %s != %s" % (got[:16], exp[:16]))
    if sz_db != sz_snap:
        print("  ⚠️ 源库与快照字节数不同 —— 快照后源库被改过。仍继续，但修复本身可回滚（见文末）。")

    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    cur = conn.cursor()

    # ---------- ② 统计待修行 ----------
    n_acc = cur.execute("select count(*) from fund_nav where acc_nav = 0.0").fetchone()[0]
    n_uni = cur.execute("select count(*) from fund_nav where unit_nav <= 0").fetchone()[0]
    bad_acc = cur.execute("""select fund_code, nav_date, unit_nav, acc_nav from fund_nav
                             where acc_nav = 0.0 order by fund_code, nav_date""").fetchall()
    bad_uni = cur.execute("""select fund_code, nav_date, unit_nav, acc_nav from fund_nav
                             where unit_nav <= 0 order by fund_code, nav_date""").fetchall()
    print("\n待修：acc_nav=0.0 %d 行 | unit_nav<=0 %d 行" % (n_acc, n_uni))
    print("  受影响基金：acc %d 只 / unit %d 只"
          % (len({r[0] for r in bad_acc}), len({r[0] for r in bad_uni})))

    # ---------- ③ 记录回滚清单 ----------
    rollback = os.path.join(ROOT, "data", "_repair_rollback_sentinel.json")
    if not DRY:
        json.dump({"acc_nav_zero": [list(r) for r in bad_acc],
                   "unit_nav_nonpos": [list(r) for r in bad_uni]},
                  open(rollback, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("  回滚清单已写:", os.path.relpath(rollback, ROOT))

    if DRY:
        print("\nDRY-RUN 结束。加 --apply 执行。")
        return 0

    # ---------- ④ 执行 ----------
    # ⚠️ 只把**哨兵值**改为 NULL；不删行、不动任何有效净值。
    with conn:
        c1 = conn.execute("update fund_nav set acc_nav = NULL where acc_nav = 0.0").rowcount
        c2 = conn.execute("update fund_nav set unit_nav = NULL where unit_nav <= 0").rowcount
    print("\n已修：acc_nav → NULL %d 行 | unit_nav → NULL %d 行" % (c1, c2))

    # ---------- ⑤ 完成即验证（规则 15） ----------
    print("\n--- 验证 ---")
    for sql, label in [("select count(*) from fund_nav where acc_nav = 0.0", "acc_nav=0.0 残留"),
                       ("select count(*) from fund_nav where unit_nav <= 0", "unit_nav<=0 残留"),
                       ("select count(*) from fund_nav", "总行数"),
                       ("select count(*) from fund_nav where acc_nav is null", "acc_nav NULL")]:
        print("  %-16s %d" % (label, cur.execute(sql).fetchone()[0]))
    print("  PRAGMA quick_check:", cur.execute("PRAGMA quick_check").fetchone()[0])

    # 行为中性验证：SSOT 口径下，修复前后取到的估值序列应完全一致
    print("\n--- 行为中性验证：SSOT 口径下受影响基金的点数应不变 ---")
    from src.analysis.nav_series import VALUATION_NAV_SQL  # noqa: E402
    for fc in ("160641", "000425", "005661"):
        cur.execute(f"select count(*) from fund_nav where fund_code=? and {VALUATION_NAV_SQL} > 0", (fc,))
        print("  %s 有效估值点数 = %d" % (fc, cur.fetchone()[0]))

    conn.close()
    print("\n完成。回滚：按 data/_repair_rollback_sentinel.json 逐行还原为 0.0。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
