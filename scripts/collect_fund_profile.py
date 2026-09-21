# -*- coding: utf-8 -*-
"""
⑤ 基金档案补采（fund_size / manager_tenure / 费率 / 同类百分位）
=================================================================
背景：`fund_scorer.THRESHOLDS` 8 个阈值中 **4 个是死的**，因为它们依赖的字段填充率为 0：
      min_size_yi(0.5亿) / max_size_yi(200亿) → 依赖 fund_size
      min_manager_years(2年)                  → 依赖 manager_tenure
      max_total_fee(2.0%) / warn_total_fee    → 依赖 custodian_fee（源头不可得，见下）
      实测（本脚本 [0] 段打印）。

数据源验证（规则 1，已前置完成，见 docs/_objfields.txt）：
  pingzhongdata 里**有**：
    Data_fluctuationScale = {"categories":[季度], "series":[{"y":亿元,"mom":..}]}  → fund_size
    Data_currentFundManager[].workTime = "13年又361天"                            → manager_tenure
    fund_Rate / fund_sourceRate                                                  → 实收/原申购费率
    fund_minsg                                                                   → 最小申购额
    Data_rateInSimilarPersent = [[ts, 百分位]]                                    → **天天基金自己的同类百分位**
    syl_1n/6y/3y/1y                                                              → 近1年/6月/3月/1月收益
  pingzhongdata 里**没有**：custodian_fee / redeem_fee / benchmark / mgt_fee
    → 因此 `max_total_fee` 的口径改为**只管管理费**，而不是引入第二条采集通道。
      静默用 0 充当托管费会让该阈值永不触发（0 会把总费率算低）。

产物：
  1. `fund_info` 已有列：fund_size / manager_tenure / manager_name / purchase_fee（COALESCE 只填空）
  2. 新表 `fund_profile_extra`：本脚本新增字段（**纯追加，零风险**）
  3. `data/fund_profile_cache.jsonl`：解析结果落盘缓存 —— 以后重解析**不用再联网**

用法：
    python scripts/collect_fund_profile.py --limit 20      # 试跑
    python scripts/collect_fund_profile.py                 # 全量
    python scripts/collect_fund_profile.py --replay        # 只用缓存重建库，不联网
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "fund_quant.db")
CACHE = os.path.join(ROOT, "data", "fund_profile_cache.jsonl")
PROGRESS = os.path.join(ROOT, "data", "fund_profile_progress.json")
CN = timezone(timedelta(hours=8))

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120 Safari/537.36",
       "Referer": "https://fund.eastmoney.com/"}


# ============================ 解析 ============================

def _balanced(txt, start):
    """从 start 处的 [ 或 { 开始按括号配对取完整字面量（支持字符串内的括号）"""
    if start >= len(txt) or txt[start] not in "[{":
        return None
    oc, cc = txt[start], ("]" if txt[start] == "[" else "}")
    depth, i, in_str, esc = 0, start, False, False
    while i < len(txt):
        c = txt[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == oc:
                depth += 1
            elif c == cc:
                depth -= 1
                if depth == 0:
                    return txt[start:i + 1]
        i += 1
    return None


def _obj(txt, var):
    m = re.search(r"var\s+%s\s*=\s*" % re.escape(var), txt)
    if not m:
        return None
    lit = _balanced(txt, m.end())
    if not lit:
        return None
    try:
        return json.loads(lit)
    except Exception:
        return None


def _num(txt, var):
    m = re.search(r'var\s+%s\s*=\s*"?(-?[0-9.]+)"?' % re.escape(var), txt)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def parse_worktime(s):
    """'13年又361天' → 13.99 年；'6年又27天' → 6.07；兼容 '3年'、'100天'"""
    if not s:
        return None
    y = re.search(r"(\d+)\s*年", s)
    d = re.search(r"(\d+)\s*天", s)
    yy = int(y.group(1)) if y else 0
    dd = int(d.group(1)) if d else 0
    if not y and not d:
        return None
    return round(yy + dd / 365.0, 2)


def parse_profile(code, txt):
    """从 pingzhongdata 原文抽全部档案字段。返回 dict（键与 fund_profile_extra 对齐）"""
    p = {"fund_code": code}

    # ---- 规模：Data_fluctuationScale（季度序列，**必须连日期一起存**）----
    fs = _obj(txt, "Data_fluctuationScale")
    if isinstance(fs, dict):
        cats = fs.get("categories") or []
        ser = fs.get("series") or []
        if ser and isinstance(ser[-1], dict) and ser[-1].get("y") is not None:
            p["fund_size"] = float(ser[-1]["y"])
            p["fund_size_date"] = cats[-1] if cats else None
            p["fund_size_n"] = len(ser)

    # ---- 基金经理 ----
    mgrs = _obj(txt, "Data_currentFundManager")
    if isinstance(mgrs, list) and mgrs:
        m0 = mgrs[0]
        p["manager_name"] = m0.get("name")
        p["mgr_star"] = m0.get("star")
        p["mgr_worktime_raw"] = m0.get("workTime")
        p["manager_tenure"] = parse_worktime(m0.get("workTime"))
        p["mgr_count"] = len(mgrs)

    # ---- 费率 / 起购 ----
    p["fee_actual"] = _num(txt, "fund_Rate")           # 实收费率（打折后）
    p["fee_source"] = _num(txt, "fund_sourceRate")      # 原费率
    p["min_purchase"] = _num(txt, "fund_minsg")

    # ---- 同类百分位（天天基金自己的口径，用于**独立验证本项目参照系**）----
    rp = _obj(txt, "Data_rateInSimilarPersent")
    if isinstance(rp, list) and rp and isinstance(rp[-1], list):
        p["peer_pct"] = rp[-1][1]
        p["peer_date"] = datetime.fromtimestamp(rp[-1][0] / 1000, CN).strftime("%Y-%m-%d")
        p["peer_n"] = len(rp)
    rt = _obj(txt, "Data_rateInSimilarType")
    if isinstance(rt, list) and rt and isinstance(rt[-1], dict):
        try:
            p["peer_rank"] = int(rt[-1]["y"])
            p["peer_total"] = int(rt[-1]["sc"])
        except (TypeError, ValueError, KeyError):
            pass

    # ---- 阶段收益（源头口径，可交叉验证本地计算）----
    for k, var in [("ret_1m", "syl_1y"), ("ret_3m", "syl_3y"),
                   ("ret_6m", "syl_6y"), ("ret_1y", "syl_1n")]:
        p[k] = _num(txt, var)

    # ---- 资产配置（股票占净比，最近一期）----
    aa = _obj(txt, "Data_assetAllocation")
    if isinstance(aa, dict):
        for s in (aa.get("series") or []):
            if not isinstance(s, dict):
                continue
            nm = str(s.get("name") or "")
            dat = s.get("data") or []
            if dat and dat[-1] is not None:
                if "股票" in nm:
                    p["stock_pct"] = float(dat[-1])
                elif "债券" in nm:
                    p["bond_pct"] = float(dat[-1])
                elif "现金" in nm:
                    p["cash_pct"] = float(dat[-1])

    p["profile_at"] = datetime.now(CN).strftime("%Y-%m-%d %H:%M:%S")
    return p


# ============================ 抓取 ============================

def fetch(code, retry=2):
    url = "https://fund.eastmoney.com/pingzhongdata/%s.js" % code
    last = None
    for a in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers=HDR)
            with urllib.request.urlopen(req, timeout=25) as r:
                b = r.read()
            if len(b) < 200:
                raise ValueError("body too small %d" % len(b))
            return b.decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            if a < retry:
                time.sleep(0.8 * (a + 1))
    raise last


def job(code):
    try:
        return code, True, parse_profile(code, fetch(code))
    except Exception as e:
        return code, False, "%s: %s" % (type(e).__name__, str(e)[:60])


# ============================ 建表 ============================

EXTRA_COLS = [
    ("fund_size", "REAL"), ("fund_size_date", "TEXT"), ("fund_size_n", "INTEGER"),
    ("manager_name", "TEXT"), ("manager_tenure", "REAL"),
    ("mgr_star", "INTEGER"), ("mgr_worktime_raw", "TEXT"), ("mgr_count", "INTEGER"),
    ("fee_actual", "REAL"), ("fee_source", "REAL"), ("min_purchase", "REAL"),
    ("peer_pct", "REAL"), ("peer_rank", "INTEGER"), ("peer_total", "INTEGER"),
    ("peer_date", "TEXT"), ("peer_n", "INTEGER"),
    ("ret_1m", "REAL"), ("ret_3m", "REAL"), ("ret_6m", "REAL"), ("ret_1y", "REAL"),
    ("stock_pct", "REAL"), ("bond_pct", "REAL"), ("cash_pct", "REAL"),
    ("profile_at", "TEXT"),
]


def ensure_table(conn):
    """纯追加建表：新增表不会影响任何既有代码（零风险）"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fund_profile_extra (
            fund_code TEXT PRIMARY KEY,
            fund_size REAL, fund_size_date TEXT, fund_size_n INTEGER,
            manager_name TEXT, manager_tenure REAL,
            mgr_star INTEGER, mgr_worktime_raw TEXT, mgr_count INTEGER,
            fee_actual REAL, fee_source REAL, min_purchase REAL,
            peer_pct REAL, peer_rank INTEGER, peer_total INTEGER,
            peer_date TEXT, peer_n INTEGER,
            ret_1m REAL, ret_3m REAL, ret_6m REAL, ret_1y REAL,
            stock_pct REAL, bond_pct REAL, cash_pct REAL,
            profile_at TEXT
        )""")
    have = {r[1] for r in conn.execute("PRAGMA table_info(fund_profile_extra)")}
    for c, t in EXTRA_COLS:
        if c not in have:
            conn.execute(f"ALTER TABLE fund_profile_extra ADD COLUMN {c} {t}")


def upsert(conn, rows):
    cols = [c for c, _ in EXTRA_COLS]
    ph = ",".join("?" * (len(cols) + 1))
    sets = ",".join(f"{c}=COALESCE(excluded.{c}, fund_profile_extra.{c})" for c in cols)
    sql = (f"INSERT INTO fund_profile_extra (fund_code,{','.join(cols)}) VALUES ({ph}) "
           f"ON CONFLICT(fund_code) DO UPDATE SET {sets}, profile_at=excluded.profile_at")
    conn.executemany(sql, [tuple([r["fund_code"]] + [r.get(c) for c in cols]) for r in rows])


def fill_fund_info(conn, rows):
    """只填空值，绝不覆盖已有数据（铁律：不污染既有事实）"""
    sql = """UPDATE fund_info SET
               fund_size      = COALESCE(fund_size, ?),
               manager_tenure = COALESCE(manager_tenure, ?),
               manager_name   = COALESCE(manager_name, ?),
               purchase_fee   = COALESCE(purchase_fee, ?)
             WHERE fund_code = ?"""
    conn.executemany(sql, [(r.get("fund_size"), r.get("manager_tenure"),
                            r.get("manager_name"), r.get("fee_actual"),
                            r["fund_code"]) for r in rows])


# ============================ 主流程 ============================

def report_fill(conn, tag):
    cur = conn.cursor()
    n = cur.execute("select count(*) from fund_info").fetchone()[0]
    print("  [%s] fund_info 填充率（分母 %s）:" % (tag, f"{n:,}"))
    for c in ["fund_size", "manager_tenure", "manager_name", "purchase_fee",
              "custodian_fee", "establish_date", "mgt_fee"]:
        v = cur.execute(f"select count(*) from fund_info where {c} is not null").fetchone()[0]
        print("      %-16s %7s  %5.1f%%" % (c, f"{v:,}", v / n * 100))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--replay", action="store_true", help="只用缓存重建，不联网")
    ap.add_argument("--batch", type=int, default=400, help="每 N 只落盘一次")
    args = ap.parse_args()

    # ---- 铁律 2：写库前必须有快照 ----
    snaps = [f for f in os.listdir(os.path.join(ROOT, "data"))
             if f.startswith("_snapshot_") and f.endswith(".db")]
    if not snaps:
        print("未找到 data/_snapshot_*.db —— 写库前必须先做完整快照（铁律 2）")
        return 1
    print("✓ 快照存在: %s" % sorted(snaps)[-1])

    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    ensure_table(conn)

    print("\n[0] 补采前填充率")
    report_fill(conn, "before")

    # ---- 目标：数据源覆盖池 ----
    codes = [r[0] for r in conn.execute(
        "select fund_code from fund_info where purchase_status like '%开放%' order by fund_code")]

    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    cache[r["fund_code"]] = r
                except Exception:
                    pass
    print("\n[1] 目标 %s 只 | 已有缓存 %s 只" % (f"{len(codes):,}", f"{len(cache):,}"))

    todo = [c for c in codes if c not in cache] if not args.replay else []
    if args.limit:
        todo = todo[:args.limit]
    print("    本次待采 %s 只" % f"{len(todo):,}")

    # ---- 抓取 ----
    ok = fail = 0
    failed = []
    buf = []
    if todo:
        t0 = time.time()
        with open(CACHE, "a", encoding="utf-8") as cf, ThreadPoolExecutor(args.workers) as ex:
            for i, (code, good, res) in enumerate(ex.map(job, todo), 1):
                if good:
                    ok += 1
                    buf.append(res)
                    cache[code] = res
                    cf.write(json.dumps(res, ensure_ascii=False) + "\n")
                else:
                    fail += 1
                    failed.append((code, res))
                if i % args.batch == 0 or i == len(todo):
                    cf.flush()
                    with conn:
                        upsert(conn, buf)
                        fill_fund_info(conn, buf)
                    buf = []
                if i % 500 == 0 or i == len(todo):
                    el = time.time() - t0
                    print("    %6d/%s  成功 %s 失败 %s  %.1f 只/秒  剩余约 %.0f 分钟"
                          % (i, f"{len(todo):,}", f"{ok:,}", fail, i / max(el, .1),
                             (len(todo) - i) / max(i / max(el, .1), .1) / 60), flush=True)
        with conn:
            upsert(conn, buf)
            fill_fund_info(conn, buf)
    else:
        # replay：把缓存全量重灌（不联网即可重建）
        print("    --replay：从缓存重灌 %s 只" % f"{len(cache):,}")
        rows = list(cache.values())
        with conn:
            for i in range(0, len(rows), 1000):
                upsert(conn, rows[i:i + 1000])
                fill_fund_info(conn, rows[i:i + 1000])

    if failed:
        json.dump(failed, open(os.path.join(ROOT, "data", "fund_profile_failed.json"), "w",
                               encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n[2] 补采后填充率")
    report_fill(conn, "after")

    # ---- 完成即验证（规则 15）----
    cur = conn.cursor()
    print("\n[3] fund_profile_extra 验证")
    n = cur.execute("select count(*) from fund_profile_extra").fetchone()[0]
    print("    行数 %s" % f"{n:,}")
    for c in ["fund_size", "manager_tenure", "fee_actual", "peer_pct",
              "ret_1y", "stock_pct", "fund_size_date"]:
        v = cur.execute(f"select count(*) from fund_profile_extra where {c} is not null").fetchone()[0]
        print("    %-16s %7s  %5.1f%%" % (c, f"{v:,}", v / n * 100 if n else 0))
    print("\n    抽样 5 条:")
    for r in cur.execute("""select fund_code, fund_size, fund_size_date, manager_name,
                                   manager_tenure, fee_actual, fee_source, peer_pct,
                                   peer_rank, peer_total, ret_1y
                            from fund_profile_extra limit 5"""):
        print("     ", r)

    print("\n[4] 死阈值复活检查（4 个依赖字段）")
    for c, th in [("fund_size", "min_size_yi 0.5亿 / max_size_yi 200亿"),
                  ("manager_tenure", "min_manager_years 2年")]:
        v = cur.execute(f"select count(*) from fund_info where {c} is not null").fetchone()[0]
        tot = cur.execute("select count(*) from fund_info").fetchone()[0]
        print("    %-16s %7s / %s  %.1f%%   ← %s" % (c, f"{v:,}", f"{tot:,}", v / tot * 100, th))

    conn.close()
    print("\n缓存: %s" % os.path.relpath(CACHE, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
