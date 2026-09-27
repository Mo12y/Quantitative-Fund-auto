# -*- coding: utf-8 -*-
"""净值双源校验（数据源扩展计划书 阶段 2）—— **只读，绝不覆盖主源**。

第二源的选择与它的局限（必须写进报告）
--------------------------------------
计划书原文建议用同花顺，但其配额/计费未核实 → 按铁律「付费服务先设闸」不接入。
本脚本用**天天基金 pingzhongdata**（`fund.eastmoney.com/pingzhongdata/<code>.js`），
它与主源 akshare 的 `fund_open_fund_info_em`（JSON API）是**两条不同链路**。
因此它抓得住**解析/口径 bug**（本项目历史上出过 ms_to_date 时区事故），
但**抓不住"东财整体算错"** —— 报告里如实声明，不夸大成"两数据商互校"。

验收条款（计划书 阶段 2）："给出比对结果；并故意注入一个日期错位，证明校验器能抓到。"
→ 注入验证在 `tests/test_nav_dual_source.py::test_injected_date_shift_is_caught`（确定性，不依赖网络）。

用法
----
    python scripts/verify_nav_dual_source.py                 # 当前持仓，最多 5 只
    python scripts/verify_nav_dual_source.py --all           # 当前持仓全部
    python scripts/verify_nav_dual_source.py --codes 016453,007029
    python scripts/verify_nav_dual_source.py --json data/nav_dual_source_report.json
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.nav_dual_source import SEV_MAJOR, SEV_MISSING_LOCAL, SEV_MISSING_REMOTE, compare_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "fund_quant.db")
TZ = ZoneInfo("Asia/Shanghai")       # 时间戳→日期必须锁北京时区（本项目踩过 ms_to_date 时区坑）

REMOTE_NOTE = ("第二源 = 天天基金 pingzhongdata（与 akshare 走的 JSON 接口不同链路，但**同属东财**）："
               "能抓解析/口径差异，抓不到'东财整体算错'。")


def load_local(conn, code: str) -> dict:
    rows = conn.execute(
        "SELECT nav_date, unit_nav, acc_nav FROM fund_nav WHERE fund_code=? ORDER BY nav_date",
        (code,))
    return {r[0]: {"unit_nav": r[1], "acc_nav": r[2]} for r in rows}


def _ts_to_date(ts) -> str:
    return datetime.fromtimestamp(float(ts) / 1000.0, TZ).strftime("%Y-%m-%d")


def fetch_remote(code: str, timeout: int = 20) -> dict:
    """抓天天基金 pingzhongdata → `{date: {unit_nav, acc_nav}}`。失败抛异常（由调用方声明）。"""
    import requests
    url = "https://fund.eastmoney.com/pingzhongdata/%s.js" % code
    r = requests.get(url, timeout=timeout,
                     headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://fund.eastmoney.com/"})
    r.raise_for_status()
    txt = r.text
    out = {}
    m = re.search(r"var\s+Data_netWorthTrend\s*=\s*(\[.*?\]);", txt, re.S)
    if m:
        for it in json.loads(m.group(1)):
            if it.get("x") is None or it.get("y") in (None, ""):
                continue
            try:
                out.setdefault(_ts_to_date(it["x"]), {})["unit_nav"] = float(it["y"])
            except (TypeError, ValueError):
                continue
    m2 = re.search(r"var\s+Data_ACWorthTrend\s*=\s*(\[.*?\]);", txt, re.S)
    if m2:
        for pair in json.loads(m2.group(1)):
            try:
                out.setdefault(_ts_to_date(pair[0]), {})["acc_nav"] = float(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
    return out


def pick_codes(conn, args) -> list:
    if args.codes:
        return [c.strip() for c in args.codes.split(",") if c.strip()]
    rows = conn.execute(
        "SELECT DISTINCT fund_code FROM holdings WHERE status IN "
        "('holding','pending_confirm','sell_pending') ORDER BY fund_code")
    codes = [r[0] for r in rows]
    return codes if args.all else codes[:5]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", help="逗号分隔的基金代码（默认取当前持仓）")
    ap.add_argument("--all", action="store_true", help="持仓全部（默认只前 5 只）")
    ap.add_argument("--json", help="把报告写到该路径")
    ap.add_argument("--sleep", type=float, default=0.4, help="两只之间的间隔秒数（礼貌抓取）")
    args = ap.parse_args()

    conn = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)   # 只读打开：本脚本绝不写库
    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "remote_note": REMOTE_NOTE,
              "funds": [], "totals": {}}
    tot = {SEV_MAJOR: 0, SEV_MISSING_LOCAL: 0, SEV_MISSING_REMOTE: 0, "n_shifted": 0,
           "n_common": 0, "n_funds": 0, "n_failed": 0}

    print("第二源：%s\n" % REMOTE_NOTE)
    for code in pick_codes(conn, args):
        local = load_local(conn, code)
        try:
            remote = fetch_remote(code)
            err = None
        except Exception as e:                       # noqa: BLE001
            remote, err = {}, str(e)[:120]
        if err or not remote or not local:
            tot["n_failed"] += 1
            print("[%-6s] 跳过：%s" % (code, err or ("本地 %d 条 / 远端 %d 条" % (len(local), len(remote)))))
            report["funds"].append({"code": code, "error": err, "n_local": len(local),
                                    "n_remote": len(remote)})
            time.sleep(args.sleep)
            continue

        r = compare_series(local, remote)
        c = r["counts"]
        tot[SEV_MAJOR] += c[SEV_MAJOR]
        tot[SEV_MISSING_LOCAL] += c[SEV_MISSING_LOCAL]
        tot[SEV_MISSING_REMOTE] += c[SEV_MISSING_REMOTE]
        tot["n_shifted"] += r["n_shifted"]
        tot["n_common"] += r["n_common"]
        tot["n_funds"] += 1

        flag = "⚠️" if (c[SEV_MAJOR] or r["n_shifted"]) else "✅"
        print("%s [%-6s] 本地 %-5d 远端 %-5d 共同 %-5d | 一致 %-5d 轻微 %-4d 严重 %-3d 缺失 %-3d 错位 %d"
              % (flag, code, r["n_local"], r["n_remote"], r["n_common"],
                 c["ok"], c["minor"], c[SEV_MAJOR],
                 c[SEV_MISSING_LOCAL] + c[SEV_MISSING_REMOTE], r["n_shifted"]))
        # 明细优先展示"严重差 / 日期错位"，没有时才展示"单侧缺失"
        # （缺失常是"远端已更新、本地还没采"，也需要看见，否则会被当成一致）
        interesting = [a for a in r["alerts"]
                       if a["severity"] == SEV_MAJOR or a.get("shift_days")]
        if not interesting:
            interesting = [a for a in r["alerts"]
                           if a["severity"] in (SEV_MISSING_LOCAL, SEV_MISSING_REMOTE)]
        for a in interesting[:5]:
            print("       %s %s 本地=%s 远端=%s %s%s"
                  % (a["date"], a["field"], a["local"], a["remote"], a["severity"],
                     ("  ← " + a["hint"]) if a.get("hint") else ""))
        miss = sorted({a["date"] for a in r["alerts"]
                       if a["severity"] in (SEV_MISSING_LOCAL, SEV_MISSING_REMOTE)})
        if miss:
            # 缺失几乎总是"远端已更新、本地还没采" —— 把它汇成日期范围，
            # 一眼能看出是"落后 N 天"还是"某个历史区间整段缺失"（后者才是数据事故）
            print("       缺失日期范围: %s ~ %s（%d 个日期）"
                  % (miss[0], miss[-1], len(miss)))
        report["funds"].append({"code": code, **{k: r[k] for k in
                                                 ("n_local", "n_remote", "n_common", "counts", "n_shifted", "verdict")},
                                "alerts": r["alerts"][:50]})
        time.sleep(args.sleep)

    conn.close()
    report["totals"] = tot
    print("\n=== 合计 ===")
    print("比对基金 %d 只（失败 %d）· 共同日期 %d 个" % (tot["n_funds"], tot["n_failed"], tot["n_common"]))
    print("严重差 %d · 单侧缺失 %d · 疑似日期错位 %d" % (tot[SEV_MAJOR], tot[SEV_MISSING_LOCAL] + tot[SEV_MISSING_REMOTE], tot["n_shifted"]))
    print("注：本脚本**只读**。发现差异不改库，由人决定如何处理。")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print("报告已写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
