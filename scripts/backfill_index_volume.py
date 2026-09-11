"""
回填 index_daily.volume（指数日线成交量）。

背景：`save_index_val_to_db` 之前只从 PB 表取 close，从不写 volume，导致
`index_daily.volume` 全表 NULL —— 温度计的"量能"维度因此一直走 50.0 兜底
（约 26% 的温度权重是死数）。

数据源：新浪 `CN_MarketData.getKLineData`（与 akshare `stock_zh_index_daily`
**同一个源**，量的口径一致）。本机 akshare 出网失败，故用 curl.exe 子进程抓取。

默认 **dry-run，只报告不写库**。写库只补 `volume IS NULL` 的行，绝不覆盖
close / pe / pb。

用法：
    python scripts/backfill_index_volume.py            # 只看方案（只读）
    python scripts/backfill_index_volume.py --apply    # 真正写库（请先整库快照）
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "fund_quant.db")

# index_code -> sina 代码
SINA_SYMBOL = {
    "000300": "sh000300",
    "000905": "sh000905",
    "000016": "sh000016",
}

URL = ("http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
       "CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=6000")


def fetch_kline(sym: str, retries: int = 3):
    """抓取指数日线（含 volume）；失败返回 None。"""
    url = URL.format(sym=sym)
    for _ in range(retries):
        p = subprocess.run(
            ["curl.exe", "-sS", "-m", "30", "--ssl-no-revoke", "-A", "Mozilla/5.0",
             "-H", "Referer: https://finance.sina.com.cn", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode == 0 and p.stdout:
            try:
                return json.loads(p.stdout)
            except Exception:
                pass
        time.sleep(1.0)
    return None


def main() -> int:
    apply_it = "--apply" in sys.argv
    if not os.path.exists(DB_PATH):
        print(f"未找到数据库: {DB_PATH}")
        return 1

    ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    null_counts = {
        r["index_code"]: r["n"]
        for r in ro.execute(
            "SELECT index_code, COUNT(*) AS n FROM index_daily "
            "WHERE volume IS NULL GROUP BY index_code")
    }
    existing = {r["index_code"]: set(
        x[0] for x in ro.execute(
            "SELECT trade_date FROM index_daily WHERE index_code = ?", (r["index_code"],)))
        for r in ro.execute("SELECT DISTINCT index_code FROM index_daily")}
    ro.close()

    print("index_daily.volume 回填方案" + ("（dry-run，只读）" if not apply_it else "（将写库）"))
    print("=" * 78)
    print(f"当前 volume 为 NULL 的行: {dict(null_counts)}")

    plans = {}
    for code, sym in SINA_SYMBOL.items():
        data = fetch_kline(sym)
        if not data:
            print(f"  [FAIL] {code} ({sym}) 抓取失败 —— 跳过")
            continue
        pairs = []
        for row in data:
            d = str(row.get("day", ""))[:10]
            try:
                v = float(row.get("volume") or 0)
            except Exception:
                v = 0.0
            if d and v > 0 and d in existing.get(code, set()):
                pairs.append((v, code, d))
        plans[code] = pairs
        print(f"  [OK] {code} ({sym}) 抓到 {len(data)} 行，可回填 {len(pairs)} 行")

    total = sum(len(p) for p in plans.values())
    print("-" * 78)
    print(f"合计可回填 {total} 行（仅补 volume IS NULL 的行）。")

    if not apply_it:
        print()
        print("这是 dry-run，**没有写任何数据**。")
        print("确认无误后执行（请先整库快照）：")
        print("    python scripts/backfill_index_volume.py --apply")
        return 0

    if total == 0:
        print("无可回填数据，未写库。")
        return 0

    db = sqlite3.connect(DB_PATH)
    cur = db.cursor()
    written = 0
    try:
        cur.execute("BEGIN IMMEDIATE")
        for pairs in plans.values():
            cur.executemany(
                "UPDATE index_daily SET volume = ? "
                "WHERE index_code = ? AND trade_date = ? AND volume IS NULL", pairs)
            written += cur.rowcount
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"写库失败，已回滚: {e}")
        db.close()
        return 1
    db.close()
    print(f"已回填 {written} 行（只补 volume 为 NULL 的行，未触碰 close/pe/pb）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
