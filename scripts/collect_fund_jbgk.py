# -*- coding: utf-8 -*-
"""
⑥ 东财 F10「基金概况」补采 —— 数据源扩展计划书 · 阶段 1 的剩余部分
=====================================================================
为什么做（实测，2026-09-26 只读盘点 fund_info 27,864 只）：
    establish_date  550   1.97%   ← 筛选器「成立不足 12 个月 → 不通过」几乎全跳过
    company_name    550   1.97%
    benchmark         0   0.00%   ← 计划书 T1-3 点名的字段
    redeem_fee        0   0.00%
    sales_service_fee 11,054 39.67%   ← TER 的组成项（fund_fee.compute_ter）
    purchase_fee      12,987 46.61%
    **持仓 7 只：establish_date / company_name / benchmark 全部 7/7 缺失**
另外 fund_size 64.60%（本脚本会对缺的持仓 2 只补上）。

数据源（已实测，2026-09-26 抓取 017470 页面并解析成功）：
    东财 F10 `https://fundf10.eastmoney.com/jbgk_<code>.html`
    可解析字段：成立日期/规模、基金管理人、业绩比较基准、跟踪标的、
    管理费率、托管费率、销售服务费率、最高认购/申购/赎回费率、净资产规模、基金经理人

为什么不用同花顺 HiThink（计划书 T1-1 的原方案）：
    计划书 §6 待确认事项 #1（配额/计费）**未核实** —— 按项目铁律「付费服务先设闸」，
    在计费方式确认前不接入；本脚本走已验证的**免费东财通道**达到同一目的。

写入策略：**只填空、绝不覆盖**
    走 `Database.upsert_fund_info` 的局部更新语义（NULL/空串不覆盖；数字字段 0 视为未采到）。
    因此 C 类「申购费 0.00%」不会被写入（项目另有按份额类别的 `purchase_fee_rate` 兜底，
    属预期行为，不是丢数据）。

范围控制（计划书 T1 明文：先跑持仓 + 候选池，**不要**一次跑 27,852 只）：
    --scope holdings          持仓（holding / pending_confirm）
    --scope pool              UI「基金筛选池」快照（analysis_snapshot key=funds）
    --scope holdings,pool     默认

用法：
    python scripts/collect_fund_jbgk.py --dry-run --limit 3      # 只抓取解析、不写库
    python scripts/collect_fund_jbgk.py                          # 默认范围，写库（需先有快照）
    python scripts/collect_fund_jbgk.py --replay                 # 只用缓存重建，不联网
缓存：data/fund_jbgk_cache.jsonl（以后重解析不必再联网）

铁律：写库前必须有 `data/_snapshot_*.db`（本脚本会检查并中止）。
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DB = os.path.join(ROOT, "data", "fund_quant.db")
CACHE = os.path.join(ROOT, "data", "fund_jbgk_cache.jsonl")
CN = timezone(timedelta(hours=8))

from src.data.database import Database            # noqa: E402
from src.data.collector import DataCollector      # noqa: E402  费率解析 SSOT（不重复实现）

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120 Safari/537.36",
       "Referer": "https://fund.eastmoney.com/"}

#: 本脚本负责的字段（打印填充率对照用）
FIELDS = ("establish_date", "company_name", "benchmark", "fund_size", "manager_name",
          "mgt_fee", "custodian_fee", "sales_service_fee", "purchase_fee", "redeem_fee")


# ============================ 解析 ============================

def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return s.replace("&nbsp;", " ").replace("&amp;", "&").strip()


def _pairs(html: str) -> dict:
    """取 jbgk 表格里的「表头 → 值」。

    ⚠️ 该页 HTML 有**未闭合的 `<td>`**（如 `…<td>017470（前端）<th>基金类型</th>…`），
    正则直接配 th/td 会串行错位。改为：先按 `<tr>` 切行，再按开标签切段，
    依标签出现顺序 `zip` 回内容，最后「th 后面紧跟的 td」即为该表头的值。
    """
    out = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        kinds = re.findall(r"<t([hd])[^>]*>", row)
        segs = re.split(r"<t[hd][^>]*>", row)[1:]
        cells = [(k, _clean(re.split(r"</t[hd]>", seg)[0])) for k, seg in zip(kinds, segs)]
        i = 0
        while i < len(cells) - 1:
            k, label = cells[i]
            if k == "h" and cells[i + 1][0] == "d" and label:
                out[label] = cells[i + 1][1]
                i += 2
            else:
                i += 1
    return out


def _date_cn(raw):
    """'2022年12月07日 / 0.116亿份' → '2022-12-07'（无日期 → None）"""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", str(raw or ""))
    return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


def _size_yi(raw):
    """'157.00亿元（截止至：2026年06月30日）' → (157.0, '2026-06-30')；非亿元 → (None, None)"""
    s = str(raw or "")
    m = re.search(r"([\d.]+)\s*亿", s)
    if not m:
        return None, None
    try:
        v = float(m.group(1))
    except ValueError:
        return None, None
    d = _date_cn(s)
    return (v if v > 0 else None), d


def _redeem_text(raw):
    """'1.50%（前端）' → '1.50%'（解析口径与 portfolio.parse_redeem_fee 兼容）"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(raw or ""))
    return ("%s%%" % m.group(1)) if m else None


def parse_jbgk(code: str, html: str) -> dict:
    """页面 → 字段 dict（键 = fund_info 列名；缺字段给 None，不编造）"""
    d = _pairs(html)
    size, size_date = _size_yi(d.get("净资产规模"))
    out = {
        "fund_code": code,
        "establish_date": _date_cn(d.get("成立日期/规模")),
        "company_name": d.get("基金管理人") or None,
        "benchmark": d.get("业绩比较基准") or None,
        "fund_size": size,
        "fund_size_date": size_date,
        "manager_name": d.get("基金经理人") or None,
        "mgt_fee": DataCollector._parse_fee(d.get("管理费率")),
        "custodian_fee": DataCollector._parse_fee(d.get("托管费率")),
        "sales_service_fee": DataCollector._parse_fee(d.get("销售服务费率")),
        "purchase_fee": DataCollector._parse_fee(d.get("最高申购费率")),
        "redeem_fee": _redeem_text(d.get("最高赎回费率")),
        "tracking": d.get("跟踪标的") or None,
        "profile_at": datetime.now(CN).strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 该页空值标记（'---'/'--'）一律归 None：不把"没采到"写成 0 或空串
    for k, v in list(out.items()):
        if isinstance(v, str) and v.strip() in ("---", "--", "-", "暂无", "暂无数据"):
            out[k] = None
    return out


def fetch(code: str, retry: int = 2) -> str:
    url = "https://fundf10.eastmoney.com/jbgk_%s.html" % code
    last = None
    for a in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers=HDR)
            with urllib.request.urlopen(req, timeout=25) as r:
                b = r.read()
            if len(b) < 500:
                raise ValueError("body too small %d" % len(b))
            txt = b.decode("utf-8", errors="replace")
            if "成立日期" not in txt:
                raise ValueError("页面不含基金概况表（可能代码不存在）")
            return txt
        except Exception as e:
            last = e
            if a < retry:
                time.sleep(0.8 * (a + 1))
    raise last


def job(code: str):
    try:
        return code, True, parse_jbgk(code, fetch(code))
    except Exception as e:
        return code, False, "%s: %s" % (type(e).__name__, str(e)[:60])


# ============================ 写库 ============================

def upsert(db: Database, rows):
    """走 Database.upsert_fund_info（局部更新语义，只填空不覆盖）。

    rows 里的额外键（fund_size_date / tracking / profile_at）不在 upsert 的字段清单里，
    会被它忽略（它只遍历 `_FUND_INFO_FIELDS`）—— 故可整条传入。
    """
    for r in rows:
        db.upsert_fund_info(r, commit=False)
    db.conn.commit()


def fill_report(db: Database, tag: str, codes=None):
    """打印目标字段在当前库里的**非空率**（可按 codes 限定范围）"""
    cur = db.conn.cursor()
    where = ""
    params = []
    if codes:
        where = " WHERE fund_code IN (%s)" % ",".join("?" * len(codes))
        params = list(codes)
    n = cur.execute("SELECT COUNT(*) FROM fund_info" + where, params).fetchone()[0]
    print("  [%s] 范围 %s 只" % (tag, f"{n:,}"))
    for c in FIELDS:
        v = cur.execute("SELECT COUNT(*) FROM fund_info%s AND %s IS NOT NULL AND TRIM(%s) != ''"
                        % (where or " WHERE 1=1", c, c), params).fetchone()[0]
        print("      %-18s %6d  %6.2f%%" % (c, v, v / n * 100 if n else 0))


# ============================ 范围 ============================

def scope_codes(db: Database, scope: str) -> list:
    """返回本次要采的基金代码（去重排序，可复现）"""
    codes = set()
    if "holdings" in scope:
        codes |= {r[0] for r in db.conn.execute(
            "SELECT DISTINCT fund_code FROM holdings WHERE status IN ('holding','pending_confirm')")}
    if "pool" in scope:
        snap = db.get_analysis_snapshot("funds")     # UI 基金筛选池快照
        if snap:
            codes |= {str(f.get("code")) for f in (snap.get("funds") or []) if f.get("code")}
        else:
            print("  ⚠️ 未找到 funds 快照（筛选池未预热）→ pool 范围为空；"
                  "可先刷新 Web 页面或显式 --codes")
    return sorted(codes)


# ============================ 主流程 ============================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="holdings,pool")
    ap.add_argument("--codes", default="", help="显式代码列表（逗号分隔），给了就只用它")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="只抓取解析，不写库")
    ap.add_argument("--replay", action="store_true", help="只用缓存重建，不联网")
    args = ap.parse_args()

    # ---- 铁律：写库前必须有整库快照 ----
    snaps = sorted([f for f in os.listdir(os.path.join(ROOT, "data"))
                    if f.startswith("_snapshot_") and f.endswith(".db")])
    if not snaps:
        print("未找到 data/_snapshot_*.db —— 写库前必须先做完整快照（铁律）")
        return 1
    print("✓ 快照存在: %s（最新）" % snaps[-1])

    db = Database(DB)
    if args.codes:
        codes = sorted({c.strip() for c in args.codes.split(",") if c.strip()})
    else:
        codes = scope_codes(db, args.scope)
    if args.limit:
        codes = codes[:args.limit]
    print("\n[1] 目标 %d 只：%s" % (len(codes), ",".join(codes[:12]) + (" ..." if len(codes) > 12 else "")))
    if not codes:
        print("    无可采代码，退出")
        return 0

    if not args.dry_run:
        print("\n[0] 补采前填充率")
        fill_report(db, "before", codes)

    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    cache[r["fund_code"]] = r
                except Exception:
                    pass
    todo = [] if args.replay else [c for c in codes if c not in cache]
    print("\n[2] 已有缓存 %d 只 | 本次待采 %d 只" % (len(cache), len(todo)))

    rows, ok, fail, failed = [], 0, 0, []
    if todo:
        t0 = time.time()
        with open(CACHE, "a", encoding="utf-8") as cf, ThreadPoolExecutor(args.workers) as ex:
            for i, (code, good, res) in enumerate(ex.map(job, todo), 1):
                if good:
                    ok += 1
                    rows.append(res)
                    cache[code] = res
                    cf.write(json.dumps(res, ensure_ascii=False) + "\n")
                else:
                    fail += 1
                    failed.append((code, res))
                if i % 50 == 0 or i == len(todo):
                    cf.flush()
                    print("    %d/%d 成功 %d 失败 %d  %.1f 只/秒"
                          % (i, len(todo), ok, fail, i / max(time.time() - t0, .1)), flush=True)

    if failed:
        json.dump(failed, open(os.path.join(ROOT, "data", "fund_jbgk_failed.json"), "w",
                               encoding="utf-8"), ensure_ascii=False, indent=1)

    # ⚠️ 写库范围 = **范围内所有已在缓存里的代码**（不只是本次新抓的）——
    # 否则"上次抓了但没写"的代码会被永久跳过（2026-09-26 实测踩到：
    # dry-run 抓过的 3 只持仓因此没入库）。缓存即"已解析"，写库与抓取解耦。
    write_rows = [cache[c] for c in codes if c in cache]

    if args.dry_run:
        print("\n[dry-run] 不写库。抽样 3 条解析结果：")
        for r in (write_rows or rows)[:3]:
            print("   ", {k: r.get(k) for k in ("fund_code", "establish_date", "company_name",
                                                "benchmark", "fund_size", "redeem_fee",
                                                "sales_service_fee", "purchase_fee")})
        db.close()
        return 0

    print("\n[3] 写库 %d 只（只填空，不覆盖）…" % len(write_rows))
    if write_rows:
        upsert(db, write_rows)
    print("\n[4] 补采后填充率")
    fill_report(db, "after", codes)

    print("\n[5] 抽样 5 条（库内现值）")
    cur = db.conn.cursor()
    q = ",".join("?" * len(codes[:200]))
    for r in cur.execute(
            "SELECT fund_code, establish_date, company_name, benchmark, fund_size, "
            "sales_service_fee, purchase_fee, redeem_fee FROM fund_info "
            "WHERE fund_code IN (%s) LIMIT 5" % q, codes[:200]):
        print("   ", tuple(r))
    db.close()
    print("\n缓存: %s" % os.path.relpath(CACHE, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())